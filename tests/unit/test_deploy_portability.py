"""Fresh-tenant foundation -> native writeback -> reporting attachment, without a tenant."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import deploy
from scripts.lib.config import load_config
from scripts.lib.manifest import Manifest
from scripts.lib.sqlclient import UnsafeSqlError

TENANTS = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")
CAPACITY = {"id": "33333333-3333-4333-8333-333333333333", "displayName": "SE capacity", "sku": "F64", "state": "Active", "region": "West US 3"}


def configuration(tmp_path, tenant=TENANTS[0], prefix="DemoAlpha"):
    cfg = load_config(
        environ={"FPD_TENANT_ID": tenant, "FPD_RESOURCE_PREFIX": prefix},
        env_file=tmp_path / "absent.env",
    )
    object.__setattr__(cfg, "manifest_path", tmp_path / prefix / "manifest.json")
    return cfg


class Tenant:
    def __init__(self):
        self.workspace = None
        self.items = []
        self.definitions = {}
        self.events = []
        self.sql_tables = {}

    def list_capacities(self):
        return [CAPACITY]

    def find_workspaces_by_name(self, name):
        return [self.workspace] if self.workspace and self.workspace["displayName"] == name else []

    def get_workspace(self, workspace_id):
        assert self.workspace["id"] == workspace_id
        return self.workspace

    def create_workspace(self, name, **kwargs):
        self.events.append("create-workspace")
        self.workspace = {"id": "workspace-1", "displayName": name, "capacityId": kwargs["capacity_id"]}
        return self.workspace

    def list_items(self, workspace_id, item_type=None):
        return [item for item in self.items if item_type is None or item["type"] == item_type]

    def _create(self, kind, name):
        item = {"id": f"item-{len(self.items) + 1}", "displayName": name, "name": name, "type": kind}
        self.items.append(item)
        self.events.append(f"create-{kind}")
        return item

    def ensure_sql_database(self, workspace_id, name, **kwargs):
        existing = next((i for i in self.items if i["displayName"] == name and i["type"] == "SQLDatabase"), None)
        item = existing or self._create("SQLDatabase", name)
        return dict(item, properties={"serverFqdn": "example.database.fabric.microsoft.com", "databaseName": name}), existing is None

    def ensure_plan(self, workspace_id, name, **kwargs):
        existing = next((i for i in self.items if i["displayName"] == name and i["type"] == "Plan"), None)
        return (existing, False) if existing else (self._create("Plan", name), True)

    def list_datasets(self, workspace_id):
        return self.list_items(workspace_id, "SemanticModel")

    def find_dataset(self, workspace_id, name):
        return next((i for i in self.list_datasets(workspace_id) if i["name"] == name), None)

    def create_semantic_model(self, workspace_id, name, parts, **kwargs):
        item = self._create("SemanticModel", name)
        self.definitions[item["id"]] = parts
        return item

    def get_semantic_model_definition(self, workspace_id, model_id):
        from scripts.lib.fabric import FabricClient

        self.events.append("get-definition")
        return {"definition": FabricClient._definition_body(self.definitions[model_id])}

    def update_semantic_model_definition(self, workspace_id, model_id, parts):
        self.events.append("update-definition")
        self.definitions[model_id] = parts

    def set_oauth_credentials(self, workspace_id, model_id, access_token):
        self.events.append("bind-sql")
        return 1

    def refresh(self, workspace_id, model_id):
        parts = self.definitions[model_id]
        for path in parts:
            if path.endswith(("Plan Revenue Budget.tmdl", "Plan Revenue Forecast.tmdl")) and not self.sql_tables:
                pytest.fail("The initial refresh must not read tables Planning has not created yet")
        self.events.append("refresh")
        return "refresh-1"

    def wait_for_refresh(self, workspace_id, model_id, request_id):
        return {"requestId": request_id, "status": "Completed", "endTime": "2026-10-28T00:00:00Z"}


def clients(tenant):
    return SimpleNamespace(
        fabric=tenant, powerbi=tenant,
        tokens=SimpleNamespace(get=lambda resource: SimpleNamespace(value="test-only"), identity=lambda: {"upn": "se@example.test", "tenantId": TENANTS[0]}),
    )


def successful_checks(monkeypatch):
    monkeypatch.setattr(deploy, "verify_model", lambda *args: [SimpleNamespace(label="total", expected=1, actual=1, ok=True)])


@pytest.mark.parametrize(("tenant_id", "prefix"), zip(TENANTS, ("DemoAlpha", "DemoBeta")))
def test_fresh_tenant_foundation_has_no_sql_dependency_and_redeploy_reuses_every_item(tmp_path, monkeypatch, tenant_id, prefix):
    cfg, tenant = configuration(tmp_path, tenant_id, prefix), Tenant()
    successful_checks(monkeypatch)
    selected = {"workspace", "sql", "medtech_model", "medtech_plan"}
    first = deploy.apply(clients(tenant), cfg, CAPACITY, selected, False)
    manifest = Manifest(cfg.manifest_path, tenant_id)
    model = manifest.find("SemanticModel", cfg.names.medtech_model, first["workspaceId"])
    assert all("Plan Revenue" not in p for p in tenant.definitions[model.id])
    assert "bind-sql" not in tenant.events
    assert len(tenant.items) == 4
    events = list(tenant.events)
    deploy.apply(clients(tenant), cfg, CAPACITY, selected, False)
    assert tenant.events == events and len(tenant.items) == 4
    assert all(i["displayName"].startswith(prefix) for i in tenant.items)


def test_reporting_phase_requires_native_tables_and_does_not_replace_the_model_on_failure(tmp_path, monkeypatch):
    cfg, tenant = configuration(tmp_path), Tenant()
    successful_checks(monkeypatch)
    deploy.apply(clients(tenant), cfg, CAPACITY, {"workspace", "sql", "medtech_model"}, False)
    tenant.events.clear()
    monkeypatch.setattr(deploy, "check_writeback_sources", lambda *args: (_ for _ in ()).throw(RuntimeError("Native writeback table is missing")))
    result = deploy.apply(clients(tenant), cfg, CAPACITY, {"medtech_model"}, False, True, include_writeback=True)
    assert any(s["status"] == "BLOCKED" and "missing" in s["detail"] for s in result["steps"])
    assert "update-definition" not in tenant.events and "refresh" not in tenant.events


def test_reporting_phase_attaches_tables_from_this_tenants_manifest_only(tmp_path, monkeypatch):
    cfg, tenant = configuration(tmp_path), Tenant()
    successful_checks(monkeypatch)
    deploy.apply(clients(tenant), cfg, CAPACITY, {"workspace", "sql", "medtech_model"}, False)
    tenant.sql_tables = {"budget": True, "forecast": True}
    monkeypatch.setattr(deploy, "check_writeback_sources", lambda *args: None)
    tenant.events.clear()
    result = deploy.apply(clients(tenant), cfg, CAPACITY, {"medtech_model"}, False, True, include_writeback=True)
    assert not any(s["status"] in {"BLOCKED", "FAILED"} for s in result["steps"])
    assert tenant.events.index("get-definition") < tenant.events.index("update-definition") < tenant.events.index("bind-sql") < tenant.events.index("refresh")
    model = tenant.find_dataset("workspace-1", cfg.names.medtech_model)
    text = tenant.definitions[model["id"]]["definition/tables/Plan Revenue Forecast.tmdl"].decode()
    assert "DemoAlpha_Writeback" in text and "example.database.fabric.microsoft.com" in text
    tenant.events.clear()
    result = deploy.apply(clients(tenant), cfg, CAPACITY, {"medtech_model"}, False, True)
    assert any(s["status"] == "BLOCKED" and "--include-writeback" in s["detail"] for s in result["steps"])
    assert "update-definition" not in tenant.events


def test_an_existing_workspace_never_moves_to_a_different_capacity(tmp_path, monkeypatch):
    cfg, tenant = configuration(tmp_path), Tenant()
    tenant.workspace = {"id": "workspace-1", "displayName": cfg.names.workspace, "capacityId": "some-other-capacity"}
    with pytest.raises(RuntimeError, match="capacity"):
        deploy.apply(clients(tenant), cfg, CAPACITY, {"medtech_model"}, False)
    assert not cfg.manifest_path.exists() and not tenant.events


def test_explicit_workspace_id_must_match_the_configured_name(tmp_path):
    cfg, tenant = configuration(tmp_path), Tenant()
    object.__setattr__(cfg, "workspace_id", "workspace-1")
    tenant.workspace = {"id": "workspace-1", "displayName": "SomeoneElsesWorkspace", "capacityId": CAPACITY["id"]}
    with pytest.raises(RuntimeError, match="name"):
        deploy.find_workspace(clients(tenant), cfg)


def test_existing_unowned_model_cannot_be_replaced(tmp_path, monkeypatch):
    cfg, tenant = configuration(tmp_path), Tenant()
    tenant.workspace = {"id": "workspace-1", "displayName": cfg.names.workspace, "capacityId": CAPACITY["id"]}
    tenant.create_semantic_model("workspace-1", cfg.names.medtech_model, {"unrelated": b"keep"})
    tenant.events.clear()
    result = deploy.apply(clients(tenant), cfg, CAPACITY, {"medtech_model"}, False, True)
    assert any(s["status"] == "BLOCKED" and "owned" in s["detail"] for s in result["steps"])
    assert "update-definition" not in tenant.events


def test_plan_shows_model_update_and_new_tables_instead_of_false_reuse(tmp_path, monkeypatch):
    cfg, tenant = configuration(tmp_path), Tenant()
    successful_checks(monkeypatch)
    deploy.apply(clients(tenant), cfg, CAPACITY, {"workspace", "sql", "medtech_model"}, False)
    _, changes = deploy.build_plan(clients(tenant), cfg, CAPACITY, {"medtech_model"}, update_medtech=True, include_writeback=True)
    assert changes[0].action == "UPDATE" and "writeback" in changes[0].detail.lower()


def test_plan_blocks_unowned_model_and_missing_foundation_before_any_mutation(tmp_path):
    cfg, tenant = configuration(tmp_path), Tenant()
    _, changes = deploy.build_plan(clients(tenant), cfg, CAPACITY, {"medtech_model"}, update_medtech=True, include_writeback=True)
    assert changes[0].action == "BLOCKED"
    tenant.workspace = {"id": "workspace-1", "displayName": cfg.names.workspace, "capacityId": CAPACITY["id"]}
    tenant.create_semantic_model("workspace-1", cfg.names.medtech_model, {})
    _, changes = deploy.build_plan(clients(tenant), cfg, CAPACITY, {"medtech_model"}, update_medtech=True)
    assert changes[0].action == "BLOCKED" and "unowned" in changes[0].detail


def test_interrupted_initial_refresh_resumes_without_duplicate_model(tmp_path, monkeypatch):
    cfg, tenant = configuration(tmp_path), Tenant()
    successful_checks(monkeypatch)
    wait = tenant.wait_for_refresh

    def fail(*args):
        from scripts.lib.http import ApiError

        raise ApiError(408, "RefreshTimeout", "Not finished")

    monkeypatch.setattr(tenant, "wait_for_refresh", fail)
    result = deploy.apply(clients(tenant), cfg, CAPACITY, {"workspace", "medtech_model"}, False)
    assert deploy.result_exit_code(result) == 1
    record = Manifest(cfg.manifest_path, cfg.tenant_id).find("SemanticModel", cfg.names.medtech_model, "workspace-1")
    assert record.extra["refreshPending"]
    monkeypatch.setattr(tenant, "wait_for_refresh", wait)
    deploy.apply(clients(tenant), cfg, CAPACITY, {"medtech_model"}, False)
    assert len(tenant.items) == 1 and tenant.events.count("refresh") == 2
    record = Manifest(cfg.manifest_path, cfg.tenant_id).find("SemanticModel", cfg.names.medtech_model, "workspace-1")
    assert not record.extra["refreshPending"]


def test_cli_refuses_incoherent_stage_flags_before_connecting(monkeypatch):
    monkeypatch.setattr(deploy, "build_clients", lambda *args: pytest.fail("No network for invalid arguments"))
    for args in (["apply", "--include-writeback"], ["plan", "--only", "sql", "--update-medtech-model"]):
        with pytest.raises(SystemExit) as exc:
            deploy.main(args)
        assert exc.value.code == 2


def test_failed_numeric_verification_is_a_failed_deployment_result(tmp_path, monkeypatch):
    cfg, tenant = configuration(tmp_path), Tenant()
    monkeypatch.setattr(deploy, "verify_model", lambda *args: [SimpleNamespace(label="total", expected=1, actual=2, ok=False)])
    result = deploy.apply(clients(tenant), cfg, CAPACITY, {"workspace", "medtech_model"}, False)
    assert any(s["status"] == "FAILED" for s in result["steps"])
    assert deploy.result_exit_code(result) == 1


def test_sql_prerequisite_check_requires_schema_and_committed_rows():
    from scripts.data.medtech_tmdl import WritebackSource

    source = WritebackSource("Plan Revenue Budget", "server", "database", "dbo", "budget", "Budget", "Budget")

    class Session:
        def __init__(self, names, count):
            self.names, self.count = names, count

        def table_columns(self, schema, table):
            return [{"COLUMN_NAME": n} for n in self.names]

        def scalar(self, sql, params):
            from scripts.lib.sqlclient import assert_read_only

            assert_read_only(sql)
            assert params == ["Budget"]
            return self.count

    columns = {"IsLatest", "ValueColumnName", "Region", "Category", "Year", "Quarter", "MonthShort", "Value", "PreviousValue"}
    for missing in (set(), columns - {"PreviousValue"}):
        with pytest.raises(RuntimeError, match="columns"):
            deploy.validate_writeback_tables(Session(missing, 1), (source,))
    with pytest.raises(RuntimeError, match="committed"):
        deploy.validate_writeback_tables(Session(columns, 0), (source,))
    deploy.validate_writeback_tables(Session(columns, 1), (source,))
    with pytest.raises((ValueError, UnsafeSqlError)):
        deploy.validate_writeback_tables(Session(columns, 1), (WritebackSource("M", "s", "d", "dbo", "x;DROP TABLE y", "B", "B"),))
