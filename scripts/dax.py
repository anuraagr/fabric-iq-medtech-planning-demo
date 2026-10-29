"""Run a DAX query file (or inline text) against the demo semantic model and print the rows.

    python scripts/dax.py model/dax/01_smoke_2025_totals.dax
    python scripts/dax.py --query "EVALUATE ROW(\"x\", [Gross Revenue])"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.context import Context  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file", nargs="?", help="path to a .dax file")
    parser.add_argument("--query", help="inline DAX (must start with EVALUATE)")
    args = parser.parse_args(argv)
    if bool(args.file) == bool(args.query):
        parser.error("give exactly one of FILE or --query")
    dax = args.query or Path(args.file).read_text(encoding="utf-8")
    ctx = Context()
    wid = ctx.workspace_id()
    model = ctx.manifest.find("SemanticModel", ctx.cfg.names.semantic_model, wid)
    rows = ctx.clients.powerbi.execute_queries(wid, model.id, dax)
    print(json.dumps(rows, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
