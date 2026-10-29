"""Reporting round trip (T-10): the semantic model must return what Planning committed to SQL, at every level it can be sliced.

A measure over a writeback table that has no relationships returns the grand total for every region, so the check compares the model with
T-SQL for the total *and* per region, plus one named leaf (the cell edited in the T-09 writeback proof).

The plan-versus-actual measures are reconciled against two sources that do not depend on the model: the actuals come from the deterministic
generator that produced the model's data, the budget from the writeback table in T-SQL. Variance = Actual - Budget, for the months that
already have actuals; the percentage divides by the absolute budget (see docs/architecture.md, "Finance conventions").
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from ..data.medtech_catalog import MONTH_SHORT
from ..data.medtech_tmdl import ACTUAL_VS_BUDGET, ACTUAL_VS_BUDGET_PCT, BUDGET_ACTUAL_PERIODS, FORECAST_VS_BUDGET, FORECAST_VS_BUDGET_PCT
from .finance import D, variance, variance_pct

LEAF_FILTER = "Region <> 'All' AND Category <> 'All' AND MonthShort <> 'All'"
PCT_TOLERANCE = Decimal("1e-9")


def leaf_filter(dimensions: Sequence[str]) -> str:
    """T-SQL predicate that keeps only leaf rows of a writeback table: Planning also stores every aggregation node, with 'All' in the collapsed columns."""
    return " AND ".join(f"{d} <> 'All'" for d in (*dimensions, "MonthShort"))


@dataclass(frozen=True)
class Reporting:
    table: str  # dbo.<writeback table>
    series: str  # ValueColumnName
    measure: str  # model measure
    leaf: Mapping[str, str]  # region, category, year, month

    @classmethod
    def from_config(cls, raw: Mapping[str, Any]) -> "Reporting":
        medtech = raw.get("medtech") or {}
        entry = next((e for e in medtech.get("writeback_tables") or [] if e.get("role", "budget") == "budget"), None)
        leaf = (medtech.get("reporting_check") or {}).get("leaf")
        if not entry or not leaf:
            raise LookupError("a budget entry in medtech.writeback_tables and medtech.reporting_check.leaf must be configured")
        return cls(f"{entry.get('schema', 'dbo')}.{entry['source_table']}", str(entry["series"]), str(entry["measure"]), {k: str(v) for k, v in leaf.items()})


@dataclass(frozen=True)
class ForecastReporting:
    """The rolling-forecast writeback table (planned by region, no product category) and the model measure that surfaces it."""

    table: str
    series: str
    measure: str
    dimensions: tuple[str, ...]
    leaf: Mapping[str, str]  # region, year, month (the Category of the budget leaf does not exist at the forecast's grain)

    @classmethod
    def from_config(cls, raw: Mapping[str, Any]) -> "ForecastReporting | None":
        medtech = raw.get("medtech") or {}
        entry = next((e for e in medtech.get("writeback_tables") or [] if e.get("role") == "forecast"), None)
        if entry is None:
            return None
        leaf = (medtech.get("reporting_check") or {}).get("leaf")
        if not leaf:
            raise LookupError("medtech.reporting_check.leaf must be configured to check the forecast")
        dimensions = tuple(str(d) for d in entry.get("dimensions") or ("Region", "Category"))
        return cls(
            f"{entry.get('schema', 'dbo')}.{entry['source_table']}", str(entry["series"]), str(entry["measure"]), dimensions,
            {k: str(leaf[k]) for k in ("region", "year", "month")},
        )


@dataclass(frozen=True)
class Comparison:
    label: str
    sql: Decimal | None  # the reference value: T-SQL for the writeback checks, generator and SQL for the variance checks
    model: Decimal | None
    tolerance: Decimal = Decimal("0.01")
    source: str = "SQL"

    @property
    def ok(self) -> bool:
        return self.sql is not None and self.model is not None and abs(self.sql - self.model) <= self.tolerance

    def __str__(self) -> str:
        return f"{self.label}: {self.source} {self.sql} vs model {self.model} ({'ok' if self.ok else 'MISMATCH'})"


def measure_ref(name: str) -> str:
    if "]" in name or "'" in name:
        raise ValueError(f"Unsafe measure name {name!r}")
    return f"[{name}]"


def dax_total(measure: str) -> str:
    return f'EVALUATE ROW("v", {measure_ref(measure)})'


def dax_by_region(measure: str) -> str:
    return f'EVALUATE SUMMARIZECOLUMNS(Geography[Region], "v", {measure_ref(measure)})'


def _dax_text(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def dax_leaf(measure: str, leaf: Mapping[str, str]) -> str:
    if not str(leaf["year"]).isdigit():
        raise ValueError(f"Year must be numeric, got {leaf['year']!r}")
    return (
        f'EVALUATE ROW("v", CALCULATE({measure_ref(measure)}, Geography[Region] = {_dax_text(leaf["region"])}, Product[Category] = {_dax_text(leaf["category"])}, '
        f"'Date'[Year] = {int(leaf['year'])}, 'Date'[Month Short] = {_dax_text(leaf['month'])}))"
    )


def dax_region_leaf(measure: str, leaf: Mapping[str, str]) -> str:
    """One region x month of a measure over a table that has no product category (the rolling forecast)."""
    if not str(leaf["year"]).isdigit():
        raise ValueError(f"Year must be numeric, got {leaf['year']!r}")
    return (
        f'EVALUATE ROW("v", CALCULATE({measure_ref(measure)}, Geography[Region] = {_dax_text(leaf["region"])}, '
        f"'Date'[Year] = {int(leaf['year'])}, 'Date'[Month Short] = {_dax_text(leaf['month'])}))"
    )


def first_value(rows: Sequence[Mapping[str, Any]]) -> Decimal | None:
    return D(rows[0]["[v]"]) if rows and rows[0].get("[v]") is not None else None


def region_values(rows: Sequence[Mapping[str, Any]]) -> dict[str, Decimal]:
    return {str(row["Geography[Region]"]): D(row["[v]"]) for row in rows if row.get("[v]") is not None}


def compare(sql_total: Decimal | None, sql_regions: Mapping[str, Decimal], sql_leaf: Decimal | None,
            model_total: Decimal | None, model_regions: Mapping[str, Decimal], model_leaf: Decimal | None, leaf_label: str) -> list[Comparison]:
    results = [Comparison("grand total", sql_total, model_total)]
    for region in sorted(set(sql_regions) | set(model_regions)):
        results.append(Comparison(f"region {region}", sql_regions.get(region), model_regions.get(region)))
    results.append(Comparison(f"leaf {leaf_label}", sql_leaf, model_leaf))
    return results


# --- actual versus budget ------------------------------------------------------------------------------------------------------------------
def _year(year: int | str) -> int:
    if not str(year).isdigit():
        raise ValueError(f"Year must be numeric, got {year!r}")
    return int(year)


ACTUAL_MEASURES = ("Gross Revenue", BUDGET_ACTUAL_PERIODS, ACTUAL_VS_BUDGET, ACTUAL_VS_BUDGET_PCT)


def forecast_measures(forecast_measure: str, budget_measure: str) -> tuple[str, str, str, str]:
    return forecast_measure, budget_measure, FORECAST_VS_BUDGET, FORECAST_VS_BUDGET_PCT


def dax_variance_total(year: int | str, measures: Sequence[str] = ACTUAL_MEASURES) -> str:
    """Compared value, reference, variance and variance % for one year, as one row (keys a, b, v, p).

    By default the actuals against the budget of the months that have actuals; `forecast_measures(...)` gives the rolling forecast against the full-year budget."""
    y = _year(year)
    wrapped = {key: f"CALCULATE({measure_ref(name)}, 'Date'[Year] = {y})" for key, name in zip("abvp", measures)}
    return "EVALUATE ROW(" + ", ".join(f'"{key}", {expression}' for key, expression in wrapped.items()) + ")"


def dax_variance_by_region(year: int | str, variance_measure: str = ACTUAL_VS_BUDGET) -> str:
    return f'EVALUATE SUMMARIZECOLUMNS(Geography[Region], "v", CALCULATE({measure_ref(variance_measure)}, \'Date\'[Year] = {_year(year)}))'


def named_value(rows: Sequence[Mapping[str, Any]], key: str) -> Decimal | None:
    return D(rows[0][f"[{key}]"]) if rows and rows[0].get(f"[{key}]") is not None else None


def actuals_by_region(dataset: Any, year: int | str) -> tuple[dict[str, Decimal], list[str]]:
    """Gross revenue per region for one year from the generator (independent of the semantic model), and the months that have actuals (Jan, Feb, ...)."""
    y = _year(year)
    region_of = {geo[0]: geo[1] for geo in dataset.geographies}
    totals: dict[str, Decimal] = defaultdict(Decimal)
    months: set[int] = set()
    for _product, geography, key, _units, revenue, _cogs in dataset.sales:
        if key // 100 == y:
            totals[region_of[geography]] += Decimal(revenue)
            months.add(key % 100)
    return dict(totals), [MONTH_SHORT[m - 1] for m in sorted(months)]


def region_totals_sql(table_sql: str, months: Sequence[str], dimensions: Sequence[str] = ("Region", "Category")) -> str:
    """Latest leaf value per region for the given months. Parameters: series, year (text: the column also holds 'All'), then the months."""
    if not months:
        raise ValueError("At least one month is required")
    placeholders = ", ".join("?" for _ in months)
    return (
        f"SELECT Region, SUM(CAST(Value AS DECIMAL(38,2))) AS v FROM {table_sql} "
        f"WHERE IsLatest = 1 AND ValueColumnName = ? AND {leaf_filter(dimensions)} AND Year = ? AND MonthShort IN ({placeholders}) GROUP BY Region"
    )


def budget_for_months_sql(table_sql: str, months: Sequence[str]) -> str:
    """Latest leaf budget per region for the given months (the budget is planned by region x category x month)."""
    return region_totals_sql(table_sql, months)


def dax_by_month(year: int | str, first: str, second: str) -> str:
    """Two measures for every month of one year, as a monthly chart with `Month Short` as its category plots them (keys f and b)."""
    return (
        "EVALUATE SUMMARIZECOLUMNS('Date'[Month Short], "
        f"FILTER(ALL('Date'[Year]), 'Date'[Year] = {_year(year)}), "
        f'"f", {measure_ref(first)}, "b", {measure_ref(second)})'
    )


def month_totals_sql(table_sql: str, dimensions: Sequence[str] = ("Region", "Category")) -> str:
    """Latest leaf value per month of one year. Parameters: series, year (text: the column also holds 'All')."""
    return (
        f"SELECT MonthShort, SUM(CAST(Value AS DECIMAL(38,2))) AS v FROM {table_sql} "
        f"WHERE IsLatest = 1 AND ValueColumnName = ? AND {leaf_filter(dimensions)} AND Year = ? GROUP BY MonthShort"
    )


def compare_months(sql_first: Mapping[str, Decimal], sql_second: Mapping[str, Decimal], model_rows: Sequence[Mapping[str, Any]],
                   labels: tuple[str, str], months: Sequence[str] = MONTH_SHORT) -> list[Comparison]:
    """The two series of a monthly chart, model against T-SQL: one comparison per month and series (a month the model or SQL lacks is a mismatch)."""
    model = {str(row["Date[Month Short]"]): row for row in model_rows}
    checks = []
    for month in months:
        row = model.get(month, {})
        for key, label, reference in (("f", labels[0], sql_first), ("b", labels[1], sql_second)):
            value = row.get(f"[{key}]")
            checks.append(Comparison(f"{label} {month}", reference.get(month), D(value) if value is not None else None))
    return checks


@dataclass(frozen=True)
class VarianceExpectation:
    actual: Decimal
    budget: Decimal
    variance: Decimal
    variance_pct: Decimal | None
    variance_by_region: Mapping[str, Decimal]


def expected_variance(actual_by_region: Mapping[str, Decimal], budget_by_region: Mapping[str, Decimal]) -> VarianceExpectation:
    """Variance = Actual - Budget (finance.variance); a region that is missing on one side counts as zero there, which the model comparison then exposes."""
    regions = sorted(set(actual_by_region) | set(budget_by_region))
    by_region = {r: variance(actual_by_region.get(r, 0), budget_by_region.get(r, 0)) for r in regions}
    actual = sum((D(v) for v in actual_by_region.values()), Decimal(0))
    budget = sum((D(v) for v in budget_by_region.values()), Decimal(0))
    return VarianceExpectation(actual, budget, variance(actual, budget), variance_pct(actual, budget), by_region)


@dataclass(frozen=True)
class VarianceChecks:
    window: str  # e.g. "Jan-Sep 2026"
    headline: Comparison  # the total variance
    checks: Sequence[Comparison]  # headline, components, percentage and every region

    @property
    def regions(self) -> list[Comparison]:
        return [c for c in self.checks if c.label.startswith("variance region ")]


def compare_variance(expected: VarianceExpectation, total_rows: Sequence[Mapping[str, Any]], region_rows: Sequence[Mapping[str, Any]], year: int | str, months: Sequence[str],
                     *, labels: tuple[str, str] = ("actual revenue", "budget for the same months"), source: str = "generator/SQL") -> VarianceChecks:
    """Model variance (headline, components, percentage, every region) against the independently computed expectation.

    `labels` name the compared value and its reference in the report ("forecast revenue" / "budget" for the forecast comparison)."""
    if not months:
        window = str(year)
    elif len(months) == 1:
        window = f"{months[0]} {year}"
    else:
        window = f"{months[0]}-{months[-1]} {year}"
    compared, reference = labels
    headline = Comparison(f"variance total {window}", expected.variance, named_value(total_rows, "v"), source=source)
    checks = [
        Comparison(f"{compared} {window}", expected.actual, named_value(total_rows, "a"), source=source),
        Comparison(f"{reference} {window}", expected.budget, named_value(total_rows, "b"), source=source),
        headline,
        Comparison(f"variance % {window}", expected.variance_pct, named_value(total_rows, "p"), tolerance=PCT_TOLERANCE, source=source),
    ]
    model_regions = region_values(region_rows)
    for region in sorted(set(expected.variance_by_region) | set(model_regions)):
        checks.append(Comparison(f"variance region {region} {window}", expected.variance_by_region.get(region), model_regions.get(region), source=source))
    return VarianceChecks(window, headline, checks)


@dataclass(frozen=True)
class ForecastChecks:
    """The rolling forecast against SQL (total, regions, one region x month) and against the budget (variance)."""

    reporting: ForecastReporting
    comparisons: Sequence[Comparison]
    variance: VarianceChecks
    monthly: Sequence[Comparison] = ()  # the forecast and budget series of the Intelligence forecast-vs-budget chart, month by month

    @property
    def regions(self) -> list[Comparison]:
        return [c for c in self.comparisons if c.label.startswith("region ")]


def summarize(results: Sequence[Comparison], reporting: Reporting, variance_checks: VarianceChecks | None = None, forecast: ForecastChecks | None = None) -> tuple[str, str]:
    extra = list(variance_checks.checks) if variance_checks else []
    if forecast:
        extra += [*forecast.comparisons, *forecast.variance.checks, *forecast.monthly]
    bad = [str(r) for r in (*results, *extra) if not r.ok]
    by_region = [r for r in results if r.label.startswith("region ")]
    distinct_regions = len({r.model for r in by_region if r.model is not None})
    status = "failed" if bad or len(by_region) < 2 or (forecast is not None and len(forecast.regions) < 2) else "passed"
    total, leaf = results[0], results[-1]
    observed = (
        f"measure [{reporting.measure}] vs {reporting.table} ({reporting.series}): total {total.model} = SQL {total.sql}; {len(by_region)} regions match "
        f"({distinct_regions} distinct values); {leaf.label} {leaf.model} = SQL {leaf.sql}"
    )
    if variance_checks:
        passed = sum(1 for c in variance_checks.checks if c.ok)
        observed += (
            f"; actual-vs-budget ({variance_checks.window}): variance {variance_checks.headline.model} = actuals (generator) - budget (SQL) {variance_checks.headline.sql}, "
            f"{passed}/{len(variance_checks.checks)} variance checks ok incl. {len(variance_checks.regions)} regions"
        )
    if forecast:
        f_total, f_leaf = forecast.comparisons[0], forecast.comparisons[-1]
        f_passed = sum(1 for c in forecast.variance.checks if c.ok)
        observed += (
            f"; forecast [{forecast.reporting.measure}] vs {forecast.reporting.table} ({forecast.reporting.series}): total {f_total.model} = SQL {f_total.sql}, "
            f"{len(forecast.regions)} regions, {f_leaf.label} {f_leaf.model} = SQL {f_leaf.sql}; forecast-vs-budget ({forecast.variance.window}): "
            f"variance {forecast.variance.headline.model} = forecast (SQL) - budget (SQL) {forecast.variance.headline.sql}, {f_passed}/{len(forecast.variance.checks)} checks ok"
        )
        if forecast.monthly:
            observed += (
                f"; chart series (forecast and budget by month, {forecast.reporting.leaf['year']}): "
                f"{sum(1 for c in forecast.monthly if c.ok)}/{len(forecast.monthly)} monthly values equal SQL"
            )
    return status, observed + (f"; mismatches: {bad}" if bad else "")
