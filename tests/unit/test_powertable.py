"""PowerTable (Fabric SQL) before/after comparison used by T-07."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from scripts.lib import powertable as pt


def rows(*items: dict) -> list[dict]:
    return [dict(i) for i in items]


BEFORE_ROWS = rows(
    {"Asset Id": 1, "Asset Tag": "MD-1245", "Status": "Available", "Purchase Date": date(2023, 4, 1), "Purchase Price": 5071.1, "Notes": None},
    {"Asset Id": 2, "Asset Tag": "MD-1248", "Status": "In Service", "Purchase Date": date(2022, 6, 20), "Purchase Price": 75530.8, "Notes": None},
)


def snap(items):
    return pt.snapshot_rows(items, "Asset Tag")


def test_normalise_makes_sql_types_comparable():
    assert pt.normalise(None) == ""
    assert pt.normalise(date(2026, 10, 15)) == "2026-10-15"
    assert pt.normalise(datetime(2026, 10, 15, 8, 30)) == "2026-10-15 08:30:00"
    assert pt.normalise(Decimal("3100.00")) == "3100"
    assert pt.normalise(3100.0) == "3100"
    assert pt.normalise(5071.1) == "5071.1"
    assert pt.normalise("  Available ") == "Available"
    assert pt.normalise(8) == "8"


def test_snapshot_rejects_empty_and_duplicate_keys():
    with pytest.raises(pt.PowerTableError):
        pt.snapshot_rows(rows({"Asset Tag": None}), "Asset Tag")
    with pytest.raises(pt.PowerTableError):
        pt.snapshot_rows(rows({"Asset Tag": "A"}, {"Asset Tag": "A"}), "Asset Tag")


def test_diff_finds_exactly_the_update_and_the_insert():
    after_rows = rows(*BEFORE_ROWS)
    after_rows[1]["Status"] = "Under Maintenance"
    after_rows.append({"Asset Id": 3, "Asset Tag": "MD-1500", "Status": "Available", "Purchase Date": date(2026, 10, 15), "Purchase Price": 3100.0, "Notes": None})
    diff = pt.diff_rows(snap(BEFORE_ROWS), snap(after_rows))
    assert diff.changed == {"MD-1248": {"Status": ("In Service", "Under Maintenance")}}
    assert list(diff.added) == ["MD-1500"] and diff.removed == {}
    problems = pt.check_expectations(diff, {"MD-1248": {"Status": "Under Maintenance"}}, {"MD-1500": {"Status": "Available", "Purchase Price": "3100"}})
    assert problems == []


def test_unexpected_changes_are_reported():
    after_rows = rows(*BEFORE_ROWS)
    after_rows[0]["Status"] = "Retired"
    after_rows[1]["Status"] = "Under Maintenance"
    after_rows[1]["Notes"] = "x"
    diff = pt.diff_rows(snap(BEFORE_ROWS), snap(after_rows))
    problems = pt.check_expectations(diff, {"MD-1248": {"Status": "Under Maintenance"}}, {})
    assert any("MD-1245" in p and "not expected" in p for p in problems)
    assert any("MD-1248" in p and "unexpected additional changes" in p for p in problems)


def test_missing_update_wrong_value_missing_insert_and_removal_are_reported():
    diff = pt.diff_rows(snap(BEFORE_ROWS), snap(BEFORE_ROWS[:1]))
    problems = pt.check_expectations(diff, {"MD-1245": {"Status": "Retired"}}, {"MD-1500": {}})
    assert any("no change found" in p for p in problems)
    assert any("inserted row not found" in p for p in problems)
    assert any("rows removed" in p for p in problems)
    changed = rows(*BEFORE_ROWS)
    changed[1]["Status"] = "Retired"
    wrong = pt.check_expectations(pt.diff_rows(snap(BEFORE_ROWS), snap(changed)), {"MD-1248": {"Status": "Under Maintenance"}}, {})
    assert any("expected 'Under Maintenance', got 'Retired'" in p for p in wrong)


def test_unexpected_insert_is_reported():
    extra = rows(*BEFORE_ROWS, {"Asset Id": 3, "Asset Tag": "MD-9", "Status": "x", "Purchase Date": None, "Purchase Price": None, "Notes": None})
    problems = pt.check_expectations(pt.diff_rows(snap(BEFORE_ROWS), snap(extra)), {}, {})
    assert any("MD-9" in p and "not expected" in p for p in problems)


def test_parse_expectation():
    assert pt.parse_expectation("MD-1248|Status=Under Maintenance|Location=1") == ("MD-1248", {"Status": "Under Maintenance", "Location": "1"})
    assert pt.parse_expectation("MD-1500") == ("MD-1500", {})
    with pytest.raises(pt.PowerTableError):
        pt.parse_expectation("|Status=x")
    with pytest.raises(pt.PowerTableError):
        pt.parse_expectation("MD-1|Status")


def test_snapshot_round_trip(tmp_path):
    path = tmp_path / "s.json"
    original = snap(BEFORE_ROWS)
    pt.save_snapshot(path, "dbo.assets", "Asset Tag", original)
    meta, loaded = pt.load_snapshot(path)
    assert meta["table"] == "dbo.assets" and loaded == original
