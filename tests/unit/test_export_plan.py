"""Sanitising exported plan definitions so they can be committed."""

from __future__ import annotations

from scripts.export_plan import sanitize


def test_sanitize_replaces_known_ids_case_insensitively_and_longest_first():
    text = '{"workspaceId": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", "item": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa-extra"}'
    replaced = sanitize(text, {"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa": "<workspace-id>", "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa-extra": "<special>"})
    assert "aaaaaaaa" not in replaced.lower()
    assert "<workspace-id>" in replaced and "<special>" in replaced


def test_sanitize_masks_the_fabric_sql_server_name():
    text = '"dbHost": "example-warehouse.database.fabric.microsoft.com"'
    assert sanitize(text, {}) == '"dbHost": "<fabric-sql-server>.database.fabric.microsoft.com"'


def test_sanitize_leaves_unrelated_text_alone():
    assert sanitize("sheet 01a160a9 FY2026 Revenue Budget", {"abc": "<x>"}) == "sheet 01a160a9 FY2026 Revenue Budget"


def test_export_rejects_part_paths_outside_its_local_directory(tmp_path):
    import base64
    from types import SimpleNamespace

    import pytest

    from scripts.export_plan import export_plan

    for path in ("../outside.json", "/outside.json", "C:\\outside.json"):
        payload = {"definition": {"parts": [{"path": path, "payload": base64.b64encode(b"{}").decode()}]}}
        record = SimpleNamespace(id="p", kind="Plan", name="demo")
        ctx = SimpleNamespace(
            cfg=SimpleNamespace(tenant_id="fake"), workspace_id=lambda: "ws",
            manifest=SimpleNamespace(find=lambda *args: record, resources=[record]),
            clients=SimpleNamespace(fabric=SimpleNamespace(get_plan_definition=lambda *args: payload)),
        )
        with pytest.raises(ValueError, match="leaves"):
            export_plan(ctx, "demo", tmp_path / "exports")
        assert not (tmp_path / "outside.json").exists()
