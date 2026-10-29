"""Planning cell comments (T-16).

A comment added to a planning-sheet cell (Comments > Add Comment) is saved with the plan and, once the sheet is written back, stored in the `IrComments` column of
that cell's writeback row as a JSON list `[{"comment", "updatedByUser", "dateTime", "threadStatus", "threadAssignee"}]`. A comment-only writeback adds a new latest
row with the value unchanged (`Value` = `PreviousValue`) and supersedes the cell's older row.

The check proves that the comment sits on the intended cell, that no thread is assigned and nothing is @mentioned (either one notifies a real person through
Teams or e-mail) and that the number did not move. The author's identity is deliberately not part of the observation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .finance import D

SELECT_COLUMNS = ("Region", "Category", "Year", "Quarter", "MonthShort", "ValueColumnName", "Value", "PreviousValue", "IrComments")


@dataclass(frozen=True)
class CommentSpec:
    table: str  # schema.table of the writeback table that holds the commented cell
    series: str  # the writeback series (ValueColumnName) of the cell
    region: str
    category: str
    year: str
    month: str
    contains: str  # text the comment must contain

    @classmethod
    def from_config(cls, raw: Mapping[str, Any]) -> "CommentSpec | None":
        block = (raw.get("medtech") or {}).get("comment_check")
        if not block:
            return None
        leaf = block["leaf"]
        return cls(
            table=str(block["table"]), series=str(block["series"]), region=str(leaf["region"]), category=str(leaf["category"]),
            year=str(leaf["year"]), month=str(leaf["month"]), contains=str(block["contains"]),
        )


def select_sql(table_sql: str) -> str:
    """Latest rows that carry a comment (read-only); `table_sql` is the quoted table reference."""
    return f"SELECT {', '.join(SELECT_COLUMNS)} FROM {table_sql} WHERE IsLatest = 1 AND IrComments IS NOT NULL"


def parse_comments(text: Any) -> list[dict[str, Any]]:
    """The comment objects of one row. Raises ValueError for anything that is not a JSON list of objects."""
    try:
        value = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"not valid JSON ({exc})") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("not a JSON list of comment objects")
    return value


def _cell(row: Mapping[str, Any]) -> str:
    return f"{row['Region']} / {row['Category']} / {row['Year']} {row['Quarter']} {row['MonthShort']} [{row['ValueColumnName']}]"


def _is_target(row: Mapping[str, Any], spec: CommentSpec) -> bool:
    return (row["Region"], row["Category"], str(row["Year"]), row["MonthShort"], row["ValueColumnName"]) == (spec.region, spec.category, spec.year, spec.month, spec.series)


def evaluate(rows: Sequence[Mapping[str, Any]], spec: CommentSpec) -> tuple[list[str], str]:
    """Return (problems, observed). `rows` are the latest rows of the table that have a comment."""
    problems: list[str] = []
    assigned: list[str] = []
    mentioned: list[str] = []
    target_comments: list[dict[str, Any]] = []
    targets = [row for row in rows if _is_target(row, spec)]
    for row in rows:
        try:
            items = parse_comments(row["IrComments"])
        except ValueError as exc:
            problems.append(f"IrComments of {_cell(row)} is {exc}")
            continue
        for item in items:
            if item.get("threadAssignee"):
                assigned.append(_cell(row))
            if "@" in str(item.get("comment", "")):
                mentioned.append(_cell(row))
        if row in targets:
            target_comments = items
    where = f"{spec.region} / {spec.category} / {spec.year} {spec.month} [{spec.series}]"
    if len(targets) != 1:
        problems.append(f"expected exactly one latest commented row for {where}, found {len(targets)}")
    elif not any(spec.contains.lower() in str(item.get("comment", "")).lower() for item in target_comments):
        problems.append(f"no comment on {where} contains {spec.contains!r}")
    unchanged = None
    shown = "n/a"
    if len(targets) == 1:
        value, previous = targets[0]["Value"], targets[0]["PreviousValue"]
        if value is None or previous is None:
            problems.append(f"Value or PreviousValue of {where} is NULL (was the comment saved before the cell's first writeback?)")
        else:
            shown = f"{D(value):,.2f}"
            unchanged = D(value) == D(previous)
            if not unchanged:
                problems.append(f"the comment write changed the value of {where}: {previous} -> {value}")
    if assigned:
        problems.append(f"comment threads are assigned to a person: {sorted(set(assigned))}")
    if mentioned:
        problems.append(f"comments contain an @mention: {sorted(set(mentioned))}")
    text = target_comments[0].get("comment") if target_comments else None
    observed = (
        f"{len(rows)} commented cell(s) in {spec.table} (latest rows); {where}: {len(target_comments)} comment(s), value {shown}"
        f"{' unchanged by the comment' if unchanged else ''}; assigned threads: {len(assigned)}; @mentions: {len(mentioned)}; text: {text!r}"
    )
    return problems, observed
