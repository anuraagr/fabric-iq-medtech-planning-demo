from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.lib import finance as f


def test_gross_profit_and_margin_with_both_cost_conventions():
    assert f.gross_profit(100, 60) == Decimal("40")
    assert f.gross_profit(100, -60, cogs_sign="negative_cost") == Decimal("40")
    assert f.gross_margin_pct(200, 150) == Decimal("0.25")
    assert f.gross_margin_pct(200, -150, cogs_sign="negative_cost") == Decimal("0.25")


def test_zero_denominators_return_none_not_zero_or_exception():
    assert f.gross_margin_pct(0, 10) is None
    assert f.variance_pct(5, 0) is None


def test_variance_is_actual_minus_reference_and_pct_uses_abs_reference():
    assert f.variance(120, 100) == Decimal("20")
    assert f.variance_pct(120, 100) == Decimal("0.2")
    assert f.variance_pct(-80, -100) == Decimal("0.2")  # negative reference: sign follows the movement
    assert f.variance_pct(80, 100) == Decimal("-0.2")


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        ("revenue", 5, "favorable"),
        ("revenue", -5, "unfavorable"),
        ("profit", 1, "favorable"),
        ("cost", 5, "unfavorable"),
        ("cost", -5, "favorable"),
        ("cost", 0, "neutral"),
    ],
)
def test_favorability(kind, value, expected):
    assert f.favorability(kind, value) == expected


def test_floats_do_not_leak_binary_error():
    assert f.variance(0.3, 0.1) == Decimal("0.2")


def test_reconcile_flags_missing_and_unexpected_keys():
    results = f.reconcile({"a": 1, "b": 2}, {"a": "1.004", "c": 3})
    by_key = {r.key: r for r in results}
    assert by_key["a"].ok
    assert not by_key["b"].ok and by_key["b"].note == "missing from observed"
    assert not by_key["c"].ok and by_key["c"].note == "unexpected in observed"
    assert not f.all_ok(results)


def test_reconcile_respects_tolerance():
    assert f.all_ok(f.reconcile({"a": 100}, {"a": 100.009}))
    assert not f.all_ok(f.reconcile({"a": 100}, {"a": 100.5}))
    assert f.all_ok(f.reconcile({"a": 100}, {"a": 100.5}, tolerance=1))


def test_snapshot_diff_and_expected_changes():
    before = {("US", "Jan", "Forecast"): 100, ("US", "Jan", "Actual"): 90, ("UK", "Jan", "Forecast"): 50}
    after = {("US", "Jan", "Forecast"): 125, ("US", "Jan", "Actual"): 90, ("UK", "Jan", "Forecast"): 50}
    diff = f.snapshot_diff(before, after)
    assert diff.changed == {("US", "Jan", "Forecast"): (Decimal("100"), Decimal("125"))}
    assert f.only_expected_changes(diff, {("US", "Jan", "Forecast"): 125}) == []


def test_unexpected_change_to_actuals_is_reported():
    before = {("US", "Actual"): 90, ("US", "Forecast"): 100}
    after = {("US", "Actual"): 91, ("US", "Forecast"): 125}
    problems = f.only_expected_changes(f.snapshot_diff(before, after), {("US", "Forecast"): 125})
    assert len(problems) == 1 and "Actual" in problems[0]


def test_missing_expected_change_and_wrong_value_are_reported():
    diff = f.snapshot_diff({("a",): 1}, {("a",): 2})
    assert f.only_expected_changes(diff, {("b",): 5})
    assert "expected 3" in f.only_expected_changes(diff, {("a",): 3})[0]


def test_added_rows_count_as_expected_when_they_match():
    diff = f.snapshot_diff({}, {("new",): 7})
    assert f.only_expected_changes(diff, {("new",): 7}) == []


def test_duplicate_grains_and_latest_rows():
    rows = [
        {"Region": "US", "Month": "Jan", "Value": 1, "IsLatest": 0},
        {"Region": "US", "Month": "Jan", "Value": 2, "IsLatest": 1},
        {"Region": "UK", "Month": "Jan", "Value": 3, "IsLatest": 1},
    ]
    assert f.find_duplicate_grains(rows, ["Region", "Month"]) == {("US", "Jan"): 2}
    latest = f.latest_rows(rows)
    assert len(latest) == 2 and f.find_duplicate_grains(latest, ["Region", "Month"]) == {}


def test_invalid_numbers_raise():
    with pytest.raises(ValueError):
        f.D("abc")
