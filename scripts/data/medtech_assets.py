"""Deterministic synthetic "Medical Device Asset Registry" for the PowerTable demo (fictional data only).

The workbook mirrors the structure of the official PowerTable tutorial dataset (Assets 99 rows x 18 columns,
Employees 30 x 9, Locations 33 x 5, Import 3 rows) but tells a healthcare story: the clinical-engineering team of a
hospital network keeps its installed base of infusion pumps, patient monitors, ventilators, defibrillators and
ultrasound systems in a PowerTable app on a Fabric SQL database. Everything is ASCII so the sample works with the
English-only table and column names PowerTable requires.

All values derive from hash-keyed noise, so `build_registry()` returns the same rows on every run and machine.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SEED = 20261021
AS_OF = date(2026, 10, 21)  # fixed "today" so the retired/active mix never drifts

ASSET_HEADERS = [
    "Asset Id", "Asset Tag", "Asset Type", "Logo URL", "Manufacturer", "Model", "Features", "Assigned To", "Assigned Date",
    "Location", "Status", "Serial Number", "Purchase Date", "Warranty Exp Date", "Expected Lifetime In Years", "Retired Date",
    "Purchase Price", "Notes",
]
EMPLOYEE_HEADERS = ["Employee Id", "Employee Code", "First Name", "Last Name", "Full Name", "Job Title", "Department", "Manager", "Hire Date"]
LOCATION_HEADERS = ["Location Id", "Location", "Description", "Building", "Floor"]
SHEETS = ("Assets", "Employees", "Locations", "Import")

STATUS_PLAN = (("In Service", 28), ("Available", 28), ("Under Maintenance", 24), ("Retired", 19))
FIRMWARE = ("Firmware: v2.4.1", "Firmware: v2.4.2", "Firmware: v3.0.0", "Firmware: v3.1.0", "Firmware: v3.1.2")
RECALLED_FIRMWARE = "Firmware: v3.0.0"  # the field-safety-notice story: find and replace it with v3.1.0

MANUFACTURERS = {
    "Adatum Biomedical": "#0F6CBD",
    "Litware Diagnostics": "#107C41",
    "Tailspin Surgical": "#8764B8",
    "Contoso Imaging": "#C239B3",
    "Woodgrove Respiratory": "#CA5010",
}

# (asset type, manufacturer, [(model, base price)], warranty years, expected lifetime years, count)
DEVICE_FAMILIES = (
    ("Infusion Pump", "Adatum Biomedical", (("InfuseCare 200", 2100.0), ("InfuseCare 300 Smart", 3050.0)), 3, 8, 28),
    ("Patient Monitor", "Litware Diagnostics", (("VitalView 12", 5200.0), ("VitalView 15 Pro", 8400.0)), 3, 10, 25),
    ("Ventilator", "Woodgrove Respiratory", (("BreatheAssist V5", 19500.0), ("BreatheAssist V7 ICU", 31000.0)), 5, 10, 14),
    ("Defibrillator", "Tailspin Surgical", (("PulseGuard D2", 9800.0), ("PulseGuard D4 AED", 14200.0)), 3, 8, 16),
    ("Ultrasound", "Contoso Imaging", (("EchoScan P3", 38500.0), ("EchoScan Cart 9", 82000.0)), 5, 7, 16),
)

FIRST_NAMES = (
    "Amara", "Bruno", "Chiara", "Dmitri", "Elena", "Farid", "Grace", "Hiro", "Ines", "Jamal", "Keiko", "Liam", "Maya", "Noah", "Olga",
    "Priya", "Quentin", "Rosa", "Samir", "Tessa", "Umar", "Vera", "Wen", "Ximena", "Yusuf", "Zoe", "Anders", "Bianca", "Callum", "Dara",
)
LAST_NAMES = (
    "Abara", "Bellamy", "Castillo", "Dvorak", "Eriksen", "Fontaine", "Gallagher", "Haddad", "Ivanova", "Jensen", "Kowalski", "Lindqvist",
    "Moreau", "Nakamura", "Okafor", "Petrov", "Quinn", "Rahman", "Santos", "Tanaka", "Ueda", "Varga", "Whitfield", "Xu", "Yilmaz",
    "Zielinski", "Archer", "Brandt", "Chaudhry", "Delacroix",
)
# (department, job titles in order of seniority) - the first member of each department leads it
DEPARTMENTS = (
    ("Clinical Engineering", ("Clinical Engineering Manager", "Senior Biomedical Engineer", "Biomedical Engineer", "Biomedical Technician")),
    ("Imaging Services", ("Imaging Services Lead", "Imaging Systems Specialist", "Imaging Field Engineer")),
    ("Quality & Compliance", ("Quality Compliance Manager", "Regulatory Affairs Specialist")),
    ("Procurement", ("Medical Equipment Buyer",)),
    ("Supply Chain", ("Asset Coordinator",)),
    ("Clinical Informatics", ("Clinical Informatics Analyst",)),
)
DEPARTMENT_SIZES = (12, 6, 4, 3, 3, 2)  # 30 employees

SPECIAL_LOCATIONS = (
    ("Central Biomed Stores", "Storage or in repair", "Main", 1),
    ("Decommissioned", "Retired from service and disposed", None, None),
    ("Mobile Cart", "Portable device moved between wards", None, None),
)
WARD_LOCATIONS = (
    ("ICU-01", "Intensive Care Unit - Bay 1", "Main", 2), ("ICU-02", "Intensive Care Unit - Bay 2", "Main", 2),
    ("ICU-03", "Intensive Care Unit - Bay 3", "Main", 2), ("ICU-04", "Intensive Care Unit - Bay 4", "Main", 2),
    ("ED-01", "Emergency Department - Resus 1", "Main", 1), ("ED-02", "Emergency Department - Resus 2", "Main", 1),
    ("ED-03", "Emergency Department - Triage", "Main", 1), ("OR-01", "Operating Theatre 1", "Main", 2),
    ("OR-02", "Operating Theatre 2", "Main", 2), ("OR-03", "Operating Theatre 3", "Main", 2),
    ("OR-04", "Operating Theatre 4", "Main", 2), ("OR-05", "Hybrid Operating Theatre", "Main", 2),
    ("PACU-01", "Post-Anaesthesia Care Unit - Bay 1", "Main", 2), ("PACU-02", "Post-Anaesthesia Care Unit - Bay 2", "Main", 2),
    ("NICU-01", "Neonatal Intensive Care - Bay 1", "Annex", 1), ("NICU-02", "Neonatal Intensive Care - Bay 2", "Annex", 1),
    ("CARD-01", "Cardiac Cath Lab 1", "Main", 1), ("CARD-02", "Cardiac Cath Lab 2", "Main", 1),
    ("RAD-01", "Radiology - Ultrasound Suite 1", "Annex", 1), ("RAD-02", "Radiology - Ultrasound Suite 2", "Annex", 1),
    ("RAD-03", "Radiology - Interventional Suite", "Annex", 1), ("MED-3A", "Medical Ward 3A", "Main", 3),
    ("MED-3B", "Medical Ward 3B", "Main", 3), ("SURG-3C", "Surgical Ward 3C", "Main", 3),
    ("ONC-01", "Oncology Day Unit", "Annex", 2), ("ONC-02", "Oncology Infusion Suite", "Annex", 2),
    ("MAT-01", "Maternity - Delivery Room 1", "Annex", 3), ("MAT-02", "Maternity - Delivery Room 2", "Annex", 3),
    ("PAED-01", "Paediatric Ward", "Annex", 2), ("HDU-01", "High Dependency Unit", "Main", 2),
)
PORTABLE_TYPES = {"Infusion Pump", "Defibrillator", "Ultrasound"}

IMPORT_ROWS = (
    ("MD-1410", "SN-202610410", "2026-09-14"), ("MD-1411", "SN-202610411", "2026-09-14"), ("MD-1412", "SN-202610412", "2026-09-14"),
)


def _uniform(*parts: object) -> float:
    digest = hashlib.blake2b("|".join([str(SEED), *map(str, parts)]).encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


def _pick(options: tuple, *parts: object) -> Any:
    return options[min(int(_uniform(*parts) * len(options)), len(options) - 1)]


def _shift_years(day: date, years: int) -> date:
    return date(day.year + years, day.month, min(day.day, 28))


def logo_uri(manufacturer: str, *, padded: bool = False) -> str:
    """A small self-contained SVG badge (data URI) per manufacturer; the PowerTable Image input type renders it."""
    initials = "".join(word[0] for word in manufacturer.split()[:2]).upper()
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='48' height='48'><rect width='48' height='48' rx='8' fill='{MANUFACTURERS[manufacturer]}'/>"
        f"<text x='24' y='31' font-size='20' text-anchor='middle' fill='white' font-family='Segoe UI'>{initials}</text>"
        + ("<!-- " + "x" * 220 + " -->" if padded else "")
        + "</svg>"
    )
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode("utf-8")).decode("ascii")


@dataclass(frozen=True)
class Registry:
    assets: list[list[Any]]
    employees: list[list[Any]]
    locations: list[list[Any]]
    imports: list[list[Any]]

    def sheets(self) -> dict[str, tuple[list[str], list[list[Any]]]]:
        return {
            "Assets": (ASSET_HEADERS, self.assets),
            "Employees": (EMPLOYEE_HEADERS, self.employees),
            "Locations": (LOCATION_HEADERS, self.locations),
            "Import": (ASSET_HEADERS, self.imports),
        }

    def digest(self) -> str:
        payload = repr([(name, headers, rows) for name, (headers, rows) in self.sheets().items()])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_employees() -> list[list[Any]]:
    rows: list[list[Any]] = []
    employee_id = 0
    for (department, titles), size in zip(DEPARTMENTS, DEPARTMENT_SIZES):
        lead_id = employee_id + 1
        for position in range(size):
            employee_id += 1
            first = FIRST_NAMES[employee_id - 1]
            last = LAST_NAMES[(employee_id * 7 + 3) % len(LAST_NAMES)]  # 7 is coprime to 30, so every full name is unique
            title = titles[min(position, len(titles) - 1)]
            manager = None if employee_id == 1 else (1 if position == 0 else lead_id)
            hired = date(2017, 1, 9) + timedelta(days=int(_uniform("hire", employee_id) * 3100))
            rows.append([employee_id, f"NW{employee_id:03d}", first, last, f"{first} {last}", title, department, manager, hired.isoformat()])
    return rows


def build_locations() -> list[list[Any]]:
    rows = [[i + 1, *loc] for i, loc in enumerate((*SPECIAL_LOCATIONS, *WARD_LOCATIONS))]
    return rows


def _status_sequence() -> list[str]:
    statuses = [status for status, count in STATUS_PLAN for _ in range(count)]
    return [s for _, s in sorted(((_uniform("status", i), s) for i, s in enumerate(statuses)))]


def build_assets(employees: list[list[Any]], locations: list[list[Any]]) -> list[list[Any]]:
    technicians = [row[0] for row in employees if row[6] in ("Clinical Engineering", "Imaging Services") and row[5] not in ("Clinical Engineering Manager", "Imaging Services Lead")]
    ward_ids = [row[0] for row in locations[len(SPECIAL_LOCATIONS):]]
    devices: list[tuple[str, str, str, float, int, int]] = []
    for kind, maker, models, warranty, lifetime, count in DEVICE_FAMILIES:
        for n in range(count):
            model, base = models[int(_uniform("model", kind, n) * len(models))]
            devices.append((kind, maker, model, base, warranty, lifetime))
    devices = [d for _, d in sorted(((_uniform("order", i), d) for i, d in enumerate(devices)), key=lambda pair: pair[0])]
    statuses = _status_sequence()

    rows: list[list[Any]] = []
    used_serials: set[str] = set()
    for index, (kind, maker, model, base, warranty, lifetime) in enumerate(devices):
        asset_id = index + 1
        status = statuses[index]
        retired = status == "Retired"
        start, span = (date(2018, 2, 1), 1700) if retired else (date(2020, 3, 1), 2400)
        purchase = start + timedelta(days=int(_uniform("purchase", asset_id) * span))
        purchase = min(purchase, date(2026, 6, 30))
        retired_date = None
        if retired:
            retired_date = max(purchase + timedelta(days=400), date(2025, 1, 6)) + timedelta(days=int(_uniform("retire", asset_id) * 240))
            retired_date = min(retired_date, date(2026, 9, 30))
        assigned_to = assigned_date = None
        if status == "In Service" or (status == "Under Maintenance" and _uniform("assign", asset_id) < 0.08):
            assigned_to = technicians[int(_uniform("tech", asset_id) * len(technicians))]
            assigned_date = (purchase + timedelta(days=7 + int(_uniform("assigned", asset_id) * 40))).isoformat()
        if status == "Retired":
            location = 2
        elif status in ("Available", "Under Maintenance"):
            location = 1
        elif kind in PORTABLE_TYPES and _uniform("mobile", asset_id) < 0.25:
            location = 3
        else:
            location = ward_ids[int(_uniform("ward", asset_id) * len(ward_ids))]
        serial = f"SN-{purchase.year}{int(10000 + _uniform('serial', asset_id) * 89999):05d}"
        while serial in used_serials:
            serial = f"SN-{purchase.year}{int(10000 + _uniform('serial', asset_id, len(used_serials)) * 89999):05d}"
        used_serials.add(serial)
        price = round(base * (0.92 + 0.16 * _uniform("price", asset_id)), 1)
        notes = None
        rows.append([
            asset_id, f"MD-{1244 + asset_id}", kind, logo_uri(maker), maker, model, _pick(FIRMWARE, "firmware", asset_id), assigned_to, assigned_date,
            location, status, serial, purchase.isoformat(), _shift_years(purchase, warranty).isoformat(), lifetime,
            retired_date.isoformat() if retired_date else None, price, notes,
        ])
    # one retired device carries a recall note, like the tutorial's single "Stolen" note
    recalled = next(row for row in rows if row[10] == "Retired" and row[6] == RECALLED_FIRMWARE)
    recalled[17] = "Withdrawn - field safety notice FSN-2026-014"
    return rows


def build_imports() -> list[list[Any]]:
    rows = []
    for tag, serial, purchased in IMPORT_ROWS:
        purchase = date.fromisoformat(purchased)
        rows.append([
            None, tag, "Pulse Oximeter", logo_uri("Litware Diagnostics", padded=True), "Litware Diagnostics", "SpO2 Spot-Check Pro", "Firmware: v1.2.0, Bluetooth",
            None, None, 1, "Available", serial, purchased, _shift_years(purchase, 2).isoformat(), 5, None, 489.0, None,
        ])
    return rows


def build_registry() -> Registry:
    employees = build_employees()
    locations = build_locations()
    return Registry(build_assets(employees, locations), employees, locations, build_imports())


def write_workbook(path: Path, registry: Registry | None = None) -> Path:
    """Write the registry as an .xlsx with one sheet per table; dates are real Excel dates, ids are integers."""
    from openpyxl import Workbook

    registry = registry or build_registry()
    date_columns = {"Assigned Date", "Purchase Date", "Warranty Exp Date", "Retired Date", "Hire Date"}
    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, (headers, rows) in registry.sheets().items():
        sheet = workbook.create_sheet(name)
        sheet.append(headers)
        for row in rows:
            sheet.append([date.fromisoformat(v) if headers[i] in date_columns and isinstance(v, str) else v for i, v in enumerate(row)])
        for index, header in enumerate(headers, start=1):
            if header in date_columns:
                for cell in sheet.iter_rows(min_row=2, min_col=index, max_col=index):
                    cell[0].number_format = "yyyy-mm-dd"
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path
