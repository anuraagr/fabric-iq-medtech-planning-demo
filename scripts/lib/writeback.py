"""Helpers for verifying native Planning writeback with T-SQL.

The destination table is created by Planning at first writeback, so nothing here assumes its schema:
column names are validated against INFORMATION_SCHEMA before being bracket-quoted into a read-only SELECT.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from .finance import D, find_duplicate_grains
from .sqlclient import IDENTIFIER_RE, SqlSession, assert_read_only

FORBIDDEN_SYSTEM_DB_PREFIX = "__fabric_plan_sys"


class WritebackError(ValueError):
    pass


@dataclass(frozen=True)
class TableRef:
    schema: str
    table: str

    def sql(self) -> str:
        return f"[{self.schema}].[{self.table}]"

    def __str__(self) -> str:
        return f"{self.schema}.{self.table}"


def parse_table_ref(text: str) -> TableRef:
    cleaned = text.replace("[", "").replace("]", "")
    parts = cleaned.split(".")
    if len(parts) != 2 or not all(IDENTIFIER_RE.match(p) for p in parts):
        raise WritebackError(f"Table must look like schema.table with plain identifiers, got {text!r}")
    return TableRef(parts[0], parts[1])


def quote_column(name: str, known: set[str]) -> str:
    if name not in known:
        raise WritebackError(f"Column {name!r} does not exist in the destination table (known: {sorted(known)})")
    return "[" + name.replace("]", "]]") + "]"


def build_select(table: TableRef, grain: Sequence[str], value: str, known: set[str], *, latest_column: str | None = None, where: str | None = None) -> str:
    columns = [quote_column(c, known) for c in (*grain, value)]
    sql = f"SELECT {', '.join(columns)} FROM {table.sql()}"
    clauses = []
    if latest_column:
        clauses.append(f"{quote_column(latest_column, known)} = 1")
    if where:
        clauses.append(f"({where})")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    assert_read_only(sql)
    return sql


def read_rows(session: SqlSession, table: TableRef, grain: Sequence[str], value: str, *, latest_column: str | None = None, where: str | None = None) -> list[dict[str, Any]]:
    if table.table.startswith(FORBIDDEN_SYSTEM_DB_PREFIX):
        raise WritebackError("Refusing to read product-managed system tables")
    known = {c["COLUMN_NAME"] for c in session.table_columns(table.schema, table.table)}
    if not known:
        raise WritebackError(f"Table {table} does not exist (has the first writeback run?)")
    return session.query(build_select(table, grain, value, known, latest_column=latest_column, where=where))


def snapshot(rows: Sequence[dict[str, Any]], grain: Sequence[str], value: str) -> tuple[dict[tuple, Decimal], dict[tuple, int]]:
    """Return ({grain key: value}, {duplicate grain: count}). Duplicates are never silently collapsed."""
    duplicates = find_duplicate_grains(rows, grain)
    values: dict[tuple, Decimal] = {}
    for row in rows:
        key = tuple("" if row.get(c) is None else str(row.get(c)) for c in grain)
        raw = row.get(value)
        if raw is not None and str(raw).strip() != "":
            values[key] = D(raw)
    return values, {tuple("" if v is None else str(v) for v in k): n for k, n in duplicates.items()}


def parse_expect(items: Sequence[str]) -> dict[tuple, Decimal]:
    """Parse 'k1|k2|k3=123.45' strings into {('k1','k2','k3'): Decimal('123.45')}."""
    expected: dict[tuple, Decimal] = {}
    for item in items:
        match = re.fullmatch(r"(.+)=(-?[0-9][0-9_,]*\.?[0-9]*)", item.strip())
        if not match:
            raise WritebackError(f"--expect must look like 'a|b|c=123.45', got {item!r}")
        expected[tuple(match.group(1).split("|"))] = D(match.group(2).replace(",", "").replace("_", ""))
    return expected


def save_snapshot(path: Path, table: TableRef, grain: Sequence[str], value: str, values: dict[tuple, Decimal]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"table": str(table), "grain": list(grain), "value": value, "rows": [{"key": list(k), "value": str(v)} for k, v in sorted(values.items())]}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_snapshot(path: Path) -> tuple[dict[str, Any], dict[tuple, Decimal]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload, {tuple(r["key"]): D(r["value"]) for r in payload["rows"]}
