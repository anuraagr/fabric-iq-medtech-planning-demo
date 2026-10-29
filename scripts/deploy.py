"""Idempotent deployment of the demo-owned Fabric items.

`plan` is read-only and shows exactly what `apply` would change. `apply` mutates the tenant and refuses to
run without explicit confirmations that match the configured tenant and workspace.

    python scripts/deploy.py plan  --capacity-id <GUID>
    python scripts/deploy.py apply --capacity-id <GUID> --confirm-tenant <TENANT_GUID> --confirm-workspace <NAME>
                                   [--only workspace,sql,model,plan,medtech_model,medtech_plan] [--with-report]
                                   [--update-medtech-model --include-writeback]

What is deployed through documented REST APIs:
    workspace (Fabric REST), two Fabric SQL databases (Fabric REST), the Northwind_FMCG semantic model
    (Power BI REST PBIX import; the verified official tutorial baseline), the synthetic MedTech semantic model
    (Fabric REST create-with-definition, TMDL with embedded data, then a Power BI REST refresh) and EMPTY Plan
    items (Fabric REST). Planning sheets, PowerTable, Intelligence, Infobridge and the writeback commit are
    native-UI steps: see docs/manual-setup.md.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.data.medtech_generator import build_dataset  # noqa: E402
from scripts.data.medtech_tmdl import MODEL_DESCRIPTION, WritebackSource, build_parts  # noqa: E402
from scripts.fetch_sample_assets import ASSET_DIR, verify as verify_assets  # noqa: E402
from scripts.lib.clients import Clients, build_clients  # noqa: E402
from scripts.lib.config import ConfigError, DemoConfig, REPO_ROOT, load_config  # noqa: E402
from scripts.lib.fabric import sku_planning_class, sql_endpoint  # noqa: E402
from scripts.lib.http import ApiError  # noqa: E402
from scripts.lib.manifest import Manifest  # noqa: E402
from scripts.lib.medtech_checks import verify_model  # noqa: E402
from scripts.lib.sqlclient import SqlSession, connect, quote_identifier  # noqa: E402

STEPS = ("workspace", "sql", "model", "plan", "medtech_model", "medtech_plan")
PBIX_NAME = "Northwind_FMCG.pbix"
SMOKE_DAX = REPO_ROOT / "model" / "dax" / "01_smoke_2025_totals.dax"
DEMO_DESCRIPTION = "Fabric IQ Planning customer demo. Demo-owned resources; created by scripts/deploy.py."


@dataclass
class Change:
    step: str
    action: str  # CREATE | REUSE | UPDATE | BLOCKED
    kind: str
    name: str
    detail: str = ""
    billable: bool = False


def _capacity(clients: Clients, capacity_id: str) -> dict:
    for capacity in clients.fabric.list_capacities():
        if capacity.get("id", "").lower() == capacity_id.lower():
            return capacity
    raise SystemExit(f"Capacity {capacity_id} is not visible to this identity")


def _check_capacity_usable(capacity: dict) -> None:
    sku, state = capacity.get("sku", ""), capacity.get("state", "")
    if sku_planning_class(sku) != "supported":
        raise SystemExit(f"Capacity '{capacity.get('displayName')}' ({sku}) is not valid for Planning")
    if state != "Active":
        raise SystemExit(
            f"Capacity '{capacity.get('displayName')}' is {state}. Resume it yourself (billable) or choose an Active capacity; "
            "this script never resumes, creates or resizes capacities."
        )


def find_workspace(clients: Clients, cfg: DemoConfig) -> dict | None:
    if cfg.workspace_id:
        workspace = clients.fabric.get_workspace(cfg.workspace_id)
        if workspace.get("displayName", "").casefold() != cfg.names.workspace.casefold():
            raise RuntimeError("FPD_WORKSPACE_ID and the configured workspace name do not match; correct the configuration before deploying.")
        return workspace
    matches = clients.fabric.find_workspaces_by_name(cfg.names.workspace)
    if len(matches) > 1:
        raise SystemExit(f"{len(matches)} workspaces are named '{cfg.names.workspace}'. Set FPD_WORKSPACE_ID to pick one.")
    return matches[0] if matches else None


def build_plan(clients: Clients, cfg: DemoConfig, capacity: dict, only: set[str], *,
               update_medtech: bool = False, include_writeback: bool = False) -> tuple[dict | None, list[Change]]:
    workspace = find_workspace(clients, cfg)
    changes: list[Change] = []
    cap_label = f"{capacity.get('displayName')} ({capacity.get('sku')}, {capacity.get('region')})"
    existing_items: dict[tuple[str, str], dict] = {}
    datasets: list[dict] = []
    if workspace:
        for item in clients.fabric.list_items(workspace["id"]):
            existing_items[(item["type"], item["displayName"].casefold())] = item
        datasets = clients.powerbi.list_datasets(workspace["id"])

    if workspace and (workspace.get("capacityId") or "").lower() != capacity["id"].lower():
        changes.append(Change("workspace", "BLOCKED", "Workspace", workspace["displayName"],
                              "selected capacity differs; existing workspaces are never reassigned automatically"))
    elif "workspace" in only:
        if not workspace:
            changes.append(Change("workspace", "CREATE", "Workspace", cfg.names.workspace, f"assigned to capacity {cap_label}"))
        else:
            changes.append(Change("workspace", "REUSE", "Workspace", workspace["displayName"], f"already on {cap_label}"))
    if "sql" in only:
        for name, role in ((cfg.names.writeback_db, "business writeback destination (table created by the product at first writeback)"),
                           (cfg.names.powertable_db, "PowerTable companion database (tables created by the PowerTable upload)")):
            present = ("SQLDatabase", name.casefold()) in existing_items
            changes.append(Change("sql", "REUSE" if present else "CREATE", "SQLDatabase", name, role, billable=not present))
    if "model" in only:
        present = any(d.get("name", "").casefold() == cfg.names.semantic_model.casefold() for d in datasets)
        changes.append(Change("model", "REUSE" if present else "CREATE", "SemanticModel", cfg.names.semantic_model,
                              f"import {PBIX_NAME} via Power BI REST (Import storage mode; embedded data; scheduled refresh is not possible)"))
    if "plan" in only:
        present = ("Plan", cfg.names.plan_item.casefold()) in existing_items
        changes.append(Change("plan", "REUSE" if present else "CREATE", "Plan", cfg.names.plan_item,
                              "EMPTY plan item via Fabric REST; sheets are authored in the native UI. Opening it starts a billed Planning session.", billable=not present))
    if "medtech_model" in only:
        present = any(d.get("name", "").casefold() == cfg.names.medtech_model.casefold() for d in datasets)
        action = "UPDATE" if present and update_medtech else ("REUSE" if present else "CREATE")
        detail = ("replace the generated model definition, attach committed SQL writeback tables, bind credentials and refresh"
                  if include_writeback else "foundation model: embedded synthetic data, no SQL writeback dependency")
        if action == "REUSE":
            detail = "leave the existing model and its reporting configuration unchanged; verify reference totals"
        if action == "UPDATE":
            record = Manifest(cfg.manifest_path, cfg.tenant_id).find("SemanticModel", cfg.names.medtech_model, workspace["id"])
            existing_id = next(d["id"] for d in datasets if d.get("name", "").casefold() == cfg.names.medtech_model.casefold())
            if not record or not record.owned or record.id != existing_id:
                action, detail = "BLOCKED", "cannot replace an unowned model or mismatched manifest ID"
        if include_writeback and not present:
            action, detail = "BLOCKED", "deploy the foundation and complete native writeback before attaching reporting"
        changes.append(Change("medtech_model", action, "SemanticModel", cfg.names.medtech_model, detail))
    if "medtech_plan" in only:
        present = ("Plan", cfg.names.medtech_plan.casefold()) in existing_items
        changes.append(Change("medtech_plan", "REUSE" if present else "CREATE", "Plan", cfg.names.medtech_plan,
                              "EMPTY plan item for the MedTech model; sheets are authored in the native UI.", billable=not present))
    return workspace, changes


def render_plan(cfg: DemoConfig, capacity: dict, workspace: dict | None, changes: list[Change]) -> str:
    lines = [
        "# Deployment plan (nothing has been changed)", "",
        f"- Tenant: `{cfg.tenant_id}`",
        f"- Workspace: `{cfg.names.workspace}` ({'exists: ' + workspace['id'] if workspace else 'will be created'})",
        f"- Capacity: `{capacity.get('displayName')}` {capacity.get('sku')} in {capacity.get('region')} ({capacity.get('state')})", "",
        "| Step | Action | Kind | Name | Detail |", "| --- | --- | --- | --- | --- |",
    ]
    for c in changes:
        lines.append(f"| {c.step} | {c.action} | {c.kind} | {c.name} | {c.detail}{' (consumes capacity)' if c.billable else ''} |")
    lines += ["", "Billing: Fabric SQL databases and Planning sessions consume capacity units (Planner 847, Stakeholder 168, Viewer 37 CU-hours per 30-day session).",
              "No capacity is created, resumed or resized by this script."]
    return "\n".join(lines)


def _await_dataset(clients: Clients, workspace_id: str, name: str, *, timeout: float = 180.0) -> dict:
    waited = 0.0
    while True:
        found = clients.powerbi.find_dataset(workspace_id, name)
        if found:
            return found
        if waited >= timeout:
            raise RuntimeError(f"Semantic model {name!r} did not appear in the workspace within {timeout:.0f}s")
        time.sleep(5)
        waited += 5


def writeback_sources(cfg: DemoConfig, manifest: Manifest, workspace_id: str) -> tuple[WritebackSource, ...]:
    """Writeback tables to surface in the semantic model, from `medtech.writeback_tables` in the configuration."""
    entries = (cfg.raw.get("medtech") or {}).get("writeback_tables") or []
    if not entries:
        return ()
    database = manifest.find("SQLDatabase", cfg.names.writeback_db, workspace_id)
    if database is None:
        raise RuntimeError(f"SQL database {cfg.names.writeback_db!r} is not in the manifest")
    return tuple(
        WritebackSource(
            model_table=str(e["model_table"]), server=database.extra["serverFqdn"], database=database.extra["databaseName"],
            schema=str(e.get("schema", "dbo")), table=str(e["source_table"]), series=str(e["series"]), measure=str(e["measure"]),
            role=str(e.get("role", "budget")), dimensions=tuple(str(d) for d in (e.get("dimensions") or ("Region", "Category"))),
        )
        for e in entries
    )


def validate_writeback_tables(session: SqlSession, sources: tuple[WritebackSource, ...]) -> None:
    for source in sources:
        table = f"{quote_identifier(source.schema)}.{quote_identifier(source.table)}"
        required = {"IsLatest", "ValueColumnName", "Year", "Quarter", "MonthShort", "Value", "PreviousValue", *source.dimensions}
        columns = {c["COLUMN_NAME"] for c in session.table_columns(source.schema, source.table)}
        if missing := required - columns:
            raise RuntimeError(f"{table} is missing columns {sorted(missing)}. Complete the native UI writeback first; no guessed tables are created.")
        count = session.scalar(f"SELECT COUNT(*) FROM {table} WHERE IsLatest = 1 AND ValueColumnName = ? AND Value IS NOT NULL", [source.series])
        if not count:
            raise RuntimeError(f"{table} has no committed current values for {source.series!r}. Save and Writeback the native sheet first.")


def check_writeback_sources(clients: Clients, sources: tuple[WritebackSource, ...]) -> None:
    if not sources:
        raise RuntimeError("No medtech.writeback_tables are configured for --include-writeback.")
    grouped: dict[tuple[str, str], list[WritebackSource]] = {}
    for source in sources:
        grouped.setdefault((source.server, source.database), []).append(source)
    token = clients.tokens.get("sql").value
    for (server, database), tables in grouped.items():
        with SqlSession(connect(server, database, token)) as session:
            validate_writeback_tables(session, tuple(tables))


def record_existing(manifest: Manifest, kind: str, name: str, item_id: str, workspace_id: str | None = None, **extra) -> None:
    previous = manifest.find(kind, name, workspace_id)
    same = previous is not None and previous.id == item_id
    manifest.record(kind, name, item_id, workspace_id=workspace_id,
                    created_by=previous.created_by if same else "preexisting",
                    owned=previous.owned if same else False, **extra)


def deploy_medtech_model(clients: Clients, cfg: DemoConfig, manifest: Manifest, workspace_id: str, *,
                         update: bool, log, results: dict, include_writeback: bool = False) -> None:
    """Create (or update) the synthetic MedTech semantic model from its generated TMDL, refresh it and verify it with DAX."""
    fabric, powerbi = clients.fabric, clients.powerbi
    name = cfg.names.medtech_model
    dataset = build_dataset()
    expected = dataset.expected_totals()
    existing = powerbi.find_dataset(workspace_id, name)
    record = manifest.find("SemanticModel", name, workspace_id)
    owned = record is not None and record.owned and record.id == (existing or {}).get("id")
    if existing and update and not owned:
        raise RuntimeError(f"{name!r} is not an owned model with a matching manifest ID; refusing to replace it.")
    if include_writeback and not existing:
        raise RuntimeError("Deploy the foundation model and complete native UI writeback before using --include-writeback.")
    resume_reporting = owned and record.extra.get("refreshPending") and record.extra.get("writebackEnabled")
    sources = writeback_sources(cfg, manifest, workspace_id) if include_writeback or resume_reporting else ()
    if include_writeback or resume_reporting:
        check_writeback_sources(clients, sources)
    refresh_needed = False
    if existing is None:
        parts = build_parts(dataset, sources)
        fabric.create_semantic_model(workspace_id, name, parts, description=MODEL_DESCRIPTION[:256])
        model = _await_dataset(clients, workspace_id, name)
        manifest.record("SemanticModel", name, model["id"], workspace_id=workspace_id, created_by="script", owned=True,
                        source="synthetic-medtech", dataDigest=expected["digest"], refreshPending=True, writebackEnabled=False)
        manifest.save()
        log("medtech_model", "CREATED", f"{name} ({model['id']}); {len(parts)} definition parts, {expected['rows']['sales']:,} sales rows")
        refresh_needed = True
    else:
        record_existing(manifest, "SemanticModel", name, existing["id"], workspace_id)
        manifest.save()
        model = existing
        if update:
            current = fabric.get_semantic_model_definition(workspace_id, model["id"])
            paths = {p["path"] for p in current["definition"]["parts"]}
            reporting_paths = {f"definition/tables/{e['model_table']}.tmdl" for e in (cfg.raw.get("medtech") or {}).get("writeback_tables", [])}
            if not include_writeback and (record.extra.get("writebackEnabled") or paths & reporting_paths):
                raise RuntimeError("This model already has reporting tables. Pass --include-writeback to avoid removing them.")
            backup = REPO_ROOT / "reports" / f"model-before-update-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json"
            backup.parent.mkdir(exist_ok=True)
            backup.write_text(json.dumps(current, indent=2), encoding="utf-8")
            record.extra.update(refreshPending=True, writebackEnabled=include_writeback)
            manifest.save()
            fabric.update_semantic_model_definition(workspace_id, model["id"], build_parts(dataset, sources))
            log("medtech_model", "UPDATED", f"{name} definition replaced" + (f" (+{len(sources)} writeback table(s))" if sources else ""))
            refresh_needed = True
        else:
            log("medtech_model", "REUSED", f"{name} ({model['id']})")
            refresh_needed = bool(owned and record.extra.get("refreshPending"))
    if refresh_needed:
        if sources:
            bound = powerbi.set_oauth_credentials(workspace_id, model["id"], clients.tokens.get("sql").value)
            if not bound:
                raise RuntimeError("The reporting model has no bound SQL data source; refresh was not started.")
            log("medtech_credentials", "OK", f"OAuth2 credentials bound to {bound} SQL data source(s) for the refresh")
        request_id = powerbi.refresh(workspace_id, model["id"])
        entry = powerbi.wait_for_refresh(workspace_id, model["id"], request_id)
        log("medtech_refresh", "OK", f"refresh {request_id or '?'} completed at {entry.get('endTime')}")
        manifest.find("SemanticModel", name, workspace_id).extra["refreshPending"] = False
        manifest.save()
    checks = verify_model(powerbi, workspace_id, model["id"], expected)
    failed = [c for c in checks if not c.ok]
    results["medtechChecks"] = [{"label": c.label, "expected": c.expected, "actual": c.actual, "ok": c.ok} for c in checks]
    log("medtech_verify", "OK" if not failed else "FAILED", f"{len(checks) - len(failed)}/{len(checks)} DAX checks match the generator" + (f"; mismatches: {[c.label for c in failed]}" if failed else ""))


def apply(clients: Clients, cfg: DemoConfig, capacity: dict, only: set[str], with_report: bool,
          update_medtech: bool = False, *, include_writeback: bool = False) -> dict:
    manifest = Manifest(cfg.manifest_path, cfg.tenant_id)
    results: dict = {"startedAt": datetime.now(timezone.utc).isoformat(), "steps": [], "workspaceId": None}
    fabric, powerbi = clients.fabric, clients.powerbi

    def log(step: str, status: str, detail: str = "", **extra) -> None:
        results["steps"].append({"step": step, "status": status, "detail": detail, **extra})
        print(f"[{status:7}] {step}: {detail}")

    workspace = find_workspace(clients, cfg)
    if workspace and (workspace.get("capacityId") or "").lower() != capacity["id"].lower():
        raise RuntimeError("The workspace is on a different capacity. Select its existing capacity or reassign it separately with approval.")
    if workspace is None:
        if "workspace" not in only:
            raise SystemExit("Workspace does not exist and the workspace step was not selected")
        created = fabric.create_workspace(cfg.names.workspace, description=DEMO_DESCRIPTION, capacity_id=capacity["id"])
        workspace = fabric.get_workspace(created["id"])
        manifest.record("Workspace", workspace["displayName"], workspace["id"], created_by="script", owned=True, capacityId=capacity["id"])
        log("workspace", "CREATED", f"{workspace['displayName']} ({workspace['id']})")
    else:
        record_existing(manifest, "Workspace", workspace["displayName"], workspace["id"])
        log("workspace", "REUSED", f"{workspace['displayName']} ({workspace['id']})")
    manifest.save()
    wid = workspace["id"]
    results["workspaceId"] = wid

    if "sql" in only:
        for name in (cfg.names.writeback_db, cfg.names.powertable_db):
            try:
                database, created = fabric.ensure_sql_database(wid, name, description=DEMO_DESCRIPTION)
                server, db_name = sql_endpoint(database)
                if created:
                    manifest.record("SQLDatabase", name, database["id"], workspace_id=wid, serverFqdn=server, databaseName=db_name)
                else:
                    record_existing(manifest, "SQLDatabase", name, database["id"], wid, serverFqdn=server, databaseName=db_name)
                manifest.save()
                log("sql", "CREATED" if created else "REUSED", f"{name} -> {server} / {db_name}")
            except ApiError as exc:
                log("sql", "BLOCKED", f"{name}: {exc}")

    if "model" in only:
        try:
            existing = powerbi.find_dataset(wid, cfg.names.semantic_model)
            if existing:
                record_existing(manifest, "SemanticModel", cfg.names.semantic_model, existing["id"], wid)
                log("model", "REUSED", f"{cfg.names.semantic_model} ({existing['id']})")
            else:
                problems = verify_assets(ASSET_DIR, [f for f in cfg.sample.files if f.name == PBIX_NAME])
                if problems:
                    raise SystemExit(f"Sample asset check failed: {problems}. Run: python scripts/fetch_sample_assets.py")
                record = powerbi.import_pbix(wid, ASSET_DIR / PBIX_NAME, cfg.names.semantic_model, name_conflict="Abort", skip_report=not with_report)
                dataset = record["datasets"][0]
                manifest.record("SemanticModel", cfg.names.semantic_model, dataset["id"], workspace_id=wid, created_by="script", owned=True,
                                importId=record.get("id"), sourceAsset=PBIX_NAME, sampleCommit=cfg.sample.commit)
                for report in record.get("reports", []):
                    manifest.record("Report", report["name"], report["id"], workspace_id=wid, created_by="script", owned=True)
                manifest.save()
                log("model", "CREATED", f"{cfg.names.semantic_model} ({dataset['id']})")
        except ApiError as exc:
            log("model", "BLOCKED", str(exc))

    if "plan" in only:
        try:
            plan, created = fabric.ensure_plan(wid, cfg.names.plan_item, description=DEMO_DESCRIPTION)
            if created:
                manifest.record("Plan", cfg.names.plan_item, plan["id"], workspace_id=wid)
            else:
                record_existing(manifest, "Plan", cfg.names.plan_item, plan["id"], wid)
            manifest.save()
            log("plan", "CREATED" if created else "REUSED", f"{cfg.names.plan_item} ({plan['id']})")
        except ApiError as exc:
            log("plan", "BLOCKED", str(exc))

    if "medtech_model" in only:
        try:
            deploy_medtech_model(clients, cfg, manifest, wid, update=update_medtech, include_writeback=include_writeback, log=log, results=results)
        except (ApiError, RuntimeError) as exc:
            log("medtech_model", "BLOCKED", str(exc))

    if "medtech_plan" in only:
        try:
            plan, created = fabric.ensure_plan(wid, cfg.names.medtech_plan, description=DEMO_DESCRIPTION)
            if created:
                manifest.record("Plan", cfg.names.medtech_plan, plan["id"], workspace_id=wid)
            else:
                record_existing(manifest, "Plan", cfg.names.medtech_plan, plan["id"], wid)
            manifest.save()
            log("medtech_plan", "CREATED" if created else "REUSED", f"{cfg.names.medtech_plan} ({plan['id']})")
        except ApiError as exc:
            log("medtech_plan", "BLOCKED", str(exc))

    model = manifest.find("SemanticModel", cfg.names.semantic_model, wid)
    if model and "model" in only:
        try:
            rows = powerbi.execute_queries(wid, model.id, SMOKE_DAX.read_text(encoding="utf-8"))
            log("smoke", "OK" if rows else "EMPTY", f"DAX smoke query returned {rows}")
            results["smokeRows"] = rows
        except ApiError as exc:
            log("smoke", "BLOCKED", f"DAX smoke query failed: {exc}")

    results["finishedAt"] = datetime.now(timezone.utc).isoformat()
    results["workspaceUrl"] = f"https://app.fabric.microsoft.com/groups/{wid}"
    manifest.save()
    return results


def result_exit_code(results: dict) -> int:
    return int(any(step["status"] in {"BLOCKED", "FAILED", "EMPTY"} for step in results["steps"]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["plan", "apply"])
    parser.add_argument("--capacity-id", help="Active Fabric capacity GUID (defaults to FPD_CAPACITY_ID)")
    parser.add_argument("--only", default=",".join(STEPS), help=f"comma-separated subset of {STEPS}")
    parser.add_argument("--with-report", action="store_true", help="also import the (incidental) report in the PBIX; default skips it")
    parser.add_argument("--update-medtech-model", action="store_true", help="replace the MedTech model definition and refresh it (apply only)")
    parser.add_argument("--include-writeback", action="store_true", help="attach SQL reporting tables AFTER native writeback; requires --update-medtech-model")
    parser.add_argument("--confirm-tenant", help="must equal the configured tenant id (apply only)")
    parser.add_argument("--confirm-workspace", help="must equal the workspace display name (apply only)")
    args = parser.parse_args(argv)

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    if not only or not only <= set(STEPS):
        parser.error(f"--only must be a subset of {STEPS}")
    if args.update_medtech_model and "medtech_model" not in only:
        parser.error("--update-medtech-model requires medtech_model in --only")
    if args.include_writeback and not args.update_medtech_model:
        parser.error("--include-writeback requires --update-medtech-model")
    try:
        cfg = load_config()
    except ConfigError as exc:
        parser.error(str(exc))
    capacity_id = args.capacity_id or cfg.capacity_id
    if not capacity_id:
        parser.error("--capacity-id (or FPD_CAPACITY_ID) is required")

    clients = build_clients(cfg)
    identity = clients.tokens.identity()
    capacity = _capacity(clients, capacity_id)
    workspace, changes = build_plan(clients, cfg, capacity, only, update_medtech=args.update_medtech_model, include_writeback=args.include_writeback)
    print(f"Signed in as {identity['upn']} (tenant {identity['tenantId']})")
    print(render_plan(cfg, capacity, workspace, changes))
    if args.command == "plan":
        return int(any(change.action == "BLOCKED" for change in changes))
    if any(change.action == "BLOCKED" for change in changes):
        print("REFUSING to apply a blocked deployment preview.", file=sys.stderr)
        return 1

    if args.confirm_tenant != cfg.tenant_id or args.confirm_workspace != cfg.names.workspace:
        print("\nREFUSING to apply: --confirm-tenant and --confirm-workspace must exactly match the configured tenant id and workspace name.", file=sys.stderr)
        return 2
    _check_capacity_usable(capacity)
    results = apply(clients, cfg, capacity, only, args.with_report, args.update_medtech_model, include_writeback=args.include_writeback)
    out = REPO_ROOT / "reports"
    out.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (out / f"deploy-{stamp}.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nWorkspace: {results['workspaceUrl']}")
    return result_exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
