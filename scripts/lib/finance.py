"""Business definitions and reconciliation logic for the plan-versus-actual story.

Conventions (documented in docs/architecture.md and verified against the real semantic model):
- Amounts are currency-agnostic decimals; the sample model carries no currency column, so no conversion is applied.
- `cogs_sign="positive_cost"` means COGS is stored as a positive magnitude (Gross profit = Revenue - COGS);
  `"negative_cost"` means COGS is stored negative (Gross profit = Revenue + COGS).
- Variance is always Actual - Reference. Percentages divide by |Reference| and are None (BLANK) when the
  reference is zero, never 0 and never an exception.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Hashable, Iterable, Literal, Mapping, Sequence

Number = int | float | Decimal | str
CogsSign = Literal["positive_cost", "negative_cost"]
Kind = Literal["revenue", "profit", "cost"]

DEFAULT_TOLERANCE = Decimal("0.01")


def D(value: Number) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Not a number: {value!r}") from exc


def gross_profit(revenue: Number, cogs: Number, *, cogs_sign: CogsSign = "positive_cost") -> Decimal:
    if cogs_sign == "positive_cost":
        return D(revenue) - D(cogs)
    if cogs_sign == "negative_cost":
        return D(revenue) + D(cogs)
    raise ValueError(f"Unknown cogs_sign {cogs_sign!r}")


def gross_margin_pct(revenue: Number, cogs: Number, *, cogs_sign: CogsSign = "positive_cost") -> Decimal | None:
    """Gross profit divided by revenue, as a fraction; None when revenue is zero."""
    rev = D(revenue)
    if rev == 0:
        return None
    return gross_profit(rev, cogs, cogs_sign=cogs_sign) / rev


def variance(actual: Number, reference: Number) -> Decimal:
    return D(actual) - D(reference)


def variance_pct(actual: Number, reference: Number) -> Decimal | None:
    ref = D(reference)
    if ref == 0:
        return None
    return (D(actual) - ref) / abs(ref)


def favorability(kind: Kind, variance_value: Number) -> Literal["favorable", "unfavorable", "neutral"]:
    """Revenue and profit are favorable when above the reference; cost is favorable when below it."""
    v = D(variance_value)
    if v == 0:
        return "neutral"
    above = v > 0
    if kind in ("revenue", "profit"):
        return "favorable" if above else "unfavorable"
    if kind == "cost":
        return "unfavorable" if above else "favorable"
    raise ValueError(f"Unknown kind {kind!r}")


@dataclass(frozen=True)
class Reconciliation:
    key: Hashable
    expected: Decimal | None
    observed: Decimal | None
    delta: Decimal | None
    ok: bool
    note: str = ""


def reconcile(expected: Mapping[Hashable, Number], observed: Mapping[Hashable, Number], *, tolerance: Number = DEFAULT_TOLERANCE) -> list[Reconciliation]:
    """Compare two keyed value sets; keys missing on either side are failures, never silently ignored."""
    tol = D(tolerance)
    results: list[Reconciliation] = []
    for key in sorted(set(expected) | set(observed), key=repr):
        if key not in observed:
            results.append(Reconciliation(key, D(expected[key]), None, None, False, "missing from observed"))
        elif key not in expected:
            results.append(Reconciliation(key, None, D(observed[key]), None, False, "unexpected in observed"))
        else:
            exp, obs = D(expected[key]), D(observed[key])
            delta = obs - exp
            results.append(Reconciliation(key, exp, obs, delta, abs(delta) <= tol))
    return results


def all_ok(results: Iterable[Reconciliation]) -> bool:
    return all(r.ok for r in results)


@dataclass(frozen=True)
class SnapshotDiff:
    changed: dict[Hashable, tuple[Decimal, Decimal]]
    added: dict[Hashable, Decimal]
    removed: dict[Hashable, Decimal]

    @property
    def touched(self) -> set[Hashable]:
        return set(self.changed) | set(self.added) | set(self.removed)


def snapshot_diff(before: Mapping[Hashable, Number], after: Mapping[Hashable, Number], *, tolerance: Number = Decimal("0")) -> SnapshotDiff:
    tol = D(tolerance)
    changed: dict[Hashable, tuple[Decimal, Decimal]] = {}
    for key in set(before) & set(after):
        old, new = D(before[key]), D(after[key])
        if abs(new - old) > tol:
            changed[key] = (old, new)
    added = {k: D(after[k]) for k in set(after) - set(before)}
    removed = {k: D(before[k]) for k in set(before) - set(after)}
    return SnapshotDiff(changed, added, removed)


def only_expected_changes(diff: SnapshotDiff, expected_new_values: Mapping[Hashable, Number], *, tolerance: Number = DEFAULT_TOLERANCE) -> list[str]:
    """Return problems if the writeback touched anything other than the intended grains/values (empty list == clean)."""
    tol = D(tolerance)
    problems: list[str] = []
    for key, value in expected_new_values.items():
        want = D(value)
        if key in diff.changed:
            got = diff.changed[key][1]
        elif key in diff.added:
            got = diff.added[key]
        else:
            problems.append(f"{key!r}: expected change to {want} but the value did not change")
            continue
        if abs(got - want) > tol:
            problems.append(f"{key!r}: expected {want}, observed {got}")
    for key in diff.touched - set(expected_new_values):
        problems.append(f"{key!r}: changed unexpectedly (scenario/actuals separation violated)")
    return problems


def find_duplicate_grains(rows: Sequence[Mapping[str, object]], grain_columns: Sequence[str]) -> dict[tuple, int]:
    """Grains that appear more than once; callers filter to the latest rows first for the 'with Changes' formats."""
    counts: dict[tuple, int] = {}
    for row in rows:
        key = tuple(row.get(column) for column in grain_columns)
        counts[key] = counts.get(key, 0) + 1
    return {key: n for key, n in counts.items() if n > 1}


def latest_rows(rows: Sequence[Mapping[str, object]], is_latest_column: str = "IsLatest") -> list[Mapping[str, object]]:
    """Rows flagged IsLatest = 1 (Long/Wide with Changes keep history; only the latest row is the active value)."""
    return [row for row in rows if str(row.get(is_latest_column)) in {"1", "True", "true"}]
