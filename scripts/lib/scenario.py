"""Verification of a scenario writeback table (T-06).

Planning writes the active scenario name into the `IrScenario` column and, with the "Long with Changes" writeback
type, persists every aggregation node (with 'All' sentinels) only for cells that were changed. The Base scenario of the
demo is a copy of the semantic-model baseline that nobody edits, so it is *not* stored: it is checked against the
model instead. The Upside scenario is checked against the documented driver change (sales +3 %, COGS -2 %).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .finance import D, find_duplicate_grains

GRAIN = ("IrScenario", "Region", "Year", "Quarter", "MonthShort", "ValueColumnName")
VALUE = "Value"
ALL = "All"
BASELINE_DAX = """EVALUATE
ROW(
    "Revenue", CALCULATE([Baseline Revenue], 'Date'[Year] = {year}),
    "COGS", CALCULATE([Baseline COGS], 'Date'[Year] = {year})
)"""


@dataclass(frozen=True)
class ScenarioSpec:
    table: str
    scenario: str
    year: str
    sales_series: str
    cogs_series: str
    sales_driver: Decimal  # +0.03 means +3 %
    cogs_driver: Decimal  # -0.02 means -2 %


@dataclass(frozen=True)
class Finding:
    label: str
    expected: Decimal | None
    actual: Decimal | None
    tolerance: Decimal = Decimal("1")

    @property
    def ok(self) -> bool:
        if self.expected is None or self.actual is None:
            return False
        return abs(self.expected - self.actual) <= self.tolerance

    def __str__(self) -> str:
        return f"{self.label}: expected {self.expected} actual {self.actual} ({'ok' if self.ok else 'MISMATCH'})"


def load_specs(raw: Mapping[str, Any]) -> list[ScenarioSpec]:
    """Every scenario on the sheet. `medtech.scenario` holds the shared settings (table, year, series); its optional `scenarios` list gives each
    scenario's name and drivers. Without that list the block itself is the single scenario (the original layout)."""
    block = (raw.get("medtech") or {}).get("scenario")
    if not block:
        raise LookupError("medtech.scenario is not configured")
    return [
        ScenarioSpec(
            table=str(block["table"]),
            scenario=str(entry["name"]),
            year=str(block["year"]),
            sales_series=str(block["sales_series"]),
            cogs_series=str(block["cogs_series"]),
            sales_driver=D(entry["sales_driver"]),
            cogs_driver=D(entry["cogs_driver"]),
        )
        for entry in block.get("scenarios") or [block]
    ]


def load_spec(raw: Mapping[str, Any]) -> ScenarioSpec:
    return load_specs(raw)[0]


def baseline_query(year: str) -> str:
    if not str(year).isdigit():
        raise ValueError(f"Year must be numeric, got {year!r}")
    return BASELINE_DAX.format(year=int(year))


def _rows_for(rows: Sequence[Mapping[str, Any]], scenario: str, series: str, year: str) -> list[Mapping[str, Any]]:
    return [r for r in rows if r["IrScenario"] == scenario and r["ValueColumnName"] == series and str(r["Year"]) == year]


def node_total(rows: Sequence[Mapping[str, Any]], scenario: str, series: str, year: str) -> Decimal | None:
    matches = [r for r in _rows_for(rows, scenario, series, year) if (r["Region"], r["Quarter"], r["MonthShort"]) == (ALL, ALL, ALL)]
    return D(matches[0][VALUE]) if len(matches) == 1 else None


def leaf_sum(rows: Sequence[Mapping[str, Any]], scenario: str, series: str, year: str) -> Decimal:
    """Sum of the leaf nodes (every dimension below 'All'): the grain at which the plan is really stored."""
    return sum((D(r[VALUE]) for r in _rows_for(rows, scenario, series, year) if ALL not in (r["Region"], r["Quarter"], r["MonthShort"])), Decimal(0))


def region_sum(rows: Sequence[Mapping[str, Any]], scenario: str, series: str, year: str) -> Decimal:
    return sum((D(r[VALUE]) for r in _rows_for(rows, scenario, series, year) if r["Region"] != ALL and (r["Quarter"], r["MonthShort"]) == (ALL, ALL)), Decimal(0))


