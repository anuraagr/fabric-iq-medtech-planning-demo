from __future__ import annotations

import pytest

from scripts.lib.fabric import FabricClient, sku_planning_class, sql_endpoint
from scripts.lib.http import ApiError
from scripts.lib.powerbi import PowerBiClient


@pytest.mark.parametrize(
    ("sku", "expected"),
    [("F4", "supported"), ("F64", "supported"), ("FTL4", "supported"), ("FT1", "supported"), ("P1", "supported"),
     ("PP3", "unsupported"), ("A1", "unsupported"), ("EM2", "unsupported"), ("", "unknown"), ("X9", "unknown")],
)
def test_sku_planning_class(sku, expected):
    assert sku_planning_class(sku) == expected


class FakeApi:
    def __init__(self, items=None):
        self.items = items or []
        self.requests = []
        self.json_by_path = {}

    def paginate(self, path, **kwargs):
        return iter(self.items)

    def get_json(self, path, **kwargs):
        return self.json_by_path[path]

    def request(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        raise AssertionError("unexpected request")


def test_find_workspaces_by_name_is_exact_case_insensitive_and_workspace_type_only():
    api = FakeApi([
        {"id": "1", "displayName": "FabricPlanningDemo", "type": "Workspace"},
        {"id": "2", "displayName": "fabricplanningdemo", "type": "Workspace"},
        {"id": "3", "displayName": "FabricPlanningDemo2", "type": "Workspace"},
        {"id": "4", "displayName": "FabricPlanningDemo", "type": "Personal"},
    ])
    assert [w["id"] for w in FabricClient(api).find_workspaces_by_name("FabricPlanningDemo")] == ["1", "2"]


def test_my_workspace_role_matches_object_id():
    api = FakeApi([
        {"principal": {"id": "AAA"}, "role": "Admin"},
        {"principal": {"id": "bbb"}, "role": "Contributor"},
    ])
    client = FabricClient(api)
    assert client.my_workspace_role("w", "aaa") == "Admin"
    assert client.my_workspace_role("w", "zzz") is None


class RecordingFabric(FabricClient):
    def __init__(self, existing=None):
        super().__init__(FakeApi())
        self.existing = existing
        self.created = []

    def find_item(self, workspace_id, item_type, display_name):
        return self.existing

    def create_sql_database(self, workspace_id, display_name, *, description=None):
        self.created.append(display_name)
        return {"id": "new-id"}

    def get_sql_database(self, workspace_id, database_id):
        return {"id": database_id, "properties": {"serverFqdn": "s.database.fabric.microsoft.com,1433", "databaseName": "db-guid"}}


def test_ensure_sql_database_reuses_existing_and_does_not_create():
    client = RecordingFabric(existing={"id": "old-id", "displayName": "X"})
    database, created = client.ensure_sql_database("w", "X")
    assert created is False and database["id"] == "old-id" and client.created == []


def test_ensure_sql_database_creates_when_absent():
    client = RecordingFabric(existing=None)
    database, created = client.ensure_sql_database("w", "X")
    assert created is True and database["id"] == "new-id" and client.created == ["X"]


def test_sql_endpoint_strips_port_and_requires_properties():
    assert sql_endpoint({"properties": {"serverFqdn": "s.example,1433", "databaseName": "d"}}) == ("s.example", "d")
    with pytest.raises(ApiError):
        sql_endpoint({"properties": {}})


class ImportApi:
    def __init__(self, states):
        self.states = list(states)
        self.posted = None

    def request(self, method, path, **kwargs):
        self.posted = (method, path, kwargs)

        class Response:
            status_code = 202

            def json(self_inner):
                return {"id": "imp-1"}

        return Response()

    def get_json(self, path, **kwargs):
        return self.states.pop(0)


def test_import_pbix_waits_for_success_and_sends_multipart(tmp_path):
    pbix = tmp_path / "Northwind_FMCG.pbix"
    pbix.write_bytes(b"PK\x03\x04")
    api = ImportApi([{"importState": "Publishing"}, {"importState": "Succeeded", "datasets": [{"id": "d1"}], "reports": [{"id": "r1"}]}])
    sleeps = []
    record = PowerBiClient(api, sleep=sleeps.append).import_pbix("ws", pbix, "Model", name_conflict="Abort")
    assert record["datasets"][0]["id"] == "d1" and sleeps == [5.0]
    method, path, kwargs = api.posted
    assert (method, path) == ("POST", "groups/ws/imports")
    assert kwargs["params"] == {"datasetDisplayName": "Model", "nameConflict": "Abort"}
    assert kwargs["files"]["file"][0] == "Northwind_FMCG.pbix"
    assert kwargs["retry_server_errors"] is False


def test_import_pbix_failure_and_bad_conflict(tmp_path):
    pbix = tmp_path / "a.pbix"
    pbix.write_bytes(b"x")
    with pytest.raises(ApiError, match="failed"):
        PowerBiClient(ImportApi([{"importState": "Failed"}]), sleep=lambda s: None).import_pbix("ws", pbix, "M")
    with pytest.raises(ValueError):
        PowerBiClient(ImportApi([])).import_pbix("ws", pbix, "M", name_conflict="Nope")


def test_import_times_out(tmp_path):
    api = ImportApi([{"importState": "Publishing"}] * 5)
    with pytest.raises(ApiError, match="still"):
        PowerBiClient(api, sleep=lambda s: None).wait_for_import("ws", "imp", timeout=10, interval=5)
