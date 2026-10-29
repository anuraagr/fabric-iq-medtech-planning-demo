from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.cleanup import classify_workspace_items
from scripts.lib.sqlclient import UnsafeSqlError
from scripts.lib.writeback import (
    TableRef, WritebackError, build_select, load_snapshot, parse_expect, parse_table_ref, quote_column, read_rows, save_snapshot, snapshot,
)
from scripts.reset import scenario_filter, validate_scenarios

KNOWN = {"Category", "Sub Category", "Month", "Value", "Scenario", "IsLatest", "We]ird"}


def test_parse_table_ref_accepts_brackets_and_rejects_injection():
    assert parse_table_ref("[dbo].[Forecast]") == TableRef("dbo", "Forecast")
    assert parse_table_ref("dbo.Forecast").sql() == "[dbo].[Forecast]"
    for bad in ("Forecast", "dbo.Fore cast", "dbo.x;DROP TABLE y", "a.b.c"):
        with pytest.raises(WritebackError):
            parse_table_ref(bad)


def test_quote_column_whitelists_against_real_columns_and_escapes_brackets():
    assert quote_column("Sub Category", KNOWN) == "[Sub Category]"
    assert quote_column("We]ird", KNOWN) == "[We]]ird]"
    with pytest.raises(WritebackError):
        quote_column("Nope", KNOWN)
    with pytest.raises(WritebackError):
        quote_column("Value]; DROP TABLE x; --", KNOWN)


def test_build_select_with_latest_and_where_is_read_only():
    sql = build_select(TableRef("dbo", "Forecast"), ["Category", "Month"], "Value", KNOWN, latest_column="IsLatest", where="[Scenario] = 'Base'")
    assert sql == "SELECT [Category], [Month], [Value] FROM [dbo].[Forecast] WHERE [IsLatest] = 1 AND ([Scenario] = 'Base')"
    with pytest.raises(UnsafeSqlError):
        build_select(TableRef("dbo", "Forecast"), ["Category"], "Value", KNOWN, where="1=1; DELETE FROM dbo.Forecast")


class FakeSession:
    def __init__(self, columns, rows):
        self._columns, self._rows = columns, rows

    def table_columns(self, schema, table):
        return [{"COLUMN_NAME": c} for c in self._columns]

    def query(self, sql, params=()):
        self.last_sql = sql
        return self._rows


def test_read_rows_refuses_system_tables_and_missing_tables():
    session = FakeSession(["Category", "Value"], [])
    with pytest.raises(WritebackError, match="system"):
        read_rows(session, TableRef("dbo", "__fabric_plan_sys_x"), ["Category"], "Value")
    with pytest.raises(WritebackError, match="does not exist"):
        read_rows(FakeSession([], []), TableRef("dbo", "Forecast"), ["Category"], "Value")


def test_snapshot_detects_duplicates_and_normalises_values():
    rows = [
        {"Category": "Beverages", "Month": "2026-01-01", "Value": 10},
        {"Category": "Beverages", "Month": "2026-01-01", "Value": 11},
        {"Category": "Dairy", "Month": "2026-01-01", "Value": "5.5"},
        {"Category": "Dairy", "Month": None, "Value": None},
    ]
    values, duplicates = snapshot(rows, ["Category", "Month"], "Value")
    assert duplicates == {("Beverages", "2026-01-01"): 2}
    assert values[("Dairy", "2026-01-01")] == Decimal("5.5")
    assert ("Dairy", "") not in values


def test_parse_expect_formats():
    assert parse_expect(["Beverages|2026-02-01=1,250.50", "Dairy|2026-02-01=-3"]) == {
        ("Beverages", "2026-02-01"): Decimal("1250.50"),
        ("Dairy", "2026-02-01"): Decimal("-3"),
    }
    with pytest.raises(WritebackError):
        parse_expect(["no-equals-sign"])


def test_snapshot_roundtrip(tmp_path):
    path = tmp_path / "s" / "wb.json"
    save_snapshot(path, TableRef("dbo", "Forecast"), ["Category"], "Value", {("A",): Decimal("1.5")})
    meta, values = load_snapshot(path)
    assert meta["table"] == "dbo.Forecast" and values == {("A",): Decimal("1.5")}


def test_reset_scenario_guardrails():
    assert validate_scenarios(["Upside", "Downside"], include_base=False) == ["Upside", "Downside"]
    with pytest.raises(WritebackError, match="required"):
        validate_scenarios([], include_base=False)
    with pytest.raises(WritebackError, match="protected"):
        validate_scenarios(["Upside", "Base"], include_base=False)
    assert validate_scenarios(["Base"], include_base=True) == ["Base"]
    where, params = scenario_filter("Scenario", KNOWN, ["Upside", "Downside"])
    assert where == "[Scenario] IN (?, ?)" and params == ["Upside", "Downside"]


def test_cleanup_classification_protects_unknown_and_product_items():
    items = [
        {"id": "1", "type": "SQLDatabase", "displayName": "FabricPlanningDemo_Writeback"},
        {"id": "2", "type": "SQLEndpoint", "displayName": "FabricPlanningDemo_Writeback"},
        {"id": "3", "type": "SQLDatabase", "displayName": "__fabric_plan_sys"},
        {"id": "4", "type": "Notebook", "displayName": "Someone Else's Notebook"},
    ]
    owned, managed, unknown = classify_workspace_items(items, {"1"}, {"FabricPlanningDemo_Writeback"})
    assert [i["id"] for i in owned] == ["1"]
    assert sorted(i["id"] for i in managed) == ["2", "3"]
    assert [i["id"] for i in unknown] == ["4"]
