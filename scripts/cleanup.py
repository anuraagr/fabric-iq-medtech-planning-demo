"""Delete ONLY demo-owned resources recorded in the deployment manifest. Dry run unless confirmed.

    python scripts/cleanup.py                                                   # dry run
    python scripts/cleanup.py --confirm DELETE-FabricPlanningDemo               # delete items, keep the workspace
    python scripts/cleanup.py --confirm DELETE-FabricPlanningDemo --include-workspace

Safety rules
- Only manifest records with owned=true and created_by in {script, manual} are considered.
- The workspace is deleted only with --include-workspace, only if the demo created it, and only if every item
  inside is either in the manifest or product-managed (__fabric_plan_sys*, SQL analytics endpoints of demo
  databases). Anything else aborts workspace deletion.
- Capacities are never touched. Deleting a Plan item or capacity does NOT stop an active 30-day Planning
  session from being billed (see docs/cost-and-cleanup.md).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.context import Context  # noqa: E402
from scripts.lib.http import ApiError  # noqa: E402
from scripts.lib.manifest import ResourceRecord  # noqa: E402

PRODUCT_MANAGED_PREFIX = "__fabric_plan_sys"
SKIP_KINDS = {"WritebackTable"}  # removed together with their database


def classify_workspace_items(items: list[dict], manifest_ids: set[str], demo_item_names: set[str]) -> tuple[list[dict], list[dict], list[dict]]:
    """Split workspace items into (demo-owned, product-managed, unknown)."""
    owned, managed, unknown = [], [], []
    for item in items:
        name = item.get("displayName", "")
        if item["id"] in manifest_ids:
            owned.append(item)
        elif name.startswith(PRODUCT_MANAGED_PREFIX) or (item.get("type") == "SQLEndpoint" and name in demo_item_names):
            managed.append(item)
        else:
            unknown.append(item)
    return owned, managed, unknown


def delete_record(ctx: Context, record: ResourceRecord) -> None:
    fabric = ctx.clients.fabric
    if record.kind == "Plan":
        fabric.delete_plan(record.workspace_id, record.id)
    elif record.kind == "Workspace":
        fabric.delete_workspace(record.id)
    else:
        fabric.delete_item(record.workspace_id, record.id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--confirm", help="DELETE-<workspace name> to actually delete")
    parser.add_argument("--include-workspace", action="store_true")
    args = parser.parse_args(argv)

    ctx = Context()
    expected = f"DELETE-{ctx.cfg.names.workspace}"
    apply = args.confirm == expected
    if args.confirm and not apply:
        print(f"--confirm must equal {expected}", file=sys.stderr)
        return 2

    workspace_id = ctx.workspace_id()
    records = [r for r in ctx.manifest.owned_resources_for_cleanup()
               if r.kind not in SKIP_KINDS and (r.id == workspace_id if r.kind == "Workspace" else r.workspace_id == workspace_id)]
    workspace_record = next((r for r in records if r.kind == "Workspace"), None)
    children = [r for r in records if r.kind != "Workspace"]
    print(("APPLYING cleanup" if apply else "DRY RUN (nothing deleted)") + f" for tenant {ctx.cfg.tenant_id}")
    for r in children:
        print(f"  delete {r.kind:14} {r.name} ({r.id})")
    if args.include_workspace and workspace_record:
        items = ctx.clients.fabric.list_items(workspace_record.id)
        names = {r.name for r in children}
        owned, managed, unknown = classify_workspace_items(items, {r.id for r in children}, names)
        print(f"  delete Workspace      {workspace_record.name} ({workspace_record.id}) containing {len(owned)} demo items, {len(managed)} product-managed items, {len(unknown)} unknown")
        for item in unknown:
            print(f"    UNKNOWN item: {item['type']} {item['displayName']} ({item['id']})")
        if unknown:
            print("Refusing to delete a workspace containing unrecorded items. Move them or omit --include-workspace.", file=sys.stderr)
            return 3
    elif args.include_workspace:
        print("  (no demo-owned workspace in the manifest; the workspace will not be deleted)")
    if not apply:
        print(f"To delete, rerun with --confirm {expected}")
        return 0

    failures = 0
    targets = list(children)
    if args.include_workspace and workspace_record:
        targets.append(workspace_record)
    for record in targets:
        if record.kind == "Workspace" and failures:
            print("Keeping the workspace because an item deletion failed.", file=sys.stderr)
            continue
        try:
            delete_record(ctx, record)
            ctx.manifest.remove(record.kind, record.id)
            ctx.manifest.save()
            print(f"  deleted {record.kind} {record.name}")
        except ApiError as exc:
            failures += 1
            print(f"  FAILED {record.kind} {record.name}: {exc}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
