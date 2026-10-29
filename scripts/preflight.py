"""Read-only preflight: classify every prerequisite as Verified / Requires administrator action /
Requires user input / Unsupported / Not yet tested. Never mutates the tenant.

Usage:
    python scripts/preflight.py                 # live read-only checks, prints Markdown
    python scripts/preflight.py --offline       # local checks only; tenant checks are 'Not yet tested'
    python scripts/preflight.py --write-docs    # also write reports/preflight-report.md (ignored)
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.fetch_sample_assets import ASSET_DIR, verify as verify_assets  # noqa: E402
from scripts.lib.auth import AuthError  # noqa: E402
from scripts.lib.clients import Clients, build_clients  # noqa: E402
from scripts.lib.config import ConfigError, DemoConfig, REPO_ROOT, load_config  # noqa: E402
from scripts.lib.fabric import ADMIN_ROLES, sku_planning_class  # noqa: E402
from scripts.lib.http import ApiError  # noqa: E402
from scripts.lib.report import Check, Report, Status  # noqa: E402

LEARN = "https://learn.microsoft.com/en-us/fabric/iq/plan"
ADMIN_SETTINGS_TO_VERIFY = (
    "Integration settings > 'Allow XMLA endpoints and Analyze in Excel with on-premises semantic models' = Enabled",
    "Planning: 'Users can upgrade to a Planner session' and 'Users can upgrade to a Stakeholder session' = Enabled "
    "(without them users only get Reading view)",
    "Developer settings > 'Service principals can call Fabric public APIs' (only if you later use service principals)",
    "Fabric SQL database creation is allowed for the signed-in admin (item creation tenant settings)",
)


def add(report: Report, id: str, area: str, title: str, status: Status, evidence: str = "", action: str = "", source: str = "") -> None:
    report.add(Check(id, area, title, status, evidence, action, source))


def check_local(report: Report) -> None:
    py = sys.version_info
    add(report, "L-PY", "Local", "Python 3.11+", Status.VERIFIED if py >= (3, 11) else Status.REQUIRES_USER_INPUT,
        f"Python {py.major}.{py.minor}.{py.micro}", "" if py >= (3, 11) else "Install Python 3.11 or newer")

    az = shutil.which("az")
    if az:
        try:
            out = subprocess.run([az, "version", "--query", '"azure-cli"', "--output", "tsv"], capture_output=True, text=True, timeout=60)
            add(report, "L-AZ", "Local", "Azure CLI", Status.VERIFIED, f"azure-cli {out.stdout.strip() or 'present'}")
        except subprocess.TimeoutExpired:
            add(report, "L-AZ", "Local", "Azure CLI", Status.REQUIRES_USER_INPUT, "az version timed out", "Reinstall or repair Azure CLI")
    else:
        add(report, "L-AZ", "Local", "Azure CLI", Status.REQUIRES_USER_INPUT, "az not found on PATH", "Install Azure CLI: https://aka.ms/installazurecliwindows")

    try:
        import pyodbc

        drivers = pyodbc.drivers()
        ok = "ODBC Driver 18 for SQL Server" in drivers
        add(report, "L-ODBC", "Local", "pyodbc + ODBC Driver 18 for SQL Server", Status.VERIFIED if ok else Status.REQUIRES_USER_INPUT,
            f"pyodbc {pyodbc.version}; drivers: {', '.join(d for d in drivers if 'SQL Server' in d) or 'none'}",
            "" if ok else "Install the Microsoft ODBC Driver 18 for SQL Server")
    except ImportError:
        add(report, "L-ODBC", "Local", "pyodbc + ODBC Driver 18 for SQL Server", Status.REQUIRES_USER_INPUT, "pyodbc is not installed", "pip install -r requirements.txt")

    add(report, "L-GIT", "Local", "git", Status.VERIFIED if shutil.which("git") else Status.REQUIRES_USER_INPUT,
        shutil.which("git") or "git not found")

    browsers = [Path(p) for p in (
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )]
    found = [str(p) for p in browsers if p.exists()]
    add(report, "L-BROWSER", "Local", "Microsoft Edge or Google Chrome for native Planning UI (Safari is unsupported)",
        Status.VERIFIED if found else Status.NOT_YET_TESTED, found[0] if found else "No Edge/Chrome at the usual Windows paths",
        "" if found else "Confirm Edge or Chrome is installed on the presenter machine", f"{LEARN}/planning-tutorial/planning/tutorial-0-introduction")


def check_assets(report: Report, config: DemoConfig) -> None:
    problems = verify_assets(ASSET_DIR, config.sample.files)
    if not problems:
        add(report, "A-SAMPLE", "Assets", "Official sample assets present and SHA-256 verified", Status.VERIFIED,
            f"{len(config.sample.files)} files at commit {config.sample.commit[:8]}", source="https://github.com/microsoft/fabric-samples/tree/main/docs-samples/iq/plan")
    else:
        add(report, "A-SAMPLE", "Assets", "Official sample assets present and SHA-256 verified", Status.REQUIRES_USER_INPUT,
            "; ".join(problems), "Run: python scripts/fetch_sample_assets.py")


def check_config(report: Report, config: DemoConfig) -> None:
    add(report, "C-TENANT", "Config", "Target Microsoft Entra tenant configured", Status.VERIFIED, config.tenant_id)
    recorded = _recorded_workspace(config)
    if config.workspace_id:
        add(report, "C-WS", "Config", "Target workspace selected", Status.VERIFIED, f"workspace id {config.workspace_id}")
    elif recorded is not None:
        add(report, "C-WS", "Config", "Target workspace selected", Status.VERIFIED, f"workspace '{recorded.name}' ({recorded.id}) recorded in the deployment manifest")
    else:
        add(report, "C-WS", "Config", "Target workspace selected", Status.REQUIRES_USER_INPUT,
            f"No FPD_WORKSPACE_ID. Default plan: create a dedicated workspace named '{config.names.workspace}'.",
            "Approve workspace creation (or set FPD_WORKSPACE_ID to an existing workspace)")
    if config.capacity_id:
        add(report, "C-CAP", "Config", "Target capacity selected", Status.VERIFIED, f"capacity id {config.capacity_id}")
    elif recorded is not None and recorded.extra.get("capacityId"):
        add(report, "C-CAP", "Config", "Target capacity selected", Status.VERIFIED, f"capacity id {recorded.extra['capacityId']} recorded in the deployment manifest")
    else:
        add(report, "C-CAP", "Config", "Target capacity selected", Status.REQUIRES_USER_INPUT, "No FPD_CAPACITY_ID set", "Choose one of the capacities listed below and set FPD_CAPACITY_ID")


def _recorded_workspace(config: DemoConfig):
    """The workspace a previous deploy.py run recorded in the (local) deployment manifest, if any."""
    from scripts.lib.manifest import Manifest

    try:
        return Manifest(config.manifest_path, config.tenant_id).find("Workspace", config.names.workspace)
    except (OSError, ValueError):
        return None


def probe(report: Report, id: str, area: str, title: str, call, ok_text, *, source: str = "", unsupported_codes: tuple[str, ...] = ()) -> object | None:
    """Run a read-only API call and classify the outcome."""
    try:
        result = call()
    except ApiError as exc:
        if exc.code in unsupported_codes or exc.status == 404:
            add(report, id, area, title, Status.UNSUPPORTED, f"{exc.status} {exc.code}: {exc.message}"[:300], source=source)
        elif exc.status in (401, 403):
            add(report, id, area, title, Status.REQUIRES_ADMIN, f"{exc.status} {exc.code}: {exc.message}"[:300], "Ask a Fabric/workspace admin to grant access", source)
        else:
            add(report, id, area, title, Status.REQUIRES_USER_INPUT, f"{exc.status} {exc.code}: {exc.message}"[:300], "Investigate and re-run preflight", source)
        return None
    add(report, id, area, title, Status.VERIFIED, ok_text(result), source=source)
    return result


def check_tenant(report: Report, clients: Clients) -> None:
    config = clients.config
    try:
        identity = clients.tokens.identity()
    except AuthError as exc:
        add(report, "I-TOKEN", "Identity", "Interactive Entra sign-in for the target tenant", Status.REQUIRES_USER_INPUT, str(exc)[:400], "Sign in with Azure CLI (see README)")
        return
    report.identity = identity["upn"]
    add(report, "I-TOKEN", "Identity", "Interactive Entra sign-in for the target tenant", Status.VERIFIED,
        f"{identity['upn']} (tid {identity['tenantId']}, amr {identity['amr'] or 'n/a'})")
    is_guest = "#EXT#" in identity["upn"].upper()
    add(report, "I-GUEST", "Identity", "Signed-in identity is a native tenant member (Plan does not support Entra B2B guests)",
        Status.UNSUPPORTED if is_guest else Status.VERIFIED, identity["upn"], "Use a member account of the target tenant" if is_guest else "",
        f"{LEARN}/planning-tutorial/planning/tutorial-0-introduction")
    for audience, label in (("powerbi", "Power BI"), ("sql", "Fabric SQL (TDS)")):
        try:
            clients.tokens.get(audience)
            add(report, f"I-TOKEN-{audience.upper()}", "Identity", f"Access token for {label} audience", Status.VERIFIED, "issued for the target tenant")
        except AuthError as exc:
            add(report, f"I-TOKEN-{audience.upper()}", "Identity", f"Access token for {label} audience", Status.REQUIRES_USER_INPUT, str(exc)[:300])

    fabric = clients.fabric
    capacities = probe(report, "F-CAPS", "Fabric", "List Fabric capacities", fabric.list_capacities, lambda r: f"{len(r)} capacities visible")
    active_capacity_ids: set[str] = set()
    for capacity in capacities or []:
        cap_id, sku, state = capacity.get("id", ""), capacity.get("sku", ""), capacity.get("state", "")
        klass = sku_planning_class(sku)
        trial = sku.upper().startswith("FT") or capacity.get("displayName", "").lower().startswith("trial")
        label = f"Capacity '{capacity.get('displayName')}' ({sku}, {capacity.get('region')}, {state})"
        check_id = f"F-CAP-{cap_id[:8]}"
        if klass == "unsupported":
            add(report, check_id, "Fabric", label, Status.UNSUPPORTED, "Per-user/A/EM capacities are not supported for Planning XMLA scenarios", source=f"{LEARN}/overview-prerequisites")
        elif state != "Active":
            add(report, check_id, "Fabric", label, Status.REQUIRES_USER_INPUT, f"State is {state}: it must be resumed before use (billable pay-as-you-go while running)",
                "Approve resuming this capacity, or choose another", f"{LEARN}/resources/billing-fabric-plan")
        else:
            note = " Trial capacity: expiry is not readable via API; confirm days remaining in the Fabric portal. A Fabric trial is permitted by tutorial 0." if trial else ""
            add(report, check_id, "Fabric", label, Status.VERIFIED, f"Active, planning-capable SKU class.{note}", source=f"{LEARN}/planning-tutorial/planning/tutorial-0-introduction")
            active_capacity_ids.add(cap_id)
    if config.capacity_id and capacities is not None and config.capacity_id not in {c.get("id") for c in capacities}:
        add(report, "F-CAP-SELECTED", "Fabric", "Configured FPD_CAPACITY_ID is visible to this identity", Status.REQUIRES_USER_INPUT, config.capacity_id, "Fix FPD_CAPACITY_ID")

    workspaces = probe(report, "F-WS-LIST", "Fabric", "List workspaces", fabric.list_workspaces, lambda r: f"{len(r)} workspaces visible")
    probe_workspace = None
    for workspace in workspaces or []:
        if workspace.get("type") == "Workspace" and workspace.get("capacityId") in active_capacity_ids:
            probe_workspace = workspace
            break
    existing = [w for w in (workspaces or []) if w.get("displayName", "").casefold() == config.names.workspace.casefold() and w.get("type") == "Workspace"]
    if config.workspace_id:
        existing = [w for w in (workspaces or []) if w.get("id") == config.workspace_id]
    if existing:
        target = existing[0]
        try:
            role = fabric.my_workspace_role(target["id"], identity["objectId"])
        except ApiError as exc:
            role = None
            add(report, "F-WS-ROLE", "Fabric", f"Role in workspace '{target['displayName']}'", Status.REQUIRES_ADMIN, f"{exc.status} {exc.code}", "Need Admin or Member")
        else:
            add(report, "F-WS-ROLE", "Fabric", f"Role in workspace '{target['displayName']}'",
                Status.VERIFIED if role in ADMIN_ROLES else Status.REQUIRES_ADMIN, f"role = {role}",
                "" if role in ADMIN_ROLES else "Tutorial 0 requires Admin or Member (Contributor is documented as insufficient; the FAQ says Contributor works)",
                f"{LEARN}/planning-tutorial/planning/tutorial-0-introduction")
        probe_workspace = target
    else:
        add(report, "F-WS-TARGET", "Fabric", f"Dedicated workspace '{config.names.workspace}'", Status.NOT_YET_TESTED,
            "Does not exist yet; creator becomes Admin on creation", "Created by deploy.py apply after approval")

    if probe_workspace:
        wid, wname = probe_workspace["id"], probe_workspace["displayName"]
        probe(report, "F-PLAN-API", "Fabric", "Plan item REST API (List Plans) responds", lambda: fabric.list_plans(wid),
              lambda r: f"GET /workspaces/<{wname}>/plans -> 200, {len(r)} plans", source="https://learn.microsoft.com/en-us/rest/api/fabric/plan/items/list-plans")
        probe(report, "F-SQLDB-API", "Fabric", "Fabric SQL database REST API (List SQL Databases) responds",
              lambda: list(fabric.api.paginate(f"workspaces/{wid}/sqlDatabases")), lambda r: f"GET /workspaces/<{wname}>/sqlDatabases -> 200, {len(r)} databases",
              source="https://learn.microsoft.com/en-us/rest/api/fabric/sqldatabase/items")
    else:
        add(report, "F-PLAN-API", "Fabric", "Plan item REST API (List Plans) responds", Status.NOT_YET_TESTED, "No workspace on an active capacity to probe")
        add(report, "F-SQLDB-API", "Fabric", "Fabric SQL database REST API responds", Status.NOT_YET_TESTED, "No workspace on an active capacity to probe")

    probe(report, "F-PBI-API", "Fabric", "Power BI REST API (List Workspaces) responds", lambda: clients.powerbi.api.get_json("groups", params={"$top": 1}),
          lambda r: "GET /groups -> 200", source="https://learn.microsoft.com/en-us/rest/api/power-bi/groups/get-groups")

    try:
        fabric.api.get_json("admin/tenantsettings")
        add(report, "F-ADMIN-SETTINGS", "Admin", "Read tenant settings via Admin API", Status.VERIFIED, "Admin API readable; review settings in the Admin portal output")
    except ApiError as exc:
        add(report, "F-ADMIN-SETTINGS", "Admin", "Tenant settings required by Planning", Status.REQUIRES_ADMIN,
            f"Admin API not usable with the Azure CLI token ({exc.status} {exc.code}), so these cannot be verified programmatically.",
            "A Fabric admin must confirm in Admin portal > Tenant settings: " + " | ".join(ADMIN_SETTINGS_TO_VERIFY), f"{LEARN}/overview-prerequisites")

    add(report, "F-XMLA", "Admin", "Capacity XMLA Endpoint = Read Only or Read Write", Status.REQUIRES_ADMIN,
        "Not exposed by a documented REST API", "Admin portal > Capacity settings > Power BI workloads > XMLA Endpoint", f"{LEARN}/overview-prerequisites")


def check_governance(report: Report) -> None:
    add(report, "G-APPROVAL", "Governance", "Explicit approval of tenant, workspace, capacity and intended changes", Status.REQUIRES_USER_INPUT,
        "Required before any mutation (deploy.py apply refuses to run without matching confirmation flags)", "Review `python scripts/deploy.py plan` and approve")
    add(report, "G-BILLING", "Governance", "Billing and Planning session implications acknowledged", Status.REQUIRES_USER_INPUT,
        "Planner 847 / Stakeholder 168 / Viewer 37 CU-hours per non-cancellable 30-day session; paused capacity bills the remainder", "See docs/cost-and-cleanup.md",
        f"{LEARN}/resources/billing-fabric-plan")
    add(report, "G-TESTUSERS", "Governance", "Approved native-member test identities for multi-user security validation", Status.REQUIRES_USER_INPUT,
        "None supplied. B2B guest accounts are unsupported by Planning.", "Provide Stakeholder/Viewer test users or accept 'multi-user validation not executed'")
    add(report, "G-NATIVE-UI", "Governance", "Native UI authoring (sheets, PowerTable, Intelligence, Infobridge) and writeback commit", Status.NOT_YET_TESTED,
        "Plan REST supports create/get/update (PlanV1 definition) but documents no way to author sheets from scratch or to trigger writeback",
        "Follow docs/manual-setup.md", "https://learn.microsoft.com/en-us/rest/api/fabric/articles/item-management/definitions/plan-definition")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--offline", action="store_true", help="skip tenant checks")
    parser.add_argument("--write-docs", action="store_true", help="write reports/preflight-report.md (local, git-ignored)")
    parser.add_argument("--strict", action="store_true", help="exit 1 if any check is Unsupported or Requires admin action")
    args = parser.parse_args(argv)

    report = Report("Preflight report")
    check_local(report)
    try:
        config = load_config()
    except ConfigError as exc:
        add(report, "C-TENANT", "Config", "Target Microsoft Entra tenant configured", Status.REQUIRES_USER_INPUT, str(exc), "Copy .env.example to .env and edit")
        config = None
    if config is not None:
        report.tenant_id = config.tenant_id
        check_config(report, config)
        check_assets(report, config)
        if args.offline:
            add(report, "F-OFFLINE", "Fabric", "Tenant checks", Status.NOT_YET_TESTED, "Skipped (--offline)")
        else:
            check_tenant(report, build_clients(config))
    check_governance(report)

    markdown = report.to_markdown()
    print(markdown)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = REPO_ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"preflight-{stamp}.md").write_text(markdown, encoding="utf-8")
    (out_dir / f"preflight-{stamp}.json").write_text(report.to_json(), encoding="utf-8")
    if args.write_docs:
        (out_dir / "preflight-report.md").write_text(markdown, encoding="utf-8")
    if args.strict and (report.by_status(Status.UNSUPPORTED) or report.by_status(Status.REQUIRES_ADMIN)):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
