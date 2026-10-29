"""Reset demo scenario data in the DEMO-OWNED writeback tables. Dry run unless explicitly confirmed.

    python scripts/reset.py --scenarios "Upside,Downside"                         # dry run: counts rows
    python scripts/reset.py --scenarios "Upside,Downside" --confirm RESET-FabricPlanningDemo

Only tables registered with `verify_writeback.py register --scenario-column ...` are touched, only rows of the
named scenarios are deleted (Base is protected unless --include-base), and the product's internal
__fabric_plan_sys database is never accessed. Planning itself never deletes destination rows when a sheet
row is deleted, so this script is the supported way to return the destination to a clean demo state.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.context import Context  # noqa: E402
from scripts.lib.manifest import ResourceRecord  # noqa: E402
from scripts.lib.sqlclient import SqlSession  # noqa: E402
from scripts.lib.writeback import WritebackError, parse_table_ref, quote_column  # noqa: E402

PROTECTED = {"base", "actual", "actuals"}


def registered_tables(ctx: Context) -> list[ResourceRecord]:
    wid = ctx.workspace_id()
    return [r for r in ctx.manifest.resources if r.kind == "WritebackTable" and r.owned and r.workspace_id == wid
            and r.extra.get("database") == ctx.cfg.names.writeback_db and r.extra.get("scenarioColumn")]


def scenario_filter(column: str, known: set[str], scenarios: list[str]) -> tuple[str, list[str]]:
    placeholders = ", ".join("?" for _ in scenarios)
    return f"{quote_column(column, known)} IN ({placeholders})", scenarios


def validate_scenarios(scenarios: list[str], include_base: bool) -> list[str]:
    if not scenarios:
        raise WritebackError("--scenarios is required: reset never deletes unscoped rows")
    blocked = [s for s in scenarios if s.casefold() in PROTECTED and not include_base]
    if blocked:
        raise WritebackError(f"Refusing to delete protected scenario(s) {blocked}; pass --include-base to override")
    return scenarios


def reset_table(session: SqlSession, record_name: str, scenario_column: str, scenarios: list[str], apply: bool) -> int:
    table = parse_table_ref(record_name)
    known = {c["COLUMN_NAME"] for c in session.table_columns(table.schema, table.table)}
    if not known:
        print(f"  {table}: table not found, nothing to reset")
        return 0
    where, params = scenario_filter(scenario_column, known, scenarios)
    count = int(session.scalar(f"SELECT COUNT(*) FROM {table.sql()} WHERE {where}", params) or 0)
    print(f"  {table}: {count} rows match {scenario_column} IN {scenarios}")
    if apply and count:
        deleted = session.delete_rows(table.schema, table.table, where, params)
        print(f"  {table}: deleted {deleted} rows")
        return deleted
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenarios", default="", help="comma-separated scenario names to remove")
    parser.add_argument("--include-base", action="store_true", help="allow deleting Base/Actual rows (not recommended)")
    parser.add_argument("--confirm", help="RESET-<workspace name> to actually delete")
    args = parser.parse_args(argv)

    ctx = Context()
    try:
        scenarios = validate_scenarios([s.strip() for s in args.scenarios.split(",") if s.strip()], args.include_base)
    except WritebackError as exc:
        print(f"Refused: {exc}", file=sys.stderr)
        return 2
    expected_confirm = f"RESET-{ctx.cfg.names.workspace}"
    apply = args.confirm == expected_confirm
    if args.confirm and not apply:
        print(f"--confirm must equal {expected_confirm}", file=sys.stderr)
        return 2
    tables = registered_tables(ctx)
    if not tables:
        print("No registered writeback tables with a scenario column. Run verify_writeback.py register first.")
        return 2
    print(("APPLYING reset" if apply else "DRY RUN (nothing deleted)") + f" in {ctx.cfg.names.writeback_db}")
    total = 0
    with ctx.sql_session(ctx.cfg.names.writeback_db) as session:
        for record in tables:
            total += reset_table(session, record.name, record.extra["scenarioColumn"], scenarios, apply)
    if not apply:
        print(f"To delete, rerun with --confirm {expected_confirm}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
