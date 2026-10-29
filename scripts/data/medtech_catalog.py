"""Catalog for the fictional "Northwind MedTech & Life Sciences" demo company.

Every name, price, volume and ratio in this module is synthetic. No real company, product, customer or
patient data is used or implied; the brand names are invented for the demo.
"""

from __future__ import annotations

from dataclasses import dataclass

COMPANY = "Northwind MedTech & Life Sciences"
SEED = 20261021
FIRST_YEAR = 2023
LAST_ACTUAL = (2026, 9)  # last closed month; Oct 2026 onwards is open for planning
CALENDAR_LAST_YEAR = 2027
BASE_YEAR = 2025  # calibration year and the baseline year of the "2025 ..." measures
TARGET_BASE_YEAR_REVENUE = 250_000_000
COST_INFLATION = 0.010  # annual unit-cost inflation applied to every product


@dataclass(frozen=True)
class Category:
    name: str
    gross_margin: float  # list-price gross margin before the regional price index
    price_drift: float  # annual list-price change applied each January
    growth: float  # annual unit growth before product and regional modifiers
    season: tuple[float, ...]  # 12 monthly multipliers, Jan..Dec (normalised to mean 1 by the generator)


@dataclass(frozen=True)
class Product:
    category: str
    sub_category: str
    name: str
    list_price: float  # USD, United States list price in the first year
    share: float  # share of base-year gross revenue (fractions sum to 1)
    growth_mod: float = 0.0


@dataclass(frozen=True)
class Country:
    name: str
    sub_region: str
    region: str
    size: float  # relative market size (United States = 1)
    price_index: float  # price level relative to the United States list


@dataclass(frozen=True)
class Event:
    country: str
    sub_category: str
    start_key: int  # first YYYYMM the event applies to
    price_factor: float
    volume_factor: float
    note: str


CATEGORIES: tuple[Category, ...] = (
    Category("Cardiovascular", 0.78, -0.015, 0.055, (0.97, 0.96, 1.00, 1.00, 1.01, 1.00, 0.95, 0.96, 1.00, 1.03, 1.05, 1.07)),
    Category("Orthopedics", 0.76, -0.010, 0.035, (0.88, 0.92, 0.98, 0.99, 1.00, 1.00, 0.94, 0.95, 1.02, 1.08, 1.11, 1.13)),
    Category("Diabetes & Metabolic", 0.67, -0.025, 0.110, (1.04, 0.98, 1.00, 1.00, 1.00, 0.99, 0.98, 0.99, 1.00, 1.00, 1.00, 1.02)),
    Category("Diagnostics & Imaging", 0.62, -0.005, 0.065, (1.09, 1.07, 1.02, 0.97, 0.94, 0.91, 0.90, 0.93, 0.98, 1.02, 1.05, 1.12)),
    Category("Surgical & Robotics", 0.71, 0.000, 0.085, (0.95, 0.97, 1.02, 1.01, 1.02, 1.00, 0.93, 0.94, 1.01, 1.04, 1.04, 1.07)),
    Category("Life Sciences Tools", 0.63, 0.015, 0.060, (0.92, 0.94, 0.99, 1.00, 1.01, 1.03, 0.98, 0.97, 1.01, 1.05, 1.04, 1.06)),
)

