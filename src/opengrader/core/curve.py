"""Per-assignment score curve — a transparent, reversible transform on totals.

Grading truth is the rubric selections in the ``.submission.md`` files, and the
raw total is derived from them (`core/qa_file/scoring.py`). A *curve* is a
separate layer on top: a pure function of ``(raw_total, possible_total)`` an
instructor sets once for the whole assignment (flat bonus, scale, or a
square-root curve). It never touches a rubric selection or a stored grade, so it
stays fully reversible — clearing the curve restores the raw totals exactly.

Design decisions (mirroring `core/policy.py`):

* **Location** — ``<submissions>/.opengrader/curve.json``. Co-located with the
  assignment data so it travels with the folder, namespaced in the same
  ``.opengrader`` dot-dir as the policy/comments/history so it reads as tool
  metadata and stays out of submission discovery.
* **Absent file = no curve** — a missing, unreadable, or malformed file resolves
  to the identity curve (``Curve()``), so existing folders and un-curved
  assignments behave exactly as before and a corrupt write never changes a grade.
* **Applied at the edges, shown both ways** — the raw total is what's stored; the
  curved total is computed on demand by every place a final score leaves the tool
  (Canvas export, gradebook CSV, the returnable summary page), always alongside
  the raw value so nothing is hidden.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any, Literal

CurveMethod = Literal["none", "bonus", "scale", "sqrt"]

_CURVE_DIRNAME = ".opengrader"
_CURVE_FILENAME = "curve.json"

_VALID_METHODS: tuple[CurveMethod, ...] = ("none", "bonus", "scale", "sqrt")


@dataclass(frozen=True)
class Curve:
    """One assignment's score curve.

    ``method`` picks the transform; ``value`` is its single parameter:

    * ``none``  — identity (``value`` ignored).
    * ``bonus`` — add ``value`` points to every total (may be negative).
    * ``scale`` — multiply every total by ``value`` (e.g. 1.1 = +10%).
    * ``sqrt``  — the classic root curve: ``new% = sqrt(raw%)``, i.e.
      ``sqrt(raw/possible) * possible`` (lifts low scores more than high ones).

    ``cap_at_max`` clamps the curved total to the possible points (on by
    default); turn it off to allow a curve to push past 100% (extra credit).
    The result is always floored at 0.
    """

    method: CurveMethod = "none"
    value: float = 0.0
    cap_at_max: bool = True

    @property
    def is_active(self) -> bool:
        return self.method != "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "value": self.value,
            "cap_at_max": self.cap_at_max,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Curve":
        method = data.get("method")
        if method not in _VALID_METHODS:
            method = "none"
        try:
            value = float(data.get("value", 0.0))
        except (TypeError, ValueError):
            value = 0.0
        return cls(
            method=method,
            value=value,
            cap_at_max=bool(data.get("cap_at_max", True)),
        )


def describe_curve(curve: Curve) -> str:
    """Short human description of a curve ("bonus +5 pts", "scale x1.1").

    Lives in core rather than in the TUI's formatting module because every
    front end that mentions a curve needs the same sentence — the curve editor,
    the CLI preview, and the release report that has to say *why* a total is not
    the raw sum. A second phrasing of "square-root curve" would eventually
    describe the same curve two ways in one workflow.
    """
    # ``×`` is U+00D7, which is inside WinAnsi — safe if this string is ever
    # drawn into a PDF with the base-14 fonts (see core/pdf/annotations.py).
    if not curve.is_active:
        return "none (raw totals)"
    cap = "" if curve.cap_at_max else "  (no cap)"
    if curve.method == "bonus":
        return f"bonus {curve.value:+g} pts{cap}"
    if curve.method == "scale":
        return f"scale ×{curve.value:g}{cap}"
    if curve.method == "sqrt":
        return f"square-root curve{cap}"
    return curve.method


def apply_curve(raw_total: float, possible: float | None, curve: Curve) -> float:
    """Return the curved total for one student (pure).

    ``possible`` may be ``None``/0 (unknown max) — then the ``sqrt`` curve and
    the max cap are skipped, since both need a denominator. The result is always
    ``>= 0``.
    """
    method = curve.method
    if method == "bonus":
        result = raw_total + curve.value
    elif method == "scale":
        result = raw_total * curve.value
    elif method == "sqrt":
        if possible and possible > 0 and raw_total > 0:
            result = math.sqrt(raw_total / possible) * possible
        else:
            result = raw_total
    else:  # "none" or anything unexpected
        result = raw_total

    result = max(0.0, result)
    if curve.cap_at_max and possible is not None and possible > 0:
        result = min(result, possible)
    return result


@dataclass(frozen=True)
class CurveEffect:
    """A preview of what a curve does to a cohort's totals."""

    count: int
    possible: float | None
    raw_mean: float | None
    curved_mean: float | None
    raw_median: float | None
    curved_median: float | None
    students_raised: int
    students_lowered: int
    curved_at_max: int


