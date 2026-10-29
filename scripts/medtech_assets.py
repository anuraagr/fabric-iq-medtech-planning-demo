"""Build the synthetic MedTech asset registry workbook used by the PowerTable demo (fictional data).

    python scripts/medtech_assets.py build [--out model/medtech/Northwind-MedTech-asset-registry.xlsx]

The workbook has the same four sheets as the official PowerTable tutorial file (Assets, Employees, Locations, Import)
so the tutorial's click path applies unchanged. It is deterministic: the content digest printed below is pinned by
tests/unit/test_medtech_assets.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.data.medtech_assets import build_registry, write_workbook  # noqa: E402
from scripts.lib.config import REPO_ROOT  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "model" / "medtech" / "Northwind-MedTech-asset-registry.xlsx"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="write the workbook")
    build.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)
    registry = build_registry()
    path = write_workbook(Path(args.out), registry)
    sizes = {name: len(rows) for name, (_, rows) in registry.sheets().items()}
    print(f"Wrote {path} ({path.stat().st_size:,} bytes); rows per sheet: {sizes}")
    print(f"Content digest {registry.digest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
