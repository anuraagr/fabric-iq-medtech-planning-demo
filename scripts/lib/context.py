"""Lazy runtime context shared by the CLI scripts (configuration, API clients, deployment manifest)."""

from __future__ import annotations

from .clients import Clients, build_clients
from .config import DemoConfig, load_config
from .manifest import Manifest, ResourceRecord


class Context:
    def __init__(self) -> None:
        self._cfg: DemoConfig | None = None
        self._clients: Clients | None = None
        self._manifest: Manifest | None = None

    @property
    def cfg(self) -> DemoConfig:
        self._cfg = self._cfg or load_config()
        return self._cfg

    @property
    def clients(self) -> Clients:
        self._clients = self._clients or build_clients(self.cfg)
        return self._clients

    @property
    def manifest(self) -> Manifest:
        self._manifest = self._manifest or Manifest(self.cfg.manifest_path, self.cfg.tenant_id)
        return self._manifest

    def workspace_id(self) -> str:
        record = self.manifest.find("Workspace", self.cfg.names.workspace)
        if not record:
            raise LookupError("no workspace in the deployment manifest; run deploy.py apply")
        if self.cfg.workspace_id and self.cfg.workspace_id.lower() != record.id.lower():
            raise LookupError("Configured workspace ID differs from the deployment manifest. Use a separate manifest for this deployment.")
        return record.id

    def sql_database(self, name: str) -> ResourceRecord:
        record = self.manifest.find("SQLDatabase", name, self.workspace_id())
        if not record:
            raise LookupError(f"SQL database {name!r} is not in the deployment manifest")
        return record

    def sql_session(self, name: str):
        from .sqlclient import SqlSession, connect

        record = self.sql_database(name)
        token = self.clients.tokens.get("sql").value
        return SqlSession(connect(record.extra["serverFqdn"], record.extra["databaseName"], token))
