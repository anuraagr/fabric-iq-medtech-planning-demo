"""Build, inspect and verify the synthetic MedTech / Life Sciences semantic model.

    python scripts/medtech_model.py build  [--out state/build/medtech] [--write-expected]
    python scripts/medtech_model.py verify                       # live DAX checks against the deployed model

`build` is offline and deterministic: it writes a Power BI project style `definition/` folder (TMDL with embedded data)
and prints the reference totals that `verify` and the acceptance tests compare against.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.data.medtech_generator import build_dataset  # noqa: E402
from scripts.data.medtech_tmdl import build_parts  # noqa: E402
from scripts.lib.config import REPO_ROOT  # noqa: E402

EXPECTED_PATH = REPO_ROOT / "model" / "medtech" / "expected-totals.json"


def cmd_build(args: argparse.Namespace) -> int:
    dataset = build_dataset()
    parts = build_parts(dataset)
    out = Path(args.out)
    if not out.is_absolute():
        out = REPO_ROOT / out
    for relative, data in parts.items():
        target = out / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    expected = dataset.expected_totals()
    print(f"Wrote {len(parts)} definition parts ({sum(len(d) for d in parts.values()):,} bytes) to {out}")
    print(f"Dataset digest {expected['digest']}")
    print(json.dumps({"rows": expected["rows"], "salesByYear": expected["salesByYear"]}, indent=2))
    if args.write_expected:
        EXPECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXPECTED_PATH.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {EXPECTED_PATH.relative_to(REPO_ROOT)}")
    return 0


def cmd_verify(_args: argparse.Namespace) -> int:
    from scripts.lib.context import Context
    from scripts.lib.medtech_checks import verify_model

    ctx = Context()
    wid = ctx.workspace_id()
    record = ctx.manifest.find("SemanticModel", ctx.cfg.names.medtech_model, wid)
    if record is None:
        print("The MedTech model is not in the deployment manifest. Run scripts/deploy.py apply --only medtech_model first.", file=sys.stderr)
        return 2
    checks = verify_model(ctx.clients.powerbi, wid, record.id, build_dataset().expected_totals())
    for check in checks:
        print(f"[{'PASS' if check.ok else 'FAIL'}] {check.label}: expected {check.expected} actual {check.actual}")
    return 0 if all(c.ok for c in checks) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="generate the TMDL definition folder offline")
    build.add_argument("--out", default="state/build/medtech")
    build.add_argument("--write-expected", action="store_true", help="refresh model/medtech/expected-totals.json")
    build.set_defaults(func=cmd_build)
    verify = sub.add_parser("verify", help="compare the deployed model with the generator's reference totals")
    verify.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
