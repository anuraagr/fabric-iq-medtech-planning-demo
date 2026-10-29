"""Print the effective permissions of the signed-in identity at each layer (read-only evidence for T-11).

    python scripts/permissions.py              # human-readable matrix
    python scripts/permissions.py --json       # the same as JSON (one object per layer check)

Run it once per test identity, each with its own Azure CLI profile:

    $env:AZURE_CONFIG_DIR = "$HOME\\.azure-fabricplanningdemo-viewer"
    az login --tenant <tenant-guid> --allow-no-subscriptions
    python scripts/permissions.py

Nothing is written to the tenant. Tokens stay in memory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib import permissions as perm  # noqa: E402
from scripts.lib.context import Context  # noqa: E402
from scripts.lib.writeback import parse_table_ref  # noqa: E402

POWERTABLE_TABLE = "dbo.assets"


def sql_layer(ctx: Context, layer: str, database: str, tables: list[str]) -> list[perm.LayerResult]:
    try:
        with ctx.sql_session(database) as session:
            rows = session.query(perm.sql_probe_query(tables))
    except Exception as exc:  # noqa: BLE001 - a login failure is itself the finding for this layer
        outcome, detail = perm.classify_error(exc)
        return [perm.LayerResult(layer, "connect and read", outcome, detail)]
    return perm.interpret_sql_row(layer, rows[0], tables)


def collect(ctx: Context) -> tuple[str, list[perm.LayerResult]]:
    """(upn, results) for the signed-in identity across every readable layer."""
    identity = ctx.clients.tokens.identity()
    upn = identity["upn"]
    wid = ctx.workspace_id()
    fabric, powerbi = ctx.clients.fabric, ctx.clients.powerbi
    results = [
        perm.LayerResult("Microsoft Entra", "tenant member (not a B2B guest)", perm.ALLOWED if "#EXT#" not in upn.upper() else perm.DENIED, f"{upn}; tenant {identity['tenantId']}"),
        perm.probe("Workspace", "read workspace", lambda: f"capacity {fabric.get_workspace(wid).get('capacityId', '?')}"),
        perm.probe("Workspace", "role assignment", lambda: str(fabric.my_workspace_role(wid, identity["objectId"]) or "not listed directly (may be via a group)")),
    ]
    plan = ctx.manifest.find("Plan", ctx.cfg.names.medtech_plan, wid)
    if plan:
        results.append(perm.probe("Plan item", "read plan item", lambda: str(fabric.get_plan(wid, plan.id).get("displayName"))))
    model = ctx.manifest.find("SemanticModel", ctx.cfg.names.medtech_model, wid)
    if model:
        results.append(perm.probe("Semantic model", "execute a DAX query (read + build)", lambda: str(powerbi.execute_queries(wid, model.id, 'EVALUATE ROW("v", 1)'))))
    medtech = ctx.cfg.raw.get("medtech") or {}
    writeback_tables = [f"{t.get('schema', 'dbo')}.{t['source_table']}" for t in medtech.get("writeback_tables") or []]
    scenario_table = (medtech.get("scenario") or {}).get("table")
    if scenario_table:
        writeback_tables.append(scenario_table)
    for entry in writeback_tables:
        parse_table_ref(entry)  # validates the name before it reaches a query
    results += sql_layer(ctx, "SQL writeback database", ctx.cfg.names.writeback_db, writeback_tables)
    results += sql_layer(ctx, "SQL PowerTable database", ctx.cfg.names.powertable_db, [POWERTABLE_TABLE])
    return upn, results + perm.manual_layers()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)
    upn, results = collect(Context())
    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
    else:
        print(f"Effective permissions for {upn} (read-only probe)\n")
        for result in results:
            print(f"  [{result.outcome:>7}] {result.layer:<26} {result.check}" + (f"  - {result.detail}" if result.detail else ""))
        print("\n" + perm.summarize_author(results, upn))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
