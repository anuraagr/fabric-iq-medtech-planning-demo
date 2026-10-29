"""Configuration loading. Reads no secrets: authentication is interactive Entra via Azure CLI."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]

GUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,39}$")
FORBIDDEN_KEY_FRAGMENTS = ("secret", "password", "passwd", "pwd", "token", "apikey", "api_key", "connectionstring", "connection_string")
ZERO_GUID = "00000000-0000-0000-0000-000000000000"


class ConfigError(ValueError):
    """Raised when configuration is missing or unsafe."""


@dataclass(frozen=True)
class Names:
    workspace: str
    semantic_model: str
    writeback_db: str
    powertable_db: str
    plan_item: str
    medtech_model: str = ""
    medtech_plan: str = ""


@dataclass(frozen=True)
class SampleFile:
    name: str
    sha256: str
    bytes: int


@dataclass(frozen=True)
class SampleConfig:
    repository: str
    path: str
    commit: str
    files: tuple[SampleFile, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DemoConfig:
    tenant_id: str
    resource_prefix: str
    workspace_id: str | None
    capacity_id: str | None
    az_config_dir: str | None
    live: bool
    names: Names
    sample: SampleConfig
    manifest_path: Path
    raw: Mapping[str, Any]


def _scan_for_secret_keys(node: Any, trail: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            here = f"{trail}.{key}" if trail else str(key)
            lowered = str(key).lower().replace("-", "_")
            if any(fragment in lowered for fragment in FORBIDDEN_KEY_FRAGMENTS):
                hits.append(here)
            hits.extend(_scan_for_secret_keys(value, here))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            hits.extend(_scan_for_secret_keys(value, f"{trail}[{index}]"))
    return hits


def _read_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env reader (KEY=VALUE, # comments); avoids a hard dependency at import time."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _load_yaml(yaml_path: Path | None) -> dict[str, Any]:
    config_dir = REPO_ROOT / "config"
    if yaml_path is None:
        candidate = config_dir / "demo.config.yaml"
        yaml_path = candidate if candidate.is_file() else config_dir / "demo.config.example.yaml"
    raw: dict[str, Any] = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    leaked = _scan_for_secret_keys(raw)
    if leaked:
        raise ConfigError(f"Configuration must not contain secret-like keys: {', '.join(leaked)}")
    return raw


def _parse_sample(raw: Mapping[str, Any]) -> SampleConfig:
    sample_raw = raw.get("sample") or {}
    return SampleConfig(
        repository=str(sample_raw.get("repository", "microsoft/fabric-samples")),
        path=str(sample_raw.get("path", "docs-samples/iq/plan")),
        commit=str(sample_raw.get("commit", "")),
        files=tuple(
            SampleFile(name=str(name), sha256=str(meta.get("sha256", "")), bytes=int(meta.get("bytes", 0)))
            for name, meta in (sample_raw.get("files") or {}).items()
        ),
    )


def load_sample(yaml_path: Path | None = None) -> SampleConfig:
    """Load only the pinned sample-asset section; needs no tenant configuration."""
    return _parse_sample(_load_yaml(yaml_path))


def load_config(
    *,
    yaml_path: Path | None = None,
    env_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> DemoConfig:
    """Merge config/demo.config.yaml (or the .example), .env and process environment (highest priority)."""
    raw = _load_yaml(yaml_path)

    merged_env: dict[str, str] = {}
    merged_env.update(_read_dotenv(env_file if env_file is not None else REPO_ROOT / ".env"))
    merged_env.update(dict(environ if environ is not None else os.environ))

    tenant_id = (merged_env.get("FPD_TENANT_ID") or raw.get("tenant_id") or "").strip()
    prefix = (merged_env.get("FPD_RESOURCE_PREFIX") or raw.get("resource_prefix") or "FabricPlanningDemo").strip()
    workspace_name = (merged_env.get("FPD_WORKSPACE_NAME") or raw.get("workspace_name") or prefix).strip()
    workspace_id = (merged_env.get("FPD_WORKSPACE_ID") or raw.get("workspace_id") or "").strip() or None
    capacity_id = (merged_env.get("FPD_CAPACITY_ID") or raw.get("capacity_id") or "").strip() or None
    az_config_dir = (merged_env.get("AZURE_CONFIG_DIR") or "").strip() or None

    if not tenant_id or tenant_id == ZERO_GUID:
        raise ConfigError("FPD_TENANT_ID is not set. Copy .env.example to .env and set your Microsoft Entra tenant ID.")
    for label, value in (("FPD_TENANT_ID", tenant_id), ("FPD_WORKSPACE_ID", workspace_id), ("FPD_CAPACITY_ID", capacity_id)):
        if value and not GUID_RE.match(value):
            raise ConfigError(f"{label} must be a GUID, got {value!r}")
    if not PREFIX_RE.match(prefix):
        raise ConfigError("FPD_RESOURCE_PREFIX must start with a letter and contain 3-40 letters, digits or underscores")

    name_templates = raw.get("names") or {}
    fmt = {"prefix": prefix}
    names = Names(
        workspace=workspace_name,
        semantic_model=str(name_templates.get("semantic_model", "{prefix}_Northwind_FMCG")).format(**fmt),
        writeback_db=str(name_templates.get("writeback_db", "{prefix}_Writeback")).format(**fmt),
        powertable_db=str(name_templates.get("powertable_db", "{prefix}_PowerTable")).format(**fmt),
        plan_item=str(name_templates.get("plan_item", "{prefix}_Plan")).format(**fmt),
        medtech_model=str(name_templates.get("medtech_model", "{prefix}_MedTech")).format(**fmt),
        medtech_plan=str(name_templates.get("medtech_plan", "{prefix}_MedTech_Plan")).format(**fmt),
    )

    sample = _parse_sample(raw)

    manifest_path = Path(merged_env.get("FPD_MANIFEST_PATH") or raw.get("manifest_path", "state/deployment-manifest.json"))
    if not manifest_path.is_absolute():
        manifest_path = REPO_ROOT / manifest_path

    return DemoConfig(
        tenant_id=tenant_id,
        resource_prefix=prefix,
        workspace_id=workspace_id,
        capacity_id=capacity_id,
        az_config_dir=az_config_dir,
        live=_truthy(merged_env.get("FPD_LIVE")),
        names=names,
        sample=sample,
        manifest_path=manifest_path,
        raw=raw,
    )
