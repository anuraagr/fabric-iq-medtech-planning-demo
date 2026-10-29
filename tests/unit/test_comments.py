import json
from types import SimpleNamespace

import pytest

from scripts import validate
from scripts.lib import comments as cm

SPEC = cm.CommentSpec(
    table="dbo.bud", series="FY2026 Revenue Budget", region="Europe", category="Diagnostics & Imaging", year="2026", month="Oct", contains="diagnostics launch",
)
RAW = {"medtech": {"comment_check": {
    "table": "dbo.bud", "series": "FY2026 Revenue Budget", "contains": "diagnostics launch",
    "leaf": {"region": "Europe", "category": "Diagnostics & Imaging", "year": "2026", "month": "Oct"},
}}}


def thread(text="Includes the diagnostics launch; owner Europe planner", assignee=None):
    return json.dumps([{"comment": text, "updatedByUser": "someone", "dateTime": "2026-10-23 07:57:20 (UTC)", "threadStatus": 10, "threadAssignee": assignee}])


def row(comments=None, *, region="Europe", category="Diagnostics & Imaging", month="Oct", series="FY2026 Revenue Budget", value="1172743.48", previous="1172743.48"):
    return {
        "Region": region, "Category": category, "Year": "2026", "Quarter": "Q4", "MonthShort": month, "ValueColumnName": series,
        "Value": value, "PreviousValue": previous, "IrComments": thread() if comments is None else comments,
    }


def test_from_config_reads_the_block_and_is_none_without_it():
    assert cm.CommentSpec.from_config(RAW) == SPEC
    assert cm.CommentSpec.from_config({"medtech": {}}) is None


def test_select_sql_reads_only_latest_commented_rows():
    sql = cm.select_sql("[dbo].[bud]")
    assert sql.startswith("SELECT Region, Category") and "FROM [dbo].[bud]" in sql and "IsLatest = 1 AND IrComments IS NOT NULL" in sql
    assert not any(word in sql.upper() for word in ("UPDATE", "DELETE", "INSERT"))


def test_parse_comments_accepts_a_list_of_objects_only():
    assert cm.parse_comments(thread())[0]["comment"].startswith("Includes")
    for bad in ("not json", '{"comment": "x"}', "[1, 2]", None):
        with pytest.raises(ValueError):
            cm.parse_comments(bad)


def test_a_clean_comment_on_the_target_cell_passes_with_an_observation_that_names_no_person():
    problems, observed = cm.evaluate([row()], SPEC)
    assert problems == []
    assert "1 commented cell(s)" in observed and "Europe / Diagnostics & Imaging / 2026 Oct" in observed and "unchanged by the comment" in observed
    assert "assigned threads: 0" in observed and "@mentions: 0" in observed and "someone" not in observed


def test_the_comment_must_be_on_the_target_cell_and_contain_the_expected_text():
    problems, _ = cm.evaluate([row(month="Nov")], SPEC)
    assert any("exactly one latest commented row" in p for p in problems)
    problems, _ = cm.evaluate([row(thread("something else"))], SPEC)
    assert any("contains 'diagnostics launch'" in p for p in problems)
    problems, _ = cm.evaluate([row(), row()], SPEC)
    assert any("found 2" in p for p in problems)


def test_an_assignee_or_a_mention_anywhere_in_the_table_fails_the_check():
    other = row(thread("ping @someone", assignee=None), month="Nov")
    problems, observed = cm.evaluate([row(), other], SPEC)
    assert any("@mention" in p for p in problems) and "@mentions: 1" in observed
    assigned = row(thread(assignee="a.person@example.com"), month="Dec")
    problems, observed = cm.evaluate([row(), assigned], SPEC)
    assert any("assigned to a person" in p for p in problems) and "assigned threads: 1" in observed


def test_a_comment_write_that_moved_the_value_fails():
    problems, observed = cm.evaluate([row(value="1200000", previous="1172743.48")], SPEC)
    assert any("changed the value" in p for p in problems) and "unchanged by the comment" not in observed


def test_malformed_comments_are_reported_not_raised():
    problems, _ = cm.evaluate([row("garbage")], SPEC)
    assert any("IrComments of Europe" in p for p in problems)


def test_a_null_value_or_previous_value_is_reported_not_raised():
    # a comment saved before the cell's first writeback can leave PreviousValue NULL; the run must record a failure, not crash
    for broken in (row(previous=None), row(value=None), row(value=None, previous=None)):
        problems, observed = cm.evaluate([broken], SPEC)
        assert any("is NULL" in p for p in problems), broken
        assert "unchanged by the comment" not in observed


# --- the runner ---------------------------------------------------------------------------------------------------------------------------
def comment_context(rows, raw=RAW):
    class Session:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def query(self, sql, params=()):
            assert sql == cm.select_sql("[dbo].[bud]")
            return rows

    return SimpleNamespace(cfg=SimpleNamespace(raw=raw, names=SimpleNamespace(writeback_db="Writeback")), sql_session=lambda database: Session())


def test_t16_passes_fails_or_is_not_executed():
    status, observed = validate.t16(comment_context([row()]))
    assert status == "passed" and "assigned threads: 0" in observed
    status, observed = validate.t16(comment_context([row(thread(assignee="a@b.c"))]))
    assert status == "failed" and "problems" in observed
    assert validate.t16(comment_context([]))[0] == "not_executed"  # nothing was written back yet
    assert validate.t16(comment_context([row()], raw={"medtech": {}}))[0] == "not_executed"  # not configured
