"""Minimal TMDL reader used to produce a sanitized schema summary of a semantic model.

It extracts only structural facts (tables, columns, data types, measures + DAX, partition kinds,
relationships). It deliberately ignores model/table descriptions, partition source expressions
(Power Query / file paths) and annotations so that nothing personal or environment-specific leaks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Column:
    name: str
    data_type: str = ""
    summarize_by: str = ""
    hidden: bool = False
    calculated: bool = False


@dataclass
class Measure:
    name: str
    expression: str = ""
    format_string: str = ""


@dataclass
class Partition:
    name: str
    kind: str = ""  # m | calculated | entity
    mode: str = ""


@dataclass
class Table:
    name: str
    hidden: bool = False
    columns: list[Column] = field(default_factory=list)
    measures: list[Measure] = field(default_factory=list)
    partitions: list[Partition] = field(default_factory=list)


@dataclass
class Relationship:
    from_column: str
    to_column: str
    cross_filter: str = "single"
    active: bool = True


def _read_name(rest: str) -> tuple[str, str]:
    """Return (unquoted name, remainder) from the text after a TMDL object keyword."""
    rest = rest.strip()
    if rest.startswith("'"):
        i = 1
        out = []
        while i < len(rest):
            if rest[i] == "'" and i + 1 < len(rest) and rest[i + 1] == "'":
                out.append("'")
                i += 2
            elif rest[i] == "'":
                return "".join(out), rest[i + 1:].strip()
            else:
                out.append(rest[i])
                i += 1
        return "".join(out), ""
    match = re.match(r"([^\s=]+)\s*(.*)", rest)
    return (match.group(1), match.group(2)) if match else (rest, "")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip("\t"))


def _dedent_expression(lines: list[str]) -> str:
    cleaned = [line.rstrip() for line in lines]
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    if not cleaned:
        return ""
    base = min(_indent(ln) for ln in cleaned if ln.strip())
    return "\n".join(ln[base:] if ln.strip() else "" for ln in cleaned).strip("`\n ")


def parse_table(text: str) -> Table:
    lines = text.splitlines()
    table: Table | None = None
    current: object | None = None
    expr_lines: list[str] = []
    expr_owner: Measure | None = None

    def flush_expression() -> None:
        nonlocal expr_lines, expr_owner
        if expr_owner is not None:
            expr_owner.expression = _dedent_expression(expr_lines)
        expr_lines, expr_owner = [], None

    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("///"):
            if expr_owner is not None:
                expr_lines.append(raw)
            continue
        level = _indent(raw)
        content = raw.strip()
        if level == 0:
            if content.startswith("table "):
                name, _ = _read_name(content[len("table "):])
                table = Table(name=name)
            continue
        if table is None:
            continue
        if level == 1:
            flush_expression()
            current = None
            keyword, _, rest = content.partition(" ")
            if keyword == "column":
                name, remainder = _read_name(rest)
                col = Column(name=name, calculated=remainder.startswith("="))
                table.columns.append(col)
                current = col
            elif keyword == "measure":
                name, remainder = _read_name(rest)
                measure = Measure(name=name)
                table.measures.append(measure)
                current = measure
                expr_owner = measure
                if remainder.startswith("="):
                    first = remainder[1:].strip()
                    expr_lines = [first] if first else []
            elif keyword == "partition":
                name, remainder = _read_name(rest)
                part = Partition(name=name, kind=remainder.lstrip("= ").strip())
                table.partitions.append(part)
                current = part
            elif content == "isHidden":
                table.hidden = True
            continue
        if level == 2:
            if isinstance(current, Measure) and expr_owner is current and not re.match(r"^(lineageTag|formatString|displayFolder|isHidden|description|annotation|changedProperty|summarizeBy|dataType)\b", content):
                expr_lines.append(raw)
                continue
            flush_expression()
            key, _, value = content.partition(":")
            key, value = key.strip(), value.strip()
            if isinstance(current, Column):
                if key == "dataType":
                    current.data_type = value
                elif key == "summarizeBy":
                    current.summarize_by = value
                elif content == "isHidden":
                    current.hidden = True
            elif isinstance(current, Measure) and key == "formatString":
                current.format_string = value
            elif isinstance(current, Partition) and key == "mode":
                current.mode = value
            continue
        if expr_owner is not None:
            expr_lines.append(raw)
    flush_expression()
    if table is None:
        raise ValueError("No 'table' declaration found")
    return table


def parse_relationships(text: str) -> list[Relationship]:
    relationships: list[Relationship] = []
    current: Relationship | None = None
    for raw in text.splitlines():
        content = raw.strip()
        if content.startswith("relationship "):
            current = Relationship("", "")
            relationships.append(current)
        elif current is not None and ":" in content:
            key, _, value = content.partition(":")
            value = value.strip()
            if key == "fromColumn":
                current.from_column = value
            elif key == "toColumn":
                current.to_column = value
            elif key == "crossFilteringBehavior":
                current.cross_filter = value
            elif key == "isActive":
                current.active = value.lower() != "false"
    return relationships
