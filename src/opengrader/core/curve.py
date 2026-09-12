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
    # The multiplication sign is in WinAnsi and works with PDF base-14 fonts.
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
    else:
        result = raw_total

    result = max(0.0, result)
    if curve.cap_at_max and possible is not None and possible > 0:
        result = min(result, possible)
    return result


@dataclass(frozen=True)
class CurveEffect:
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

_SOLVABLE_METHODS: tuple[CurveMethod, ...] = ("bonus", "scale")


def _percent_statistic(
    totals: list[tuple[float, float | None]],
    curve: Curve,
    statistic: CurveStatistic,
) -> float | None:
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
    if method not in _SOLVABLE_METHODS:
        return None
    usable = [(raw, possible) for raw, possible in totals if possible and possible > 0]
    if not usable:
        return None

    def stat(value: float) -> float:
        return _percent_statistic(usable, Curve(method, value, cap_at_max), statistic)  # type: ignore[return-value]

    if method == "scale":
        lo, hi = 0.0, 1.0
    else:
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
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / _CURVE_DIRNAME / _CURVE_FILENAME


def read_curve(submissions_path: str | Path) -> Curve:
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
    path = resolve_curve_path(submissions_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(curve.to_dict(), ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8")
    return path


def clear_curve(submissions_path: str | Path) -> None:
    path = resolve_curve_path(submissions_path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
