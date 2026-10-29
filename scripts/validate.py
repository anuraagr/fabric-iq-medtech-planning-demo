"""Run, record and render acceptance tests.

    python scripts/validate.py run --scope local            # local checks only (no tenant access)
    python scripts/validate.py run --scope live             # read-only live checks against the tenant
    python scripts/validate.py record T-09 --status passed --observed "..." [--evidence "..."]
    python scripts/validate.py render                       # write the table in reports/deployment-status.md (ignored)
    python scripts/validate.py list

A test that cannot run yet (for example because a native UI step has not been performed) is reported as
`not_executed`, never as `passed`.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import permissions as permissions_cli  # noqa: E402
from scripts import reset as reset_cli  # noqa: E402
from scripts.data.medtech_catalog import MONTH_SHORT  # noqa: E402
from scripts.data.medtech_generator import build_dataset  # noqa: E402
from scripts.fetch_sample_assets import ASSET_DIR, verify as verify_assets  # noqa: E402
from scripts.lib import comments as comments_lib  # noqa: E402
from scripts.lib import permissions as permissions_lib  # noqa: E402
from scripts.lib import powertable_rules  # noqa: E402
from scripts.lib import reporting as reporting_lib  # noqa: E402
from scripts.lib import scenario as scenario_lib  # noqa: E402
from scripts.lib.acceptance import ResultStore, AcceptanceTest, load_definitions, render_markdown, summarize  # noqa: E402
from scripts.lib.context import Context  # noqa: E402
from scripts.lib.config import REPO_ROOT, load_sample  # noqa: E402
from scripts.lib.fabric import sku_planning_class  # noqa: E402
from scripts.lib.finance import D, find_duplicate_grains  # noqa: E402
from scripts.lib.http import ApiError, is_capacity_rejection  # noqa: E402
from scripts.lib.medtech_checks import verify_model  # noqa: E402
from scripts.lib.sqlclient import SqlSession, connect  # noqa: E402
from scripts.lib.writeback import parse_table_ref, read_rows  # noqa: E402

DEFS_PATH = REPO_ROOT / "config" / "acceptance-tests.yaml"
RESULTS_PATH = REPO_ROOT / "state" / "acceptance-results.json"
STATUS_DOC = REPO_ROOT / "reports" / "deployment-status.md"
MEDTECH_EXPECTED = REPO_ROOT / "model" / "medtech" / "expected-totals.json"
BEGIN, END = "<!-- ACCEPTANCE:BEGIN -->", "<!-- ACCEPTANCE:END -->"
EXPECTED_2025 = {"Gross Revenue 2025": Decimal("25866900"), "COGS 2025": Decimal("14166310"), "Gross Profit 2025": Decimal("11700590")}
# Sheets that the MedTech plan must contain after the native UI build (see docs/manual-setup.md)
MEDTECH_SHEETS = {"FY2026 Revenue Budget", "Rolling Forecast", "Scenario Comparison", "Profitability P&L", "Assets", "Employees", "Locations", "Executive Overview", "Scenario Overview"}
# The Infobridge flow built in the UI: a measure shared from one planning sheet to another with identical row/column dimensions
INFOBRIDGE = {"source": "Rolling Forecast", "measure": "FY2026 Forecast", "consumer": "Scenario Comparison"}
# What the saved visuals of each Intelligence sheet are bound to: the semantic-model measure backed by the written-back budget table (Executive Overview),
# and the planning-sheet scenario measures read through Data > From Sheets, which need no model change (Scenario Overview)
INTELLIGENCE_MARKERS = {"Executive Overview": ("Plan Budget Revenue",), "Scenario Overview": ("Plan (Upside)", "Plan (Downside)")}
DEFAULT_INTELLIGENCE_MARKERS = ("Plan Budget Revenue",)
SECRET_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/-]{30,}"),
    re.compile(r"(?i)(client_secret|password|pwd|accountkey|sharedaccesskey)\s*[:=]\s*['\"]?[^\s'\"<>{}$]{6,}"),
)

Outcome = tuple[str, str]  # (status, observed)


def _pytest(args: list[str]) -> Outcome:
    completed = subprocess.run([sys.executable, "-m", "pytest", *args, "-q", "--no-header"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=600)
    last = [line for line in completed.stdout.strip().splitlines() if line.strip()][-1:] or ["no output"]
    return ("passed" if completed.returncode == 0 else "failed", last[0])


def l01(_: Context) -> Outcome:
    return _pytest(["tests/unit"])


def l02(_: Context) -> Outcome:
    sample = load_sample()
    problems = verify_assets(ASSET_DIR, sample.files)
    return ("passed", f"{len(sample.files)} assets match pinned SHA-256 at commit {sample.commit[:8]}") if not problems else ("failed", "; ".join(problems))


def l03(_: Context) -> Outcome:
    tracked = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.splitlines()
    hits: list[str] = []
    for name in tracked:
        if name.startswith("tests/") or name == "scripts/validate.py" or name.endswith((".png", ".jpg", ".pbix", ".xlsx")):
            continue
        text = (REPO_ROOT / name).read_text(encoding="utf-8", errors="ignore") if (REPO_ROOT / name).is_file() else ""
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                hits.append(f"{name}: {pattern.pattern[:30]}")
    return ("passed", f"{len(tracked)} tracked files scanned, no secret patterns") if not hits else ("failed", "; ".join(hits[:5]))


def l04(_: Context) -> Outcome:
    return _pytest(["tests/unit/test_finance.py"])


def t01(ctx: Context) -> Outcome:
    identity = ctx.clients.tokens.identity()
    if identity["tenantId"].lower() != ctx.cfg.tenant_id.lower():
        return "failed", f"token tenant {identity['tenantId']} != configured {ctx.cfg.tenant_id}"
    workspace = ctx.clients.fabric.get_workspace(ctx.workspace_id())
    capacity = next((c for c in ctx.clients.fabric.list_capacities() if c["id"].lower() == str(workspace.get("capacityId", "")).lower()), None)
    if not capacity:
        return "failed", "workspace is not assigned to a visible capacity"
    ok = capacity.get("state") == "Active" and sku_planning_class(capacity.get("sku", "")) == "supported"
    observed = f"{identity['upn']} @ tenant {identity['tenantId']}; workspace '{workspace['displayName']}' on '{capacity['displayName']}' ({capacity['sku']}, {capacity['state']})"
    return ("passed" if ok else "failed"), observed


def t02(ctx: Context) -> Outcome:
    wid = ctx.workspace_id()
    parts: list[str] = []
    failed = False

    medtech = ctx.manifest.find("SemanticModel", ctx.cfg.names.medtech_model, wid)
    if not medtech:
        return "failed", "MedTech semantic model is not in the manifest"
    checks = verify_model(ctx.clients.powerbi, wid, medtech.id, json.loads(MEDTECH_EXPECTED.read_text(encoding="utf-8")))
    bad = [c for c in checks if not c.ok]
    failed |= bool(bad)
    parts.append(f"MedTech model: {len(checks) - len(bad)}/{len(checks)} reference totals match" + (f" (mismatch: {[c.label for c in bad]})" if bad else ""))

    fmcg = ctx.manifest.find("SemanticModel", ctx.cfg.names.semantic_model, wid)
    if not fmcg:
        return "failed", "; ".join(parts + ["FMCG baseline model is not in the manifest"])
    rows = ctx.clients.powerbi.execute_queries(wid, fmcg.id, (REPO_ROOT / "model" / "dax" / "01_smoke_2025_totals.dax").read_text(encoding="utf-8"))
    if not rows:
        return "failed", "; ".join(parts + ["FMCG DAX returned no rows"])
    observed = {k.strip("[]"): Decimal(str(v)) for k, v in rows[0].items()}
    mismatches = [f"{k}: {observed.get(k)} != {v}" for k, v in EXPECTED_2025.items() if k not in observed or abs(observed[k] - v) > 1]
    failed |= bool(mismatches)
    parts.append("FMCG baseline: " + ", ".join(f"{k}={v:,.0f}" for k, v in observed.items()) + (f"; mismatches {mismatches}" if mismatches else ""))
    return ("failed" if failed else "passed"), "; ".join(parts)


def _writeback_db(ctx: Context):
    record = ctx.manifest.find("SQLDatabase", ctx.cfg.names.writeback_db, ctx.workspace_id())
    if not record:
        raise LookupError("writeback database not in manifest")
    return record


def t03(ctx: Context) -> Outcome:
    record = _writeback_db(ctx)
    token = ctx.clients.tokens.get("sql").value
    with SqlSession(connect(record.extra["serverFqdn"], record.extra["databaseName"], token)) as session:
        name = session.scalar("SELECT DB_NAME()")
        who = session.scalar("SELECT SUSER_NAME()")
        tables = [f"{t['TABLE_SCHEMA']}.{t['TABLE_NAME']}" for t in session.tables()]
    return "passed", f"connected as {who} to {name}; user tables: {tables or 'none yet'}"


def t04(ctx: Context) -> Outcome:
    wid = ctx.workspace_id()
    items = ctx.clients.fabric.list_items(wid)
    wanted = [ctx.cfg.names.medtech_plan, ctx.cfg.names.plan_item]
    present = {name: any(i["type"] == "Plan" and i["displayName"] == name for i in items) for name in wanted}
    system = [i["displayName"] for i in items if i["type"] == "SQLDatabase" and i["displayName"].startswith("__fabric_plan_sys")]
    ok = all(present.values()) and bool(system)
    return ("passed" if ok else "failed"), f"Plan items present: {present}; internal system databases (never queried): {system}"


def _plan_parts(ctx: Context, plan_name: str) -> dict[str, str]:
    wid = ctx.workspace_id()
    plan = ctx.manifest.find("Plan", plan_name, wid)
    body = ctx.clients.fabric.get_plan_definition(wid, plan.id)
    return {p["path"]: base64.b64decode(p["payload"]).decode("utf-8", "replace") for p in body["definition"]["parts"]}


def _intelligence_note(sheets: list[dict], parts: dict[str, str]) -> str:
    """What each saved Intelligence (REPORTING) sheet holds: visuals persisted in the plan definition and how many are bound to the measures it exists to show."""
    notes = []
    for sheet in (s for s in sheets if s.get("sheetType") == "REPORTING"):
        markers = INTELLIGENCE_MARKERS.get(sheet.get("displayName"), DEFAULT_INTELLIGENCE_MARKERS)
        prefix = f"sheets/{sheet['recordGuid']}/visuals/"
        visuals = {path[len(prefix):].split("/")[0]: text for path, text in parts.items() if path.startswith(prefix) and path.endswith("/properties.json")}
        bound = sum(1 for text in visuals.values() if any(marker in text for marker in markers))
        notes.append(f"Intelligence sheet {sheet.get('displayName')!r}: {len(visuals)} saved visual(s), {bound} bound to {' / '.join(repr(m) for m in markers)}")
    return "; ".join(notes) if notes else "no Intelligence sheet"


def t05(ctx: Context) -> Outcome:
    parts = _plan_parts(ctx, ctx.cfg.names.medtech_plan)
    sheets = json.loads(parts["definition.json"]).get("sheets", [])
    if not sheets:
        return "not_executed", "exported definition has 0 sheets: the native UI authoring has not been done yet"
    names = [s.get("displayName") for s in sheets]
    kinds = sorted({s.get("sheetType") for s in sheets})
    missing = sorted(MEDTECH_SHEETS - set(names))
    return ("failed" if missing else "passed"), f"sheets={names}; types={kinds}; missing={missing}; {_intelligence_note(sheets, parts)}"


def t06(ctx: Context) -> Outcome:
    specs = scenario_lib.load_specs(ctx.cfg.raw)
    wid = ctx.workspace_id()
    model = ctx.manifest.find("SemanticModel", ctx.cfg.names.medtech_model, wid)
    if not model:
        return "failed", "MedTech semantic model is not in the manifest"
    baseline = ctx.clients.powerbi.execute_queries(wid, model.id, scenario_lib.baseline_query(specs[0].year))
    if not baseline:
        return "failed", "baseline DAX returned no rows"
    values = {key.strip("[]"): Decimal(str(value)) for key, value in baseline[0].items()}
    with ctx.sql_session(ctx.cfg.names.writeback_db) as session:
        rows = read_rows(session, parse_table_ref(specs[0].table), scenario_lib.GRAIN, scenario_lib.VALUE, latest_column="IsLatest")
    results = [(spec, *scenario_lib.evaluate(rows, spec, values["Revenue"], values["COGS"])) for spec in specs]
    return scenario_lib.summarize_all(results, rows, values["Revenue"], values["COGS"])


def t12(ctx: Context) -> Outcome:
    """Reset is scoped to demo-owned writeback tables and scenarios; reruns leave no duplicate grains."""
    tables = reset_cli.registered_tables(ctx)
    if not tables:
        return "not_executed", "no writeback table is registered in the manifest"
    notes: list[str] = []
    ok = True
    with ctx.sql_session(ctx.cfg.names.writeback_db) as session:
        for record in tables:
            table = parse_table_ref(record.name)
            grain = record.extra.get("grain") or []
            value = record.extra.get("valueColumn") or "Value"
            latest = record.extra.get("latestColumn") or None
            rows = read_rows(session, table, grain, value, latest_column=latest)
            duplicates = find_duplicate_grains(rows, grain)
            ok &= not duplicates
            notes.append(f"{table}: {len(rows)} latest grains, duplicates={len(duplicates)}")
    names = ",".join(spec.scenario for spec in scenario_lib.load_specs(ctx.cfg.raw))
    reset = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "reset.py"), "--scenarios", names], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    base_refused = subprocess.run([sys.executable, str(REPO_ROOT / "scripts" / "reset.py"), "--scenarios", "Base"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=300)
    dry_run_only = "DRY RUN" in reset.stdout and "APPLYING" not in reset.stdout
    ok &= reset.returncode == 0 and dry_run_only and base_refused.returncode == 2
    notes.append(f"reset dry run ({names}): {'; '.join(line.strip() for line in reset.stdout.splitlines() if 'rows match' in line)}; Base refused: {base_refused.returncode == 2}")
    if reset.returncode:
        notes.append(f"reset exited {reset.returncode}: {reset.stderr.strip()[-400:]}")
    return ("passed" if ok else "failed"), " | ".join(notes)


def _measure_names(query: dict) -> list[str]:
    """Measure names of an Infobridge query (`meta.measures` holds dicts with a `name`)."""
    return [m.get("name", "") if isinstance(m, dict) else str(m) for m in (query.get("meta") or {}).get("measures") or []]


def t08(ctx: Context) -> Outcome:
    """Infobridge: the Rolling Forecast sheet exposes a query with the shared measure and the Scenario Comparison sheet consumes it."""
    parts = _plan_parts(ctx, ctx.cfg.names.medtech_plan)
    bridge = json.loads(parts.get("connectedPlanning/infobridge.json") or "{}")
    sources = [s for s in bridge.get("sources", []) if s.get("name") == INFOBRIDGE["source"] and s.get("type") == "PLANNING"]
    queries = [q for s in sources for q in s.get("queries", []) if INFOBRIDGE["measure"] in _measure_names(q)]
    if not queries:
        return "not_executed", f"no Infobridge query exposes {INFOBRIDGE['measure']!r} from the {INFOBRIDGE['source']!r} sheet in the plan definition"
    sheets = json.loads(parts["definition.json"]).get("sheets", [])
    consumer = next((s for s in sheets if s.get("displayName") == INFOBRIDGE["consumer"]), None)
    ids = {q["queryId"] for q in queries}
    consumed = consumer is not None and any(
        qid in text for path, text in parts.items() if path.startswith(f"sheets/{consumer['recordGuid']}/") for qid in ids
    )
    query = queries[0]
    observed = (
        f"Infobridge source '{INFOBRIDGE['source']}' (PLANNING) exposes {query['name'].replace('  ', ' ')!r} with measure {INFOBRIDGE['measure']!r} "
        f"({(query.get('meta') or {}).get('recordCount', '?')} records); sheet '{INFOBRIDGE['consumer']}' "
        + ("references the query in its definition" if consumed else "does NOT reference the query")
    )
    return ("passed" if consumed else "failed"), observed


def t10(ctx: Context) -> Outcome:
    """Reporting round trip: the semantic model returns what Planning committed to SQL - in total, per region and for the edited leaf -
    and its actual-vs-budget variance equals the generator's actuals minus the budget in SQL."""
    reporting = reporting_lib.Reporting.from_config(ctx.cfg.raw)
    wid = ctx.workspace_id()
    model = ctx.manifest.find("SemanticModel", ctx.cfg.names.medtech_model, wid)
    if not model:
        return "failed", "MedTech semantic model is not in the manifest"
    powerbi = ctx.clients.powerbi
    model_total = reporting_lib.first_value(powerbi.execute_queries(wid, model.id, reporting_lib.dax_total(reporting.measure)))
    model_regions = reporting_lib.region_values(powerbi.execute_queries(wid, model.id, reporting_lib.dax_by_region(reporting.measure)))
    model_leaf = reporting_lib.first_value(powerbi.execute_queries(wid, model.id, reporting_lib.dax_leaf(reporting.measure, reporting.leaf)))
    leaf = reporting.leaf
    actuals, months = reporting_lib.actuals_by_region(build_dataset(), leaf["year"])

    table = parse_table_ref(reporting.table)
    with ctx.sql_session(ctx.cfg.names.writeback_db) as session:
        known = {c["COLUMN_NAME"] for c in session.table_columns(table.schema, table.table)}
        base = f"FROM {table.sql()} WHERE IsLatest = 1 AND ValueColumnName = ? AND {reporting_lib.LEAF_FILTER}"
        assert {"IsLatest", "ValueColumnName", "Region", "Category", "MonthShort", "Year", "Value"} <= known
        sql_total = D(session.scalar(f"SELECT SUM(CAST(Value AS DECIMAL(38,2))) {base}", [reporting.series]))
        sql_regions = {row["Region"]: D(row["v"]) for row in session.query(f"SELECT Region, SUM(CAST(Value AS DECIMAL(38,2))) AS v {base} GROUP BY Region", [reporting.series])}
        sql_leaf = session.scalar(
            f"SELECT SUM(CAST(Value AS DECIMAL(38,2))) {base} AND Region = ? AND Category = ? AND Year = ? AND MonthShort = ?",
            [reporting.series, leaf["region"], leaf["category"], leaf["year"], leaf["month"]],
        )
        budget = {row["Region"]: D(row["v"]) for row in session.query(reporting_lib.budget_for_months_sql(table.sql(), months), [reporting.series, leaf["year"], *months])}
    leaf_label = f"{leaf['region']}/{leaf['category']}/{leaf['month']} {leaf['year']}"
    results = reporting_lib.compare(sql_total, sql_regions, D(sql_leaf) if sql_leaf is not None else None, model_total, model_regions, model_leaf, leaf_label)

    try:
        total_rows = powerbi.execute_queries(wid, model.id, reporting_lib.dax_variance_total(leaf["year"]))
        region_rows = powerbi.execute_queries(wid, model.id, reporting_lib.dax_variance_by_region(leaf["year"]))
    except ApiError as exc:
        if exc.status != 400 or is_capacity_rejection(exc):  # a throttled capacity is infrastructure (blocked), not a model that lacks the measures
            raise
        return "failed", f"the semantic model rejected the actual-vs-budget variance query (measures missing? run deploy.py --update-medtech-model): {exc}"
    variance_checks = reporting_lib.compare_variance(reporting_lib.expected_variance(actuals, budget), total_rows, region_rows, leaf["year"], months)
    forecast = reporting_lib.ForecastReporting.from_config(ctx.cfg.raw)
    try:
        forecast_checks = _forecast_checks(ctx, model.id, reporting, forecast) if forecast else None
    except ModelRejectedQuery as exc:
        return "failed", str(exc)
    return reporting_lib.summarize(results, reporting, variance_checks, forecast_checks)


