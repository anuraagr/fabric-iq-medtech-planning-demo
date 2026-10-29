"""Make reporting show what Planning last committed: refresh the MedTech semantic model with a fresh SQL credential.

    python scripts/refresh_model.py             # bind a fresh SQL token, refresh, wait for it to finish (about a minute)
    python scripts/refresh_model.py --verify    # ... then run the T-10 reconciliation: the model equals the SQL tables
    python scripts/refresh_model.py --status    # read-only: the latest refreshes of the model and why one failed

The model is Import mode, so a commit in the Fabric SQL database reaches Intelligence only after a refresh. The SQL data source is authenticated with an
OAuth2 access token that this repository binds with the Power BI "Update Datasource" API. That token lasts about an hour and comes without a refresh
token, so a refresh started later - the portal's *Refresh now*, a schedule - retries for about nine minutes and then ends with "The credentials provided
for the SQL source are invalid" (observed on 2026-10-27, four days after the last bind). This command binds a new token first. The token is sent to
Power BI only; it is never printed, logged or stored.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.auth import AuthError  # noqa: E402
from scripts.lib.context import Context  # noqa: E402
from scripts.lib.http import ApiError  # noqa: E402

IN_PROGRESS = "Unknown"  # how the Power BI refresh history reports a refresh that is still running (it has no end time yet)
IDLE_TIMEOUT = 600.0  # a refresh that fails on an expired credential runs for about nine minutes
REFRESH_TIMEOUT = 300.0  # a refresh with a valid credential took 4-45 s here


def is_running(entry: dict) -> bool:
    return entry.get("status") == IN_PROGRESS and not entry.get("endTime")


@dataclass(frozen=True)
class RefreshOutcome:
    request_id: str
    sources: int
    seconds: float
    ended: str


def wait_until_idle(powerbi, workspace_id: str, dataset_id: str, *, timeout: float = IDLE_TIMEOUT, interval: float = 10.0,
                    sleep: Callable[[float], None] = time.sleep, report: Callable[[str], None] = print) -> None:
    """Power BI runs one refresh at a time: wait for one that is still running (for example a *Refresh now* with the expired credential)."""
    waited = 0.0
    while True:
        history = powerbi.refresh_history(workspace_id, dataset_id, top=1)
        newest = history[0] if history else None
        if not newest or not is_running(newest):
            return
        if waited >= timeout:
            raise ApiError(409, "RefreshInProgress", f"the refresh started at {newest.get('startTime')} is still running after {timeout:.0f} s")
        if waited == 0:
            report(f"A refresh started at {newest.get('startTime')} is still running; waiting for it to end first ...")
        sleep(interval)
        waited += interval


def refresh(powerbi, workspace_id: str, dataset_id: str, access_token: str, *, timeout: float = REFRESH_TIMEOUT, interval: float = 5.0,
            idle_timeout: float = IDLE_TIMEOUT, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
            report: Callable[[str], None] = print) -> RefreshOutcome:
    """Bind the token to the model's SQL data sources, wait for any running refresh, start a new one and wait for it."""
    sources = powerbi.set_oauth_credentials(workspace_id, dataset_id, access_token)
    if sources == 0:
        raise RuntimeError("the model has no SQL data source to bind a credential to; add the written-back tables with "
                           "the reporting-stage command in docs/deployment-guide.md (--update-medtech-model --include-writeback)")
    report(f"Bound a fresh OAuth2 credential to {sources} SQL data source(s).")
    wait_until_idle(powerbi, workspace_id, dataset_id, timeout=idle_timeout, sleep=sleep, report=report)
    started = clock()
    request_id = powerbi.refresh(workspace_id, dataset_id)
    if not request_id:
        # Without the id, waiting would match the newest history entry - an older, finished refresh - and report a success that did not happen.
        raise ApiError(502, "NoRefreshRequestId", "Power BI accepted the refresh but returned no request id, so it cannot be followed; check `--status`")
    report(f"Refresh {request_id[:8]} started ...")
    entry = powerbi.wait_for_refresh(workspace_id, dataset_id, request_id, timeout=timeout, interval=interval)
    return RefreshOutcome(request_id, sources, clock() - started, str(entry.get("endTime") or ""))


def failure_reason(entry: dict) -> str:
    """The service's own error text of a failed refresh, one line."""
    raw = entry.get("serviceExceptionJson")
    if not raw:
        return ""
    try:
        detail = json.loads(raw) if isinstance(raw, str) else raw
        text = str(detail.get("errorDescription") or detail.get("errorCode") or raw)
    except (ValueError, AttributeError):
        text = str(raw)
    return " ".join(text.split())[:300]


def describe(history: list[dict]) -> list[str]:
    lines = []
    for entry in history:
        status = str(entry.get("status") or "?")
        label = "running" if is_running(entry) else status.lower()
        text = f"{entry.get('startTime', '?')}  {label:9}"
        if entry.get("endTime"):
            text += f" until {entry['endTime']}"
        reason = failure_reason(entry)
        lines.append(f"{text}  {reason}" if reason else text)
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="read-only: show the latest refreshes of the model and stop")
    parser.add_argument("--verify", action="store_true", help="after the refresh, run the T-10 reconciliation of the model against the SQL tables")
    parser.add_argument("--timeout", type=float, default=REFRESH_TIMEOUT, help=f"seconds to wait for the refresh itself (default {REFRESH_TIMEOUT:g})")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be a positive number of seconds")
    ctx = Context()
    wid = ctx.workspace_id()
    model = ctx.manifest.find("SemanticModel", ctx.cfg.names.medtech_model, wid)
    if model is None:
        print("The MedTech semantic model is not in the manifest: run scripts/deploy.py first.")
        return 2
    powerbi = ctx.clients.powerbi
    try:
        if args.status:
            history = powerbi.refresh_history(wid, model.id, top=5)
            print("\n".join(describe(history)) or "No refresh has been recorded yet.")
            if history and str(history[0].get("status")) == "Failed":
                print("The latest refresh failed: run `python scripts/refresh_model.py` to bind a fresh credential and refresh.")
            return 0
        outcome = refresh(powerbi, wid, model.id, ctx.clients.tokens.get("sql").value, timeout=args.timeout)
    except AuthError as exc:
        print(f"Cannot sign in: {exc}")
        return 2
    except (ApiError, RuntimeError) as exc:
        print(f"REFRESH FAILED: {' '.join(str(exc).split())[:600]}")
        return 1
    print(f"REFRESHED: {outcome.request_id[:8]} ended {outcome.ended} after {outcome.seconds:.0f} s; reporting now reads the SQL tables as they are. "
          "Press Refresh Data on the Intelligence page.")
    if not args.verify:
        return 0
    from scripts.validate import t10

    try:
        status, observed = t10(ctx)
    except (ApiError, RuntimeError) as exc:
        print(f"VERIFY FAILED: {' '.join(str(exc).split())[:600]}")
        return 1
    print(f"T-10 {status.upper()}: {observed}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
