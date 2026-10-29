"""Acceptance-test definitions and results store.

A test is only ever recorded as `passed` or `failed` together with the observed result; anything that
was not executed stays `not_executed`, and externally blocked work is `blocked`.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

STATUSES = ("passed", "failed", "not_executed", "blocked")
SCOPES = ("local", "live")


class AcceptanceError(ValueError):
    pass


@dataclass(frozen=True)
class AcceptanceTest:
    id: str
    scope: str  # local | live
    title: str
    expected: str
    mode: str = "automated"  # automated | manual-ui | manual-admin


def load_definitions(path: Path) -> list[AcceptanceTest]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defs: list[AcceptanceTest] = []
    seen: set[str] = set()
    for item in raw.get("tests", []):
        definition = AcceptanceTest(
            id=str(item["id"]),
            scope=str(item["scope"]),
            title=str(item["title"]),
            expected=str(item["expected"]),
            mode=str(item.get("mode", "automated")),
        )
        if definition.scope not in SCOPES:
            raise AcceptanceError(f"{definition.id}: scope must be one of {SCOPES}")
        if definition.id in seen:
            raise AcceptanceError(f"Duplicate test id {definition.id}")
        seen.add(definition.id)
        defs.append(definition)
    return defs


class ResultStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.results: dict[str, dict] = {}
        if self.path.is_file():
            self.results = json.loads(self.path.read_text(encoding="utf-8")).get("results", {})

    def record(self, test_id: str, status: str, observed: str, *, evidence: str = "") -> None:
        """Record a verdict. Evidence recorded earlier (for example a one-off UI comparison) is kept unless new evidence is given,
        so a re-run of an automated check never silently drops it."""
        if status not in STATUSES:
            raise AcceptanceError(f"status must be one of {STATUSES}")
        if status in ("passed", "failed") and not observed.strip():
            raise AcceptanceError("A passed/failed result needs the observed result (never report a test that was not executed)")
        self.results[test_id] = {
            "status": status,
            "observed": observed.strip(),
            "evidence": evidence.strip() or self.results.get(test_id, {}).get("evidence", ""),
            "recordedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    def get(self, test_id: str) -> dict:
        return self.results.get(test_id, {"status": "not_executed", "observed": "", "evidence": "", "recordedAt": ""})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name, suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"results": self.results}, handle, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise


def summarize(defs: list[AcceptanceTest], store: ResultStore) -> dict[str, int]:
    counts = {status: 0 for status in STATUSES}
    for definition in defs:
        counts[store.get(definition.id)["status"]] += 1
    return counts


def render_markdown(defs: list[AcceptanceTest], store: ResultStore) -> str:
    lines = ["| ID | Scope | Mode | Test | Expected result | Observed result | Status |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for d in defs:
        r = store.get(d.id)
        observed = r["observed"] or "-"
        if r.get("evidence"):
            observed += f" Evidence: {r['evidence']}"
        lines.append(f"| {d.id} | {d.scope} | {d.mode} | {_cell(d.title)} | {_cell(d.expected)} | {_cell(observed)} | **{r['status'].replace('_', ' ')}** |")
    return "\n".join(lines)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()
