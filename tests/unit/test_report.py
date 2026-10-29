from __future__ import annotations

import json

import pytest

from scripts.lib.report import Check, Report, Status


def sample_report() -> Report:
    report = Report("Preflight", tenant_id="t", identity="me@x")
    report.add(Check("L1", "Local", "Python", Status.VERIFIED, "3.13"))
    report.add(Check("F7", "Fabric", "XMLA | endpoint", Status.REQUIRES_ADMIN, "not readable\nvia API", "Check Admin portal"))
    report.add(Check("I1", "Identity", "Sign-in", Status.REQUIRES_USER_INPUT))
    report.add(Check("F8", "Fabric", "PPU capacity", Status.UNSUPPORTED))
    report.add(Check("S2", "SQL", "TDS connectivity", Status.NOT_YET_TESTED))
    return report


def test_counts_cover_all_five_classifications():
    counts = sample_report().counts()
    assert counts == {
        "Verified": 1,
        "Requires administrator action": 1,
        "Requires user input": 1,
        "Unsupported": 1,
        "Not yet tested": 1,
    }


def test_duplicate_ids_are_rejected():
    report = sample_report()
    with pytest.raises(ValueError):
        report.add(Check("L1", "Local", "again", Status.VERIFIED))


def test_blocking_excludes_verified_and_not_yet_tested():
    assert {c.id for c in sample_report().blocking()} == {"F7", "I1", "F8"}


def test_markdown_escapes_pipes_and_newlines_and_orders_sections():
    markdown = sample_report().to_markdown()
    assert "XMLA \\| endpoint" in markdown
    assert "not readable via API" in markdown
    order = [markdown.index(f"## {name}") for name in ("Verified", "Requires administrator action", "Requires user input", "Unsupported", "Not yet tested")]
    assert order == sorted(order)


def test_json_roundtrip_has_status_values():
    data = json.loads(sample_report().to_json())
    assert data["counts"]["Verified"] == 1
    assert {c["status"] for c in data["checks"]} >= {"Verified", "Unsupported"}
