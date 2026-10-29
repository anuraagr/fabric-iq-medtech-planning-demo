"""The model check covers the numbers the plan sheet `Profitability P&L` shows (docs/images/13-profitability-pnl.jpg)."""

import json
from pathlib import Path

from scripts.lib.medtech_checks import VERIFY_DAX, Check, expected_values

EXPECTED = json.loads((Path(__file__).resolve().parents[2] / "model" / "medtech" / "expected-totals.json").read_text(encoding="utf-8"))

# The All row of the sheet, in millions: every year of actuals, no filter.
ALL_ROW = {"PnlGrossAll": 898.82, "PnlNetAll": 817.52, "PnlCogsAll": 313.58, "PnlGrossProfitAll": 503.94, "PnlOpexAll": 347.58, "PnlOperatingAll": 156.36}


def test_expected_all_year_pnl_totals_equal_the_all_row_shown_on_the_sheet():
    values = expected_values(EXPECTED)
    for key, millions in ALL_ROW.items():
        assert round(values[key] / 1e6, 2) == millions, key


def test_every_expected_value_is_queried_by_the_verification_dax():
    for key in expected_values(EXPECTED):
        assert f'"{key}"' in VERIFY_DAX, key


def test_the_all_row_pnl_relationships_hold():
    values = expected_values(EXPECTED)
    assert abs(values["PnlNetAll"] - values["PnlCogsAll"] - values["PnlGrossProfitAll"]) <= 1
    assert abs(values["PnlGrossProfitAll"] - values["PnlOpexAll"] - values["PnlOperatingAll"]) <= 1


def test_pnl_checks_use_a_one_currency_unit_tolerance_through_check():
    assert Check("PnlGrossAll", 100.0, 100.9, tolerance=1.0).ok and not Check("PnlGrossAll", 100.0, 101.5, tolerance=1.0).ok
