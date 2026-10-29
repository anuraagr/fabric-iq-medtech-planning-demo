"""Export Plan definitions locally to state/plan-exports with some known identifiers replaced.

    python scripts/export_plan.py                 # both plans in the deployment manifest
    python scripts/export_plan.py --plan medtech

The definition is the supported, documented way to read a plan back (`getDefinition`); it is exported for review and
diffing, not for re-import or publication. Masking known IDs is not a complete privacy guarantee;
arbitrary visual properties may retain user/account details. Keep these exports out of Git.
"""

from __future__ import annotations

import argparse
import base64
import re
import sys
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.config import REPO_ROOT  # noqa: E402
from scripts.lib.context import Context  # noqa: E402

EXPORT_DIR = REPO_ROOT / "state" / "plan-exports"
FQDN_RE = re.compile(r"[a-z0-9-]+\.(?:database|datawarehouse)\.fabric\.microsoft\.com", re.I)


def sanitize(text: str, replacements: dict[str, str]) -> str:
    for needle, placeholder in sorted(replacements.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(re.escape(needle), placeholder, text, flags=re.I)
    return FQDN_RE.sub("<fabric-sql-server>.database.fabric.microsoft.com", text)


def replacements_for(ctx: Context) -> dict[str, str]:
    mapping = {ctx.cfg.tenant_id: "<tenant-id>", ctx.workspace_id(): "<workspace-id>"}
    for record in ctx.manifest.resources:
        if record.id and record.kind in {"Plan", "SemanticModel", "SQLDatabase"}:
            mapping[record.id] = f"<{record.kind.lower()}-id:{record.name}>"
    return mapping


def export_plan(ctx: Context, name: str, target: Path) -> int:
    workspace_id = ctx.workspace_id()
    record = ctx.manifest.find("Plan", name, workspace_id)
    if record is None:
        raise LookupError(f"Plan {name!r} is not in the deployment manifest")
    body = ctx.clients.fabric.get_plan_definition(workspace_id, record.id)
    replacements = replacements_for(ctx)
    count = 0
    for part in body["definition"]["parts"]:
        text = base64.b64decode(part["payload"]).decode("utf-8", "replace")
        relative = part["path"]
        path = target / relative
        if PureWindowsPath(relative).drive or target.resolve() not in path.resolve().parents:
            raise ValueError("Plan definition part path leaves the export directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(sanitize(text, replacements), encoding="utf-8", newline="\n")
        count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", choices=["medtech", "fmcg", "all"], default="all")
    args = parser.parse_args(argv)
    ctx = Context()
    selected = {"medtech": ctx.cfg.names.medtech_plan, "fmcg": ctx.cfg.names.plan_item}
    for key, name in selected.items():
        if args.plan in (key, "all"):
            target = EXPORT_DIR / key
            print(f"{name}: exported {export_plan(ctx, name, target)} parts to {target.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