class ModelRejectedQuery(Exception):
    """The semantic model answered 400 to a reporting query: its measures are older than the configuration."""


def _forecast_checks(ctx: Context, model_id: str, budget: reporting_lib.Reporting, forecast: reporting_lib.ForecastReporting) -> reporting_lib.ForecastChecks:
    """The rolling forecast: the model measure against the SQL writeback table (total, regions, the edited region x month), the forecast-vs-budget
    variance of the plan year against SQL forecast minus SQL budget, and the 12 monthly forecast and budget values the Intelligence chart plots."""
    wid, powerbi = ctx.workspace_id(), ctx.clients.powerbi
    year = forecast.leaf["year"]
    model_total = reporting_lib.first_value(powerbi.execute_queries(wid, model_id, reporting_lib.dax_total(forecast.measure)))
    model_regions = reporting_lib.region_values(powerbi.execute_queries(wid, model_id, reporting_lib.dax_by_region(forecast.measure)))
    model_leaf = reporting_lib.first_value(powerbi.execute_queries(wid, model_id, reporting_lib.dax_region_leaf(forecast.measure, forecast.leaf)))
    measures = reporting_lib.forecast_measures(forecast.measure, budget.measure)
    try:
        total_rows = powerbi.execute_queries(wid, model_id, reporting_lib.dax_variance_total(year, measures))
        region_rows = powerbi.execute_queries(wid, model_id, reporting_lib.dax_variance_by_region(year, measures[2]))
        month_rows = powerbi.execute_queries(wid, model_id, reporting_lib.dax_by_month(year, forecast.measure, budget.measure))
    except ApiError as exc:
        if exc.status != 400 or is_capacity_rejection(exc):
            raise
        raise ModelRejectedQuery(f"the semantic model rejected the forecast-vs-budget query (measures missing? run deploy.py --update-medtech-model): {exc}") from exc

    table, budget_table = parse_table_ref(forecast.table), parse_table_ref(budget.table)
    with ctx.sql_session(ctx.cfg.names.writeback_db) as session:
        base = f"FROM {table.sql()} WHERE IsLatest = 1 AND ValueColumnName = ? AND {reporting_lib.leaf_filter(forecast.dimensions)}"
        sql_total = D(session.scalar(f"SELECT SUM(CAST(Value AS DECIMAL(38,2))) {base}", [forecast.series]))
        sql_regions = {row["Region"]: D(row["v"]) for row in session.query(f"SELECT Region, SUM(CAST(Value AS DECIMAL(38,2))) AS v {base} GROUP BY Region", [forecast.series])}
        sql_leaf = session.scalar(
            f"SELECT SUM(CAST(Value AS DECIMAL(38,2))) {base} AND Region = ? AND Year = ? AND MonthShort = ?",
            [forecast.series, forecast.leaf["region"], year, forecast.leaf["month"]],
        )
        forecast_year = {row["Region"]: D(row["v"]) for row in session.query(reporting_lib.region_totals_sql(table.sql(), MONTH_SHORT, forecast.dimensions), [forecast.series, year, *MONTH_SHORT])}
        budget_year = {row["Region"]: D(row["v"]) for row in session.query(reporting_lib.budget_for_months_sql(budget_table.sql(), MONTH_SHORT), [budget.series, year, *MONTH_SHORT])}
        forecast_months = {row["MonthShort"]: D(row["v"]) for row in session.query(reporting_lib.month_totals_sql(table.sql(), forecast.dimensions), [forecast.series, year])}
        budget_months = {row["MonthShort"]: D(row["v"]) for row in session.query(reporting_lib.month_totals_sql(budget_table.sql()), [budget.series, year])}
    label = f"{forecast.leaf['region']}/{forecast.leaf['month']} {year}"
    comparisons = reporting_lib.compare(sql_total, sql_regions, D(sql_leaf) if sql_leaf is not None else None, model_total, model_regions, model_leaf, label)
    variance = reporting_lib.compare_variance(
        reporting_lib.expected_variance(forecast_year, budget_year), total_rows, region_rows, year, MONTH_SHORT,
        labels=("forecast revenue", "budget"), source="SQL",
    )
    monthly = reporting_lib.compare_months(forecast_months, budget_months, month_rows, ("forecast", "budget"))
    return reporting_lib.ForecastChecks(forecast, comparisons, variance, monthly)


