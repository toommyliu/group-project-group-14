import math
from pathlib import Path

import pytest

from opengrader.core.curve import (
    Curve,
    apply_curve,
    clear_curve,
    read_curve,
    resolve_curve_path,
    solve_curve,
    summarize_curve,
    write_curve,
)


def _mean_percent(totals: list[tuple[float, float | None]], curve: Curve) -> float:
    percents = [
        apply_curve(raw, possible, curve) / possible * 100.0
        for raw, possible in totals
        if possible
    ]
    return sum(percents) / len(percents)


def test_solve_curve_bonus_hits_target_mean() -> None:
    totals = [(50.0, 100.0), (60.0, 100.0), (70.0, 100.0)]
    curve = solve_curve(totals, "bonus", 75.0)
    assert curve is not None and curve.method == "bonus"
    assert _mean_percent(totals, curve) == pytest.approx(75.0, abs=1e-2)


def test_solve_curve_scale_hits_target_mean() -> None:
    totals = [(50.0, 100.0), (60.0, 100.0), (70.0, 100.0)]
    curve = solve_curve(totals, "scale", 75.0)
    assert curve is not None and curve.method == "scale"
    assert _mean_percent(totals, curve) == pytest.approx(75.0, abs=1e-2)


def test_solve_curve_median_target() -> None:
    totals = [(40.0, 100.0), (50.0, 100.0), (90.0, 100.0)]
    curve = solve_curve(totals, "bonus", 70.0, statistic="median")
    assert curve is not None
    curved = sorted(apply_curve(r, p, curve) / p * 100 for r, p in totals)
    assert curved[1] == pytest.approx(70.0, abs=1e-2)


def test_solve_curve_multi_version_uses_each_students_possible() -> None:
    totals = [(45.0, 90.0), (30.0, 60.0), (50.0, 100.0)]
    curve = solve_curve(totals, "scale", 80.0)
    assert curve is not None
    assert _mean_percent(totals, curve) == pytest.approx(80.0, abs=1e-2)


def test_solve_curve_unsolvable_methods_return_none() -> None:
    totals = [(50.0, 100.0), (60.0, 100.0)]
    assert solve_curve(totals, "none", 75.0) is None
    assert solve_curve(totals, "sqrt", 75.0) is None


def test_solve_curve_unreachable_target_returns_none() -> None:
    assert solve_curve([(0.0, 100.0), (50.0, 100.0)], "scale", 100.0) is None
    assert solve_curve([(50.0, 100.0)], "bonus", 105.0, cap_at_max=True) is None
    assert solve_curve([(50.0, 100.0)], "bonus", 105.0, cap_at_max=False) is not None


def test_solve_curve_no_scorable_students_returns_none() -> None:
    assert solve_curve([], "bonus", 75.0) is None
    assert solve_curve([(50.0, None), (0.0, 0.0)], "bonus", 75.0) is None


def test_apply_none_is_identity() -> None:
    assert apply_curve(42.0, 100.0, Curve()) == 42.0


def test_apply_bonus_with_and_without_cap() -> None:
    assert apply_curve(90.0, 100.0, Curve("bonus", 5.0)) == 95.0
    assert apply_curve(98.0, 100.0, Curve("bonus", 5.0)) == 100.0
    assert apply_curve(98.0, 100.0, Curve("bonus", 5.0, cap_at_max=False)) == 103.0
    assert apply_curve(2.0, 100.0, Curve("bonus", -5.0)) == 0.0


def test_apply_scale() -> None:
    assert math.isclose(apply_curve(80.0, 100.0, Curve("scale", 1.1)), 88.0)
    assert apply_curve(95.0, 100.0, Curve("scale", 1.2)) == 100.0


def test_apply_sqrt() -> None:
    assert math.isclose(apply_curve(36.0, 100.0, Curve("sqrt")), 60.0)
    assert apply_curve(36.0, None, Curve("sqrt")) == 36.0
    assert apply_curve(0.0, 100.0, Curve("sqrt")) == 0.0


def test_summarize_curve_before_after() -> None:
    effect = summarize_curve([(36.0, 100.0), (49.0, 100.0)], Curve("sqrt"))
    assert effect.count == 2
    assert effect.possible == 100.0
    assert math.isclose(effect.raw_mean, 42.5)
    assert math.isclose(effect.curved_mean, 65.0)
    assert effect.students_raised == 2
    assert effect.students_lowered == 0


def test_summarize_curve_mixed_possible_reports_no_shared_max() -> None:
    effect = summarize_curve([(40.0, 50.0), (80.0, 100.0)], Curve("bonus", 5.0))
    assert effect.possible is None
    assert effect.students_raised == 2


def test_summarize_empty() -> None:
    effect = summarize_curve([], Curve("bonus", 5.0))
    assert effect.count == 0
    assert effect.raw_mean is None


def test_persistence_round_trip_and_defaults(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()

    assert read_curve(subs) == Curve()

    write_curve(subs, Curve("scale", 1.1, cap_at_max=False))
    assert resolve_curve_path(subs).exists()
    loaded = read_curve(subs)
    assert loaded == Curve("scale", 1.1, cap_at_max=False)

    clear_curve(subs)
    assert not resolve_curve_path(subs).exists()
    assert read_curve(subs) == Curve()


def test_malformed_curve_file_falls_back_to_default(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    path = resolve_curve_path(subs)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json", encoding="utf-8")
    assert read_curve(subs) == Curve()
