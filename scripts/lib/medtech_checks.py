"""Live verification of the deployed MedTech semantic model against the generator's expected totals."""

from __future__ import annotations

from dataclasses import dataclass

from .powerbi import PowerBiClient

VERIFY_DAX = """EVALUATE
ROW(
    "Rows", COUNTROWS('Sales Transactions'),
    "PnlRows", COUNTROWS('P&L Drivers'),
    "Rev2023", CALCULATE([Gross Revenue], 'Date'[Year] = 2023),
    "Rev2024", CALCULATE([Gross Revenue], 'Date'[Year] = 2024),
    "Rev2025", CALCULATE([Gross Revenue], 'Date'[Year] = 2025),
    "Cogs2025", CALCULATE([COGS], 'Date'[Year] = 2025),
    "Units2025", CALCULATE([Units Sold], 'Date'[Year] = 2025),
    "Rev2026Ytd", CALCULATE([Gross Revenue], 'Date'[Year] = 2026),
    "Baseline2025", CALCULATE([Baseline Revenue], 'Date'[Year] = 2025),
    "Baseline2026", CALCULATE([Baseline Revenue], 'Date'[Year] = 2026),
    "BaselineCogs2026", CALCULATE([Baseline COGS], 'Date'[Year] = 2026),
    "BaselineUnits2026", CALCULATE([Baseline Units], 'Date'[Year] = 2026),
    "BaselineAll", [Baseline Revenue],
    "PriorYear2026", CALCULATE([PY Gross Revenue], 'Date'[Year] = 2026),
    "Rev202609", CALCULATE([Gross Revenue], 'Date'[Date Key] = 202609),
    "Rev202610", CALCULATE([Gross Revenue], 'Date'[Date Key] = 202610),
    "PnlGross2025", CALCULATE([P&L Gross Revenue], 'Date'[Year] = 2025),
    "PnlNet2025", CALCULATE([P&L Net Revenue], 'Date'[Year] = 2025),
    "PnlOperating2025", CALCULATE([P&L Operating Profit], 'Date'[Year] = 2025),
    "Procedures2025", CALCULATE([Procedure Volume], 'Date'[Year] = 2025),
    "PnlGrossAll", [P&L Gross Revenue],
    "PnlNetAll", [P&L Net Revenue],
    "PnlCogsAll", [P&L COGS],
    "PnlGrossProfitAll", [P&L Gross Profit],
    "PnlOpexAll", [P&L Operating Expenses],
    "PnlOperatingAll", [P&L Operating Profit]
)"""


@dataclass(frozen=True)
class Check:
    label: str
    expected: float | None
    actual: float | None
    tolerance: float = 0.5

    @property
    def ok(self) -> bool:
        if self.expected is None or self.actual is None:
            return self.expected is None and self.actual in (None, 0)
        return abs(self.expected - self.actual) <= self.tolerance


def expected_values(expected: dict) -> dict[str, float | None]:
    sales, pnl = expected["salesByYear"], expected["pnlByYear"]
    return {
        "Rows": expected["rows"]["sales"],
        "PnlRows": expected["rows"]["pnl"],
        "Rev2023": sales["2023"]["revenue"],
        "Rev2024": sales["2024"]["revenue"],
        "Rev2025": sales["2025"]["revenue"],
        "Cogs2025": sales["2025"]["cogs"],
        "Units2025": sales["2025"]["units"],
        "Rev2026Ytd": sales["2026"]["revenue"],
        "Baseline2025": None,
        "Baseline2026": sales["2025"]["revenue"],
        "BaselineCogs2026": sales["2025"]["cogs"],
        "BaselineUnits2026": sales["2025"]["units"],
        "BaselineAll": sales["2025"]["revenue"],
        "PriorYear2026": sales["2025"]["revenue"],
        "Rev202609": expected["monthlyRevenue"]["202609"],
        "Rev202610": None,
        "PnlGross2025": pnl["2025"]["grossRevenue"],
        "PnlNet2025": pnl["2025"]["netRevenue"],
        "PnlOperating2025": pnl["2025"]["operatingProfit"],
        "Procedures2025": pnl["2025"]["procedures"],
        # the All row of the plan sheet `Profitability P&L`: every year of actuals, no filter
        "PnlGrossAll": sum(year["grossRevenue"] for year in pnl.values()),
        "PnlNetAll": sum(year["netRevenue"] for year in pnl.values()),
        "PnlCogsAll": sum(year["cogs"] for year in pnl.values()),
        "PnlGrossProfitAll": sum(year["grossProfit"] for year in pnl.values()),
        "PnlOpexAll": sum(year["opex"] for year in pnl.values()),
        "PnlOperatingAll": sum(year["operatingProfit"] for year in pnl.values()),
    }


def verify_model(powerbi: PowerBiClient, workspace_id: str, model_id: str, expected: dict) -> list[Check]:
    rows = powerbi.execute_queries(workspace_id, model_id, VERIFY_DAX)
    if not rows:
        raise RuntimeError("The verification query returned no rows (has the model been refreshed?)")
    actual = {key.strip("[]"): value for key, value in rows[0].items()}
    return [Check(name, want, actual.get(name), tolerance=1.0 if name.startswith("Pnl") else 0.5) for name, want in expected_values(expected).items()]
