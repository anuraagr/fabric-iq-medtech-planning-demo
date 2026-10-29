"""Effective permissions of the signed-in identity at each layer (support for T-11, role-based security).

Planning needs every layer to allow an action: Entra identity, workspace role, plan item, semantic model, SQL database and the
native Planning / PowerTable roles. Run `python scripts/permissions.py` once per test identity (each with its own `AZURE_CONFIG_DIR`);
the output is the evidence for that identity. Everything here is read-only; the native Planning and PowerTable security screens have no
read API and are reported as `manual`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

ALLOWED, DENIED, UNKNOWN, MANUAL = "allowed", "denied", "unknown", "manual"
SQL_VERBS = ("SELECT", "INSERT", "UPDATE", "DELETE")
_TABLE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}\.[A-Za-z_][A-Za-z0-9_]{0,127}$")
_DENIED_SQLSTATES = {"28000", "42000"}  # login failed / permission denied


@dataclass(frozen=True)
class LayerResult:
    layer: str
    check: str
    outcome: str  # allowed | denied | unknown | manual
    detail: str = ""

    def __str__(self) -> str:
        return f"{self.layer}: {self.check} -> {self.outcome}" + (f" ({self.detail})" if self.detail else "")


def classify_error(exc: BaseException) -> tuple[str, str]:
    """Map an API or ODBC error to (denied | unknown, detail). Only an explicit 401/403 or a SQL login/permission error counts as a denial."""
    status = getattr(exc, "status", None)
    if status in (401, 403):
        return DENIED, f"HTTP {status}"
    state = str(exc.args[0]) if exc.args else ""
    if state in _DENIED_SQLSTATES:
        return DENIED, f"SQLSTATE {state}"
    return UNKNOWN, f"{type(exc).__name__}: {str(exc)[:140]}"


def probe(layer: str, check: str, action: Callable[[], str | None]) -> LayerResult:
    """Run one read-only check; a failure is reported in the matrix instead of aborting the other layers."""
    try:
        return LayerResult(layer, check, ALLOWED, action() or "")
    except Exception as exc:  # noqa: BLE001 - the matrix must show what could not be checked
        outcome, detail = classify_error(exc)
        return LayerResult(layer, check, outcome, detail)


def sql_probe_query(tables: Sequence[str]) -> str:
    """One SELECT reporting the caller's database role and its table permissions (HAS_PERMS_BY_NAME: 1 = granted, 0 = not granted, NULL = object not visible)."""
    columns = ["USER_NAME() AS db_user", "IS_ROLEMEMBER('db_owner') AS db_owner", "HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'CONTROL') AS db_control"]
    for index, table in enumerate(tables):
        if not _TABLE_RE.match(table):
            raise ValueError(f"Table must be schema.table with plain identifiers, got {table!r}")
        columns += [f"HAS_PERMS_BY_NAME('{table}', 'OBJECT', '{verb}') AS t{index}_{verb.lower()}" for verb in SQL_VERBS]
    return "SELECT " + ", ".join(columns)


def interpret_sql_row(layer: str, row: Mapping[str, Any], tables: Sequence[str]) -> list[LayerResult]:
    def flag(value: Any) -> str:
        return {1: ALLOWED, 0: DENIED}.get(value, UNKNOWN) if value is not None else UNKNOWN

    results = [
        LayerResult(layer, "database user", ALLOWED, str(row.get("db_user"))),
        LayerResult(layer, "db_owner / CONTROL", flag(row.get("db_control")), f"db_owner={row.get('db_owner')}"),
    ]
    for index, table in enumerate(tables):
        for verb in SQL_VERBS:
            value = row.get(f"t{index}_{verb.lower()}")
            results.append(LayerResult(layer, f"{verb} on {table}", flag(value), "object not visible to this identity" if value is None else ""))
    return results


def manual_layers() -> list[LayerResult]:
    return [
        LayerResult("Native Planning role", "Planner / Stakeholder / Viewer session", MANUAL, "no read API: plan > Security, or try an edit in the plan"),
        LayerResult("PowerTable security", "row / column access and roles", MANUAL, "no read API: PowerTable > Manage Access / Roles & Policies"),
    ]


def summarize_author(results: Sequence[LayerResult], upn: str) -> str:
    """One line for the acceptance record: the checks that could be read for this identity, never a claim about other roles."""
    api = [r for r in results if r.outcome != MANUAL]
    allowed = sum(1 for r in api if r.outcome == ALLOWED)
    denied = [r.check for r in api if r.outcome == DENIED]
    unknown = [r.check for r in api if r.outcome == UNKNOWN]
    return (
        f"probe for {upn}: {allowed}/{len(api)} layer checks allowed (workspace role, plan item, semantic model, SQL database and table rights)"
        + (f"; denied: {denied}" if denied else "")
        + (f"; not readable: {unknown}" if unknown else "")
        + "; native Planning / PowerTable roles are manual. Stakeholder, Viewer and approver identities were not available, so the role matrix is NOT executed"
    )
