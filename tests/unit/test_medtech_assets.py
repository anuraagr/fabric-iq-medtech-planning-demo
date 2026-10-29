"""The synthetic MedTech asset registry is deterministic, internally consistent and ASCII-only."""

from collections import Counter
from datetime import date

import pytest

from scripts.data import medtech_assets as m

# Pinned content digest. If you change the generator on purpose, run `python scripts/medtech_assets.py build` and update it.
PINNED_DIGEST = "59e862827ba5de8e13ddeb176f51d77ea16472dfed4fc92212977b1522b10d9b"
A = {name: index for index, name in enumerate(m.ASSET_HEADERS)}


@pytest.fixture(scope="module")
def registry():
    return m.build_registry()


def test_shapes_match_the_tutorial_dataset(registry):
    assert (len(registry.assets), len(registry.employees), len(registry.locations), len(registry.imports)) == (99, 30, 33, 3)
    assert all(len(r) == len(m.ASSET_HEADERS) for r in registry.assets + registry.imports)
    assert all(len(r) == len(m.EMPLOYEE_HEADERS) for r in registry.employees)
    assert all(len(r) == len(m.LOCATION_HEADERS) for r in registry.locations)


def test_digest_is_stable_across_runs(registry):
    assert m.build_registry().digest() == registry.digest() == PINNED_DIGEST


def test_keys_are_unique(registry):
    for column in ("Asset Id", "Asset Tag", "Serial Number"):
        values = [r[A[column]] for r in registry.assets]
        assert len(set(values)) == len(values), column
    assert len({r[4] for r in registry.employees}) == 30, "full names must be unique"
    assert len({r[1] for r in registry.locations}) == 33


def test_foreign_keys_resolve(registry):
    employee_ids = {r[0] for r in registry.employees}
    location_ids = {r[0] for r in registry.locations}
    assert all(r[A["Assigned To"]] in employee_ids for r in registry.assets if r[A["Assigned To"]] is not None)
    assert all(r[A["Location"]] in location_ids for r in registry.assets + registry.imports)
    assert all(r[7] in employee_ids for r in registry.employees if r[7] is not None)
    assert sum(1 for r in registry.employees if r[7] is None) == 1


def test_status_mix_and_location_conventions(registry):
    assert Counter(r[A["Status"]] for r in registry.assets) == dict(m.STATUS_PLAN)
    for row in registry.assets:
        status, location = row[A["Status"]], row[A["Location"]]
        if status == "Retired":
            assert location == 2 and row[A["Retired Date"]] and row[A["Assigned To"]] is None
        else:
            assert row[A["Retired Date"]] is None
        if status == "Available":
            assert location == 1
        if status == "In Service":
            assert row[A["Assigned To"]] is not None and row[A["Assigned Date"]]


def test_dates_are_ordered_and_in_the_past(registry):
    for row in registry.assets:
        purchase = date.fromisoformat(row[A["Purchase Date"]])
        assert purchase <= m.AS_OF
        assert date.fromisoformat(row[A["Warranty Exp Date"]]) > purchase
        if row[A["Assigned Date"]]:
            assert purchase <= date.fromisoformat(row[A["Assigned Date"]]) <= m.AS_OF
        if row[A["Retired Date"]]:
            assert purchase < date.fromisoformat(row[A["Retired Date"]]) <= m.AS_OF


def test_recall_story_data_is_present(registry):
    assert sum(1 for r in registry.assets if r[A["Features"]] == m.RECALLED_FIRMWARE) > 5
    notes = [r[A["Notes"]] for r in registry.assets if r[A["Notes"]]]
    assert len(notes) == 1 and "FSN-2026-014" in notes[0]


def test_import_rows_are_new_inserts(registry):
    tags = {r[A["Asset Tag"]] for r in registry.assets}
    assert all(r[A["Asset Id"]] is None and r[A["Asset Tag"]] not in tags for r in registry.imports)
    assert {r[A["Asset Type"]] for r in registry.imports} == {"Pulse Oximeter"}
    assert len(registry.imports[0][A["Logo URL"]]) > max(len(r[A["Logo URL"]]) for r in registry.assets)


def test_everything_is_ascii_text(registry):
    for headers, rows in registry.sheets().values():
        for row in rows:
            for value in row:
                assert str(value).isascii(), value
        assert all(h.isascii() for h in headers)


def test_workbook_round_trip(tmp_path, registry):
    from openpyxl import load_workbook

    path = m.write_workbook(tmp_path / "registry.xlsx", registry)
    book = load_workbook(path)
    assert book.sheetnames == list(m.SHEETS)
    assert book["Assets"].max_row == 100 and book["Assets"].max_column == 18
    assert [c.value for c in book["Assets"][1]] == m.ASSET_HEADERS
    assert book["Employees"].max_row == 31 and book["Locations"].max_row == 34 and book["Import"].max_row == 4
    assert book["Assets"]["M2"].is_date
