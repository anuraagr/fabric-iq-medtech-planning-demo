"""Scenario writeback verification (T-06): pure logic over rows as Planning writes them."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.lib import scenario as sc

BASE_REVENUE, BASE_COGS = Decimal("250848029"), Decimal("88503662")
SPEC = sc.ScenarioSpec("dbo.t", "Upside", "2026", "Sales Plan", "COGS Plan", Decimal("0.03"), Decimal("-0.02"))


def node(series: str, region: str, quarter: str, month: str, value: str, scenario: str = "Upside", year: str = "2026") -> dict:
    return {"IrScenario": scenario, "Region": region, "Year": year, "Quarter": quarter, "MonthShort": month, "ValueColumnName": series, "Value": Decimal(value)}


def build_rows(sales_total: str = "258373470.00", cogs_total: str = "86733589.00") -> list[dict]:
    """Two regions, two months: leaves add up to the region nodes and to the grand total."""
    rows = []
    for series, total in (("Sales Plan", Decimal(sales_total)), ("COGS Plan", Decimal(cogs_total))):
        half, quarter = total / 2, total / 4
        rows.append(node(series, "All", "All", "All", str(total)))
        for region in ("Asia Pacific", "Europe"):
            rows.append(node(series, region, "All", "All", str(half)))
            for month in ("Jan", "Feb"):
                rows.append(node(series, region, "Q1", month, str(quarter)))
    return rows


def test_a_consistent_upside_scenario_passes():
    findings, problems = sc.evaluate(build_rows(), SPEC, BASE_REVENUE, BASE_COGS)
    assert problems == []
    assert all(f.ok for f in findings), [str(f) for f in findings if not f.ok]
    status, observed = sc.summarize(findings, problems, SPEC, BASE_REVENUE, BASE_COGS)
    assert status == "passed" and "gross profit delta 9295514" in observed


def test_gross_profit_delta_follows_the_driver_formula():
    findings, _ = sc.evaluate(build_rows(), SPEC, BASE_REVENUE, BASE_COGS)
    delta = next(f for f in findings if f.label.startswith("Gross profit delta"))
    assert delta.expected == BASE_REVENUE * Decimal("0.03") + BASE_COGS * Decimal("0.02")
    assert abs(delta.actual - Decimal("9295514")) <= 1


def test_wrong_driver_is_reported():
    findings, _ = sc.evaluate(build_rows(sales_total="260000000"), SPEC, BASE_REVENUE, BASE_COGS)
    status, observed = sc.summarize(findings, [], SPEC, BASE_REVENUE, BASE_COGS)
    assert status == "failed" and "MISMATCH" in observed


def test_leaf_nodes_that_do_not_add_up_are_reported():
    rows = build_rows()
    next(r for r in rows if r["Region"] == "Europe" and r["MonthShort"] == "Jan" and r["ValueColumnName"] == "Sales Plan")["Value"] += 5000
    findings, _ = sc.evaluate(rows, SPEC, BASE_REVENUE, BASE_COGS)
    assert any(not f.ok and "leaf nodes" in f.label for f in findings)


def test_a_written_base_scenario_is_a_problem():
    rows = build_rows() + [node("Sales Plan", "All", "All", "All", "250848029", scenario="Base")]
    _, problems = sc.evaluate(rows, SPEC, BASE_REVENUE, BASE_COGS)
    assert any("Base" in p for p in problems)


def test_missing_scenario_and_duplicate_grains_are_problems():
    _, missing = sc.evaluate([node("Sales Plan", "All", "All", "All", "1", scenario="Downside")], SPEC, BASE_REVENUE, BASE_COGS)
    assert any("has no rows" in p for p in missing)
    rows = build_rows()
    _, duplicate = sc.evaluate(rows + [rows[0]], SPEC, BASE_REVENUE, BASE_COGS)
    assert any("duplicate grains" in p for p in duplicate)


def test_totals_are_none_when_the_grand_total_row_is_missing():
    rows = [r for r in build_rows() if not (r["Region"] == "All" and r["ValueColumnName"] == "Sales Plan")]
    assert sc.node_total(rows, "Upside", "Sales Plan", "2026") is None
    findings, _ = sc.evaluate(rows, SPEC, BASE_REVENUE, BASE_COGS)
    assert not findings[0].ok


def test_load_spec_reads_the_medtech_block_and_requires_it():
    raw = {"medtech": {"scenario": {"table": "dbo.t", "name": "Upside", "year": "2026", "sales_series": "Sales Plan", "cogs_series": "COGS Plan", "sales_driver": 0.03, "cogs_driver": -0.02}}}
    spec = sc.load_spec(raw)
    assert spec.sales_driver == Decimal("0.03") and spec.cogs_driver == Decimal("-0.02") and spec.year == "2026"
    with pytest.raises(LookupError):
        sc.load_spec({})


def test_baseline_query_only_accepts_numeric_years():
    assert "'Date'[Year] = 2026" in sc.baseline_query("2026")
    with pytest.raises(ValueError):
        sc.baseline_query("2026; DROP")


# --- several scenarios on one sheet (Upside and Downside) -----------------------------------------------------------------------------
DOWNSIDE = sc.ScenarioSpec("dbo.t", "Downside", "2026", "Sales Plan", "COGS Plan", Decimal("-0.04"), Decimal("0.03"))
DOWNSIDE_SALES, DOWNSIDE_COGS = "240814108.00", "91158772.00"  # base x 0.96 and base x 1.03, typed as whole numbers


def two_scenarios() -> list[dict]:
    rows = build_rows()
    for row in build_rows(sales_total=DOWNSIDE_SALES, cogs_total=DOWNSIDE_COGS):
        rows.append({**row, "IrScenario": "Downside"})
    return rows


def results_for(rows, specs=(SPEC, DOWNSIDE)):
    return [(spec, *sc.evaluate(rows, spec, BASE_REVENUE, BASE_COGS)) for spec in specs]


def test_load_specs_reads_a_scenarios_list_that_shares_the_table_year_and_series():
    raw = {"medtech": {"scenario": {
        "table": "dbo.t", "year": "2026", "sales_series": "Sales Plan", "cogs_series": "COGS Plan",
        "scenarios": [{"name": "Upside", "sales_driver": 0.03, "cogs_driver": -0.02}, {"name": "Downside", "sales_driver": -0.04, "cogs_driver": 0.03}],
    }}}
    upside, downside = sc.load_specs(raw)
    assert (upside.scenario, upside.sales_driver, upside.cogs_driver) == ("Upside", Decimal("0.03"), Decimal("-0.02"))
    assert (downside.scenario, downside.sales_driver, downside.cogs_driver) == ("Downside", Decimal("-0.04"), Decimal("0.03"))
    assert {s.table for s in (upside, downside)} == {"dbo.t"} and sc.load_spec(raw) == upside


def test_the_single_scenario_layout_still_loads_as_one_spec():
    raw = {"medtech": {"scenario": {"table": "dbo.t", "name": "Upside", "year": "2026", "sales_series": "S", "cogs_series": "C", "sales_driver": 0.03, "cogs_driver": -0.02}}}
    assert [s.scenario for s in sc.load_specs(raw)] == ["Upside"]


def test_upside_and_downside_are_each_verified_and_ranked_against_base():
    rows = two_scenarios()
    status, observed = sc.summarize_all(results_for(rows), rows, BASE_REVENUE, BASE_COGS)
    assert status == "passed", observed
    assert "Upside vs Base (2026)" in observed and "Downside vs Base (2026)" in observed
    assert "gross profit ranking Upside 171,639,881 > Base 162,344,367 > Downside 149,655,336" in observed


def test_a_downside_that_beats_base_breaks_the_ranking_even_if_each_total_matches_its_driver():
    rows = [r for r in two_scenarios() if r["IrScenario"] != "Downside"] + [{**r, "IrScenario": "Downside"} for r in build_rows(sales_total="270000000", cogs_total="80000000")]
    status, observed = sc.summarize_all(results_for(rows), rows, BASE_REVENUE, BASE_COGS)
    assert status == "failed" and "ranking" in observed and "MISMATCH" in observed


def test_the_wrong_ranking_alone_is_reported_when_the_drivers_are_inverted():
    inverted = sc.ScenarioSpec("dbo.t", "Downside", "2026", "Sales Plan", "COGS Plan", Decimal("0.05"), Decimal("-0.05"))
    rows = two_scenarios()  # the data is a real downside but the configured drivers say it should beat Base
    problems, text = sc.ordering_problems(rows, [SPEC, inverted], BASE_REVENUE, BASE_COGS)
    assert any("ranking" in p for p in problems) and "Downside" in text


def test_a_missing_scenario_is_reported_once_per_scenario_and_shared_problems_are_not_repeated():
    rows = build_rows() + [build_rows()[0]]  # Upside only, with one duplicated grain
    status, observed = sc.summarize_all(results_for(rows), rows, BASE_REVENUE, BASE_COGS)
    assert status == "failed"
    assert observed.count("duplicate grains") == 1 and "scenario 'Downside' has no rows" in observed


def test_gross_profit_needs_both_grand_total_nodes():
    assert sc.gross_profit(build_rows(), SPEC) == Decimal("171639881.00")
    assert sc.gross_profit([r for r in build_rows() if r["ValueColumnName"] != "COGS Plan"], SPEC) is None
