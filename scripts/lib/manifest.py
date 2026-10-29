"""Local deployment manifest: IDs of demo-owned resources, never secrets."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
SECRET_FRAGMENTS = ("secret", "password", "passwd", "pwd", "token", "apikey", "api_key", "connectionstring", "connection_string", "credential")
CREATED_BY = {"script", "manual", "preexisting"}


class ManifestError(ValueError):
    pass


@dataclass
class ResourceRecord:
    kind: str
    name: str
    id: str
    workspace_id: str | None = None
    created_by: str = "script"
    owned: bool = True
    created_at: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reject_secret_like(extra: dict[str, Any]) -> None:
    for key in extra:
        lowered = str(key).lower().replace("-", "_")
        if any(fragment in lowered for fragment in SECRET_FRAGMENTS):
            raise ManifestError(f"Refusing to store secret-like key {key!r} in the manifest")


class Manifest:
    """JSON manifest with atomic writes. `owned` marks resources the demo may reset or delete."""

    def __init__(self, path: Path, tenant_id: str = "") -> None:
        self.path = Path(path)
        self.tenant_id = tenant_id
        self.resources: list[ResourceRecord] = []
        self.updated_at = ""
        if self.path.is_file():
            self._load()

    def _load(self) -> None:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schemaVersion") != SCHEMA_VERSION:
            raise ManifestError(f"Unsupported manifest schema version: {data.get('schemaVersion')!r}")
        stored_tenant = data.get("tenantId", "")
        if self.tenant_id and stored_tenant and stored_tenant.lower() != self.tenant_id.lower():
            raise ManifestError(
                f"Manifest belongs to tenant {stored_tenant}, but the configured tenant is {self.tenant_id}. "
                "Use a different manifest_path or remove the stale manifest."
            )
        self.tenant_id = self.tenant_id or stored_tenant
        self.updated_at = data.get("updatedAt", "")
        self.resources = [ResourceRecord(**record) for record in data.get("resources", [])]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = _now()
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "tenantId": self.tenant_id,
            "updatedAt": self.updated_at,
            "resources": [asdict(record) for record in self.resources],
        }
        fd, tmp_name = tempfile.mkstemp(prefix=self.path.name, suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp_name, self.path)
        except BaseException:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    def find(self, kind: str, name: str | None = None, workspace_id: str | None = None) -> ResourceRecord | None:
        for record in self.resources:
            if record.kind != kind:
                continue
            if name is not None and record.name != name:
                continue
            if workspace_id is not None and record.workspace_id != workspace_id:
                continue
            return record
        return None

    def record(
        self,
        kind: str,
        name: str,
        resource_id: str,
        *,
        workspace_id: str | None = None,
        created_by: str = "script",
        owned: bool = True,
        **extra: Any,
    ) -> ResourceRecord:
        """Insert or update (idempotent on kind+name+workspace) and return the record."""
        if created_by not in CREATED_BY:
            raise ManifestError(f"created_by must be one of {sorted(CREATED_BY)}")
        _reject_secret_like(extra)
        existing = self.find(kind, name, workspace_id)
        if existing is not None:
            if existing.id != resource_id:
                existing.id = resource_id
                existing.created_by = created_by
                existing.owned = owned
                existing.created_at = _now()
                existing.extra = dict(extra)
            else:
                existing.extra.update(extra)
            return existing
        created = ResourceRecord(
            kind=kind,
            name=name,
            id=resource_id,
            workspace_id=workspace_id,
            created_by=created_by,
            owned=owned,
            created_at=_now(),
            extra=dict(extra),
        )
        self.resources.append(created)
        return created

    def remove(self, kind: str, resource_id: str) -> bool:
        before = len(self.resources)
        self.resources = [r for r in self.resources if not (r.kind == kind and r.id == resource_id)]
        return len(self.resources) != before

    def owned_resources_for_cleanup(self) -> list[ResourceRecord]:
        """Demo-owned resources in reverse creation order (children before the workspace)."""
        owned = [r for r in self.resources if r.owned and r.created_by in {"script", "manual"}]
        return list(reversed(owned))
