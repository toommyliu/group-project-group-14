"""Manage reversible score curves for assignment totals."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, Group
from opengrader.core.curve import (
    Curve,
    CurveEffect,
    clear_curve,
    read_curve,
    resolve_curve_path,
    solve_curve,
    summarize_curve,
    write_curve,
)
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    load_student_submission_markdown,
)

EPILOG = """\
\b
Examples:
  uv run curve show   --submissions out/student-submissions/
  uv run curve set    --submissions out/student-submissions/ --method bonus --value 5
  uv run curve set    --submissions out/student-submissions/ --method scale --value 1.1
  uv run curve set    --submissions out/student-submissions/ --method sqrt
  uv run curve set    --submissions out/student-submissions/ --method scale --value 1.1 --no-cap
  uv run curve suggest --submissions out/student-submissions/ --method scale --target-mean 78
  uv run curve suggest --submissions out/student-submissions/ --method bonus --target-median 75 --apply
  uv run curve clear  --submissions out/student-submissions/
"""


def _submission_paths(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        direct = sorted(path.glob("*.submission.md"))
        nested = sorted(path.glob("*/*.submission.md"))
        return direct + nested
    raise FileNotFoundError(f"submissions path not found: {path}")


def _student_totals(path: Path) -> list[tuple[float, float | None]]:
    totals: list[tuple[float, float | None]] = []
    for submission_path in _submission_paths(path):
        doc = load_student_submission_markdown(submission_path)
        totals.append((_raw_total(doc), _possible_total(doc)))
    return totals


def _raw_total(doc: StudentSubmissionDocument) -> float:
    return sum(q.points_awarded for q in doc.questions if q.points_awarded is not None)


def _possible_total(doc: StudentSubmissionDocument) -> float | None:
    values = [q.possible_points for q in doc.questions if q.possible_points is not None]
    return sum(values) if values else None


def _describe_curve(curve: Curve) -> str:
    if curve.method == "none":
        return "none (raw totals)"
    cap = "" if curve.cap_at_max else "  [no cap — extra credit allowed]"
    if curve.method == "bonus":
        return f"bonus: {curve.value:+g} points{cap}"
    if curve.method == "scale":
        return f"scale: ×{curve.value:g}{cap}"
    if curve.method == "sqrt":
        return f"square-root curve (new% = sqrt(raw%)){cap}"
    return curve.method


def _num(value: float | None) -> str:
    return f"{value:.10g}" if value is not None else "—"


def _print_effect(effect: CurveEffect) -> None:
    if effect.count == 0:
        print("  (no graded submissions to preview)")
        return
    poss = f" / {_num(effect.possible)}" if effect.possible is not None else ""
    print(f"  Students: {effect.count}")
    print(f"  Mean:   {_num(effect.raw_mean)}{poss}  ->  {_num(effect.curved_mean)}{poss}")
    print(f"  Median: {_num(effect.raw_median)}{poss}  ->  {_num(effect.curved_median)}{poss}")
    print(
        f"  Raised: {effect.students_raised}   Lowered: {effect.students_lowered}"
        f"   At max after: {effect.curved_at_max}"
    )


def _load_and_preview(submissions_path: Path, curve: Curve) -> None:
    try:
        totals = _student_totals(submissions_path)
    except FileNotFoundError as exc:
        print(f"warning: {exc}", file=sys.stderr)
        return
    _print_effect(summarize_curve(totals, curve))


def _submissions_option(function):
    return click.option(
        "--submissions",
        "submissions_path",
        type=click.Path(path_type=Path),
        required=True,
        metavar="PATH",
        help="Submissions directory (or a single .submission.md file).",
    )(function)


def _no_cap_option(function):
    return click.option(
        "--no-cap",
        is_flag=True,
        help="Allow the curve to exceed the possible points (extra credit).",
    )(function)


@click.group("curve", cls=Group, context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
def command() -> None:
    """Get, set, or clear an assignment's reversible score curve."""


@command.command("show")
@_submissions_option
def show_command(*, submissions_path: Path) -> None:
    """Show the current curve and its effect."""
    curve = read_curve(submissions_path)
    print(f"Curve: {_describe_curve(curve)}")
    print(f"  file: {resolve_curve_path(submissions_path)}")
    _load_and_preview(submissions_path, curve)


@command.command("set")
@_submissions_option
@click.option(
    "--method",
    required=True,
    type=click.Choice(["bonus", "scale", "sqrt"]),
    help="bonus (add points), scale (multiply), or sqrt (root curve).",
)
@click.option(
    "--value",
    type=float,
    help="Points for bonus, or the multiplier for scale. Ignored for sqrt.",
)
@_no_cap_option
def set_command(
    *, submissions_path: Path, method: str, value: float | None, no_cap: bool
) -> None:
    """Set the curve."""
    if method in ("bonus", "scale") and value is None:
        print(
            f"error: --method {method} requires --value.",
            file=sys.stderr,
        )
        sys.exit(1)
    curve = Curve(
        method=method,
        value=float(value) if value is not None else 0.0,
        cap_at_max=not no_cap,
    )
    write_curve(submissions_path, curve)
    print(f"Set curve: {_describe_curve(curve)}")
    print(f"  file: {resolve_curve_path(submissions_path)}")
    _load_and_preview(submissions_path, curve)


@command.command("suggest")
@_submissions_option
@click.option(
    "--method",
    required=True,
    type=click.Choice(["bonus", "scale"]),
    help="bonus (add points) or scale (multiply) — the methods with a "
    "solvable value. (sqrt has no adjustable value to target.)",
)
@click.option(
    "--target-mean",
    type=float,
    metavar="PERCENT",
    help="Target class mean, as a percentage (e.g. 78).",
)
@click.option(
    "--target-median",
    type=float,
    metavar="PERCENT",
    help="Target class median, as a percentage (e.g. 75).",
)
@_no_cap_option
@click.option(
    "--apply",
    is_flag=True,
    help="Write the suggested curve (otherwise just preview it).",
)
def suggest_command(
    *,
    submissions_path: Path,
    method: str,
    target_mean: float | None,
    target_median: float | None,
    no_cap: bool,
    apply: bool,
) -> None:
    """Solve for the curve that lands the class average on a target."""
    if target_mean is None and target_median is None:
        print(
            "error: give --target-mean or --target-median (a class average, %).",
            file=sys.stderr,
        )
        sys.exit(1)
    if target_mean is not None and target_median is not None:
        print(
            "error: give only one of --target-mean / --target-median.",
            file=sys.stderr,
        )
        sys.exit(1)

    statistic = "mean" if target_mean is not None else "median"
    target = target_mean if target_mean is not None else target_median

    try:
        totals = _student_totals(submissions_path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    curve = solve_curve(
        totals,
        method,
        float(target),
        statistic=statistic,
        cap_at_max=not no_cap,
    )
    if curve is None:
        print(
            f"Could not find a {method} curve for a {statistic} of {target:g}%. "
            "Try the other method, --no-cap, or a target closer to the current "
            "average.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Suggested curve: {_describe_curve(curve)}")
    print(f"  targets a {statistic} of {target:g}%")
    _print_effect(summarize_curve(totals, curve))

    if apply:
        write_curve(submissions_path, curve)
        print(f"Applied. file: {resolve_curve_path(submissions_path)}")
    else:
        print("  (preview only — re-run with --apply to save, or use curve set)")


@command.command("clear")
@_submissions_option
def clear_command(*, submissions_path: Path) -> None:
    """Remove the curve (restore raw totals)."""
    clear_curve(submissions_path)
    print("Cleared curve — raw totals restored.")
