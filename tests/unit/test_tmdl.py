from __future__ import annotations

from scripts.lib.tmdl import parse_relationships, parse_table

MEASURES_TABLE = """table 'Measures Table'
\tlineageTag: ca2bdfb0-5676-444e-ab5d-b74fa8acb7ac

\tmeasure COGS =
\t\t\t
\t\t\tSUM('Fact Transactions'[COGS])
\t\tlineageTag: 78025e5c-b90d-4575-b65b-721dac07a26e

\t\tannotation PBI_FormatHint = {"isGeneralNumber":true}

\tmeasure '2025 Gross Revenue' =
\t\t\t
\t\t\tCALCULATE(
\t\t\t    SUM('Fact Transactions'[Gross Revenue]),
\t\t\t    FILTER(
\t\t\t        'Fact Transactions',
\t\t\t        VALUE('Fact Transactions'[Date Key]) >= 202501
\t\t\t    )
\t\t\t)
\t\tlineageTag: d9668a2f-88b1-4439-9472-e2c838a067ac

\tcolumn Value
\t\tisHidden
\t\tsummarizeBy: none
\t\tsourceColumn: [Value]

\tpartition 'Measures Table' = calculated
\t\tmode: import
\t\tsource =
\t\t\t\t{ "Measures" }
"""

FACT = """table 'Fact Transactions'
\tcolumn 'Sales Key'
\t\tdataType: string
\t\tsummarizeBy: none
\t\tsourceColumn: Sales Key

\tcolumn 'Gross Revenue'
\t\tdataType: double
\t\tsummarizeBy: sum

\tcolumn 'Gross Profit'
\t\tdataType: double
\t\tisHidden
\t\tsummarizeBy: sum

\tpartition 'Fact Transactions' = calculated
\t\tmode: import
\t\tsource = UNION(a, b)
"""

RELATIONSHIPS = """relationship 893dca77
\tfromColumn: 'Fact Transactions'.'Geography Key'
\ttoColumn: Geography.'Geography Key'

relationship 0d91bc99
\tjoinOnDateBehavior: datePartOnly
\tfromColumn: 'P&L Rows'.Date
\ttoColumn: LocalDateTable_24a7.Date
\tisActive: false
"""


def test_measures_and_partition_are_parsed_without_leaking_source():
    table = parse_table(MEASURES_TABLE)
    assert table.name == "Measures Table"
    assert [m.name for m in table.measures] == ["COGS", "2025 Gross Revenue"]
    assert table.measures[0].expression == "SUM('Fact Transactions'[COGS])"
    assert table.measures[1].expression.startswith("CALCULATE(")
    assert table.measures[1].expression.rstrip().endswith(")")
    assert "lineageTag" not in table.measures[1].expression
    assert table.columns[0].name == "Value" and table.columns[0].hidden
    assert table.partitions[0].kind == "calculated" and table.partitions[0].mode == "import"


def test_columns_types_and_hidden_flags():
    table = parse_table(FACT)
    by_name = {c.name: c for c in table.columns}
    assert by_name["Sales Key"].data_type == "string" and by_name["Sales Key"].summarize_by == "none"
    assert by_name["Gross Revenue"].summarize_by == "sum" and not by_name["Gross Revenue"].hidden
    assert by_name["Gross Profit"].hidden


def test_relationships_including_inactive():
    relationships = parse_relationships(RELATIONSHIPS)
    assert len(relationships) == 2
    assert relationships[0].from_column == "'Fact Transactions'.'Geography Key'"
    assert relationships[0].active is True and relationships[1].active is False