def t11(ctx: Context) -> Outcome:
    """Role-based security: the author's effective permissions per layer are probed and recorded as evidence, but the role matrix
    (Planner / Stakeholder / Viewer / approver) needs other identities, so the status can only be `not_executed` with this probe attached."""
    upn, results = permissions_cli.collect(ctx)
    return "not_executed", permissions_lib.summarize_author(results, upn)


def t15(ctx: Context) -> Outcome:
    """PowerTable column rules: the saved plan definition holds the configured rules for the sheet and every stored row conforms to them."""
    rules = powertable_rules.Rules.from_config(ctx.cfg.raw)
    if rules is None:
        return "not_executed", "medtech.powertable_rules is not configured"
    parts = _plan_parts(ctx, ctx.cfg.names.medtech_plan)
    sheet = next((s for s in json.loads(parts["definition.json"]).get("sheets", []) if s.get("displayName") == rules.sheet), None)
    if sheet is None:
        return "failed", f"sheet {rules.sheet!r} is not in the plan definition"
    prefix = f"sheets/{sheet['recordGuid']}/visuals/"
    configs = [
        column
        for path, text in sorted(parts.items())
        if path.startswith(prefix) and path.endswith("/columnConfigs.json")
        for column in json.loads(text).get("columnConfigs", [])
    ]
    if not configs:
        return "not_executed", f"sheet {rules.sheet!r} has no saved column configuration"
    definition = powertable_rules.check_definition(rules, configs)
    violations: dict[str, int] = {}
    distinct: dict[str, list[str]] = {}
    with ctx.sql_session(ctx.cfg.names.powertable_db) as session:
        rows = int(session.scalar(f"SELECT COUNT(*) FROM {parse_table_ref(rules.table).sql()}"))
        for label, statement in powertable_rules.data_checks(rules):
            violations[label] = int(session.scalar(statement))
        for column in rules.closed_lists:
            distinct[column] = [str(r["v"]) for r in session.query(powertable_rules.distinct_values_sql(rules, column))]
    return powertable_rules.summarize(rules, definition, violations, rows, distinct)


