"""Plan-definition based acceptance runners (T-05, T-08) against a fake Fabric client."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from scripts import validate
from scripts.data.medtech_catalog import MONTH_SHORT

def encode(parts: dict[str, object]) -> dict:
    return {"definition": {"parts": [
        {"path": path, "payload": base64.b64encode((json.dumps(body) if not isinstance(body, str) else body).encode("utf-8")).decode("ascii")}
        for path, body in parts.items()
    ]}}


def fake_context(parts: dict[str, object]):
    plan = SimpleNamespace(id="plan-1")
    fabric = SimpleNamespace(get_plan_definition=lambda workspace_id, plan_id: encode(parts))
    return SimpleNamespace(
        cfg=SimpleNamespace(names=SimpleNamespace(medtech_plan="MedTech_Plan")),
        workspace_id=lambda: "ws-1",
        manifest=SimpleNamespace(find=lambda kind, name, workspace_id=None: plan),
        clients=SimpleNamespace(fabric=fabric),
    )


SHEETS = [
    {"displayName": "FY2026 Revenue Budget", "recordGuid": "g1", "sheetType": "PLANNING"},
    {"displayName": "Rolling Forecast", "recordGuid": "g2", "sheetType": "PLANNING"},
    {"displayName": "Scenario Comparison", "recordGuid": "g3", "sheetType": "PLANNING"},
    {"displayName": "Profitability P&L", "recordGuid": "g8", "sheetType": "PLANNING"},
    {"displayName": "Assets", "recordGuid": "g4", "sheetType": "POWERTABLE"},
    {"displayName": "Employees", "recordGuid": "g5", "sheetType": "POWERTABLE"},
    {"displayName": "Locations", "recordGuid": "g6", "sheetType": "POWERTABLE"},
    {"displayName": "Executive Overview", "recordGuid": "g7", "sheetType": "REPORTING"},
    {"displayName": "Scenario Overview", "recordGuid": "g9", "sheetType": "REPORTING"},
]


def bridge(measures: list, source: str = "Rolling Forecast") -> dict:
    return {"sources": [{"name": source, "type": "PLANNING", "queries": [{"name": "Query 1 -  Rolling Forecast", "queryId": "Q1", "meta": {"measures": measures, "recordCount": 300}}]}]}


def test_measure_names_accepts_dicts_and_strings():
    assert validate._measure_names({"meta": {"measures": [{"name": "FY2026 Forecast"}, "Plain"]}}) == ["FY2026 Forecast", "Plain"]
    assert validate._measure_names({}) == []


def test_infrastructure_errors_cover_api_lookup_and_odbc_failures():
    import pyodbc

    errors = validate.infrastructure_errors()
    for exc in (pyodbc.OperationalError("08001", "login timeout"), LookupError("x"), KeyError("k"), OSError("net"), validate.ApiError(500, "Boom", "boom")):
        assert isinstance(exc, errors), type(exc)
    assert not isinstance(AssertionError("a real bug must stay a crash"), errors)


def test_t05_requires_every_expected_sheet():
    status, observed = validate.t05(fake_context({"definition.json": {"sheets": SHEETS}}))
    assert status == "passed" and "POWERTABLE" in observed and "REPORTING" in observed
    status, observed = validate.t05(fake_context({"definition.json": {"sheets": SHEETS[:3]}}))
    assert status == "failed" and "Assets" in observed
    status, _ = validate.t05(fake_context({"definition.json": {"sheets": []}}))
    assert status == "not_executed"
    for name in ("Executive Overview", "Scenario Overview"):  # both Intelligence sheets are deliverables, not optional extras
        status, observed = validate.t05(fake_context({"definition.json": {"sheets": [s for s in SHEETS if s["displayName"] != name]}}))
        assert status == "failed" and name in observed.split("missing=")[1].split(";")[0]


def test_t05_reports_the_saved_visuals_of_every_intelligence_sheet_without_making_them_a_pass_criterion():
    parts = {
        "definition.json": {"sheets": SHEETS},
        "sheets/g7/sheet.json": "{}",
        "sheets/g7/visuals/v1/properties.json": '{"actuals": ["Plan Budget Revenue"]}',
        "sheets/g7/visuals/v1/writeback.json": "{}",
        "sheets/g7/visuals/v2/properties.json": '{"actuals": ["Gross Revenue"]}',
        "sheets/g1/visuals/v9/properties.json": '{"actuals": ["Plan Budget Revenue"]}',  # another sheet's visual does not count
        "sheets/g9/visuals/s1/properties.json": '{"name": "Sum of Sales Plan (Upside)"}',
        "sheets/g9/visuals/s2/properties.json": '{"name": "Sum of COGS Plan (Downside)"}',
        "sheets/g9/visuals/s3/properties.json": '{"name": "Sum of Sales Plan"}',  # the Base value is shown, but it is not a scenario variant
        "sheets/g9/visuals/t1/properties.json": "{}",  # the commentary text
    }
    status, observed = validate.t05(fake_context(parts))
    assert status == "passed"
    assert "'Executive Overview': 2 saved visual(s), 1 bound to 'Plan Budget Revenue'" in observed
    assert "'Scenario Overview': 4 saved visual(s), 2 bound to 'Plan (Upside)' / 'Plan (Downside)'" in observed

    empty = {"definition.json": {"sheets": SHEETS}, "sheets/g7/sheet.json": "{}"}
    status, observed = validate.t05(fake_context(empty))
    assert status == "passed" and observed.count("0 saved visual(s), 0 bound") == 2


def test_t08_passes_when_the_consumer_sheet_references_the_shared_query():
    parts = {
        "definition.json": {"sheets": SHEETS},
        "connectedPlanning/infobridge.json": bridge([{"name": "FY2026 Forecast"}]),
        "sheets/g3/visuals/v1/properties.json": '{"series": "Sum of FY2026 Forecast", "queryId": "Q1"}',
    }
    status, observed = validate.t08(fake_context(parts))
    assert status == "passed" and "300 records" in observed and "Scenario Comparison" in observed


def test_t08_fails_when_the_consumer_does_not_reference_the_query():
    parts = {
        "definition.json": {"sheets": SHEETS},
        "connectedPlanning/infobridge.json": bridge([{"name": "FY2026 Forecast"}]),
        "sheets/g3/visuals/v1/properties.json": "{}",
        "sheets/g1/visuals/v1/properties.json": '{"queryId": "Q1"}',  # another sheet referencing it does not count
    }
    status, observed = validate.t08(fake_context(parts))
    assert status == "failed" and "does NOT reference" in observed


def test_t08_is_not_executed_without_a_matching_query():
    for infobridge in ({}, bridge([{"name": "Other"}]), bridge([{"name": "FY2026 Forecast"}], source="Budget")):
        parts = {"definition.json": {"sheets": SHEETS}, "connectedPlanning/infobridge.json": infobridge}
        assert validate.t08(fake_context(parts))[0] == "not_executed"


class FakeSqlSession:
    def __init__(self, total, regions, leaf, budget=None, months=None):
        self.total, self.regions, self.leaf, self.budget, self.months = total, regions, leaf, budget or {}, months or {}
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def table_columns(self, schema, table):
        return [{"COLUMN_NAME": name} for name in ("IsLatest", "ValueColumnName", "Region", "Category", "MonthShort", "Year", "Value", "Quarter")]

    def scalar(self, sql, params=()):
        return self.leaf if "AND Region = ?" in sql else self.total

    def query(self, sql, params=()):
        self.queries.append((sql, list(params)))
        if "GROUP BY MonthShort" in sql:
            return [{"MonthShort": month, "v": value} for month, value in self.months.items()]
        rows = self.budget if "MonthShort IN" in sql else self.regions
        return [{"Region": region, "v": value} for region, value in rows.items()]


class FakeDataset:
    """Actuals for the variance check: Jan-Feb 2026 by region (a 2025 row must be ignored)."""

    geographies = [(1, "Europe", "West", "Germany"), (2, "APAC", "East", "Japan")]
    sales = [(1, 1, 202601, 1, 100, 1), (1, 1, 202602, 1, 160, 1), (1, 2, 202601, 1, 200, 1), (1, 2, 202602, 1, 210, 1), (1, 1, 202512, 1, 999, 1)]


# Europe 260 vs budget 250 (+10), APAC 410 vs 450 (-40): total 670 vs 700 (-30, -4.29 %)
BUDGET_FOR_ACTUAL_MONTHS = {"Europe": 250, "APAC": 450}
VARIANCE_ROWS = {"total": [{"[a]": 670, "[b]": 700, "[v]": -30, "[p]": -30 / 700}], "regions": {"Europe": 10, "APAC": -40}}


def reporting_context(model_total, model_regions, model_leaf, sql_session, variance=VARIANCE_ROWS, variance_error=None, forecast=None, forecast_error=None):
    def execute_queries(workspace_id, dataset_id, dax):
        if forecast and "SUMMARIZECOLUMNS('Date'[Month Short]" in dax:
            return [{"Date[Month Short]": month, "[f]": f, "[b]": b} for month, (f, b) in forecast["months"].items()]
        if "Forecast vs Budget Variance" in dax:
            if forecast_error:
                raise forecast_error
            if "SUMMARIZECOLUMNS" in dax:
                return [{"Geography[Region]": region, "[v]": value} for region, value in forecast["variance"]["regions"].items()]
            return forecast["variance"]["total"]
        if "Actual vs Budget Variance" in dax or "Budget for Actual Periods" in dax:
            if variance_error:
                raise variance_error
            if "SUMMARIZECOLUMNS" in dax:
                return [{"Geography[Region]": region, "[v]": value} for region, value in variance["regions"].items()]
            return variance["total"]
        if forecast and "Plan Forecast Revenue" in dax:
            if "SUMMARIZECOLUMNS" in dax:
                return [{"Geography[Region]": region, "[v]": value} for region, value in forecast["regions"].items()]
            return [{"[v]": forecast["leaf"] if "CALCULATE" in dax else forecast["total"]}]
        if "SUMMARIZECOLUMNS" in dax:
            return [{"Geography[Region]": region, "[v]": value} for region, value in model_regions.items()]
        return [{"[v]": model_leaf if "CALCULATE" in dax else model_total}]

    tables = [{"schema": "dbo", "source_table": "medtech_revenue_budget_fy2026", "series": "Budget Revenue", "measure": "Plan Budget Revenue"}]
    if forecast:
        tables.append({"schema": "dbo", "source_table": "medtech_rolling_forecast_fy2026", "series": "FY2026 Forecast", "measure": "Plan Forecast Revenue", "role": "forecast", "dimensions": ["Region"]})
    raw = {"medtech": {
        "writeback_tables": tables,
        "reporting_check": {"leaf": {"region": "Europe", "category": "Diagnostics & Imaging", "year": "2026", "month": "Oct"}},
    }}
    return SimpleNamespace(
        cfg=SimpleNamespace(raw=raw, names=SimpleNamespace(medtech_model="MedTech_Model", writeback_db="Writeback")),
        workspace_id=lambda: "ws-1",
        manifest=SimpleNamespace(find=lambda kind, name, workspace_id=None: SimpleNamespace(id="model-1")),
        clients=SimpleNamespace(powerbi=SimpleNamespace(execute_queries=execute_queries)),
        sql_session=lambda database: sql_session,
    )


@pytest.fixture(autouse=True)
def small_generator(monkeypatch):
    monkeypatch.setattr(validate, "build_dataset", lambda: FakeDataset)


def good_session():
    regions = {"Europe": 60, "APAC": 40}
    return regions, FakeSqlSession(100, regions, 5.5, BUDGET_FOR_ACTUAL_MONTHS)


def test_t10_passes_when_the_model_equals_sql_in_total_per_region_and_for_the_leaf_and_the_variance_reconciles():
    regions, session = good_session()
    status, observed = validate.t10(reporting_context(100, regions, 5.5, session))
    assert status == "passed", observed
    assert "total 100" in observed and "2 regions match" in observed and "Europe/Diagnostics & Imaging/Oct 2026" in observed
    assert "actual-vs-budget (Jan-Feb 2026): variance -30" in observed and "6/6 variance checks ok incl. 2 regions" in observed
    budget_sql, params = next(q for q in session.queries if "MonthShort IN" in q[0])
    assert params == ["Budget Revenue", "2026", "Jan", "Feb"]  # the budget is read for exactly the months that have actuals; the year is text


def test_t10_fails_when_a_region_in_the_model_differs_from_sql():
    ctx = reporting_context(100, {"Europe": 100, "APAC": 100}, 5.5, FakeSqlSession(100, {"Europe": 60, "APAC": 40}, 5.5, BUDGET_FOR_ACTUAL_MONTHS))
    status, observed = validate.t10(ctx)
    assert status == "failed" and "MISMATCH" in observed


def test_t10_fails_when_the_model_variance_is_taken_against_the_full_year_budget():
    regions, session = good_session()
    wrong = {"total": [{"[a]": 670, "[b]": 268250000, "[v]": -268249330, "[p]": -0.99}], "regions": {"Europe": -66233277, "APAC": -59931000}}
    status, observed = validate.t10(reporting_context(100, regions, 5.5, session, variance=wrong))
    assert status == "failed" and "variance total Jan-Feb 2026" in observed and "MISMATCH" in observed


def test_t10_reports_missing_variance_measures_as_failed_and_other_api_errors_as_infrastructure_problems():
    regions, session = good_session()
    status, observed = validate.t10(reporting_context(100, regions, 5.5, session, variance_error=validate.ApiError(400, "BadRequest", "measure not found")))
    assert status == "failed" and "variance" in observed and "--update-medtech-model" in observed
    with pytest.raises(validate.ApiError):
        validate.t10(reporting_context(100, regions, 5.5, good_session()[1], variance_error=validate.ApiError(500, "Internal", "capacity busy")))


CAPACITY_REJECTION = validate.ApiError(400, "DatasetExecuteQueriesError", "Your organization's Fabric compute capacity has exceeded its limits")


def test_t10_treats_a_capacity_rejection_as_infrastructure_in_both_variance_paths_not_as_missing_measures():
    regions, session = good_session()
    with pytest.raises(validate.ApiError):  # becomes `blocked` in run(), instead of telling the operator to redeploy the model
        validate.t10(reporting_context(100, regions, 5.5, session, variance_error=CAPACITY_REJECTION))
    regions, session = forecast_session()
    with pytest.raises(validate.ApiError):
        validate.t10(reporting_context(100, regions, 5.5, session, forecast=FORECAST, forecast_error=CAPACITY_REJECTION))


def test_t10_is_not_passed_without_the_semantic_model_in_the_manifest():
    ctx = reporting_context(1, {}, 1, FakeSqlSession(1, {}, 1))
    ctx.manifest = SimpleNamespace(find=lambda kind, name, workspace_id=None: None)
    assert validate.t10(ctx)[0] == "failed"


class RoutedSession:
    """SQL fake for two writeback tables. The full-year budget (series, year and 12 months = 14 parameters) differs from the budget of the months that have actuals."""

    def __init__(self, budget, forecast, budget_full_year):
        self.budget, self.forecast, self.budget_full_year = budget, forecast, budget_full_year

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def table_columns(self, schema, table):
        return self.budget.table_columns(schema, table)

    def scalar(self, sql, params=()):
        return (self.forecast if "rolling_forecast" in sql else self.budget).scalar(sql, params)

    def query(self, sql, params=()):
        if "rolling_forecast" in sql:
            return self.forecast.query(sql, params)
        if "MonthShort IN" in sql and len(params) == 14:
            return [{"Region": region, "v": value} for region, value in self.budget_full_year.items()]
        return self.budget.query(sql, params)


# Forecast 700 + 500 (all years in the table; 2026 holds all of it) against the full-year budget 650 + 520: +50, -20, total +30
# Monthly series (what the Intelligence chart plots): 12 x 100 forecast against 12 x 97.5 budget = 1,200 and 1,170
FORECAST = {
    "total": 1200, "regions": {"Europe": 700, "APAC": 500}, "leaf": 70,
    "variance": {"total": [{"[a]": 1200, "[b]": 1170, "[v]": 30, "[p]": 30 / 1170}], "regions": {"Europe": 50, "APAC": -20}},
    "months": {month: (100, 97.5) for month in MONTH_SHORT},
}


def forecast_session():
    regions, budget = good_session()
    budget.months = {month: 97.5 for month in MONTH_SHORT}
    forecast_sql = FakeSqlSession(1200, {"Europe": 700, "APAC": 500}, 70, {"Europe": 700, "APAC": 500}, months={month: 100 for month in MONTH_SHORT})
    return regions, RoutedSession(budget, forecast_sql, {"Europe": 650, "APAC": 520})


def test_t10_also_reconciles_the_rolling_forecast_and_its_variance_to_the_budget_when_a_forecast_table_is_configured():
    regions, session = forecast_session()
    status, observed = validate.t10(reporting_context(100, regions, 5.5, session, forecast=FORECAST))
    assert status == "passed", observed
    assert "forecast [Plan Forecast Revenue] vs dbo.medtech_rolling_forecast_fy2026 (FY2026 Forecast): total 1200 = SQL 1200, 2 regions, leaf Europe/Oct 2026 70 = SQL 70" in observed
    assert "forecast-vs-budget (Jan-Dec 2026): variance 30 = forecast (SQL) - budget (SQL) 30, 6/6 checks ok" in observed
    assert "chart series (forecast and budget by month, 2026): 24/24 monthly values equal SQL" in observed
    assert "actual-vs-budget (Jan-Feb 2026): variance -30" in observed  # the actual-versus-budget checks still run
    forecast_queries = [q for q in session.forecast.queries]
    assert all("Category" not in sql for sql, _ in forecast_queries)  # the forecast has no Category column to filter on
    year_sql, params = next(q for q in forecast_queries if "MonthShort IN" in q[0])
    assert params[:2] == ["FY2026 Forecast", "2026"] and len(params) == 14  # the full plan year, every month
    month_sql, params = next(q for q in forecast_queries if "GROUP BY MonthShort" in q[0])
    assert params == ["FY2026 Forecast", "2026"]  # the chart series are read for the plan year only


def test_t10_fails_when_a_month_the_chart_plots_differs_from_sql_or_is_missing_in_the_model():
    regions, session = forecast_session()
    status, observed = validate.t10(reporting_context(100, regions, 5.5, session, forecast=dict(FORECAST, months=dict(FORECAST["months"], Oct=(120, 97.5)))))
    assert status == "failed" and "forecast Oct" in observed and "23/24 monthly values equal SQL" in observed
    regions, session = forecast_session()
    months = {month: values for month, values in FORECAST["months"].items() if month != "Dec"}
    status, observed = validate.t10(reporting_context(100, regions, 5.5, session, forecast=dict(FORECAST, months=months)))
    assert status == "failed" and "budget Dec" in observed and "22/24 monthly values equal SQL" in observed


def test_t10_fails_when_the_forecast_variance_ignores_the_budget_or_a_forecast_region_differs():
    regions, session = forecast_session()
    no_budget_guard = dict(FORECAST, variance={"total": [{"[a]": 1200, "[b]": 1170, "[v]": 1200, "[p]": 1.0}], "regions": {"Europe": 700, "APAC": 500}})
    status, observed = validate.t10(reporting_context(100, regions, 5.5, session, forecast=no_budget_guard))
    assert status == "failed" and "variance total Jan-Dec 2026" in observed
    regions, session = forecast_session()
    status, observed = validate.t10(reporting_context(100, regions, 5.5, session, forecast=dict(FORECAST, regions={"Europe": 1200, "APAC": 1200})))
    assert status == "failed" and "MISMATCH" in observed


def test_t10_reports_missing_forecast_measures_as_failed_and_other_api_errors_as_infrastructure_problems():
    regions, session = forecast_session()
    status, observed = validate.t10(reporting_context(100, regions, 5.5, session, forecast=FORECAST, forecast_error=validate.ApiError(400, "BadRequest", "measure not found")))
    assert status == "failed" and "forecast-vs-budget" in observed and "--update-medtech-model" in observed
    with pytest.raises(validate.ApiError):
        validate.t10(reporting_context(100, regions, 5.5, forecast_session()[1], forecast=FORECAST, forecast_error=validate.ApiError(500, "Internal", "capacity busy")))


def test_the_live_integration_suite_only_lists_checks_that_have_a_runner_and_an_acceptance_definition():
    from tests.live.test_live_acceptance import READ_ONLY_CHECKS

    known = {test.id for test in validate.load_definitions(validate.DEFS_PATH)}
    assert set(READ_ONLY_CHECKS) <= set(validate.RUNNERS) and set(READ_ONLY_CHECKS) <= known
    assert {"T-07", "T-09", "T-11"}.isdisjoint(READ_ONLY_CHECKS)  # manual-UI proofs and the multi-user matrix are never asserted as read-only passes


# --- T-15: PowerTable column rules ------------------------------------------------------------------------------------------------------------
def powertable_context(configs, *, sheets=SHEETS, violations=None, rows=100, statuses=("Available", "In Service")):
    ctx = fake_context({
        "definition.json": {"sheets": sheets},
        "sheets/g4/visuals/v1/columnConfigs.json": {"columnConfigs": configs},
    })

    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def scalar(self, sql, params=()):
            if sql.startswith("SELECT COUNT(*) FROM [dbo].[assets]") and "WHERE" not in sql:
                return rows
            return (violations or {}).get(sql, 0)

        def query(self, sql, params=()):
            return [{"v": s} for s in statuses]

    from tests.unit.test_powertable_rules import RAW

    ctx.cfg = SimpleNamespace(raw=RAW, names=SimpleNamespace(medtech_plan="MedTech_Plan", powertable_db="PowerTable"))
    ctx.sql_session = lambda database: Session()
    return ctx


def test_t15_passes_when_the_saved_rules_match_and_the_stored_rows_conform():
    from tests.unit.test_powertable_rules import saved

    status, observed = validate.t15(powertable_context(saved()))
    assert status == "passed", observed
    assert "7/7 rules saved in the plan definition" in observed and "100 stored rows, 0 violating a rule" in observed and "Status = ['Available', 'In Service']" in observed


def test_t15_fails_when_a_rule_is_missing_from_the_definition_or_a_row_violates_one():
    from scripts.lib import powertable_rules
    from tests.unit.test_powertable_rules import RULES, column, saved

    status, observed = validate.t15(powertable_context(saved(**{"Asset Tag": column("Asset Tag", mandatory=0)})))
    assert status == "failed" and "required Asset Tag" in observed

    blank_tag = dict(powertable_rules.data_checks(RULES))["blank Asset Tag"]
    status, observed = validate.t15(powertable_context(saved(), violations={blank_tag: 3}))
    assert status == "failed" and "violating rows: {'blank Asset Tag': 3}" in observed


def test_t15_is_not_executed_without_rules_configuration_or_saved_column_configuration():
    from tests.unit.test_powertable_rules import saved

    ctx = powertable_context(saved())
    ctx.cfg.raw = {"medtech": {}}
    assert validate.t15(ctx)[0] == "not_executed"
    assert validate.t15(powertable_context([]))[0] == "not_executed"  # the sheet exists but nothing was configured
    assert validate.t15(powertable_context(saved(), sheets=SHEETS[:3]))[0] == "failed"  # the sheet itself is missing