def summarize_curve(
    totals: list[tuple[float, float | None]], curve: Curve
) -> CurveEffect:
    """Compute before/after cohort statistics for a curve (pure, no I/O).

    ``totals`` is one ``(raw_total, possible)`` pair per student, so a
    multi-version cohort curves each student against their own maximum. The
    reported ``possible`` is that shared maximum when every student has the same
    one, else ``None``.
    """
    if not totals:
        return CurveEffect(
            count=0,
            possible=None,
            raw_mean=None,
            curved_mean=None,
            raw_median=None,
            curved_median=None,
            students_raised=0,
            students_lowered=0,
            curved_at_max=0,
        )
    raw_totals = [raw for raw, _possible in totals]
    curved = [apply_curve(raw, possible, curve) for raw, possible in totals]
    raised = sum(1 for raw, cur in zip(raw_totals, curved) if cur > raw + 1e-9)
    lowered = sum(1 for raw, cur in zip(raw_totals, curved) if cur < raw - 1e-9)
    at_max = sum(
        1
        for (_raw, possible), cur in zip(totals, curved)
        if possible and possible > 0 and cur >= possible - 1e-9
    )
    possibles = {possible for _raw, possible in totals}
    shared_possible = possibles.pop() if len(possibles) == 1 else None
    return CurveEffect(
        count=len(totals),
        possible=shared_possible,
        raw_mean=mean(raw_totals),
        curved_mean=mean(curved),
        raw_median=median(raw_totals),
        curved_median=median(curved),
        students_raised=raised,
        students_lowered=lowered,
        curved_at_max=at_max,
    )


CurveStatistic = Literal["mean", "median"]

# The two methods that carry an adjustable parameter, so a target is solvable.
# ``sqrt`` is a fixed, parameterless transform (its ``value`` is ignored) and
# ``none`` is the identity, so neither can be steered to an arbitrary average.
_SOLVABLE_METHODS: tuple[CurveMethod, ...] = ("bonus", "scale")


def _percent_statistic(
    totals: list[tuple[float, float | None]],
    curve: Curve,
    statistic: CurveStatistic,
) -> float | None:
    """Cohort mean/median of curved scores as a percentage (0..100), or None.

    Each student is scored against their own ``possible`` (multi-version safe);
    students with no known maximum are skipped. ``None`` when none remain.
    """
    percents = [
        apply_curve(raw, possible, curve) / possible * 100.0
        for raw, possible in totals
        if possible and possible > 0
    ]
    if not percents:
        return None
    return mean(percents) if statistic == "mean" else median(percents)


def solve_curve(
    totals: list[tuple[float, float | None]],
    method: CurveMethod,
    target_percent: float,
    *,
    statistic: CurveStatistic = "mean",
    cap_at_max: bool = True,
) -> Curve | None:
    """Find the curve of ``method`` whose cohort ``statistic`` hits a target.

    Instructors think in target grades ("I want a 78 average"), not scale
    factors. Given one ``(raw_total, possible)`` pair per student, this returns
    the :class:`Curve` whose curved cohort ``mean``/``median`` (as a **percentage**
    of each student's possible) equals ``target_percent``.

    Returns ``None`` when the target can't be solved: an unsolvable ``method``
    (``none``/``sqrt`` carry no adjustable parameter), no students with a known
    maximum, or a target outside what the method can reach (e.g. ``scale`` can
    never lift a student who scored 0, so a 100% target is unreachable — and with
    ``cap_at_max`` on, any target above 100 is too).

    Implementation: ``apply_curve`` is monotonic non-decreasing in ``value`` (for
    both solvable methods), so the cohort statistic is too. We bracket the
    parameter and bisect — exact even across the floor-at-0 and cap-at-max clamps
    that make a closed form wrong near the boundaries.
    """
    if method not in _SOLVABLE_METHODS:
        return None
    usable = [(raw, possible) for raw, possible in totals if possible and possible > 0]
    if not usable:
        return None

    def stat(value: float) -> float:
        # usable guarantees at least one scorable student, so never None here.
        return _percent_statistic(usable, Curve(method, value, cap_at_max), statistic)  # type: ignore[return-value]

    # Initial bracket, then widen until it straddles the target (or give up).
    if method == "scale":
        lo, hi = 0.0, 1.0
    else:  # bonus
        max_possible = max(possible for _raw, possible in usable)
        lo, hi = -float(max_possible), float(max_possible)

    guard = 0
    while stat(hi) < target_percent and guard < 100:
        hi *= 2
        guard += 1
    guard = 0
    while method == "bonus" and stat(lo) > target_percent and guard < 100:
        lo *= 2
        guard += 1

    if not (stat(lo) - 1e-6 <= target_percent <= stat(hi) + 1e-6):
        return None

    for _ in range(200):
        mid = (lo + hi) / 2
        s_mid = stat(mid)
        if abs(s_mid - target_percent) <= 1e-7:
            lo = hi = mid
            break
        if s_mid < target_percent:
            lo = mid
        else:
            hi = mid

    value = round((lo + hi) / 2, 2 if method == "bonus" else 4)
    return Curve(method=method, value=value, cap_at_max=cap_at_max)


def resolve_curve_path(submissions_path: str | Path) -> Path:
    """Return the ``.opengrader/curve.json`` path for a submissions file or dir."""
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / _CURVE_DIRNAME / _CURVE_FILENAME


def read_curve(submissions_path: str | Path) -> Curve:
    """Load the curve for a submissions folder, or the identity curve.

    A missing, unreadable, or malformed file resolves to ``Curve()`` (no curve)
    so grading/export is never blocked and an un-curved assignment is unchanged.
    """
    path = resolve_curve_path(submissions_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return Curve()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return Curve()
    if not isinstance(data, dict):
        return Curve()
    return Curve.from_dict(data)


def write_curve(submissions_path: str | Path, curve: Curve) -> Path:
    """Write ``curve`` into ``<submissions>/.opengrader/curve.json``.

    Returns the path written; creates the ``.opengrader`` dir if needed.
    """
    path = resolve_curve_path(submissions_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(curve.to_dict(), ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8")
    return path


def clear_curve(submissions_path: str | Path) -> None:
    """Remove the curve file, restoring raw totals. A no-op if none exists."""
    path = resolve_curve_path(submissions_path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