def t16(ctx: Context) -> Outcome:
    """Cell comment: the budget writeback persisted the comment on the intended cell with no assignee and no @mention, and the number did not move."""
    spec = comments_lib.CommentSpec.from_config(ctx.cfg.raw)
    if spec is None:
        return "not_executed", "medtech.comment_check is not configured"
    with ctx.sql_session(ctx.cfg.names.writeback_db) as session:
        rows = session.query(comments_lib.select_sql(parse_table_ref(spec.table).sql()))
    if not rows:
        return "not_executed", f"no latest row of {spec.table} carries a comment: add one in the plan and run Writeback (docs/manual-setup.md section J)"
    problems, observed = comments_lib.evaluate(rows, spec)
    return ("failed" if problems else "passed"), observed + (f"; problems: {problems}" if problems else "")


RUNNERS: dict[str, Callable[[Context], Outcome]] = {
    "L-01": l01, "L-02": l02, "L-03": l03, "L-04": l04,
    "T-01": t01, "T-02": t02, "T-03": t03, "T-04": t04, "T-05": t05, "T-06": t06, "T-08": t08, "T-10": t10, "T-11": t11, "T-12": t12, "T-15": t15, "T-16": t16,
}


def infrastructure_errors() -> tuple[type[BaseException], ...]:
    """Errors that mean a check could not run (tenant, network, ODBC), as opposed to a check that ran and found a problem."""
    errors: tuple[type[BaseException], ...] = (ApiError, LookupError, OSError, KeyError, subprocess.TimeoutExpired)
    try:
        import pyodbc
    except ImportError:
        return errors
    return errors + (pyodbc.Error,)