PRODUCTS: tuple[Product, ...] = (
    Product("Cardiovascular", "Rhythm Management", "CardioPace DR Pacemaker", 5800, 0.045),
    Product("Cardiovascular", "Rhythm Management", "CardioShield ICD", 18500, 0.035, 0.010),
    Product("Cardiovascular", "Coronary & Peripheral", "CoroFlex Drug-Eluting Stent", 1250, 0.050, -0.010),
    Product("Cardiovascular", "Coronary & Peripheral", "AngioGuide Catheter Kit", 380, 0.030),
    Product("Cardiovascular", "Structural Heart", "ValveAssist TAVR System", 28000, 0.050, 0.050),
    Product("Cardiovascular", "Structural Heart", "MitraSeal Repair Device", 21000, 0.030, 0.040),
    Product("Orthopedics", "Joint Reconstruction", "OrthoKnee Total Knee System", 4600, 0.050, 0.010),
    Product("Orthopedics", "Joint Reconstruction", "OrthoHip Total Hip System", 4200, 0.040),
    Product("Orthopedics", "Spine", "SpineFix Pedicle Screw System", 3400, 0.035),
    Product("Orthopedics", "Spine", "InterbodyLink Fusion Cage", 2300, 0.025, 0.010),
    Product("Orthopedics", "Trauma & Extremities", "TraumaLock Plate Set", 1700, 0.030, -0.010),
    Product("Orthopedics", "Trauma & Extremities", "ExtremityFix Nail", 1350, 0.020),
    Product("Diabetes & Metabolic", "Glucose Monitoring", "GlucoSense 14-Day Sensor", 62, 0.045, 0.030),
    Product("Diabetes & Metabolic", "Glucose Monitoring", "GlucoSense Smart Transmitter", 240, 0.020, 0.020),
    Product("Diabetes & Metabolic", "Insulin Delivery", "InsuPump Pro Pump", 4200, 0.030, 0.010),
    Product("Diabetes & Metabolic", "Insulin Delivery", "InsuSet Infusion Set 10-Pack", 88, 0.035),
    Product("Diabetes & Metabolic", "Digital Care", "TitrateCare Therapy Software", 950, 0.015, 0.060),
    Product("Diabetes & Metabolic", "Digital Care", "RemoteCare Monitoring Hub", 420, 0.015, 0.040),
    Product("Diagnostics & Imaging", "Imaging Systems", "SonoView Cart Ultrasound", 34000, 0.030),
    Product("Diagnostics & Imaging", "Imaging Systems", "SonoView Handheld Probe", 3800, 0.020, 0.030),
    Product("Diagnostics & Imaging", "Molecular Diagnostics", "MolecuTest PCR Panel 96", 1050, 0.040, 0.010),
    Product("Diagnostics & Imaging", "Molecular Diagnostics", "MolecuTest Rapid Cartridge", 64, 0.025),
    Product("Diagnostics & Imaging", "Point of Care", "ProCare Blood Analyzer", 6800, 0.020),
    Product("Diagnostics & Imaging", "Point of Care", "ProCare Test Cartridge", 22, 0.025, 0.010),
    Product("Surgical & Robotics", "Robotic Surgery", "RoboAssist Instrument Set", 2600, 0.040, 0.030),
    Product("Surgical & Robotics", "Robotic Surgery", "RoboAssist Drape & Accessory Kit", 420, 0.020, 0.020),
    Product("Surgical & Robotics", "Energy & Stapling", "ThermoSeal Vessel Sealer", 480, 0.025),
    Product("Surgical & Robotics", "Energy & Stapling", "StapleFlex Endo Stapler", 360, 0.020),
    Product("Surgical & Robotics", "Wound Closure", "NeoClose Suture Pack", 95, 0.015),
    Product("Surgical & Robotics", "Wound Closure", "BioMesh Hernia Repair Mesh", 780, 0.020, 0.010),
    Product("Life Sciences Tools", "Bioprocessing", "BioFlow Single-Use Bioreactor Bag", 1450, 0.030, 0.020),
    Product("Life Sciences Tools", "Bioprocessing", "BioFlow Sterile Filter Capsule", 290, 0.020, 0.010),
    Product("Life Sciences Tools", "Lab Automation", "LabSort Liquid Handler", 18000, 0.015),
    Product("Life Sciences Tools", "Lab Automation", "LabSort Tip and Plate Pack", 110, 0.010, 0.010),
    Product("Life Sciences Tools", "Reagents & Assays", "ClonoPure Cell Culture Media 10L", 210, 0.015, 0.020),
    Product("Life Sciences Tools", "Reagents & Assays", "ImmunoPlex Assay Kit", 520, 0.010, 0.030),
)

COUNTRIES: tuple[Country, ...] = (
    Country("United States", "United States", "North America", 1.00, 1.00),
    Country("Canada", "Canada", "North America", 0.10, 0.93),
    Country("Germany", "DACH", "Europe", 0.24, 0.88),
    Country("France", "Western Europe", "Europe", 0.15, 0.82),
    Country("United Kingdom", "Western Europe", "Europe", 0.16, 0.80),
    Country("Italy", "Southern Europe", "Europe", 0.11, 0.78),
    Country("Spain", "Southern Europe", "Europe", 0.08, 0.76),
    Country("Japan", "North Asia", "Asia Pacific", 0.22, 0.84),
    Country("China", "North Asia", "Asia Pacific", 0.26, 0.64),
    Country("South Korea", "North Asia", "Asia Pacific", 0.07, 0.80),
    Country("India", "South Asia & Oceania", "Asia Pacific", 0.09, 0.60),
    Country("Australia", "South Asia & Oceania", "Asia Pacific", 0.08, 0.90),
    Country("Brazil", "South America", "Latin America", 0.10, 0.68),
    Country("Mexico", "Mexico & Central America", "Latin America", 0.06, 0.66),
    Country("Saudi Arabia", "Gulf", "Middle East & Africa", 0.05, 0.85),
    Country("United Arab Emirates", "Gulf", "Middle East & Africa", 0.04, 0.88),
    Country("South Africa", "Africa", "Middle East & Africa", 0.03, 0.66),
)

