"""Row-level before/after comparison of a PowerTable (Fabric SQL) table, keyed by a business key.

T-07 expects exactly the edited field of exactly one row to change, exactly the inserted row to appear and nothing
else to move. Values are normalised to strings so SQL types (date, float, int) compare the way a person would read them.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

Row = dict[str, str]


class PowerTableError(ValueError):
    pass


def normalise(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (Decimal, float)):
        text = format(Decimal(str(value)).normalize(), "f")
        return text
    return str(value).strip()


def snapshot_rows(rows: Sequence[Mapping[str, Any]], key_column: str) -> dict[str, Row]:
    """{key: {column: normalised value}}; duplicate keys are an error, never silently collapsed."""
    result: dict[str, Row] = {}
    for row in rows:
        key = normalise(row.get(key_column))
        if not key:
            raise PowerTableError(f"A row has an empty {key_column!r}; choose a key that is always populated")
        if key in result:
            raise PowerTableError(f"Duplicate key {key!r} in column {key_column!r}")
        result[key] = {column: normalise(value) for column, value in row.items()}
    return result


@dataclass
class TableDiff:
    added: dict[str, Row] = field(default_factory=dict)
    removed: dict[str, Row] = field(default_factory=dict)
    changed: dict[str, dict[str, tuple[str, str]]] = field(default_factory=dict)


def diff_rows(before: Mapping[str, Row], after: Mapping[str, Row]) -> TableDiff:
    diff = TableDiff()
    for key, row in after.items():
        if key not in before:
            diff.added[key] = dict(row)
            continue
        fields = {c: (before[key].get(c, ""), v) for c, v in row.items() if before[key].get(c, "") != v}
        if fields:
            diff.changed[key] = fields
    diff.removed = {key: dict(row) for key, row in before.items() if key not in after}
    return diff


def parse_expectation(text: str) -> tuple[str, dict[str, str]]:
    """'MD-1248|Status=Under Maintenance|Location=1' -> ('MD-1248', {'Status': 'Under Maintenance', 'Location': '1'})."""
    key, *pairs = text.split("|")
    if not key.strip():
        raise PowerTableError(f"Expectation {text!r} has no key before the first '|'")
    values: dict[str, str] = {}
    for pair in pairs:
        match = re.fullmatch(r"([^=]+)=(.*)", pair)
        if not match:
            raise PowerTableError(f"Expectation part {pair!r} must look like Column=Value")
        values[match.group(1).strip()] = match.group(2).strip()
    return key.strip(), values


def check_expectations(diff: TableDiff, updates: Mapping[str, Mapping[str, str]], inserts: Mapping[str, Mapping[str, str]]) -> list[str]:
    """Problems; an empty list means the table changed exactly as expected."""
    problems: list[str] = []
    for key, expected in updates.items():
        actual = diff.changed.get(key)
        if actual is None:
            problems.append(f"{key}: no change found, expected {dict(expected)}")
            continue
        for column, value in expected.items():
            if column not in actual or actual[column][1] != value:
                problems.append(f"{key}: {column} expected {value!r}, got {actual.get(column, ('', 'unchanged'))[1]!r}")
        unexpected = sorted(set(actual) - set(expected))
        if unexpected:
            problems.append(f"{key}: unexpected additional changes in {unexpected}")
    for key in diff.changed.keys() - updates.keys():
        problems.append(f"{key}: changed but was not expected to ({sorted(diff.changed[key])})")
    for key, expected in inserts.items():
        row = diff.added.get(key)
        if row is None:
            problems.append(f"{key}: inserted row not found")
            continue
        for column, value in expected.items():
            if row.get(column) != value:
                problems.append(f"{key}: inserted {column} expected {value!r}, got {row.get(column)!r}")
    for key in diff.added.keys() - inserts.keys():
        problems.append(f"{key}: row was added but not expected")
    if diff.removed:
        problems.append(f"rows removed: {sorted(diff.removed)}")
    return problems


def save_snapshot(path: Path, table: str, key_column: str, rows: Mapping[str, Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"table": table, "key": key_column, "rows": rows}, indent=1), encoding="utf-8")


def load_snapshot(path: Path) -> tuple[dict[str, Any], dict[str, Row]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload, payload["rows"]
