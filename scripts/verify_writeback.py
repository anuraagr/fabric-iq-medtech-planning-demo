"""Prove a native Planning writeback with T-SQL against the DEMO-OWNED destination database.

The destination table is created by Planning at first writeback; this tool discovers its real schema and
never touches the product's internal __fabric_plan_sys database.

    python scripts/verify_writeback.py describe --table dbo.Forecast
    python scripts/verify_writeback.py register --table dbo.Forecast --grain "Category,Month" --value Value
                                                [--scenario-column Scenario] [--latest-column IsLatest]
    python scripts/verify_writeback.py snapshot --table dbo.Forecast --grain "Category,Month" --value Value --out state/wb-before.json
    python scripts/verify_writeback.py diff     --table dbo.Forecast --grain "Category,Month" --value Value
                                                --before state/wb-before.json --expect "Beverages|2026-02-01=123.45" [--record T-09]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.acceptance import ResultStore  # noqa: E402
from scripts.lib.config import REPO_ROOT  # noqa: E402
from scripts.lib.context import Context  # noqa: E402
from scripts.lib.finance import only_expected_changes, snapshot_diff  # noqa: E402
from scripts.lib.writeback import load_snapshot, parse_expect, parse_table_ref, read_rows, save_snapshot, snapshot  # noqa: E402


def _csv(text: str) -> list[str]:
    return [p.strip() for p in text.split(",") if p.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("describe", "register", "snapshot", "diff"):
        p = sub.add_parser(name)
        p.add_argument("--table", required=True, help="schema.table in the writeback database")
        if name != "describe":
            p.add_argument("--grain", required=True, help="comma-separated dimension columns that identify one value")
            p.add_argument("--value", required=True, help="numeric value column")
            p.add_argument("--latest-column", help="IsLatest-style column for the 'with Changes' writeback types")
            p.add_argument("--where", help="extra read-only SQL filter, e.g. \"[Scenario] = 'Base'\"")
        if name == "register":
            p.add_argument("--scenario-column", help="column holding the scenario name (enables scenario-scoped reset)")
        if name == "snapshot":
            p.add_argument("--out", required=True)
        if name == "diff":
            p.add_argument("--before", required=True)
            p.add_argument("--expect", action="append", default=[], help="'k1|k2=value' (repeatable)")
            p.add_argument("--record", help="acceptance test id to record the verdict under (e.g. T-09)")
    args = parser.parse_args(argv)
    if args.command == "diff" and args.record and not args.expect:
        parser.error("Recording writeback evidence requires at least one --expect value.")

    ctx = Context()
    table = parse_table_ref(args.table)
    db_name = ctx.cfg.names.writeback_db
    with ctx.sql_session(db_name) as session:
        if args.command == "describe":
            columns = session.table_columns(table.schema, table.table)
            if not columns:
                print(f"{table} does not exist in {db_name} (has the first writeback run?)")
                return 1
            for c in columns:
                print(f"{c['COLUMN_NAME']:40} {c['DATA_TYPE']}")
            print("rows:", session.scalar(f"SELECT COUNT(*) FROM {table.sql()}"))
            return 0

        grain = _csv(args.grain)
        rows = read_rows(session, table, grain, args.value, latest_column=args.latest_column, where=args.where)
        values, duplicates = snapshot(rows, grain, args.value)

    if args.command == "register":
        extra = {"grain": grain, "valueColumn": args.value, "latestColumn": args.latest_column or "", "scenarioColumn": args.scenario_column or "", "database": db_name}
        ctx.manifest.record("WritebackTable", str(table), f"{db_name}/{table}", workspace_id=ctx.workspace_id(), created_by="manual", owned=True, **extra)
        ctx.manifest.save()
        print(f"registered {table}: {len(values)} grains, duplicates: {len(duplicates)}")
        return 0
    if args.command == "snapshot":
        save_snapshot(Path(args.out), table, grain, args.value, values)
        print(f"snapshot of {len(values)} grains saved to {args.out}; duplicate grains: {len(duplicates)}")
        return 1 if duplicates else 0

    metadata, before = load_snapshot(Path(args.before))
    if metadata.get("table") != str(table) or metadata.get("grain") != grain or metadata.get("value") != args.value:
        parser.error("The baseline snapshot table/grain/value does not match this comparison.")
    diff = snapshot_diff(before, values)
    problems = only_expected_changes(diff, parse_expect(args.expect)) if args.expect else []
    if duplicates:
        problems.append(f"duplicate grains present: {dict(list(duplicates.items())[:5])}")
    print(f"changed={len(diff.changed)} added={len(diff.added)} removed={len(diff.removed)}")
    for key, (old, new) in list(diff.changed.items())[:10]:
        print(f"  {key}: {old} -> {new}")
    for problem in problems:
        print(f"PROBLEM: {problem}")
    if args.record:
        store = ResultStore(REPO_ROOT / "state" / "acceptance-results.json")
        observed = f"{table}: changed={len(diff.changed)}, added={len(diff.added)}, removed={len(diff.removed)}; expected={dict(parse_expect(args.expect))}; problems={problems or 'none'}"
        store.record(args.record, "failed" if problems else "passed", observed)
        store.save()
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