def run(defs: list[AcceptanceTest], store: ResultStore, scope: str, only: set[str]) -> int:
    ctx = Context()
    failures = 0
    for definition in defs:
        if scope != "all" and definition.scope != scope:
            continue
        if only and definition.id not in only:
            continue
        runner = RUNNERS.get(definition.id)
        if runner is None:
            previous = store.get(definition.id)
            print(f"{definition.id:5} NOT RUN       manual UI evidence required; previously recorded: {previous['status']}")
            continue
        try:
            status, observed = runner(ctx)
        except infrastructure_errors() as exc:
            status, observed = "blocked", f"{type(exc).__name__}: {exc}"[:400]
        store.record(definition.id, status, observed)
        failures += status in {"failed", "blocked"}
        print(f"{definition.id:5} {status.upper().replace('_', ' '):13} {observed[:200]}")
    store.save()
    return 1 if failures else 0


def render(defs: list[AcceptanceTest], store: ResultStore) -> None:
    counts = summarize(defs, store)
    section = "\n".join([BEGIN, "", "Counts: " + ", ".join(f"{k.replace('_', ' ')} = {v}" for k, v in counts.items()), "", render_markdown(defs, store), "", END])
    text = STATUS_DOC.read_text(encoding="utf-8") if STATUS_DOC.is_file() else f"# Deployment status\n\n{BEGIN}\n{END}\n"
    if BEGIN in text and END in text:
        text = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END), lambda _: section, text, flags=re.S)
    else:
        text += "\n" + section + "\n"
    STATUS_DOC.parent.mkdir(exist_ok=True)
    STATUS_DOC.write_text(text, encoding="utf-8")
    print(f"Updated {STATUS_DOC.relative_to(REPO_ROOT)}: {counts}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("--scope", choices=["local", "live", "all"], default="all")
    p_run.add_argument("--only", default="", help="comma-separated test ids")
    p_rec = sub.add_parser("record")
    p_rec.add_argument("test_id")
    p_rec.add_argument("--status", required=True, choices=["passed", "failed", "not_executed", "blocked"])
    p_rec.add_argument("--observed", default="")
    p_rec.add_argument("--evidence", default="")
    sub.add_parser("render")
    sub.add_parser("list")
    args = parser.parse_args(argv)

    defs = load_definitions(DEFS_PATH)
    store = ResultStore(RESULTS_PATH)
    if args.command == "run":
        selected = {s.strip() for s in args.only.split(",") if s.strip()}
        if unknown := selected - {d.id for d in defs}:
            parser.error(f"unknown test ids: {', '.join(sorted(unknown))}")
    if args.command == "list":
        for d in defs:
            print(f"{d.id:5} {d.scope:5} {d.mode:12} {store.get(d.id)['status']:13} {d.title}")
        return 0
    if args.command == "record":
        if args.test_id not in {d.id for d in defs}:
            parser.error(f"unknown test id {args.test_id}")
        store.record(args.test_id, args.status, args.observed, evidence=args.evidence)
        store.save()
        print(f"recorded {args.test_id}: {args.status}")
        return 0
    if args.command == "render":
        render(defs, store)
        return 0
    return run(defs, store, args.scope, selected)


if __name__ == "__main__":
    raise SystemExit(main())