# Additional annual unit growth (as a fraction) by region and year; produces the regional variance story.
REGION_GROWTH_MOD: dict[str, dict[int, float]] = {
    "North America": {2023: 0.000, 2024: 0.000, 2025: 0.005, 2026: 0.025},
    "Europe": {2023: 0.000, 2024: -0.005, 2025: -0.005, 2026: -0.015},
    "Asia Pacific": {2023: 0.010, 2024: 0.015, 2025: 0.010, 2026: -0.010},
    "Latin America": {2023: 0.005, 2024: 0.010, 2025: 0.000, 2026: 0.010},
    "Middle East & Africa": {2023: 0.015, 2024: 0.015, 2025: 0.020, 2026: 0.030},
}
COUNTRY_GROWTH_MOD: dict[str, dict[int, float]] = {
    "China": {2026: -0.040},
}

EVENTS: tuple[Event, ...] = (
    Event("China", "Coronary & Peripheral", 202603, 0.82, 1.10,
          "Volume-based procurement round: stent price cut, partly offset by higher volume"),
    Event("United States", "Joint Reconstruction", 202604, 1.00, 1.08,
          "Site-of-service shift to ambulatory surgery centers lifts elective joint volume"),
    Event("Germany", "Molecular Diagnostics", 202607, 0.94, 0.92,
          "Reimbursement reset on molecular panels lowers price and utilization"),
)

# Gross-to-net, selling and compliance cost ratios by region (share of gross revenue).
REGION_REBATE: dict[str, float] = {"North America": 0.125, "Europe": 0.070, "Asia Pacific": 0.055, "Latin America": 0.075, "Middle East & Africa": 0.045}
REGION_SELLING: dict[str, float] = {"North America": 0.250, "Europe": 0.215, "Asia Pacific": 0.190, "Latin America": 0.180, "Middle East & Africa": 0.170}
REGION_CLINICAL: dict[str, float] = {"North America": 0.040, "Europe": 0.050, "Asia Pacific": 0.040, "Latin America": 0.035, "Middle East & Africa": 0.030}
ADMIN_RATIO = 0.045
RND_RATIO = 0.070
OPEX_YEAR_INFLATION: dict[int, float] = {2023: 1.000, 2024: 1.012, 2025: 1.025, 2026: 1.040}

# Revenue per procedure is constant within a calendar year so Volume x Price aggregates exactly in Planning.
REVENUE_PER_PROCEDURE: dict[int, float] = {2023: 2310.0, 2024: 2355.0, 2025: 2395.0, 2026: 2430.0}
COGS_SPLIT = (("materials", 0.56), ("labor", 0.26))  # sterilization and packaging takes the remainder

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
MONTH_SHORT = tuple(name[:3] for name in MONTH_NAMES)

REGION_ORDER = ("North America", "Europe", "Asia Pacific", "Latin America", "Middle East & Africa")


def validate_catalog() -> None:
    """Fail fast if the catalog is internally inconsistent."""
    total_share = round(sum(p.share for p in PRODUCTS), 6)
    if total_share != 1.0:
        raise ValueError(f"product revenue shares must sum to 1, got {total_share}")
    categories = {c.name for c in CATEGORIES}
    unknown = {p.category for p in PRODUCTS} - categories
    if unknown:
        raise ValueError(f"products reference unknown categories: {sorted(unknown)}")
    if len({p.name for p in PRODUCTS}) != len(PRODUCTS):
        raise ValueError("product names must be unique")
    if len({c.name for c in COUNTRIES}) != len(COUNTRIES):
        raise ValueError("country names must be unique")
    unknown_regions = {c.region for c in COUNTRIES} - set(REGION_ORDER)
    if unknown_regions:
        raise ValueError(f"countries reference unknown regions: {sorted(unknown_regions)}")
    for category in CATEGORIES:
        if len(category.season) != 12:
            raise ValueError(f"{category.name} needs 12 seasonal factors")
