"""Deterministic generator for the synthetic MedTech / Life Sciences planning dataset.

The output is a pure function of the catalog and the seed: no clock, no environment, no unordered iteration.
Noise comes from a keyed BLAKE2b hash of (seed, entity, month), so the result does not depend on loop order.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field

from . import medtech_catalog as cat

COGS_NOISE = (0.98, 0.04)  # multiplier = low + span * u
UNIT_NOISE = (0.94, 0.12)


def _uniform(seed: int, *parts: object) -> float:
    digest = hashlib.blake2b("|".join([str(seed), *map(str, parts)]).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


def month_keys(first_year: int, last_year: int, last_actual: tuple[int, int] | None = None) -> list[tuple[int, int, int]]:
    """(year, month, YYYYMM) for every month in range; optionally stop at the last actual month."""
    keys = []
    for year in range(first_year, last_year + 1):
        for month in range(1, 13):
            if last_actual and (year, month) > last_actual:
                return keys
            keys.append((year, month, year * 100 + month))
    return keys


def _normalised_season(category: cat.Category) -> tuple[float, ...]:
    mean = sum(category.season) / 12
    return tuple(value / mean for value in category.season)


@dataclass
class MedTechDataset:
    seed: int
    dates: list[tuple] = field(default_factory=list)
    products: list[tuple] = field(default_factory=list)
    geographies: list[tuple] = field(default_factory=list)
    sales: list[tuple] = field(default_factory=list)  # product key, geography key, date key, units, revenue, cogs
    pnl: list[tuple] = field(default_factory=list)

    def digest(self) -> str:
        payload = json.dumps(
            {"seed": self.seed, "dates": self.dates, "products": self.products, "geographies": self.geographies, "sales": self.sales, "pnl": self.pnl},
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def sales_by_year(self) -> dict[int, list[tuple]]:
        grouped: dict[int, list[tuple]] = defaultdict(list)
        for row in self.sales:
            grouped[row[2] // 100].append(row)
        return dict(sorted(grouped.items()))

    def expected_totals(self) -> dict:
        """Reference numbers that live DAX queries are compared against."""
        product_by_key = {row[0]: row for row in self.products}
        geography_by_key = {row[0]: row for row in self.geographies}
        years: dict[int, dict[str, int]] = {}
        by_category: dict[str, int] = defaultdict(int)
        by_region: dict[str, int] = defaultdict(int)
        by_month: dict[int, int] = defaultdict(int)
        for product_key, geography_key, date_key, units, revenue, cogs in self.sales:
            year = date_key // 100
            bucket = years.setdefault(year, {"units": 0, "revenue": 0, "cogs": 0, "rows": 0})
            bucket["units"] += units
            bucket["revenue"] += revenue
            bucket["cogs"] += cogs
            bucket["rows"] += 1
            by_month[date_key] += revenue
            if year == cat.BASE_YEAR:
                by_category[product_by_key[product_key][1]] += revenue
                by_region[geography_by_key[geography_key][1]] += revenue
        for bucket in years.values():
            bucket["grossProfit"] = bucket["revenue"] - bucket["cogs"]

        pnl_years: dict[int, dict[str, float]] = {}
        for row in self.pnl:
            (_geo, date_key, volume, rpp, rebates, materials, labor, sterilization, selling, clinical, admin, rnd) = row
            bucket = pnl_years.setdefault(date_key // 100, defaultdict(float))
            bucket["procedures"] += volume
            bucket["grossRevenue"] += volume * rpp
            bucket["rebates"] += rebates
            bucket["cogs"] += materials + labor + sterilization
            bucket["opex"] += selling + clinical + admin + rnd
        for year, bucket in pnl_years.items():
            bucket["netRevenue"] = bucket["grossRevenue"] - bucket["rebates"]
            bucket["grossProfit"] = bucket["netRevenue"] - bucket["cogs"]
            bucket["operatingProfit"] = bucket["grossProfit"] - bucket["opex"]
        return {
            "digest": self.digest(),
            "rows": {"sales": len(self.sales), "pnl": len(self.pnl), "dates": len(self.dates), "products": len(self.products), "geographies": len(self.geographies)},
            "salesByYear": {str(y): dict(v) for y, v in sorted(years.items())},
            "baseYearRevenueByCategory": dict(sorted(by_category.items())),
            "baseYearRevenueByRegion": dict(sorted(by_region.items())),
            "monthlyRevenue": {str(k): v for k, v in sorted(by_month.items())},
            "pnlByYear": {str(y): {k: round(v, 2) for k, v in sorted(b.items())} for y, b in sorted(pnl_years.items())},
        }


def build_dataset(seed: int = cat.SEED) -> MedTechDataset:
    cat.validate_catalog()
    dataset = MedTechDataset(seed=seed)

    calendar = month_keys(cat.FIRST_YEAR, cat.CALENDAR_LAST_YEAR)
    for year, month, key in calendar:
        dataset.dates.append((key, year, month, cat.MONTH_NAMES[month - 1], f"Q{(month - 1) // 3 + 1}", f"{year}-Q{(month - 1) // 3 + 1}", f"{year}-{month:02d}", "H1" if month <= 6 else "H2", cat.MONTH_SHORT[month - 1]))
    for index, product in enumerate(cat.PRODUCTS, start=1):
        dataset.products.append((index, product.category, product.sub_category, product.name))
    for index, country in enumerate(cat.COUNTRIES, start=1):
        dataset.geographies.append((index, country.region, country.sub_region, country.name))

    actual_months = month_keys(cat.FIRST_YEAR, cat.CALENDAR_LAST_YEAR, cat.LAST_ACTUAL)
    category_by_name = {c.name: c for c in cat.CATEGORIES}
    seasons = {c.name: _normalised_season(c) for c in cat.CATEGORIES}

    def price(product: cat.Product, country: cat.Country, year: int, key: int) -> float:
        category = category_by_name[product.category]
        value = product.list_price * country.price_index * (1 + category.price_drift) ** (year - cat.FIRST_YEAR)
        for event in cat.EVENTS:
            if event.country == country.name and event.sub_category == product.sub_category and key >= event.start_key:
                value *= event.price_factor
        return round(value, 2)

    def volume_event(product: cat.Product, country: cat.Country, key: int) -> float:
        factor = 1.0
        for event in cat.EVENTS:
            if event.country == country.name and event.sub_category == product.sub_category and key >= event.start_key:
                factor *= event.volume_factor
        return factor

    def trend(product: cat.Product, country: cat.Country) -> list[float]:
        category = category_by_name[product.category]
        index, series = 1.0, []
        for position, (year, _month, _key) in enumerate(actual_months):
            if position:
                annual = category.growth + product.growth_mod + cat.REGION_GROWTH_MOD[country.region].get(year, 0.0) + cat.COUNTRY_GROWTH_MOD.get(country.name, {}).get(year, 0.0)
                index *= (1 + annual) ** (1 / 12)
            series.append(index)
        return series

    trends = {(p.name, c.name): trend(p, c) for p in cat.PRODUCTS for c in cat.COUNTRIES}

    def unscaled_units(product: cat.Product, country: cat.Country, position: int) -> float:
        _year, month, key = actual_months[position]
        return country.size * trends[(product.name, country.name)][position] * seasons[product.category][month - 1] * volume_event(product, country, key)

    scales: dict[str, float] = {}
    for product in cat.PRODUCTS:
        base_revenue = 0.0
        for position, (year, _month, key) in enumerate(actual_months):
            if year == cat.BASE_YEAR:
                for country in cat.COUNTRIES:
                    base_revenue += unscaled_units(product, country, position) * price(product, country, year, key)
        scales[product.name] = cat.TARGET_BASE_YEAR_REVENUE * product.share / base_revenue

    rev_by_geo_month: dict[tuple[int, int], int] = defaultdict(int)
    cogs_by_geo_month: dict[tuple[int, int], int] = defaultdict(int)
    for product_key, product in enumerate(cat.PRODUCTS, start=1):
        category = category_by_name[product.category]
        for geography_key, country in enumerate(cat.COUNTRIES, start=1):
            for position, (year, _month, key) in enumerate(actual_months):
                expected = unscaled_units(product, country, position) * scales[product.name]
                noise = UNIT_NOISE[0] + UNIT_NOISE[1] * _uniform(dataset.seed, "u", product_key, geography_key, key)
                units = int(round(expected * noise))
                if units <= 0:
                    continue
                unit_price = price(product, country, year, key)
                unit_cost = product.list_price * (1 - category.gross_margin) * (1 + cat.COST_INFLATION) ** (year - cat.FIRST_YEAR)
                cost_noise = COGS_NOISE[0] + COGS_NOISE[1] * _uniform(dataset.seed, "c", product_key, geography_key, key)
                revenue = int(round(units * unit_price))
                cogs = int(round(units * unit_cost * cost_noise))
                dataset.sales.append((product_key, geography_key, key, units, revenue, cogs))
                rev_by_geo_month[(geography_key, key)] += revenue
                cogs_by_geo_month[(geography_key, key)] += cogs

    for geography_key, country in enumerate(cat.COUNTRIES, start=1):
        for year, _month, key in actual_months:
            revenue, cogs = rev_by_geo_month[(geography_key, key)], cogs_by_geo_month[(geography_key, key)]
            rpp = cat.REVENUE_PER_PROCEDURE[year]
            volume = int(round(revenue / rpp))
            gross = volume * rpp

            def u(tag: str) -> float:
                return _uniform(dataset.seed, "p" + tag, geography_key, key)

            inflation = cat.OPEX_YEAR_INFLATION[year]
            rebates = int(round(gross * cat.REGION_REBATE[country.region] * (0.95 + 0.10 * u("r"))))
            materials = int(round(cogs * cat.COGS_SPLIT[0][1] * (0.97 + 0.06 * u("m"))))
            labor = int(round(cogs * cat.COGS_SPLIT[1][1] * (0.97 + 0.06 * u("l"))))
            sterilization = cogs - materials - labor
            selling = int(round(gross * cat.REGION_SELLING[country.region] * inflation * (0.96 + 0.08 * u("s"))))
            clinical = int(round(gross * cat.REGION_CLINICAL[country.region] * inflation * (0.96 + 0.08 * u("c"))))
            admin = int(round(gross * cat.ADMIN_RATIO * inflation * (0.96 + 0.08 * u("a"))))
            rnd = int(round(gross * cat.RND_RATIO * inflation * (0.96 + 0.08 * u("d"))))
            dataset.pnl.append((geography_key, key, volume, rpp, rebates, materials, labor, sterilization, selling, clinical, admin, rnd))
    return dataset
