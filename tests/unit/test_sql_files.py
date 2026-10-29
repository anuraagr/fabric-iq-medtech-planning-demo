"""The shipped sql/*.sql files: every one must be a single read-only statement, and writeback queries must read the latest values only."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.lib.sqlclient import UnsafeSqlError, assert_read_only

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"
FILES = sorted(SQL_DIR.glob("*.sql"))


def test_the_sql_folder_is_not_empty():
    assert len(FILES) >= 5


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_every_shipped_sql_file_is_a_single_read_only_statement(path: Path):
    assert_read_only(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_queries_over_writeback_tables_read_the_latest_rows_unless_they_show_history(path: Path):
    text = path.read_text(encoding="utf-8")
    if "medtech_" not in text or "history" in path.name:
        return
    assert "IsLatest = 1" in text, f"{path.name} must filter IsLatest = 1 (Long with Changes keeps superseded rows)"


def test_the_guard_would_reject_a_modifying_file():
    with pytest.raises(UnsafeSqlError):
        assert_read_only("-- harmless comment\nDELETE FROM dbo.medtech_revenue_budget_fy2026 WHERE 1 = 1")


def test_the_variance_budget_query_uses_the_same_leaf_filter_as_the_reporting_check():
    from scripts.lib.reporting import LEAF_FILTER

    text = (SQL_DIR / "05_budget_for_actual_months.sql").read_text(encoding="utf-8")
    assert LEAF_FILTER in text
    for month in ("Jan", "Sep"):
        assert f"'{month}'" in text


def test_the_forecast_queries_use_the_forecast_grain_without_a_category_and_the_budget_grain_for_the_budget_side():
    from scripts.lib.reporting import LEAF_FILTER, leaf_filter

    forecast_only = (SQL_DIR / "06_forecast_reconciliation.sql").read_text(encoding="utf-8")
    assert leaf_filter(("Region",)) in forecast_only and "Category" not in forecast_only.split("WITH latest", 1)[1]
    both = (SQL_DIR / "07_forecast_vs_budget.sql").read_text(encoding="utf-8")
    assert leaf_filter(("Region",)) in both and LEAF_FILTER in both  # the forecast side has no Category; the budget side keeps it
