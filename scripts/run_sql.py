"""Run one of the read-only verification queries in sql/ against a demo-owned Fabric SQL database.

    python scripts/run_sql.py sql/01_budget_reconciliation.sql --db writeback
    python scripts/run_sql.py sql/04_powertable_assets.sql --db powertable

The statement goes through `SqlSession.query`, which refuses anything but a single SELECT/WITH statement (comments are ignored),
so these files can never change data.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.context import Context  # noqa: E402


def format_rows(rows: list[dict]) -> str:
    if not rows:
        return "(no rows)"
    columns = list(rows[0])
    cells = [[str(row.get(c, "")) for c in columns] for row in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(columns)]
    line = lambda values: "  ".join(v.ljust(widths[i]) for i, v in enumerate(values))  # noqa: E731
    return "\n".join([line(columns), line(["-" * w for w in widths]), *(line(r) for r in cells)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file")
    parser.add_argument("--db", choices=["writeback", "powertable"], required=True)
    args = parser.parse_args(argv)
    ctx = Context()
    database = ctx.cfg.names.writeback_db if args.db == "writeback" else ctx.cfg.names.powertable_db
    sql = Path(args.file).read_text(encoding="utf-8")
    with ctx.sql_session(database) as session:
        print(format_rows(session.query(sql)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
