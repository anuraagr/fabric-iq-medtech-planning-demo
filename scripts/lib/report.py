"""Preflight / validation report model with the five required classifications."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable


class Status(str, Enum):
    VERIFIED = "Verified"
    REQUIRES_ADMIN = "Requires administrator action"
    REQUIRES_USER_INPUT = "Requires user input"
    UNSUPPORTED = "Unsupported"
    NOT_YET_TESTED = "Not yet tested"


ORDER = [Status.VERIFIED, Status.REQUIRES_ADMIN, Status.REQUIRES_USER_INPUT, Status.UNSUPPORTED, Status.NOT_YET_TESTED]


@dataclass
class Check:
    id: str
    area: str
    title: str
    status: Status
    evidence: str = ""
    action: str = ""
    source: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        return data


@dataclass
class Report:
    title: str
    tenant_id: str = ""
    identity: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> Check:
        if any(existing.id == check.id for existing in self.checks):
            raise ValueError(f"Duplicate check id {check.id}")
        self.checks.append(check)
        return check

    def counts(self) -> dict[str, int]:
        return {status.value: sum(1 for c in self.checks if c.status is status) for status in ORDER}

    def by_status(self, status: Status) -> list[Check]:
        return [c for c in self.checks if c.status is status]

    def blocking(self) -> list[Check]:
        """Checks that prevent live deployment until resolved."""
        return [c for c in self.checks if c.status in (Status.REQUIRES_ADMIN, Status.REQUIRES_USER_INPUT, Status.UNSUPPORTED)]

    def to_json(self) -> str:
        return json.dumps(
            {
                "title": self.title,
                "tenantId": self.tenant_id,
                "identity": self.identity,
                "generatedAt": self.generated_at,
                "counts": self.counts(),
                "checks": [c.to_dict() for c in self.checks],
            },
            indent=2,
        )

    def to_markdown(self) -> str:
        lines = [f"# {self.title}", ""]
        lines.append(f"- Generated (UTC): {self.generated_at}")
        if self.tenant_id:
            lines.append(f"- Tenant: `{self.tenant_id}`")
        if self.identity:
            lines.append(f"- Identity: `{self.identity}`")
        lines += ["", "| Classification | Count |", "| --- | ---: |"]
        for status, count in self.counts().items():
            lines.append(f"| {status} | {count} |")
        for status in ORDER:
            items: Iterable[Check] = self.by_status(status)
            items = list(items)
            if not items:
                continue
            lines += ["", f"## {status.value}", "", "| ID | Area | Check | Evidence | Next action |", "| --- | --- | --- | --- | --- |"]
            for c in items:
                cells = [c.id, c.area, c.title, c.evidence, c.action]
                lines.append("| " + " | ".join(_cell(x) for x in cells) + " |")
        lines.append("")
        return "\n".join(lines)


def _cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip() or "-"