def evaluate(rows: Sequence[Mapping[str, Any]], spec: ScenarioSpec, base_revenue: Decimal, base_cogs: Decimal) -> tuple[list[Finding], list[str]]:
    """Return (numeric findings, structural problems). `rows` must be the latest rows only (IsLatest = 1)."""
    base_revenue, base_cogs = D(base_revenue), D(base_cogs)
    problems: list[str] = []
    duplicates = find_duplicate_grains(rows, GRAIN)
    if duplicates:
        problems.append(f"duplicate grains: {dict(list(duplicates.items())[:3])}")
    scenarios = sorted({r["IrScenario"] for r in rows})
    if spec.scenario not in scenarios:
        problems.append(f"scenario {spec.scenario!r} has no rows (found {scenarios})")
    if "Base" in scenarios:
        problems.append("a 'Base' scenario was written back although Base must stay a read-only copy of the baseline")

    up_sales = node_total(rows, spec.scenario, spec.sales_series, spec.year)
    up_cogs = node_total(rows, spec.scenario, spec.cogs_series, spec.year)
    expected_sales = base_revenue * (1 + spec.sales_driver)
    expected_cogs = base_cogs * (1 + spec.cogs_driver)
    findings = [
        Finding(f"{spec.scenario} {spec.sales_series} total = baseline x (1 + {spec.sales_driver})", expected_sales, up_sales),
        Finding(f"{spec.scenario} {spec.cogs_series} total = baseline x (1 + {spec.cogs_driver})", expected_cogs, up_cogs),
    ]
    for series, total in ((spec.sales_series, up_sales), (spec.cogs_series, up_cogs)):
        findings.append(Finding(f"{series}: leaf nodes add up to the total", total, leaf_sum(rows, spec.scenario, series, spec.year)))
        findings.append(Finding(f"{series}: region nodes add up to the total", total, region_sum(rows, spec.scenario, series, spec.year)))
    if up_sales is not None and up_cogs is not None:
        base_profit = base_revenue - base_cogs
        findings.append(Finding(
            f"Gross profit delta ({spec.scenario} - Base) = sales driver x base revenue - COGS driver x base COGS",
            base_revenue * spec.sales_driver - base_cogs * spec.cogs_driver,
            (up_sales - up_cogs) - base_profit,
        ))
    return findings, problems


def summarize(findings: Sequence[Finding], problems: Sequence[str], spec: ScenarioSpec, base_revenue: Decimal, base_cogs: Decimal) -> tuple[str, str]:
    """(status, observed) for the acceptance record."""
    bad = [str(f) for f in findings if not f.ok]
    status = "failed" if bad or problems else "passed"
    gross_profit_delta = next((f.actual for f in findings if f.label.startswith("Gross profit delta")), None)
    observed = (
        f"{spec.scenario} vs Base ({spec.year}): sales {findings[0].actual} vs {D(base_revenue)}, COGS {findings[1].actual} vs {D(base_cogs)}, "
        f"gross profit delta {gross_profit_delta}; {len(findings) - len(bad)}/{len(findings)} numeric checks ok"
        + (f"; mismatches: {bad}" if bad else "")
        + (f"; problems: {list(problems)}" if problems else "")
    )
    return status, observed


def gross_profit(rows: Sequence[Mapping[str, Any]], spec: ScenarioSpec) -> Decimal | None:
    """Gross profit (sales - COGS) of one scenario from its grand-total nodes, or None when a total is missing."""
    sales = node_total(rows, spec.scenario, spec.sales_series, spec.year)
    cogs = node_total(rows, spec.scenario, spec.cogs_series, spec.year)
    return None if sales is None or cogs is None else sales - cogs


def ordering_problems(rows: Sequence[Mapping[str, Any]], specs: Sequence[ScenarioSpec], base_revenue: Decimal, base_cogs: Decimal) -> tuple[list[str], str]:
    """Scenarios must rank by gross profit the way their drivers say (for example Upside > Base > Downside). Returns (problems, 'A 171.6 > Base 162.3 > B 150.0')."""
    base_revenue, base_cogs = D(base_revenue), D(base_cogs)
    expected = {"Base": base_revenue - base_cogs}
    actual: dict[str, Decimal] = {"Base": base_revenue - base_cogs}
    problems: list[str] = []
    for spec in specs:
        expected[spec.scenario] = base_revenue * (1 + spec.sales_driver) - base_cogs * (1 + spec.cogs_driver)
        profit = gross_profit(rows, spec)
        if profit is None:
            problems.append(f"{spec.scenario}: gross profit cannot be computed (a grand-total node is missing)")
        else:
            actual[spec.scenario] = profit
    wanted = sorted(expected, key=lambda name: expected[name], reverse=True)
    observed = sorted(actual, key=lambda name: actual[name], reverse=True)
    if [n for n in wanted if n in actual] != observed:
        problems.append(f"scenario ranking by gross profit is {observed}, the drivers imply {wanted}")
    text = " > ".join(f"{name} {actual[name]:,.0f}" for name in observed)
    return problems, text


def summarize_all(results: Sequence[tuple[ScenarioSpec, Sequence[Finding], Sequence[str]]], rows: Sequence[Mapping[str, Any]], base_revenue: Decimal, base_cogs: Decimal) -> tuple[str, str]:
    """One acceptance record for every scenario on the sheet plus the ranking check; problems that are not specific to one scenario are reported once."""
    parts, statuses, shared = [], [], []
    for spec, findings, problems in results:
        status, observed = summarize(findings, [], spec, base_revenue, base_cogs)
        statuses.append(status)
        parts.append(observed)
        for problem in problems:
            if problem not in shared:
                shared.append(problem)
    specs = [spec for spec, _, _ in results]
    ranking_problems, ranking = ordering_problems(rows, specs, base_revenue, base_cogs)
    problems = shared + ranking_problems
    status = "failed" if problems or "failed" in statuses else "passed"
    observed = " | ".join(parts) + f" | gross profit ranking {ranking}" + (f" | problems: {problems}" if problems else "")
    return status, observed
