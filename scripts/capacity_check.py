"""Is the capacity answering normally right now? Times a trivial DAX query against the MedTech model and classifies the answer.

    python scripts/capacity_check.py            # three probes, 5 s apart; exit code 0 only when all three are healthy
    python scripts/capacity_check.py --wait 45  # probe every 2 minutes until three healthy answers in a row (at most 45 minutes)

    healthy   answered within 8 s
    delayed   answered, but slowly: the interactive-delay stage of Fabric's throttling adds a fixed ~20 s to every interactive request
    rejected  HTTP 400 DatasetExecuteQueriesError, "compute capacity has exceeded its limits"
    error     anything else (sign-in, missing model, network)

The query does no work, so its latency is the capacity's, not the model's. The thresholds are what this build observed on a shared trial capacity
(docs/architecture.md, section 9): about 4-7 s when healthy and a constant 21-23 s in the delay stage. Run it before a demo, not during one.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.lib.auth import AuthError  # noqa: E402
from scripts.lib.context import Context  # noqa: E402
from scripts.lib.http import is_capacity_rejection  # noqa: E402

QUERY = 'EVALUATE ROW("v", 1)'
HEALTHY_SECONDS = 8.0
HEALTHY_STREAK = 3
CONFIRM_PAUSE = 10.0  # between the confirming probes once the capacity answers quickly


@dataclass(frozen=True)
class Probe:
    state: str  # healthy | delayed | rejected | error
    seconds: float
    detail: str = ""

    @property
    def healthy(self) -> bool:
        return self.state == "healthy"

    def __str__(self) -> str:
        return f"{self.state} {self.seconds:.1f} s" + (f" - {self.detail}" if self.detail else "")


def classify(seconds: float, error: BaseException | None = None) -> str:
    if error is None:
        return "healthy" if seconds <= HEALTHY_SECONDS else "delayed"
    return "rejected" if is_capacity_rejection(error) else "error"


def probe(run_query: Callable[[], object], clock: Callable[[], float] = time.monotonic) -> Probe:
    started = clock()
    try:
        run_query()
    except Exception as exc:  # noqa: BLE001 - a probe reports every failure mode instead of raising
        return Probe(classify(clock() - started, exc), clock() - started, " ".join(str(exc).split())[:140])
    seconds = clock() - started
    return Probe(classify(seconds), seconds)


def check(run_query: Callable[[], object], *, probes: int = 3, interval: float = 5.0, sleep: Callable[[float], None] = time.sleep,
          report: Callable[[str], None] = print, clock: Callable[[], float] = time.monotonic) -> list[Probe]:
    results: list[Probe] = []
    for index in range(probes):
        if index:
            sleep(interval)
        results.append(probe(run_query, clock))
        report(f"{datetime.now():%H:%M:%S} {results[-1]}")
    return results


def wait_until_healthy(run_query: Callable[[], object], *, minutes: float, interval: float = 120.0, sleep: Callable[[float], None] = time.sleep,
                       report: Callable[[str], None] = print, clock: Callable[[], float] = time.monotonic) -> bool:
    """Probe until `HEALTHY_STREAK` healthy answers come in a row; False when `minutes` pass first (a slow or failed answer resets the streak)."""
    deadline = clock() + minutes * 60
    streak = 0
    while True:
        result = probe(run_query, clock)
        streak = streak + 1 if result.healthy else 0
        report(f"{datetime.now():%H:%M:%S} {result} (healthy in a row: {streak}/{HEALTHY_STREAK})")
        if streak >= HEALTHY_STREAK:
            return True
        pause = min(interval, CONFIRM_PAUSE) if result.healthy else interval
        if clock() + pause > deadline:
            return False
        sleep(pause)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--probes", type=int, default=3, help="number of probes without --wait (default 3)")
    parser.add_argument("--interval", type=float, help="seconds between probes (default 5, or 120 with --wait)")
    parser.add_argument("--wait", type=float, metavar="MINUTES", help="keep probing until three healthy answers in a row, for at most this many minutes")
    args = parser.parse_args(argv)
    if args.probes < 1:
        parser.error("--probes must be at least 1")
    if args.wait is not None and args.wait <= 0:
        parser.error("--wait must be a positive number of minutes")
    if args.interval is not None and args.interval < 0:
        parser.error("--interval must not be negative")
    ctx = Context()
    wid = ctx.workspace_id()
    model = ctx.manifest.find("SemanticModel", ctx.cfg.names.medtech_model, wid)
    if model is None:
        print("The MedTech semantic model is not in the manifest: run scripts/deploy.py first.")
        return 2
    try:
        ctx.clients.tokens.get("powerbi")  # the first call starts `az`; keep that out of the timed probes
    except AuthError as exc:
        print(f"Cannot sign in: {exc}")
        return 2

    def run_query() -> None:
        ctx.clients.powerbi.execute_queries(wid, model.id, QUERY)

    if args.wait is not None:
        healthy = wait_until_healthy(run_query, minutes=args.wait, interval=120.0 if args.interval is None else args.interval)
        print("HEALTHY: the capacity answers normally" if healthy else f"NOT HEALTHY within {args.wait:g} minutes: see docs/cost-and-cleanup.md")
        return 0 if healthy else 1
    results = check(run_query, probes=args.probes, interval=5.0 if args.interval is None else args.interval)
    healthy = bool(results) and all(p.healthy for p in results)
    print("HEALTHY: the capacity answers normally" if healthy else "NOT HEALTHY: wait before presenting (docs/cost-and-cleanup.md, 'Capacity sizing and throttling')")
    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
