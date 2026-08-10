"""Get/set/clear an assignment's score curve — a reversible transform on totals.

The curve is stored per-assignment at ``<submissions>/.opengrader/curve.json``
and applied wherever a final score leaves the tool (Canvas export, gradebook
CSV, the returnable summary page), always shown alongside the raw total. It
never edits a rubric selection or a stored grade, so ``curve clear`` restores
the raw totals exactly. See :mod:`opengrader.core.curve`.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

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

EPILOG = """
examples:
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
    """(raw awarded total, possible total) per student, for the curve preview."""
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


def _cmd_show(args) -> None:
    submissions_path = Path(args.submissions)
    curve = read_curve(submissions_path)
    print(f"Curve: {_describe_curve(curve)}")
    print(f"  file: {resolve_curve_path(submissions_path)}")
    _load_and_preview(submissions_path, curve)


def _cmd_set(args) -> None:
    submissions_path = Path(args.submissions)
    if args.method in ("bonus", "scale") and args.value is None:
        print(
            f"error: --method {args.method} requires --value.",
            file=sys.stderr,
        )
        sys.exit(1)
    curve = Curve(
        method=args.method,
        value=float(args.value) if args.value is not None else 0.0,
        cap_at_max=not args.no_cap,
    )
    write_curve(submissions_path, curve)
    print(f"Set curve: {_describe_curve(curve)}")
    print(f"  file: {resolve_curve_path(submissions_path)}")
    _load_and_preview(submissions_path, curve)


def _cmd_suggest(args) -> None:
    submissions_path = Path(args.submissions)
    if args.target_mean is None and args.target_median is None:
        print(
            "error: give --target-mean or --target-median (a class average, %).",
            file=sys.stderr,
        )
        sys.exit(1)
    if args.target_mean is not None and args.target_median is not None:
        print(
            "error: give only one of --target-mean / --target-median.",
            file=sys.stderr,
        )
        sys.exit(1)

    statistic = "mean" if args.target_mean is not None else "median"
    target = args.target_mean if args.target_mean is not None else args.target_median

    try:
        totals = _student_totals(submissions_path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    curve = solve_curve(
        totals,
        args.method,
        float(target),
        statistic=statistic,
        cap_at_max=not args.no_cap,
    )
    if curve is None:
        print(
            f"Could not find a {args.method} curve for a {statistic} of {target:g}%. "
            "Try the other method, --no-cap, or a target closer to the current "
            "average.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Suggested curve: {_describe_curve(curve)}")
    print(f"  targets a {statistic} of {target:g}%")
    _print_effect(summarize_curve(totals, curve))

    if args.apply:
        write_curve(submissions_path, curve)
        print(f"Applied. file: {resolve_curve_path(submissions_path)}")
    else:
        print("  (preview only — re-run with --apply to save, or use curve set)")


def _cmd_clear(args) -> None:
    submissions_path = Path(args.submissions)
    clear_curve(submissions_path)
    print("Cleared curve — raw totals restored.")


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description="Get, set, or clear an assignment's reversible score curve.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def _add_submissions(sub: ArgumentParser) -> None:
        sub.add_argument(
            "--submissions",
            required=True,
            metavar="PATH",
            help="Submissions directory (or a single .submission.md file).",
        )

    show = subparsers.add_parser("show", help="Show the current curve and its effect.")
    _add_submissions(show)
    show.set_defaults(func=_cmd_show)

    set_cmd = subparsers.add_parser("set", help="Set the curve.")
    _add_submissions(set_cmd)
    set_cmd.add_argument(
        "--method",
        required=True,
        choices=["bonus", "scale", "sqrt"],
        help="bonus (add points), scale (multiply), or sqrt (root curve).",
    )
    set_cmd.add_argument(
        "--value",
        type=float,
        default=None,
        help="Points for bonus, or the multiplier for scale. Ignored for sqrt.",
    )
    set_cmd.add_argument(
        "--no-cap",
        action="store_true",
        default=False,
        help="Allow the curve to exceed the possible points (extra credit).",
    )
    set_cmd.set_defaults(func=_cmd_set)

    suggest = subparsers.add_parser(
        "suggest",
        help="Solve for the curve that lands the class average on a target.",
    )
    _add_submissions(suggest)
    suggest.add_argument(
        "--method",
        required=True,
        choices=["bonus", "scale"],
        help="bonus (add points) or scale (multiply) — the methods with a "
        "solvable value. (sqrt has no adjustable value to target.)",
    )
    suggest.add_argument(
        "--target-mean",
        type=float,
        default=None,
        metavar="PERCENT",
        help="Target class mean, as a percentage (e.g. 78).",
    )
    suggest.add_argument(
        "--target-median",
        type=float,
        default=None,
        metavar="PERCENT",
        help="Target class median, as a percentage (e.g. 75).",
    )
    suggest.add_argument(
        "--no-cap",
        action="store_true",
        default=False,
        help="Allow the curve to exceed the possible points (extra credit).",
    )
    suggest.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write the suggested curve (otherwise just preview it).",
    )
    suggest.set_defaults(func=_cmd_suggest)

    clear = subparsers.add_parser("clear", help="Remove the curve (restore raw totals).")
    _add_submissions(clear)
    clear.set_defaults(func=_cmd_clear)

    return parser


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    args.func(args)
