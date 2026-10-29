from __future__ import annotations

import json

import pytest

from scripts.lib.manifest import Manifest, ManifestError

TENANT = "11111111-1111-4111-8111-111111111111"


def test_record_is_idempotent_and_persists(tmp_path):
    path = tmp_path / "state" / "manifest.json"
    manifest = Manifest(path, TENANT)
    first = manifest.record("Workspace", "FabricPlanningDemo", "ws-1")
    again = manifest.record("Workspace", "FabricPlanningDemo", "ws-1", note="reused")
    assert first is again and len(manifest.resources) == 1
    manifest.save()

    reloaded = Manifest(path, TENANT)
    assert reloaded.find("Workspace", "FabricPlanningDemo").id == "ws-1"
    assert reloaded.find("Workspace", "FabricPlanningDemo").extra == {"note": "reused"}
    assert json.loads(path.read_text())["tenantId"] == TENANT


def test_atomic_save_leaves_no_temp_files(tmp_path):
    path = tmp_path / "m.json"
    manifest = Manifest(path, TENANT)
    manifest.record("SQLDatabase", "db", "id-1", workspace_id="ws-1")
    manifest.save()
    assert [p.name for p in tmp_path.iterdir()] == ["m.json"]


def test_secret_like_keys_are_rejected(tmp_path):
    manifest = Manifest(tmp_path / "m.json", TENANT)
    for key in ("password", "access_token", "ClientSecret", "connection_string"):
        with pytest.raises(ManifestError):
            manifest.record("x", "n", "i", **{key: "v"})


def test_tenant_mismatch_is_refused(tmp_path):
    path = tmp_path / "m.json"
    Manifest(path, TENANT).save()
    with pytest.raises(ManifestError, match="belongs to tenant"):
        Manifest(path, "11111111-1111-1111-1111-111111111111")


def test_unknown_schema_version_is_refused(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schemaVersion": 99}))
    with pytest.raises(ManifestError, match="schema"):
        Manifest(path, TENANT)


def test_cleanup_order_is_reverse_creation_and_excludes_foreign_resources(tmp_path):
    manifest = Manifest(tmp_path / "m.json", TENANT)
    manifest.record("Workspace", "ws", "1")
    manifest.record("SQLDatabase", "db", "2", workspace_id="1")
    manifest.record("SemanticModel", "sm", "3", workspace_id="1")
    manifest.record("Capacity", "cap", "4", created_by="preexisting", owned=False)
    assert [r.id for r in manifest.owned_resources_for_cleanup()] == ["3", "2", "1"]


def test_remove_and_invalid_created_by(tmp_path):
    manifest = Manifest(tmp_path / "m.json", TENANT)
    manifest.record("SQLDatabase", "db", "2")
    assert manifest.remove("SQLDatabase", "2") is True
    assert manifest.remove("SQLDatabase", "2") is False
    with pytest.raises(ManifestError):
        manifest.record("x", "n", "i", created_by="somebody")


def test_a_new_resource_with_the_same_name_does_not_inherit_old_ownership(tmp_path):
    manifest = Manifest(tmp_path / "m.json", TENANT)
    manifest.record("SQLDatabase", "demo", "original", workspace_id="ws", serverFqdn="old", databaseName="old")
    replacement = manifest.record("SQLDatabase", "demo", "foreign", workspace_id="ws", created_by="preexisting", owned=False, serverFqdn="new")
    assert replacement.owned is False and replacement.created_by == "preexisting"
    assert replacement.extra == {"serverFqdn": "new"}
    assert manifest.owned_resources_for_cleanup() == []


def test_reusing_the_same_id_does_not_escalate_ownership(tmp_path):
    manifest = Manifest(tmp_path / "m.json", TENANT)
    manifest.record("Plan", "demo", "foreign", created_by="preexisting", owned=False)
    record = manifest.record("Plan", "demo", "foreign")
    assert not record.owned and record.created_by == "preexisting"
