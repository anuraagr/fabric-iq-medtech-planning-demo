"""PowerTable column rules (T-15): configuration parsing, comparison with the saved column configuration and the SQL conformance checks."""

from __future__ import annotations

from decimal import Decimal

import pytest

from scripts.lib import powertable_rules as pr

RAW = {"medtech": {"powertable_rules": {
    "sheet": "Assets", "table": "assets",
    "required": ["Asset Tag", "Status"],
    "closed_lists": ["Status"],
    "lookups": {"Assigned To": {"table": "employees", "key": "Employee Id", "label": "Full Name"}, "Location": {"table": "locations", "key": "Location Id", "label": "Location"}},
    "bounds": {"Expected Lifetime In Years": {"min": 1, "max": 30}, "Purchase Price": {"min": 0}},
}}}
RULES = pr.Rules.from_config(RAW)


def column(name, *, column_type=1, mandatory=0, **meta):
    return {"columnName": name, "columnType": column_type, "mandatory": mandatory, "columnMeta": meta}


def lookup(name, table, key, label):
    return column(name, column_type=2, optionLinking={"lookupConfig": [{"id": key, "label": label, "linkingColumn": name, "order": 0, "schema": "dbo", "table": table}]},
                  selectionMethod="LOAD_FROM_DATABASE", allowAddingNewOptions=False)


def saved(**overrides):
    """The configuration as exported from the plan after the rules were set in the UI."""
    columns = {
        "Asset Tag": column("Asset Tag", mandatory=1),
        "Status": column("Status", column_type=2, mandatory=1, selectionMethod="DISTINCT_VALUES", allowAddingNewOptions=False),
        "Assigned To": lookup("Assigned To", "employees", "Employee Id", "Full Name"),
        "Location": lookup("Location", "locations", "Location Id", "Location"),
        "Expected Lifetime In Years": column("Expected Lifetime In Years", column_type=6, min="1", max="30"),
        "Purchase Price": column("Purchase Price", column_type=13, min="0", max=""),
    }
    columns.update(overrides)
    return [c for c in columns.values() if c is not None]


def test_rules_are_read_from_the_configuration_and_absent_without_a_block():
    assert RULES.sheet == "Assets" and RULES.table == "dbo.assets"
    assert RULES.required == ("Asset Tag", "Status") and RULES.closed_lists == ("Status",)
    assert RULES.lookups["Location"] == pr.Lookup("dbo.locations", "Location Id", "Location")
    assert RULES.bounds["Expected Lifetime In Years"] == (Decimal("1"), Decimal("30")) and RULES.bounds["Purchase Price"] == (Decimal("0"), None)
    assert pr.Rules.from_config({}) is None and pr.Rules.from_config({"medtech": {}}) is None
    with pytest.raises(ValueError):
        pr.Rules.from_config({"medtech": {"powertable_rules": {"sheet": "A", "table": "t", "bounds": {"x": {"min": "abc"}}}}})


def test_the_saved_configuration_satisfies_every_rule():
    checks = pr.check_definition(RULES, saved())
    assert len(checks) == 2 + 1 + 2 + 2 and all(c.ok for c in checks), [str(c) for c in checks if not c.ok]


@pytest.mark.parametrize("override, label", [
    ({"Asset Tag": column("Asset Tag", mandatory=0)}, "required Asset Tag"),
    ({"Status": column("Status", column_type=1, mandatory=1)}, "closed value list Status"),  # still free text
    ({"Status": column("Status", column_type=2, mandatory=1, allowAddingNewOptions=True)}, "closed value list Status"),  # new options allowed
    ({"Assigned To": lookup("Assigned To", "assets", "Employee Id", "Full Name")}, "lookup Assigned To"),  # wrong source table
    ({"Location": lookup("Location", "locations", "Location Id", "Description")}, "lookup Location"),  # wrong display column
    ({"Expected Lifetime In Years": column("Expected Lifetime In Years", column_type=6, min="1", max="40")}, "bounds Expected Lifetime In Years"),
    ({"Purchase Price": column("Purchase Price", column_type=13, min="", max="")}, "bounds Purchase Price"),
    ({"Purchase Price": None}, "bounds Purchase Price"),  # the column is missing altogether
])
def test_any_rule_that_is_missing_or_different_in_the_definition_is_reported(override, label):
    failed = [c for c in pr.check_definition(RULES, saved(**override)) if not c.ok]
    assert [c.label.startswith(label) for c in failed] == [True], [str(c) for c in failed]


def test_data_checks_are_read_only_counts_with_quoted_identifiers():
    statements = dict(pr.data_checks(RULES))
    assert statements["blank Asset Tag"] == "SELECT COUNT(*) FROM [dbo].[assets] WHERE [Asset Tag] IS NULL OR LTRIM(RTRIM(CAST([Asset Tag] AS NVARCHAR(4000)))) = ''"
    orphan = statements["Assigned To without a matching dbo.employees"]
    assert "NOT EXISTS (SELECT 1 FROM [dbo].[employees] l WHERE l.[Employee Id] = t.[Assigned To])" in orphan and "t.[Assigned To] IS NOT NULL" in orphan
    assert statements["Expected Lifetime In Years outside 1..30"].endswith("WHERE [Expected Lifetime In Years] < 1 OR [Expected Lifetime In Years] > 30")
    assert statements["Purchase Price outside >= 0"].endswith("WHERE [Purchase Price] < 0")  # a one-sided bound has one condition
    assert all(sql.startswith("SELECT COUNT(*) FROM") for sql in statements.values()) and len(statements) == 2 + 2 + 2


def test_identifiers_are_escaped_and_tables_must_be_plain():
    assert pr._quote("a]b") == "[a]]b]"
    hostile = pr.Rules.from_config({"medtech": {"powertable_rules": {"sheet": "S", "table": "assets; DROP TABLE x", "required": ["A"]}}})
    with pytest.raises(Exception):
        pr.data_checks(hostile)
    assert pr.distinct_values_sql(RULES, "Status") == "SELECT [Status] AS v FROM [dbo].[assets] WHERE [Status] IS NOT NULL GROUP BY [Status] ORDER BY 1"


def test_a_summary_passes_only_when_the_definition_and_the_rows_conform():
    ok = pr.check_definition(RULES, saved())
    clean = {label: 0 for label, _ in pr.data_checks(RULES)}
    status, observed = pr.summarize(RULES, ok, clean, 100, {"Status": ["Available", "In Service"]})
    assert status == "passed" and "7/7 rules saved in the plan definition" in observed and "100 stored rows, 0 violating a rule" in observed
    assert "Status = ['Available', 'In Service']" in observed

    dirty = dict(clean, **{"blank Asset Tag": 2})
    status, observed = pr.summarize(RULES, ok, dirty, 100, {})
    assert status == "failed" and "violating rows: {'blank Asset Tag': 2}" in observed

    status, observed = pr.summarize(RULES, pr.check_definition(RULES, saved(**{"Asset Tag": column("Asset Tag", mandatory=0)})), clean, 100, {})
    assert status == "failed" and "definition mismatches" in observed and "required Asset Tag" in observed
