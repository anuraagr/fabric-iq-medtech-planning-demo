from __future__ import annotations

import pytest

from scripts.lib.acceptance import AcceptanceError, ResultStore, AcceptanceTest, load_definitions, render_markdown, summarize
from scripts.lib.config import REPO_ROOT


def test_definitions_load_and_are_unique_with_valid_scopes():
    defs = load_definitions(REPO_ROOT / "config" / "acceptance-tests.yaml")
    ids = [d.id for d in defs]
    assert len(ids) == len(set(ids)) and len(ids) >= 15
    assert {d.scope for d in defs} == {"local", "live"}
    assert any(d.mode == "manual-ui" for d in defs)


def test_every_acceptance_criterion_from_the_brief_is_covered():
    titles = " ".join(d.title.lower() for d in load_definitions(REPO_ROOT / "config" / "acceptance-tests.yaml"))
    for phrase in ("tenant", "semantic model", "plan sheets", "scenario", "powertable", "infobridge", "writeback", "reporting", "security", "reset", "cleanup"):
        assert phrase in titles, phrase


def test_unexecuted_tests_default_to_not_executed(tmp_path):
    store = ResultStore(tmp_path / "r.json")
    assert store.get("T-09")["status"] == "not_executed"


def test_passed_requires_observed_result(tmp_path):
    store = ResultStore(tmp_path / "r.json")
    with pytest.raises(AcceptanceError):
        store.record("T-01", "passed", "   ")
    with pytest.raises(AcceptanceError):
        store.record("T-01", "failed", "")
    store.record("T-01", "blocked", "")  # blocked/not_executed need no observation
    with pytest.raises(AcceptanceError):
        store.record("T-01", "green", "x")


def test_evidence_is_kept_across_reruns_and_rendered(tmp_path):
    defs = [AcceptanceTest("T-08", "live", "Infobridge", "exp", "manual-ui")]
    store = ResultStore(tmp_path / "r.json")
    store.record("T-08", "passed", "definition lists the shared measure", evidence="UI: 267.53 equals the source sheet")
    store.record("T-08", "passed", "definition lists the shared measure (re-run)")  # an automated re-run supplies no evidence
    assert store.get("T-08")["evidence"] == "UI: 267.53 equals the source sheet"
    store.record("T-08", "passed", "again", evidence="new evidence")
    assert store.get("T-08")["evidence"] == "new evidence"
    assert "Evidence: new evidence" in render_markdown(defs, store)


def test_store_roundtrip_summary_and_markdown(tmp_path):
    defs = [AcceptanceTest("A", "local", "t|1", "exp", "automated"), AcceptanceTest("B", "live", "t2", "exp2", "manual-ui")]
    store = ResultStore(tmp_path / "state" / "r.json")
    store.record("A", "passed", "3 passed")
    store.save()
    reloaded = ResultStore(tmp_path / "state" / "r.json")
    assert summarize(defs, reloaded) == {"passed": 1, "failed": 0, "not_executed": 1, "blocked": 0}
    markdown = render_markdown(defs, reloaded)
    assert "t\\|1" in markdown and "**passed**" in markdown and "**not executed**" in markdown
