"""Generator invariants for the synthetic MedTech / Life Sciences dataset (offline, deterministic)."""

from __future__ import annotations

import pytest

from scripts.data import medtech_catalog as cat
from scripts.data.medtech_generator import build_dataset, month_keys


@pytest.fixture(scope="module")
def dataset():
    return build_dataset()


def test_catalog_is_consistent():
    cat.validate_catalog()
    assert len(cat.PRODUCTS) == 36
    assert len(cat.COUNTRIES) == 17
    assert {p.category for p in cat.PRODUCTS} == {c.name for c in cat.CATEGORIES}


def test_generation_is_deterministic(dataset):
    assert build_dataset().digest() == dataset.digest()


def test_seed_changes_the_data(dataset):
    assert build_dataset(seed=1).digest() != dataset.digest()


def test_calendar_and_actual_boundaries(dataset):
    assert len(dataset.dates) == 60
    assert dataset.dates[0][0] == 202301 and dataset.dates[-1][0] == 202712
    sales_months = {row[2] for row in dataset.sales}
    assert min(sales_months) == 202301 and max(sales_months) == 202609
    assert not {202610, 202611, 202612} & sales_months
    assert len(month_keys(2023, 2027, (2026, 9))) == 45


def test_foreign_keys_resolve(dataset):
    product_keys = {r[0] for r in dataset.products}
    geography_keys = {r[0] for r in dataset.geographies}
    date_keys = {r[0] for r in dataset.dates}
    for product_key, geography_key, date_key, units, revenue, cogs in dataset.sales:
        assert product_key in product_keys and geography_key in geography_keys and date_key in date_keys
        assert units > 0 and revenue > 0 and cogs > 0 and revenue > cogs
    for geography_key, date_key, *_ in dataset.pnl:
        assert geography_key in geography_keys and date_key in date_keys


def test_sales_grain_is_unique(dataset):
    grains = [(p, g, d) for p, g, d, *_ in dataset.sales]
    assert len(grains) == len(set(grains))


def test_base_year_revenue_matches_target_within_one_percent(dataset):
    revenue = dataset.expected_totals()["salesByYear"]["2025"]["revenue"]
    assert abs(revenue - cat.TARGET_BASE_YEAR_REVENUE) / cat.TARGET_BASE_YEAR_REVENUE < 0.01


def test_margins_and_growth_are_plausible(dataset):
    totals = dataset.expected_totals()["salesByYear"]
    margins = [v["grossProfit"] / v["revenue"] for v in totals.values()]
    assert all(0.55 < m < 0.75 for m in margins)
    assert totals["2024"]["revenue"] > totals["2023"]["revenue"]
    assert totals["2025"]["revenue"] > totals["2024"]["revenue"]


def test_pnl_ties_to_sales_within_rounding(dataset):
    expected = dataset.expected_totals()
    for year in ("2023", "2024", "2025"):
        sales = expected["salesByYear"][year]
        pnl = expected["pnlByYear"][year]
        assert abs(pnl["grossRevenue"] - sales["revenue"]) / sales["revenue"] < 0.0001
        assert abs(pnl["cogs"] - sales["cogs"]) < 1


def test_pnl_is_profitable_with_realistic_operating_margin(dataset):
    for year, pnl in dataset.expected_totals()["pnlByYear"].items():
        margin = pnl["operatingProfit"] / pnl["netRevenue"]
        assert 0.10 < margin < 0.30, (year, margin)


def test_revenue_per_procedure_is_constant_within_a_year(dataset):
    by_year: dict[int, set[float]] = {}
    for _geo, key, _vol, rpp, *_ in dataset.pnl:
        by_year.setdefault(key // 100, set()).add(rpp)
    assert all(len(values) == 1 for values in by_year.values())


def test_china_stent_event_lowers_price_after_march_2026(dataset):
    stent = next(i for i, p in enumerate(cat.PRODUCTS, start=1) if p.name == "CoroFlex Drug-Eluting Stent")
    china = next(i for i, c in enumerate(cat.COUNTRIES, start=1) if c.name == "China")

    def avg_price(lo: int, hi: int) -> float:
        rows = [(r[3], r[4]) for r in dataset.sales if r[0] == stent and r[1] == china and lo <= r[2] <= hi]
        return sum(rev for _, rev in rows) / sum(units for units, _ in rows)

    assert avg_price(202603, 202609) < 0.9 * avg_price(202501, 202512)


def test_expected_totals_are_json_serialisable(dataset):
    import json

    json.dumps(dataset.expected_totals())
