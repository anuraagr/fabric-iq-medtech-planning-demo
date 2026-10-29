"""Reporting round-trip comparison (T-10): pure logic and DAX generation."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.lib import reporting as rp

CONFIG = {
    "medtech": {
        "writeback_tables": [{"source_table": "wb", "series": "S", "measure": "Plan Budget Revenue", "model_table": "Plan T"}],
        "reporting_check": {"leaf": {"region": "Europe", "category": "Diagnostics & Imaging", "year": 2026, "month": "Oct"}},
    }
}
REPORTING = rp.Reporting.from_config(CONFIG)


def results(model_regions, sql_regions=None, model_total="30", sql_total="30", model_leaf="5", sql_leaf="5"):
    sql_regions = sql_regions or {"Europe": Decimal("10"), "Asia Pacific": Decimal("20")}
    return rp.compare(Decimal(sql_total), sql_regions, Decimal(sql_leaf), Decimal(model_total), model_regions, Decimal(model_leaf), "Europe/D&I/Oct")


def test_matching_totals_regions_and_leaf_pass():
    out = results({"Europe": Decimal("10"), "Asia Pacific": Decimal("20")})
    status, observed = rp.summarize(out, REPORTING)
    assert status == "passed" and "2 regions match (2 distinct values)" in observed


def test_every_region_showing_the_grand_total_is_caught():
    """The defect this check exists for: no relationship, so each region returns the total."""
    out = results({"Europe": Decimal("30"), "Asia Pacific": Decimal("30")})
    status, observed = rp.summarize(out, REPORTING)
    assert status == "failed" and "MISMATCH" in observed


def test_missing_region_or_leaf_mismatch_fails():
    assert rp.summarize(results({"Europe": Decimal("10")}), REPORTING)[0] == "failed"
    assert rp.summarize(results({"Europe": Decimal("10"), "Asia Pacific": Decimal("20")}, model_leaf="6"), REPORTING)[0] == "failed"


def test_comparison_tolerates_rounding_only():
    assert rp.Comparison("x", Decimal("1.000"), Decimal("1.004")).ok
    assert not rp.Comparison("x", Decimal("1.000"), Decimal("1.02")).ok
    assert not rp.Comparison("x", None, Decimal("1")).ok


def test_dax_generation_and_injection_guards():
    assert rp.dax_total("Plan Budget Revenue") == 'EVALUATE ROW("v", [Plan Budget Revenue])'
    assert "SUMMARIZECOLUMNS(Geography[Region]" in rp.dax_by_region("Plan Budget Revenue")
    leaf = rp.dax_leaf("Plan Budget Revenue", {"region": 'Eu"rope', "category": "Diagnostics & Imaging", "year": 2026, "month": "Oct"})
    assert 'Geography[Region] = "Eu""rope"' in leaf and "'Date'[Year] = 2026" in leaf and 'Product[Category] = "Diagnostics & Imaging"' in leaf
    with pytest.raises(ValueError):
        rp.measure_ref("x]) DROP")
    with pytest.raises(ValueError):
        rp.dax_leaf("m", {"region": "r", "category": "c", "year": "2026; x", "month": "Oct"})


def test_parsing_dax_rows_and_config():
    assert rp.first_value([{"[v]": 268250000.01}]) == Decimal("268250000.01") and rp.first_value([]) is None
    assert rp.region_values([{"Geography[Region]": "Europe", "[v]": 1.5}, {"Geography[Region]": "X", "[v]": None}]) == {"Europe": Decimal("1.5")}
    assert REPORTING.table == "dbo.wb" and REPORTING.leaf["month"] == "Oct"
    with pytest.raises(LookupError):
        rp.Reporting.from_config({})


def test_the_budget_entry_is_chosen_by_role_even_when_a_forecast_table_is_listed_first():
    config = {"medtech": {
        "writeback_tables": [
            {"source_table": "fc", "series": "F", "measure": "Plan Forecast Revenue", "role": "forecast"},
            {"source_table": "bud", "series": "B", "measure": "Plan Budget Revenue"},
        ],
        "reporting_check": {"leaf": {"region": "Europe", "category": "C", "year": 2026, "month": "Oct"}},
    }}
    reporting = rp.Reporting.from_config(config)
    assert reporting.table == "dbo.bud" and reporting.measure == "Plan Budget Revenue"
    only_forecast = {"medtech": {"writeback_tables": [config["medtech"]["writeback_tables"][0]], "reporting_check": config["medtech"]["reporting_check"]}}
    with pytest.raises(LookupError):
        rp.Reporting.from_config(only_forecast)


# --- actual versus budget ---------------------------------------------------------------------------------------------------------------
class FakeDataset:
    """Two regions, Jan-Mar 2026 actuals plus one 2025 row that must be ignored: rows are (product, geography, date key, units, revenue, cogs)."""

    geographies = [(1, "Europe", "West", "Germany"), (2, "Asia Pacific", "East", "Japan"), (3, "Europe", "West", "France")]
    sales = [
        (1, 1, 202601, 1, 100, 60), (1, 3, 202601, 1, 50, 30), (1, 2, 202601, 1, 200, 120),
        (1, 1, 202602, 1, 110, 66), (1, 2, 202603, 1, 210, 126),
        (1, 1, 202512, 1, 999, 1),
    ]


def test_actuals_come_from_the_generator_rows_of_the_requested_year_only():
    actuals, months = rp.actuals_by_region(FakeDataset, 2026)
    assert actuals == {"Europe": Decimal("260"), "Asia Pacific": Decimal("410")}
    assert months == ["Jan", "Feb", "Mar"]
    with pytest.raises(ValueError):
        rp.actuals_by_region(FakeDataset, "2026; x")


def test_the_real_generator_has_actuals_through_september_2026_only():
    from scripts.data.medtech_generator import build_dataset

    actuals, months = rp.actuals_by_region(build_dataset(), 2026)
    assert months == ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep"]
    assert sum(actuals.values()) == Decimal("195831920")  # pinned in model/medtech/expected-totals.json (2026 Jan-Sep)
    assert len(actuals) == 5


def test_budget_sql_is_parameterised_and_restricted_to_leaf_rows_and_the_given_months():
    sql = rp.budget_for_months_sql("[dbo].[wb]", ["Jan", "Feb"])
    assert sql.count("?") == 4 and "MonthShort IN (?, ?)" in sql and "IsLatest = 1" in sql and rp.LEAF_FILTER in sql and "GROUP BY Region" in sql  # series, year, 2 months
    with pytest.raises(ValueError):
        rp.budget_for_months_sql("[dbo].[wb]", [])


def test_expected_variance_is_actual_minus_budget_and_the_percentage_divides_by_the_budget():
    expected = rp.expected_variance({"Europe": Decimal("260"), "Asia Pacific": Decimal("410")}, {"Europe": Decimal("250"), "Asia Pacific": Decimal("450")})
    assert expected.variance_by_region == {"Asia Pacific": Decimal("-40"), "Europe": Decimal("10")}
    assert expected.actual == 670 and expected.budget == 700 and expected.variance == -30
    assert expected.variance_pct == Decimal("-30") / Decimal("700")
    assert rp.expected_variance({"Europe": Decimal("5")}, {}).variance_pct is None  # zero budget: BLANK, never 0 or an exception


def test_variance_dax_wraps_each_measure_in_the_year_and_guards_the_year():
    dax = rp.dax_variance_total("2026")
    for key, name in (("a", "Gross Revenue"), ("b", "Budget for Actual Periods"), ("v", "Actual vs Budget Variance"), ("p", "Actual vs Budget Variance %")):
        assert f'"{key}", CALCULATE([{name}], \'Date\'[Year] = 2026)' in dax
    assert "SUMMARIZECOLUMNS(Geography[Region]" in rp.dax_variance_by_region(2026) and "[Actual vs Budget Variance]" in rp.dax_variance_by_region(2026)
    for bad in (rp.dax_variance_total, rp.dax_variance_by_region):
        with pytest.raises(ValueError):
            bad("2026) x")


def variance_checks(model_v="-30", model_regions=None, model_pct=None):
    expected = rp.expected_variance({"Europe": Decimal("260"), "Asia Pacific": Decimal("410")}, {"Europe": Decimal("250"), "Asia Pacific": Decimal("450")})
    pct = model_pct if model_pct is not None else float(Decimal("-30") / Decimal("700"))
    total_rows = [{"[a]": 670, "[b]": 700, "[v]": float(model_v), "[p]": pct}]
    region_rows = [{"Geography[Region]": r, "[v]": float(v)} for r, v in (model_regions or {"Europe": "10", "Asia Pacific": "-40"}).items()]
    return rp.compare_variance(expected, total_rows, region_rows, 2026, ["Jan", "Feb", "Mar"])


def test_a_consistent_variance_passes_and_is_summarised_with_its_window():
    checks = variance_checks()
    assert all(c.ok for c in checks.checks), [str(c) for c in checks.checks if not c.ok]
    assert checks.window == "Jan-Mar 2026" and len(checks.regions) == 2
    status, observed = rp.summarize(results({"Europe": Decimal("10"), "Asia Pacific": Decimal("20")}), REPORTING, checks)
    assert status == "passed"
    assert "actual-vs-budget (Jan-Mar 2026): variance -30" in observed and "6/6 variance checks ok incl. 2 regions" in observed


def test_a_variance_that_the_model_computes_against_the_full_year_budget_is_caught():
    """The trap the measure avoids: year-to-date actuals against a full-year budget make every region look far behind."""
    checks = variance_checks(model_v="-30000", model_regions={"Europe": "-9990", "Asia Pacific": "-20040"})
    status, observed = rp.summarize(results({"Europe": Decimal("10"), "Asia Pacific": Decimal("20")}), REPORTING, checks)
    assert status == "failed" and "MISMATCH" in observed and "variance total" in observed


def test_a_missing_region_or_percentage_in_the_model_fails():
    assert rp.summarize(results({"Europe": Decimal("10"), "Asia Pacific": Decimal("20")}), REPORTING, variance_checks(model_regions={"Europe": "10"}))[0] == "failed"
    assert rp.summarize(results({"Europe": Decimal("10"), "Asia Pacific": Decimal("20")}), REPORTING, variance_checks(model_pct=0.5))[0] == "failed"


def test_summarize_without_variance_checks_keeps_the_original_text():
    status, observed = rp.summarize(results({"Europe": Decimal("10"), "Asia Pacific": Decimal("20")}), REPORTING)
    assert status == "passed" and "actual-vs-budget" not in observed and "SQL 30" in observed


# --- rolling forecast (planned by region, no product category) ------------------------------------------------------------------------------
FORECAST_CONFIG = {"medtech": {
    "writeback_tables": [
        {"source_table": "bud", "series": "B", "measure": "Plan Budget Revenue"},
        {"source_table": "fc", "series": "FY2026 Forecast", "measure": "Plan Forecast Revenue", "role": "forecast", "dimensions": ["Region"]},
    ],
    "reporting_check": {"leaf": {"region": "Europe", "category": "Diagnostics & Imaging", "year": 2026, "month": "Oct"}},
}}


def test_leaf_filter_follows_the_dimensions_of_the_table():
    assert rp.leaf_filter(("Region", "Category")) == rp.LEAF_FILTER
    assert rp.leaf_filter(("Region",)) == "Region <> 'All' AND MonthShort <> 'All'"  # the rolling forecast has no Category column to filter on


def test_forecast_reporting_is_read_from_the_forecast_entry_and_keeps_only_the_region_month_leaf():
    forecast = rp.ForecastReporting.from_config(FORECAST_CONFIG)
    assert forecast.table == "dbo.fc" and forecast.series == "FY2026 Forecast" and forecast.measure == "Plan Forecast Revenue"
    assert forecast.dimensions == ("Region",) and forecast.leaf == {"region": "Europe", "year": "2026", "month": "Oct"}
    assert rp.ForecastReporting.from_config(CONFIG) is None and rp.ForecastReporting.from_config({}) is None  # no forecast entry: nothing to check
    with pytest.raises(LookupError):
        rp.ForecastReporting.from_config({"medtech": {"writeback_tables": FORECAST_CONFIG["medtech"]["writeback_tables"]}})


def test_the_budget_reporting_ignores_the_forecast_entry():
    reporting = rp.Reporting.from_config(FORECAST_CONFIG)
    assert reporting.table == "dbo.bud" and reporting.measure == "Plan Budget Revenue"


def test_region_leaf_dax_filters_region_year_and_month_but_not_category():
    dax = rp.dax_region_leaf("Plan Forecast Revenue", {"region": 'Eu"rope', "year": 2026, "month": "Oct"})
    assert 'Geography[Region] = "Eu""rope"' in dax and "'Date'[Year] = 2026" in dax and "'Date'[Month Short] = \"Oct\"" in dax and "Product[Category]" not in dax
    with pytest.raises(ValueError):
        rp.dax_region_leaf("m", {"region": "r", "year": "2026; x", "month": "Oct"})


def test_forecast_variance_dax_uses_the_forecast_measures_and_the_variance_measure_by_region():
    measures = rp.forecast_measures("Plan Forecast Revenue", "Plan Budget Revenue")
    assert measures == ("Plan Forecast Revenue", "Plan Budget Revenue", "Forecast vs Budget Variance", "Forecast vs Budget Variance %")
    dax = rp.dax_variance_total(2026, measures)
    for key, name in zip("abvp", measures):
        assert f'"{key}", CALCULATE([{name}], \'Date\'[Year] = 2026)' in dax
    by_region = rp.dax_variance_by_region(2026, "Forecast vs Budget Variance")
    assert "SUMMARIZECOLUMNS(Geography[Region]" in by_region and "CALCULATE([Forecast vs Budget Variance], 'Date'[Year] = 2026)" in by_region
    assert "[Actual vs Budget Variance]" in rp.dax_variance_by_region(2026)  # the default is unchanged


def test_region_totals_sql_without_category_does_not_filter_on_it():
    sql = rp.region_totals_sql("[dbo].[fc]", ["Jan", "Feb", "Mar"], ("Region",))
    assert "Region <> 'All' AND MonthShort <> 'All'" in sql and "Category" not in sql and sql.count("?") == 5 and "GROUP BY Region" in sql


def forecast_checks(model_total="1200", model_regions=None, model_leaf="70", variance_total=None, variance_regions=None):
    forecast = rp.ForecastReporting.from_config(FORECAST_CONFIG)
    sql_regions = {"Europe": Decimal("700"), "Asia Pacific": Decimal("500")}
    comparisons = rp.compare(
        Decimal("1200"), sql_regions, Decimal("70"),
        Decimal(model_total), model_regions or {"Europe": Decimal("700"), "Asia Pacific": Decimal("500")}, Decimal(model_leaf), "Europe/Oct 2026",
    )
    expected = rp.expected_variance(sql_regions, {"Europe": Decimal("650"), "Asia Pacific": Decimal("520")})
    total_rows = variance_total or [{"[a]": 1200, "[b]": 1170, "[v]": 30, "[p]": 30 / 1170}]
    region_rows = [{"Geography[Region]": r, "[v]": v} for r, v in (variance_regions or {"Europe": 50, "Asia Pacific": -20}).items()]
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    variance = rp.compare_variance(expected, total_rows, region_rows, 2026, months, labels=("forecast revenue", "budget"), source="SQL")
    return rp.ForecastChecks(forecast, comparisons, variance)


def test_a_consistent_forecast_passes_and_is_summarised_next_to_the_budget_checks():
    checks = forecast_checks()
    assert checks.variance.window == "Jan-Dec 2026" and len(checks.regions) == 2
    assert [c.label for c in checks.variance.checks[:3]] == ["forecast revenue Jan-Dec 2026", "budget Jan-Dec 2026", "variance total Jan-Dec 2026"]
    status, observed = rp.summarize(results({"Europe": Decimal("10"), "Asia Pacific": Decimal("20")}), REPORTING, variance_checks(), checks)
    assert status == "passed", observed
    assert "forecast [Plan Forecast Revenue] vs dbo.fc (FY2026 Forecast): total 1200 = SQL 1200, 2 regions, leaf Europe/Oct 2026 70 = SQL 70" in observed
    assert "forecast-vs-budget (Jan-Dec 2026): variance 30 = forecast (SQL) - budget (SQL) 30, 6/6 checks ok" in observed


def test_a_forecast_that_differs_from_sql_or_whose_variance_ignores_the_budget_fails():
    status, observed = rp.summarize(results({"Europe": Decimal("10"), "Asia Pacific": Decimal("20")}), REPORTING, None, forecast_checks(model_leaf="71"))
    assert status == "failed" and "MISMATCH" in observed
    # without the blank guard the model would report the whole forecast as variance in a year that has no budget
    status, observed = rp.summarize(
        results({"Europe": Decimal("10"), "Asia Pacific": Decimal("20")}), REPORTING, None,
        forecast_checks(variance_total=[{"[a]": 1200, "[b]": 1170, "[v]": 1200, "[p]": 1.0}], variance_regions={"Europe": 700, "Asia Pacific": 500}),
    )
    assert status == "failed" and "variance total Jan-Dec 2026" in observed


def test_a_forecast_with_fewer_than_two_regions_is_not_a_pass():
    only_europe = rp.compare(Decimal("700"), {"Europe": Decimal("700")}, Decimal("70"), Decimal("700"), {"Europe": Decimal("700")}, Decimal("70"), "Europe/Oct 2026")
    forecast = rp.ForecastReporting.from_config(FORECAST_CONFIG)
    variance = rp.compare_variance(rp.expected_variance({"Europe": Decimal("700")}, {"Europe": Decimal("650")}), [{"[a]": 700, "[b]": 650, "[v]": 50, "[p]": 50 / 650}],
                                   [{"Geography[Region]": "Europe", "[v]": 50}], 2026, ["Jan"], labels=("forecast revenue", "budget"), source="SQL")
    status, _ = rp.summarize(results({"Europe": Decimal("10"), "Asia Pacific": Decimal("20")}), REPORTING, None, rp.ForecastChecks(forecast, only_europe, variance))
    assert status == "failed"


# --- the monthly series an Intelligence chart plots -----------------------------------------------------------------------------------------
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SQL_FORECAST = {m: Decimal("100") for m in MONTHS}
SQL_BUDGET = {m: Decimal("97.5") for m in MONTHS}


def month_rows(forecast=100, budget=97.5, skip=()):
    return [{"Date[Month Short]": m, "[f]": forecast, "[b]": budget} for m in MONTHS if m not in skip]


def test_month_series_dax_filters_the_plan_year_and_returns_both_measures_by_month():
    dax = rp.dax_by_month(2026, "Plan Forecast Revenue", "Plan Budget Revenue")
    assert dax.startswith("EVALUATE SUMMARIZECOLUMNS('Date'[Month Short], ")
    assert "FILTER(ALL('Date'[Year]), 'Date'[Year] = 2026)" in dax and '"f", [Plan Forecast Revenue], "b", [Plan Budget Revenue]' in dax
    with pytest.raises(ValueError):
        rp.dax_by_month("2026; x", "a", "b")
    with pytest.raises(ValueError):
        rp.dax_by_month(2026, "a]) x", "b")


def test_month_totals_sql_reads_the_latest_leaf_rows_of_one_year_grouped_by_month():
    sql = rp.month_totals_sql("[dbo].[bud]")
    assert "IsLatest = 1" in sql and "Region <> 'All' AND Category <> 'All' AND MonthShort <> 'All'" in sql
    assert "Year = ?" in sql and sql.endswith("GROUP BY MonthShort") and sql.count("?") == 2  # the series and the year
    assert "Category" not in rp.month_totals_sql("[dbo].[fc]", ("Region",))  # the forecast has no Category column


def test_month_series_compare_every_month_and_both_series():
    checks = rp.compare_months(SQL_FORECAST, SQL_BUDGET, month_rows(), ("forecast", "budget"))
    assert len(checks) == 24 and all(c.ok for c in checks)
    assert [c.label for c in checks[:3]] == ["forecast Jan", "budget Jan", "forecast Feb"]


def test_a_month_that_differs_or_is_missing_on_either_side_is_a_mismatch():
    labels = ("forecast", "budget")
    off = rp.compare_months(SQL_FORECAST, SQL_BUDGET, month_rows(forecast=101), labels)
    assert sum(1 for c in off if not c.ok) == 12 and all(c.label.startswith("forecast") for c in off if not c.ok)
    assert [c.label for c in rp.compare_months(SQL_FORECAST, SQL_BUDGET, month_rows(skip=("Dec",)), labels) if not c.ok] == ["forecast Dec", "budget Dec"]
    without_march = {m: v for m, v in SQL_BUDGET.items() if m != "Mar"}
    assert [c.label for c in rp.compare_months(SQL_FORECAST, without_march, month_rows(), labels) if not c.ok] == ["budget Mar"]
    blank = rp.compare_months(SQL_FORECAST, SQL_BUDGET, [{"Date[Month Short]": "Jan", "[f]": None, "[b]": 97.5}], labels, ["Jan"])
    assert [c.ok for c in blank] == [False, True]  # a blank measure is not a value


def test_the_chart_series_are_part_of_the_forecast_summary_and_can_fail_it():
    good = forecast_checks()
    labels = ("forecast", "budget")
    plain = {"Europe": Decimal("10"), "Asia Pacific": Decimal("20")}
    status, observed = rp.summarize(results(plain), REPORTING, None, rp.ForecastChecks(good.reporting, good.comparisons, good.variance, rp.compare_months(SQL_FORECAST, SQL_BUDGET, month_rows(), labels)))
    assert status == "passed" and "chart series (forecast and budget by month, 2026): 24/24 monthly values equal SQL" in observed
    status, observed = rp.summarize(results(plain), REPORTING, None, rp.ForecastChecks(good.reporting, good.comparisons, good.variance, rp.compare_months(SQL_FORECAST, SQL_BUDGET, month_rows(budget=90), labels)))
    assert status == "failed" and "12/24 monthly values equal SQL" in observed and "budget Jan" in observed
    status, observed = rp.summarize(results(plain), REPORTING, None, good)
    assert status == "passed" and "chart series" not in observed  # a check set without the series (an older caller) is summarised as before
