"""Fabric REST API wrapper (https://api.fabric.microsoft.com/v1). Only operations verified in Microsoft Learn."""

from __future__ import annotations

import base64
from typing import Any

from .http import ApiClient, ApiError

FABRIC_BASE = "https://api.fabric.microsoft.com/v1"

# Capacity SKU families that are NOT valid for Planning scenarios that rely on XMLA endpoints
# (docs: "Power BI Pro and Power BI Premium Per User (PPU) aren't supported for these scenarios").
UNSUPPORTED_SKU_PREFIXES = ("PP", "A", "EM")
PLANNING_FRIENDLY_PREFIXES = ("F", "P")  # Fabric F SKUs (incl. FT*/FTL* trials) and Premium P SKUs

ADMIN_ROLES = {"Admin", "Member"}  # Tutorial 0: "Contributor access isn't sufficient"


def sku_planning_class(sku: str) -> str:
    """Classify a capacity SKU: 'supported', 'unsupported' (PPU/A/EM) or 'unknown'."""
    code = (sku or "").upper()
    if code.startswith(UNSUPPORTED_SKU_PREFIXES):
        return "unsupported"
    if code.startswith(PLANNING_FRIENDLY_PREFIXES):
        return "supported"
    return "unknown"


class FabricClient:
    def __init__(self, api: ApiClient) -> None:
        self.api = api

    # --- capacities / workspaces -------------------------------------------------------------
    def list_capacities(self) -> list[dict]:
        return list(self.api.paginate("capacities"))

    def list_workspaces(self) -> list[dict]:
        return list(self.api.paginate("workspaces"))

    def get_workspace(self, workspace_id: str) -> dict:
        return self.api.get_json(f"workspaces/{workspace_id}")

    def find_workspaces_by_name(self, name: str) -> list[dict]:
        return [w for w in self.list_workspaces() if w.get("displayName", "").casefold() == name.casefold() and w.get("type") == "Workspace"]

    def create_workspace(self, display_name: str, *, description: str | None = None, capacity_id: str | None = None) -> dict:
        body: dict[str, Any] = {"displayName": display_name}
        if description:
            body["description"] = description
        if capacity_id:
            body["capacityId"] = capacity_id
        response = self.api.request("POST", "workspaces", json_body=body, ok=(200, 201, 202))
        return self.api.wait_for_operation(response) if response.status_code == 202 else response.json()

    def assign_to_capacity(self, workspace_id: str, capacity_id: str) -> None:
        response = self.api.request("POST", f"workspaces/{workspace_id}/assignToCapacity", json_body={"capacityId": capacity_id}, ok=(200, 202))
        if response.status_code == 202:
            self.api.wait_for_operation(response)

    def list_role_assignments(self, workspace_id: str) -> list[dict]:
        return list(self.api.paginate(f"workspaces/{workspace_id}/roleAssignments"))

    def my_workspace_role(self, workspace_id: str, object_id: str) -> str | None:
        """Role of the signed-in user (by object id), including via group membership when listed directly."""
        for assignment in self.list_role_assignments(workspace_id):
            principal = assignment.get("principal", {})
            if str(principal.get("id", "")).lower() == object_id.lower():
                return assignment.get("role")
        return None

    # --- items ---------------------------------------------------------------------------------
    def list_items(self, workspace_id: str, item_type: str | None = None) -> list[dict]:
        params = {"type": item_type} if item_type else None
        return list(self.api.paginate(f"workspaces/{workspace_id}/items", params=params))

    def find_item(self, workspace_id: str, item_type: str, display_name: str) -> dict | None:
        for item in self.list_items(workspace_id, item_type):
            if item.get("displayName", "").casefold() == display_name.casefold():
                return item
        return None

    def delete_item(self, workspace_id: str, item_id: str) -> None:
        self.api.request("DELETE", f"workspaces/{workspace_id}/items/{item_id}", ok=(200, 204))

    def delete_plan(self, workspace_id: str, plan_id: str) -> None:
        self.api.request("DELETE", f"workspaces/{workspace_id}/plans/{plan_id}", ok=(200, 204))

    def delete_workspace(self, workspace_id: str) -> None:
        self.api.request("DELETE", f"workspaces/{workspace_id}", ok=(200, 204))

    # --- Plan item (documented: https://learn.microsoft.com/en-us/rest/api/fabric/plan/items) -----
    def list_plans(self, workspace_id: str) -> list[dict]:
        return list(self.api.paginate(f"workspaces/{workspace_id}/plans"))

    def get_plan(self, workspace_id: str, plan_id: str) -> dict:
        return self.api.get_json(f"workspaces/{workspace_id}/plans/{plan_id}")

    def create_plan(self, workspace_id: str, display_name: str, *, description: str | None = None) -> dict:
        """Create an EMPTY plan (no definition). Sheets are authored in the native UI; see docs/manual-setup.md."""
        body: dict[str, Any] = {"displayName": display_name}
        if description:
            body["description"] = description[:256]
        response = self.api.request("POST", f"workspaces/{workspace_id}/plans", json_body=body, ok=(200, 201, 202))
        return self.api.wait_for_operation(response) if response.status_code == 202 else response.json()

    def get_plan_definition(self, workspace_id: str, plan_id: str) -> dict:
        """Export the UI-authored definition (format PlanV1). Returns {'definition': {'parts': [...]}}."""
        response = self.api.request("POST", f"workspaces/{workspace_id}/plans/{plan_id}/getDefinition", params={"format": "PlanV1"}, json_body={}, ok=(200, 202))
        return self.api.wait_for_operation(response) if response.status_code == 202 else response.json()

    def ensure_plan(self, workspace_id: str, display_name: str, *, description: str | None = None) -> tuple[dict, bool]:
        """Idempotent create of an empty plan; reuses an existing plan with the same name."""
        for plan in self.list_plans(workspace_id):
            if plan.get("displayName", "").casefold() == display_name.casefold():
                return plan, False
        created = self.create_plan(workspace_id, display_name, description=description)
        if not created.get("id"):
            for plan in self.list_plans(workspace_id):
                if plan.get("displayName", "").casefold() == display_name.casefold():
                    return plan, True
            raise ApiError(500, "PlanNotFound", f"Plan {display_name!r} was not found after creation")
        return created, True

    # --- semantic model with an embedded TMDL definition -------------------------------------
    @staticmethod
    def _definition_body(parts: dict[str, bytes]) -> dict[str, Any]:
        return {"parts": [{"path": path, "payload": base64.b64encode(data).decode("ascii"), "payloadType": "InlineBase64"} for path, data in parts.items()]}

    def create_semantic_model(self, workspace_id: str, display_name: str, parts: dict[str, bytes], *, description: str | None = None) -> dict:
        """Create a semantic model from TMDL parts (path -> bytes). Long-running; returns the created item when available."""
        body: dict[str, Any] = {"displayName": display_name, "definition": self._definition_body(parts)}
        if description:
            body["description"] = description
        response = self.api.request("POST", f"workspaces/{workspace_id}/semanticModels", json_body=body, ok=(200, 201, 202), retry_server_errors=False)
        return self.api.wait_for_operation(response) if response.status_code == 202 else response.json()

    def update_semantic_model_definition(self, workspace_id: str, model_id: str, parts: dict[str, bytes]) -> None:
        response = self.api.request(
            "POST", f"workspaces/{workspace_id}/semanticModels/{model_id}/updateDefinition",
            json_body={"definition": self._definition_body(parts)}, ok=(200, 202), retry_server_errors=False,
        )
        if response.status_code == 202:
            self.api.wait_for_operation(response)

    def get_semantic_model_definition(self, workspace_id: str, model_id: str) -> dict:
        response = self.api.request("POST", f"workspaces/{workspace_id}/semanticModels/{model_id}/getDefinition", json_body={}, ok=(200, 202))
        return self.api.wait_for_operation(response) if response.status_code == 202 else response.json()

    # --- Fabric SQL database ---------------------------------------------------------------
    def create_sql_database(self, workspace_id: str, display_name: str, *, description: str | None = None) -> dict:
        body: dict[str, Any] = {"displayName": display_name}
        if description:
            body["description"] = description
        response = self.api.request("POST", f"workspaces/{workspace_id}/sqlDatabases", json_body=body, ok=(200, 201, 202))
        return self.api.wait_for_operation(response) if response.status_code == 202 else response.json()

    def get_sql_database(self, workspace_id: str, database_id: str) -> dict:
        return self.api.get_json(f"workspaces/{workspace_id}/sqlDatabases/{database_id}")

    def ensure_sql_database(self, workspace_id: str, display_name: str, *, description: str | None = None) -> tuple[dict, bool]:
        """Idempotent: return (database, created). Reuses an existing database of the same name."""
        existing = self.find_item(workspace_id, "SQLDatabase", display_name)
        if existing:
            return self.get_sql_database(workspace_id, existing["id"]), False
        created = self.create_sql_database(workspace_id, display_name, description=description)
        database_id = created.get("id")
        if not database_id:
            found = self.find_item(workspace_id, "SQLDatabase", display_name)
            if not found:
                raise ApiError(500, "SqlDatabaseNotFound", f"SQL database {display_name!r} was not found after creation")
            database_id = found["id"]
        return self.get_sql_database(workspace_id, database_id), True


def sql_endpoint(database: dict) -> tuple[str, str]:
    """Return (server FQDN, database name) from a Get SQL Database response."""
    properties = database.get("properties") or {}
    server = properties.get("serverFqdn", "")
    name = properties.get("databaseName", "")
    if not server or not name:
        raise ApiError(500, "MissingSqlProperties", "SQL database response did not include serverFqdn/databaseName")
    return server.split(",")[0], name
