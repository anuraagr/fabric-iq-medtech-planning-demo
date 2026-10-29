"""PowerTable column rules (T-15): what the saved plan definition says about required fields, closed value lists, lookups and numeric bounds,
and whether the rows persisted in Fabric SQL conform to them.

The definition proves that the rules are configured; conformance of the stored rows proves that nothing slipped past them. Enforcement itself (the
record form refusing an out-of-range value) is UI behaviour: it is recorded as evidence, not asserted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence

from .writeback import parse_table_ref

SINGLE_SELECT = 2  # PowerTable ColumnType


@dataclass(frozen=True)
class Lookup:
    table: str  # schema.table of the lookup source
    key: str
    label: str


@dataclass(frozen=True)
class Rules:
    sheet: str
    table: str  # schema.table behind the sheet
    required: tuple[str, ...]
    closed_lists: tuple[str, ...]  # single select columns that do not accept new options
    lookups: Mapping[str, Lookup]
    bounds: Mapping[str, tuple[Decimal | None, Decimal | None]]

    @classmethod
    def from_config(cls, raw: Mapping[str, Any]) -> "Rules | None":
        block = (raw.get("medtech") or {}).get("powertable_rules")
        if not block:
            return None
        lookups = {
            str(column): Lookup(f"{spec.get('schema', 'dbo')}.{spec['table']}", str(spec["key"]), str(spec["label"]))
            for column, spec in (block.get("lookups") or {}).items()
        }
        bounds = {
            str(column): (_number(spec.get("min")), _number(spec.get("max")))
            for column, spec in (block.get("bounds") or {}).items()
        }
        return cls(
            sheet=str(block["sheet"]),
            table=f"{block.get('schema', 'dbo')}.{block['table']}",
            required=tuple(str(c) for c in block.get("required") or ()),
            closed_lists=tuple(str(c) for c in block.get("closed_lists") or ()),
            lookups=lookups,
            bounds=bounds,
        )


def _number(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {value!r}") from exc


@dataclass(frozen=True)
class Check:
    label: str
    ok: bool
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.label}: {'ok' if self.ok else 'MISMATCH'}" + (f" ({self.detail})" if self.detail else "")


def check_definition(rules: Rules, column_configs: Sequence[Mapping[str, Any]]) -> list[Check]:
    """Compare the rules with the `columnConfigs` of the PowerTable sheet in the exported plan definition."""
    by_name = {str(c.get("columnName")): c for c in column_configs}

    def column(name: str) -> Mapping[str, Any] | None:
        return by_name.get(name)

    checks: list[Check] = []
    for name in rules.required:
        config = column(name)
        checks.append(Check(f"required {name}", bool(config and config.get("mandatory") == 1), "" if config else "column not found"))
    for name in rules.closed_lists:
        config = column(name)
        meta = (config or {}).get("columnMeta") or {}
        ok = bool(config and config.get("columnType") == SINGLE_SELECT and meta.get("allowAddingNewOptions") is False)
        checks.append(Check(f"closed value list {name}", ok, f"selection {meta.get('selectionMethod')}" if config else "column not found"))
    for name, lookup in rules.lookups.items():
        config = column(name)
        meta = (config or {}).get("columnMeta") or {}
        entries = (meta.get("optionLinking") or {}).get("lookupConfig") or []
        schema, table = _split(lookup.table)
        found = any(
            e.get("schema") == schema and e.get("table") == table and e.get("id") == lookup.key and e.get("label") == lookup.label and e.get("linkingColumn") == name
            for e in entries
        )
        ok = bool(config and config.get("columnType") == SINGLE_SELECT and found)
        checks.append(Check(f"lookup {name} -> {lookup.table}({lookup.key} / {lookup.label})", ok, "" if config else "column not found"))
    for name, (low, high) in rules.bounds.items():
        config = column(name)
        meta = (config or {}).get("columnMeta") or {}
        ok = bool(config) and _number(meta.get("min")) == low and _number(meta.get("max")) == high
        checks.append(Check(f"bounds {name} {_range(low, high)}", ok, f"saved min {meta.get('min')!r} max {meta.get('max')!r}" if config else "column not found"))
    return checks


def _split(table: str) -> tuple[str, str]:
    ref = parse_table_ref(table)
    return ref.schema, ref.table


def _range(low: Decimal | None, high: Decimal | None) -> str:
    if low is not None and high is not None:
        return f"{low}..{high}"
    if low is not None:
        return f">= {low}"
    return f"<= {high}"


def _quote(name: str) -> str:
    return "[" + str(name).replace("]", "]]") + "]"


def data_checks(rules: Rules) -> list[tuple[str, str]]:
    """(label, T-SQL) pairs: each statement returns the number of stored rows that violate one rule (0 = conforming). Read-only SELECTs."""
    table = parse_table_ref(rules.table).sql()
    statements: list[tuple[str, str]] = []
    for name in rules.required:
        column = _quote(name)
        statements.append((f"blank {name}", f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL OR LTRIM(RTRIM(CAST({column} AS NVARCHAR(4000)))) = ''"))
    for name, lookup in rules.lookups.items():
        column, source = _quote(name), parse_table_ref(lookup.table).sql()
        statements.append((
            f"{name} without a matching {lookup.table}",
            f"SELECT COUNT(*) FROM {table} t WHERE t.{column} IS NOT NULL AND NOT EXISTS (SELECT 1 FROM {source} l WHERE l.{_quote(lookup.key)} = t.{column})",
        ))
    for name, (low, high) in rules.bounds.items():
        column = _quote(name)
        conditions = []
        if low is not None:
            conditions.append(f"{column} < {low}")
        if high is not None:
            conditions.append(f"{column} > {high}")
        statements.append((f"{name} outside {_range(low, high)}", f"SELECT COUNT(*) FROM {table} WHERE {' OR '.join(conditions)}"))
    return statements


def distinct_values_sql(rules: Rules, column: str) -> str:
    return f"SELECT {_quote(column)} AS v FROM {parse_table_ref(rules.table).sql()} WHERE {_quote(column)} IS NOT NULL GROUP BY {_quote(column)} ORDER BY 1"


def summarize(rules: Rules, definition: Sequence[Check], violations: Mapping[str, int], rows: int, distinct: Mapping[str, Sequence[str]]) -> tuple[str, str]:
    bad_definition = [str(c) for c in definition if not c.ok]
    bad_rows = {label: n for label, n in violations.items() if n}
    status = "failed" if bad_definition or bad_rows else "passed"
    lists = "; ".join(f"{name} = {list(values)}" for name, values in distinct.items())
    observed = (
        f"sheet {rules.sheet!r} ({rules.table}): {sum(c.ok for c in definition)}/{len(definition)} rules saved in the plan definition "
        f"(required: {', '.join(rules.required) or '-'}; closed lists: {', '.join(rules.closed_lists) or '-'}; lookups: {', '.join(rules.lookups) or '-'}; "
        f"bounds: {', '.join(rules.bounds) or '-'}); {rows} stored rows, {sum(violations.values())} violating a rule"
        + (f"; stored values {lists}" if lists else "")
    )
    if bad_definition:
        observed += f"; definition mismatches: {bad_definition}"
    if bad_rows:
        observed += f"; violating rows: {bad_rows}"
    return status, observed
