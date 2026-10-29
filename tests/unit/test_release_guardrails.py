from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts import cleanup, validate, verify_writeback
from scripts.lib.acceptance import AcceptanceTest, ResultStore
from scripts.lib.context import Context
from scripts.lib.http import ApiError
from scripts.lib.manifest import Manifest
from scripts.lib.powerbi import PowerBiClient


def test_empty_refresh_id_cannot_match_old_history():
    api = SimpleNamespace(get_json=lambda *args, **kwargs: pytest.fail("No history query without a request id"))
    with pytest.raises(ValueError, match="request ID"):
        PowerBiClient(api).wait_for_refresh("ws", "model", "")


def test_missing_response_id_is_explicit_even_if_request_was_accepted():
    api = SimpleNamespace(request=lambda *args, **kwargs: SimpleNamespace(headers={}))
    with pytest.raises(ApiError, match="request ID"):
        PowerBiClient(api).refresh("ws", "model")


def test_manifest_cannot_redirect_an_explicit_workspace(tmp_path):
    ctx = Context()
    ctx._cfg = SimpleNamespace(names=SimpleNamespace(workspace="demo"), workspace_id="new-workspace")
    ctx._manifest = Manifest(tmp_path / "manifest.json", "test")
    ctx._manifest.record("Workspace", "demo", "old-workspace")
    with pytest.raises(LookupError, match="differs"):
        ctx.workspace_id()


def test_blocked_live_checks_return_nonzero_and_do_not_borrow_reference_success(tmp_path, monkeypatch):
    test = AcceptanceTest(id="T-X", scope="live", title="test", expected="observed", mode="automated")
    store = ResultStore(tmp_path / "results.json")
    store.record("T-X", "passed", "old evidence")

    def blocked(ctx):
        raise ApiError(403, "Forbidden", "Not allowed")

    monkeypatch.setitem(validate.RUNNERS, "T-X", blocked)
    assert validate.run([test], store, "live", set()) == 1
    assert store.get("T-X")["status"] == "blocked"


def test_unknown_acceptance_selectors_are_rejected():
    with pytest.raises(SystemExit) as exc:
        validate.main(["run", "--only", "T-DOES-NOT-EXIST"])
    assert exc.value.code == 2


def test_writeback_proof_cannot_record_an_unasserted_diff():
    with pytest.raises(SystemExit) as exc:
        verify_writeback.main(["diff", "--table", "dbo.budget", "--grain", "Region", "--value", "Value",
                               "--before", "absent.json", "--record", "T-09"])
    assert exc.value.code == 2


def context_for_cleanup(tmp_path, items):
    manifest = Manifest(tmp_path / "manifest.json", "test")
    manifest.record("Workspace", "demo", "ws")
    manifest.record("Plan", "demo_plan", "plan", workspace_id="ws")
    return SimpleNamespace(
        cfg=SimpleNamespace(names=SimpleNamespace(workspace="demo"), tenant_id="test"),
        manifest=manifest, workspace_id=lambda: "ws",
        clients=SimpleNamespace(fabric=SimpleNamespace(list_items=lambda wid: items)),
    )


def test_cleanup_refuses_unrelated_workspace_items_before_deleting_anything(tmp_path, monkeypatch):
    ctx = context_for_cleanup(tmp_path, [{"id": "not-ours", "type": "Notebook", "displayName": "unrelated"}])
    monkeypatch.setattr(cleanup, "Context", lambda: ctx)
    monkeypatch.setattr(cleanup, "delete_record", lambda *args: pytest.fail("No deletions when unrelated items exist"))
    assert cleanup.main(["--confirm", "DELETE-demo", "--include-workspace"]) == 3


def test_cleanup_scopes_to_current_workspace_and_keeps_it_when_child_deletion_fails(tmp_path, monkeypatch):
    ctx = context_for_cleanup(tmp_path, [{"id": "plan", "type": "Plan", "displayName": "demo_plan"}])
    ctx.manifest.record("Plan", "other_plan", "other", workspace_id="other-workspace")
    monkeypatch.setattr(cleanup, "Context", lambda: ctx)
    seen = []

    def fail(ctx, record):
        seen.append(record.id)
        raise ApiError(409, "Busy", "Still in use")

    monkeypatch.setattr(cleanup, "delete_record", fail)
    assert cleanup.main(["--confirm", "DELETE-demo", "--include-workspace"]) == 1
    assert seen == ["plan"]
    assert ctx.manifest.find("Workspace", "demo") and ctx.manifest.find("Plan", "other_plan")
