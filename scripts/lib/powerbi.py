"""Power BI REST API wrapper (https://api.powerbi.com/v1.0/myorg): PBIX import, DAX queries, refresh."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from .http import ApiClient, ApiError

POWERBI_BASE = "https://api.powerbi.com/v1.0/myorg"
NAME_CONFLICTS = {"Abort", "Ignore", "Overwrite", "CreateOrOverwrite", "GenerateUniqueName"}


class PowerBiClient:
    def __init__(self, api: ApiClient, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.api = api
        self._sleep = sleep

    def list_datasets(self, workspace_id: str) -> list[dict]:
        return self.api.get_json(f"groups/{workspace_id}/datasets").get("value", [])

    def find_dataset(self, workspace_id: str, name: str) -> dict | None:
        for dataset in self.list_datasets(workspace_id):
            if dataset.get("name", "").casefold() == name.casefold():
                return dataset
        return None

    def import_pbix(self, workspace_id: str, pbix_path: Path, display_name: str, *, name_conflict: str = "Abort", skip_report: bool = False) -> dict:
        """Upload a PBIX (multipart) and wait for the import to finish. Returns the final import record."""
        if name_conflict not in NAME_CONFLICTS:
            raise ValueError(f"nameConflict must be one of {sorted(NAME_CONFLICTS)}")
        params = {"datasetDisplayName": display_name, "nameConflict": name_conflict}
        if skip_report:
            params["skipReport"] = "true"
        with pbix_path.open("rb") as handle:
            response = self.api.request(
                "POST",
                f"groups/{workspace_id}/imports",
                params=params,
                files={"file": (pbix_path.name, handle, "application/octet-stream")},
                ok=(200, 201, 202),
                retry_server_errors=False,
            )
        import_id = response.json().get("id")
        if not import_id:
            raise ApiError(500, "MissingImportId", "Import response did not include an id")
        return self.wait_for_import(workspace_id, import_id)

    def wait_for_import(self, workspace_id: str, import_id: str, *, timeout: float = 900.0, interval: float = 5.0) -> dict:
        waited = 0.0
        while True:
            record = self.api.get_json(f"groups/{workspace_id}/imports/{import_id}")
            state = record.get("importState")
            if state == "Succeeded":
                return record
            if state == "Failed":
                raise ApiError(500, "ImportFailed", f"PBIX import {import_id} failed", body=record)
            if waited >= timeout:
                raise ApiError(408, "ImportTimeout", f"PBIX import {import_id} still '{state}' after {timeout:.0f}s")
            self._sleep(interval)
            waited += interval

    def execute_queries(self, workspace_id: str, dataset_id: str, dax: str) -> list[dict[str, Any]]:
        """Run one DAX query; returns the rows of the first result table."""
        body = {"queries": [{"query": dax}], "serializerSettings": {"includeNulls": True}}
        response = self.api.request("POST", f"groups/{workspace_id}/datasets/{dataset_id}/executeQueries", json_body=body, retry_server_errors=False)
        results = response.json().get("results", [])
        tables = results[0].get("tables", []) if results else []
        return tables[0].get("rows", []) if tables else []

    def refresh(self, workspace_id: str, dataset_id: str, *, body: dict | None = None) -> str:
        """Trigger a refresh and return its request id."""
        response = self.api.request("POST", f"groups/{workspace_id}/datasets/{dataset_id}/refreshes", json_body=body or {"notifyOption": "NoNotification"}, ok=(200, 202), retry_server_errors=False)
        request_id = response.headers.get("RequestId") or response.headers.get("requestid")
        if not request_id:
            raise ApiError(502, "MissingRefreshRequestId", "Refresh was accepted but its request ID is missing. Inspect refresh history before retrying.")
        return request_id

    def refresh_history(self, workspace_id: str, dataset_id: str, top: int = 5) -> list[dict]:
        return self.api.get_json(f"groups/{workspace_id}/datasets/{dataset_id}/refreshes", params={"$top": top}).get("value", [])

    def wait_for_refresh(self, workspace_id: str, dataset_id: str, request_id: str, *, timeout: float = 900.0, interval: float = 10.0) -> dict:
        """Poll the refresh history until the refresh with this request id leaves the in-progress ('Unknown') state."""
        if not request_id:
            raise ValueError("A request ID is required; never match an unrelated completed refresh.")
        waited = 0.0
        while True:
            for entry in self.refresh_history(workspace_id, dataset_id, top=10):
                if request_id and entry.get("requestId", "").lower() != request_id.lower():
                    continue
                status = entry.get("status")
                if status == "Completed":
                    return entry
                if status in {"Failed", "Disabled", "Cancelled"}:
                    raise ApiError(500, "RefreshFailed", f"Refresh {request_id} ended as {status}: {entry.get('serviceExceptionJson') or ''}", body=entry)
            if waited >= timeout:
                raise ApiError(408, "RefreshTimeout", f"Refresh {request_id} did not finish within {timeout:.0f}s")
            self._sleep(interval)
            waited += interval

    def datasources(self, workspace_id: str, dataset_id: str) -> list[dict]:
        return self.api.get_json(f"groups/{workspace_id}/datasets/{dataset_id}/datasources").get("value", [])

    def set_oauth_credentials(self, workspace_id: str, dataset_id: str, access_token: str, *, datasource_type: str = "Sql") -> int:
        """Bind a short-lived OAuth2 access token to the dataset's cloud data sources of one type (Update Datasource).

        The token is sent to Power BI only; it is never logged or stored by this repository. Returns the number of sources updated."""
        import json as _json

        updated = 0
        for source in self.datasources(workspace_id, dataset_id):
            if source.get("datasourceType") != datasource_type:
                continue
            gateway_id, datasource_id = source.get("gatewayId"), source.get("datasourceId")
            if not gateway_id or not datasource_id:
                raise ApiError(409, "DatasourceNotBound", f"{datasource_type} data source has no gatewayId/datasourceId yet: {source.get('connectionDetails')}")
            body = {"credentialDetails": {
                "credentialType": "OAuth2",
                "credentials": _json.dumps({"credentialData": [{"name": "accessToken", "value": access_token}]}),
                "encryptedConnection": "Encrypted", "encryptionAlgorithm": "None", "privacyLevel": "Organizational", "useEndUserOAuth2Credentials": "False",
            }}
            self.api.request("PATCH", f"gateways/{gateway_id}/datasources/{datasource_id}", json_body=body, ok=(200, 204))
            updated += 1
        return updated
