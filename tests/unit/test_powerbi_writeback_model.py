"""Semantic-model refresh polling and OAuth credential binding (Power BI REST), and the writeback table in the MedTech TMDL."""

from __future__ import annotations

import json
import re

import pytest

from scripts.data.medtech_generator import build_dataset
from scripts.data.medtech_tmdl import WritebackSource, build_parts, writeback_measure_expression, writeback_table
from scripts.lib.http import ApiError
from scripts.lib.powerbi import PowerBiClient

SOURCE = WritebackSource(
    model_table="Plan Revenue Budget", server="srv.database.fabric.microsoft.com", database="WB-1234", schema="dbo",
    table="medtech_revenue_budget_fy2026", series="FY2026 Revenue Budget", measure="Plan Budget Revenue",
)


class Response:
    def __init__(self, payload=None, headers=None):
        self._payload, self.headers = payload or {}, headers or {}

    def json(self):
        return self._payload


class RecordingApi:
    def __init__(self, datasources=None, refreshes=None):
        self.datasources = datasources or []
        self.refreshes = refreshes or []
        self.requests = []

    def get_json(self, path, **kwargs):
        if path.endswith("/datasources"):
            return {"value": self.datasources}
        if path.endswith("/refreshes"):
            return {"value": self.refreshes.pop(0) if self.refreshes else []}
        raise AssertionError(path)

    def request(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        return Response(headers={"RequestId": "req-1"})


def test_set_oauth_credentials_patches_only_matching_sources_with_the_token():
    api = RecordingApi(datasources=[
        {"datasourceType": "Sql", "gatewayId": "g1", "datasourceId": "d1"},
        {"datasourceType": "Web", "gatewayId": "g2", "datasourceId": "d2"},
    ])
    assert PowerBiClient(api).set_oauth_credentials("ws", "ds", "TOKEN-VALUE") == 1
    method, path, kwargs = api.requests[0]
    assert (method, path) == ("PATCH", "gateways/g1/datasources/d1")
    details = kwargs["json_body"]["credentialDetails"]
    assert details["credentialType"] == "OAuth2" and details["privacyLevel"] == "Organizational"
    assert json.loads(details["credentials"])["credentialData"] == [{"name": "accessToken", "value": "TOKEN-VALUE"}]


def test_set_oauth_credentials_refuses_unbound_sources():
    api = RecordingApi(datasources=[{"datasourceType": "Sql", "connectionDetails": {"server": "x"}}])
    with pytest.raises(ApiError, match="no gatewayId"):
        PowerBiClient(api).set_oauth_credentials("ws", "ds", "t")
    assert api.requests == []


def test_refresh_returns_the_request_id():
    api = RecordingApi()
    assert PowerBiClient(api).refresh("ws", "ds") == "req-1"
    assert api.requests[0][1] == "groups/ws/datasets/ds/refreshes"


def test_wait_for_refresh_polls_until_completed_and_surfaces_failures():
    api = RecordingApi(refreshes=[[{"requestId": "R", "status": "Unknown"}], [{"requestId": "R", "status": "Completed"}]])
    sleeps: list[float] = []
    assert PowerBiClient(api, sleep=sleeps.append).wait_for_refresh("ws", "ds", "r", interval=3)["status"] == "Completed"
    assert sleeps == [3]
    failing = RecordingApi(refreshes=[[{"requestId": "R", "status": "Failed", "serviceExceptionJson": "boom"}]])
    with pytest.raises(ApiError, match="Failed"):
        PowerBiClient(failing, sleep=lambda s: None).wait_for_refresh("ws", "ds", "R")


def test_wait_for_refresh_times_out():
    api = RecordingApi(refreshes=[[{"requestId": "R", "status": "Unknown"}]] * 5)
    with pytest.raises(ApiError, match="did not finish"):
        PowerBiClient(api, sleep=lambda s: None).wait_for_refresh("ws", "ds", "R", timeout=6, interval=3)


def test_writeback_table_reads_the_latest_leaf_rows_of_one_series():
    table = writeback_table(SOURCE)
    code = table.partitions[0].source if hasattr(table.partitions[0], "source") else str(table.partitions[0])
    assert "Sql.Database(\"srv.database.fabric.microsoft.com\", \"WB-1234\")" in code
    assert "[IsLatest] = 1" in code and "[ValueColumnName] = \"FY2026 Revenue Budget\"" in code
    for sentinel in ("Region", "Category", "MonthShort"):
        assert f"[{sentinel}] <> \"All\"" in code
    assert {c.name for c in table.columns} >= {"Region", "Category", "Year", "Quarter", "Month Short", "Value", "Previous Value"}


def test_writeback_year_is_typed_as_a_number_so_it_can_be_matched_to_the_date_table():
    table = writeback_table(SOURCE)
    assert next(c for c in table.columns if c.name == "Year").data_type == "int64"
    assert 'Table.TransformColumnTypes(Selected, {{"Year", Int64.Type}})' in str(table.partitions[0])


def test_writeback_measure_uses_virtual_relationships_to_every_dimension():
    expression = writeback_measure_expression(SOURCE)
    table = "'Plan Revenue Budget'"
    for dimension, column in (("Geography[Region]", "Region"), ("Product[Category]", "Category"), ("'Date'[Year]", "Year"), ("'Date'[Quarter]", "Quarter"), ("'Date'[Month Short]", "Month Short")):
        assert f"TREATAS(VALUES({dimension}), {table}[{column}])" in expression
    assert expression.startswith("CALCULATE(") and f"SUM({table}[Value])" in expression


def test_build_parts_with_a_writeback_source_adds_table_measure_and_model_reference():
    parts = build_parts(build_dataset(), (SOURCE,))
    assert "definition/tables/Plan Revenue Budget.tmdl" in parts
    model = parts["definition/model.tmdl"].decode("utf-8")
    assert "ref table 'Plan Revenue Budget'" in model
    measures = parts["definition/tables/Measures Table.tmdl"].decode("utf-8")
    assert re.search(r"^\tmeasure 'Plan Budget Revenue' = ", measures, re.M)
    assert "TREATAS(VALUES(Geography[Region])" in measures
    assert "lineageTag" not in parts["definition/tables/Plan Revenue Budget.tmdl"].decode("utf-8")


def test_build_parts_without_writeback_is_unchanged():
    assert "definition/tables/Plan Revenue Budget.tmdl" not in build_parts(build_dataset())


FORECAST = WritebackSource(
    model_table="Plan Revenue Forecast", server="srv.database.fabric.microsoft.com", database="WB-1234", schema="dbo",
    table="medtech_rolling_forecast_fy2026", series="FY2026 Forecast", measure="Plan Forecast Revenue", role="forecast", dimensions=("Region",),
)


def measures_text(*sources: WritebackSource) -> str:
    return build_parts(build_dataset(), sources)["definition/tables/Measures Table.tmdl"].decode("utf-8")


def test_budget_variance_measures_compare_actuals_with_the_budget_of_the_same_months():
    text = measures_text(SOURCE)
    assert re.search(r"^\tmeasure 'Budget for Actual Periods' = ```", text, re.M)
    # the budget is only counted for months that already have actuals, otherwise a year-to-date actual would look short against a full-year budget
    assert "IF(NOT ISBLANK([Gross Revenue]), [Plan Budget Revenue])" in text
    assert "ActualRevenue - [Budget for Actual Periods]" in text
    assert "DIVIDE([Actual vs Budget Variance], ABS([Budget for Actual Periods]))" in text
    assert "Forecast vs Budget Variance" not in text  # no forecast source configured, so no forecast measure


def test_variance_measures_are_absent_without_a_budget_source():
    assert "Actual vs Budget" not in measures_text()
    assert "Forecast vs Budget" not in measures_text(FORECAST)


def test_forecast_source_adds_forecast_vs_budget_measures_next_to_the_budget_ones():
    text = measures_text(SOURCE, FORECAST)
    for name in ("Plan Forecast Revenue", "Forecast vs Budget Variance", "Forecast vs Budget Variance %", "Actual vs Budget Variance"):
        assert f"measure '{name}'" in text, name
    assert "NOT ISBLANK([Plan Forecast Revenue]) && NOT ISBLANK([Plan Budget Revenue])" in text  # a forecast against a missing budget would show the whole forecast as variance
    assert "[Plan Forecast Revenue] - [Plan Budget Revenue]" in text
    assert "DIVIDE([Forecast vs Budget Variance], ABS([Plan Budget Revenue]))" in text


def test_forecast_table_has_no_category_column_and_its_measure_blanks_when_products_are_filtered():
    table = writeback_table(FORECAST)
    assert "Category" not in {c.name for c in table.columns} and "Region" in {c.name for c in table.columns}
    code = str(table.partitions[0])
    assert '[Region] <> "All"' in code and '[MonthShort] <> "All"' in code and "[Category]" not in code
    assert '{"Region", "Year", "Quarter", "MonthShort", "Value"' in code
    expression = writeback_measure_expression(FORECAST)
    assert "ISCROSSFILTERED(Product)" in expression and "BLANK()" in expression
    assert "Product[Category]" not in expression and "TREATAS(VALUES(Geography[Region]), 'Plan Revenue Forecast'[Region])" in expression


def test_unsupported_writeback_dimensions_and_roles_are_rejected_instead_of_generating_a_broken_model():
    with pytest.raises(ValueError, match="unsupported dimensions"):
        writeback_table(WritebackSource("T", "s", "d", "dbo", "t", "x", "m", dimensions=("Region", "Colour")))
    with pytest.raises(ValueError, match="unsupported role"):
        writeback_table(WritebackSource("T", "s", "d", "dbo", "t", "x", "m", role="actuals"))


def test_two_writeback_sources_keep_distinct_model_tables():
    parts = build_parts(build_dataset(), (SOURCE, FORECAST))
    assert "definition/tables/Plan Revenue Budget.tmdl" in parts and "definition/tables/Plan Revenue Forecast.tmdl" in parts
    model = parts["definition/model.tmdl"].decode("utf-8")
    assert "ref table 'Plan Revenue Forecast'" in model


def test_deploy_reads_role_and_dimensions_from_the_configuration_with_budget_defaults():
    from types import SimpleNamespace

    from scripts.deploy import writeback_sources

    entries = [
        {"source_table": "bud", "model_table": "Plan Revenue Budget", "series": "B", "measure": "Plan Budget Revenue"},
        {"source_table": "fc", "model_table": "Plan Revenue Forecast", "series": "F", "measure": "Plan Forecast Revenue", "role": "forecast", "dimensions": ["Region"]},
    ]
    cfg = SimpleNamespace(raw={"medtech": {"writeback_tables": entries}}, names=SimpleNamespace(writeback_db="WB"))
    manifest = SimpleNamespace(find=lambda kind, name, workspace_id=None: SimpleNamespace(extra={"serverFqdn": "srv.example", "databaseName": "WB-1"}))
    budget, forecast = writeback_sources(cfg, manifest, "ws-1")
    assert (budget.role, budget.dimensions) == ("budget", ("Region", "Category"))
    assert (forecast.role, forecast.dimensions, forecast.server, forecast.database) == ("forecast", ("Region",), "srv.example", "WB-1")
    assert writeback_sources(SimpleNamespace(raw={}, names=cfg.names), manifest, "ws-1") == ()
