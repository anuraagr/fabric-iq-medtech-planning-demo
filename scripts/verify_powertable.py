"""Prove PowerTable inserts and updates with T-SQL against the DEMO-OWNED PowerTable database (T-07).

    python scripts/verify_powertable.py snapshot --table dbo.assets --key "Asset Tag" --out state/pt-before.json
    python scripts/verify_powertable.py diff     --table dbo.assets --key "Asset Tag" --before state/pt-before.json
                                                 --expect-update "MD-1248|Status=Under Maintenance"
                                                 --expect-insert "MD-1500|Asset Type=Infusion Pump" [--record T-07]

`snapshot` is taken before the grid edits are committed with **Save to Database**; `diff` compares the table afterwards and
fails when anything other than the expected updates and inserts moved.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.acceptance import ResultStore  # noqa: E402
from scripts.lib.config import REPO_ROOT  # noqa: E402
from scripts.lib.context import Context  # noqa: E402
from scripts.lib.powertable import PowerTableError, check_expectations, diff_rows, load_snapshot, parse_expectation, save_snapshot, snapshot_rows  # noqa: E402
from scripts.lib.sqlclient import assert_read_only  # noqa: E402
from scripts.lib.writeback import parse_table_ref, quote_column  # noqa: E402


def read_table(session, table_name: str):
    table = parse_table_ref(table_name)
    columns = [c["COLUMN_NAME"] for c in session.table_columns(table.schema, table.table)]
    if not columns:
        raise PowerTableError(f"Table {table} does not exist in the PowerTable database")
    known = set(columns)
    sql = f"SELECT {', '.join(quote_column(c, known) for c in columns)} FROM {table.sql()}"
    assert_read_only(sql)
    return table, session.query(sql)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("snapshot", "diff"):
        p = sub.add_parser(name)
        p.add_argument("--table", required=True)
        p.add_argument("--key", required=True, help="always-populated, unique business key column")
    sub.choices["snapshot"].add_argument("--out", required=True)
    diff = sub.choices["diff"]
    diff.add_argument("--before", required=True)
    diff.add_argument("--expect-update", action="append", default=[], help="'key|Column=Value|...' (repeatable)")
    diff.add_argument("--expect-insert", action="append", default=[], help="'key|Column=Value|...' (repeatable)")
    diff.add_argument("--record", help="acceptance test id to record the verdict under (for example T-07)")
    args = parser.parse_args(argv)

    ctx = Context()
    with ctx.sql_session(ctx.cfg.names.powertable_db) as session:
        table, rows = read_table(session, args.table)
    after = snapshot_rows(rows, args.key)

    if args.command == "snapshot":
        save_snapshot(Path(args.out), str(table), args.key, after)
        print(f"snapshot of {len(after)} rows of {table} saved to {args.out}")
        return 0

    _, before = load_snapshot(Path(args.before))
    delta = diff_rows(before, after)
    updates = dict(parse_expectation(t) for t in args.expect_update)
    inserts = dict(parse_expectation(t) for t in args.expect_insert)
    problems = check_expectations(delta, updates, inserts)
    print(f"{table}: before={len(before)} after={len(after)} changed={len(delta.changed)} added={len(delta.added)} removed={len(delta.removed)}")
    for key, fields in delta.changed.items():
        print(f"  changed {key}: " + ", ".join(f"{c}: {old!r} -> {new!r}" for c, (old, new) in fields.items()))
    for key, row in delta.added.items():
        print(f"  added {key}: " + ", ".join(f"{c}={row[c]!r}" for c in list(row)[:6]))
    for problem in problems:
        print(f"PROBLEM: {problem}")
    if args.record:
        observed = (
            f"{table}: rows {len(before)} -> {len(after)}; changed={ {k: {c: new for c, (_, new) in f.items()} for k, f in delta.changed.items()} }; "
            f"added={sorted(delta.added)}; removed={sorted(delta.removed)}; problems={problems or 'none'}"
        )
        store = ResultStore(REPO_ROOT / "state" / "acceptance-results.json")
        store.record(args.record, "failed" if problems else "passed", observed)
        store.save()
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
