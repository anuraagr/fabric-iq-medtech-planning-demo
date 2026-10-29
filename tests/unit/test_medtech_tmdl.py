"""Structure of the generated TMDL definition (offline)."""

from __future__ import annotations

import base64
import json
import re
import zlib

import pytest

from scripts.data.medtech_generator import build_dataset
from scripts.data.medtech_tmdl import RELATIONSHIPS, build_parts, m_text, pack_rows, q


@pytest.fixture(scope="module")
def parts():
    return build_parts(build_dataset())


def text(parts, path: str) -> str:
    return parts[path].decode("utf-8")


def test_required_definition_parts_exist(parts):
    for path in ("definition.pbism", "definition/database.tmdl", "definition/model.tmdl", "definition/relationships.tmdl", "definition/cultures/en-US.tmdl"):
        assert path in parts
    tables = {p.split("/")[-1][:-5] for p in parts if p.startswith("definition/tables/")}
    assert tables == {"Date", "Product", "Geography", "Sales Transactions", "P&L Drivers", "Measures Table"}
    assert json.loads(text(parts, "definition.pbism"))["version"] == "4.2"


def test_the_culture_named_by_the_model_is_defined(parts):
    model = text(parts, "definition/model.tmdl")
    assert "\tculture: en-US" in model and re.search(r"^ref culture en-US$", model, re.M)
    assert text(parts, "definition/cultures/en-US.tmdl").startswith("cultureInfo en-US")


def test_model_references_every_table(parts):
    model = text(parts, "definition/model.tmdl")
    for name in ("Date", "Product", "Geography", "'Sales Transactions'", "'P&L Drivers'", "'Measures Table'"):
        assert f"ref table {name}" in model


def test_indentation_uses_tabs_only(parts):
    for path, data in parts.items():
        if not path.endswith(".tmdl"):
            continue
        for number, line in enumerate(data.decode("utf-8").splitlines(), start=1):
            assert not re.match(r"^ +\S", line), f"{path}:{number} starts with spaces"


def test_no_lineage_tags_and_no_comment_lines(parts):
    for path, data in parts.items():
        body = data.decode("utf-8")
        assert "lineageTag" not in body
        assert not re.search(r"^\s*//(?!/)", body, re.M), path


def test_every_measure_has_a_format_string(parts):
    body = text(parts, "definition/tables/Measures Table.tmdl")
    names = re.findall(r"^\tmeasure (.+?) = ", body, re.M)
    assert len(names) >= 15
    blocks = re.split(r"^\tmeasure ", body, flags=re.M)[1:]
    for block in blocks:
        assert re.search(r"^\t\tformatString: ", block, re.M), block.splitlines()[0]


def test_measures_precede_columns_and_partitions_come_last(parts):
    body = text(parts, "definition/tables/Measures Table.tmdl")
    assert body.index("\tmeasure ") < body.index("\tcolumn ") < body.index("\tpartition ")


def test_relationships_point_from_fact_to_dimension(parts):
    body = text(parts, "definition/relationships.tmdl")
    assert body.count("relationship ") == len(RELATIONSHIPS)
    for _name, source, target in RELATIONSHIPS:
        assert source.startswith(("'Sales Transactions'", "'P&L Drivers'"))
        assert target.split(".")[0] in {"Product", "Geography", "Date"}


def test_hierarchy_levels_reference_existing_columns(parts):
    for table in ("Date", "Product", "Geography"):
        body = text(parts, f"definition/tables/{table}.tmdl")
        columns = set(re.findall(r"^\tcolumn (.+)$", body, re.M))
        levels = re.findall(r"^\t\t\tcolumn: (.+)$", body, re.M)
        assert levels and set(levels) <= columns


def test_sales_has_one_partition_per_year_and_round_trips(parts):
    body = text(parts, "definition/tables/Sales Transactions.tmdl")
    names = re.findall(r"^\tpartition '(Sales Transactions \d{4})'", body, re.M)
    assert names == [f"Sales Transactions {y}" for y in (2023, 2024, 2025, 2026)]
    dataset = build_dataset()
    packed = re.findall(r'Binary\.FromText\("([A-Za-z0-9+/=]+)"', body)
    assert len(packed) == 4
    decoded = [json.loads(zlib.decompress(base64.b64decode(p), -15)) for p in packed]
    assert sum(len(d) for d in decoded) == len(dataset.sales)
    assert decoded[0][0] == list(dataset.sales_by_year()[2023][0])


def test_hidden_fact_columns_and_dimension_keys(parts):
    sales = text(parts, "definition/tables/Sales Transactions.tmdl")
    assert sales.count("isHidden") == 7
    for table in ("Date", "Product", "Geography"):
        assert "isKey" in text(parts, f"definition/tables/{table}.tmdl")


def test_date_table_has_a_real_date_column_and_is_marked_as_the_date_table(parts):
    body = text(parts, "definition/tables/Date.tmdl")
    assert re.search(r"^table Date\n\tdataCategory: Time$", body, re.M)
    date_column = re.search(r"^\tcolumn Date\n((?:\t\t.+\n)+)", body, re.M).group(1)
    assert "dataType: dateTime" in date_column and "isKey" in date_column
    assert body.count("isKey") == 1, "exactly one key column: the date column"
    assert 'Table.AddColumn(Typed, "Date", each #date([Year], [Month], 1), type date)' in body
    assert re.search(r"^\tcolumn 'Date Key'\n(?:\t\t.+\n)*\t\tisHidden", body, re.M) and "isKey" not in body.split("column Date\n", 1)[0]


def test_helpers_quote_names_and_escape_strings():
    assert q("Date") == "Date"
    assert q("Sales Transactions") == "'Sales Transactions'"
    assert q("P&L Drivers") == "'P&L Drivers'"
    assert q("O'Brien") == "'O''Brien'"
    assert m_text('say "hi"') == '"say ""hi"""'
    assert zlib.decompress(base64.b64decode(pack_rows([[1, 2], [3, 4]])), -15) == b"[[1,2],[3,4]]"
