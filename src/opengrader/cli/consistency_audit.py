"""Audit a graded set for consistency: same answer, same grade?

Clusters every graded response by its answer (the same "same answer" rule the
TUI's answer-grouping uses) and reports each cluster whose members did not all
receive the same grade — the fairness check to run before scores reach Canvas.
Reads only the persisted ``.submission.md`` files; it never re-runs OCR or
changes any grade.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.submissions import (
    ConsistencyFlag,
    ConsistencyKind,
    build_consistency_flags,
)
from opengrader.core.submissions.student_file import load_student_submission_markdown

EPILOG = """
examples:
  uv run consistency-audit --input out/student-submissions/
  uv run consistency-audit --input out/student-submissions/ --include-blank
  uv run consistency-audit --input out/student-submissions/ --json
  uv run consistency-audit --input out/student-submissions/ --fail-on-inconsistency
"""

_SEVERITY_GLYPH = {"high": "●", "low": "○"}

_KIND_HEADLINE = {
    ConsistencyKind.POINTS_DIVERGENCE: "different scores for the same answer",
    ConsistencyKind.RUBRIC_DIVERGENCE: "same score, different rubric selection",
}


def _submission_paths(input_path: Path) -> list[Path]:
    """Discover submission files, matching SubmissionFilesProvider's rules."""
    if input_path.is_file():
        return [input_path]
    direct = sorted(input_path.glob("*.submission.md"))
    nested = sorted(input_path.glob("*/*.submission.md"))
    return direct + nested


def _format_points(points: float) -> str:
    """Render a score without a trailing ``.0`` on whole numbers."""
    return f"{points:g}"


def _format_labels(labels: tuple[str, ...]) -> str:
    return ", ".join(labels) if labels else "(no rubric selection)"


def _question_heading(flag: ConsistencyFlag) -> str:
    version = f" (version {flag.assignment_version})" if flag.assignment_version else ""
    return f"{flag.question_id}{version}"


def _answer_line(flag: ConsistencyFlag) -> str:
    if flag.is_blank:
        return "answer: (blank)"
    sample = flag.sample_response_text.replace("\n", " ")
    if len(sample) > 70:
        sample = sample[:69] + "…"
    return f'answer: "{sample}"'


def _print_human(flags: list[ConsistencyFlag]) -> None:
    if not flags:
        print("No grading inconsistencies found. ✓")
        return

    high = sum(1 for flag in flags if flag.severity == "high")
    low = len(flags) - high
    print(
        f"{len(flags)} inconsistent answer group(s): "
        f"{high} scoring, {low} rubric-only.\n"
    )

    current: str | None = None
    for flag in flags:
        if flag.severity != current:
            current = flag.severity
            glyph = _SEVERITY_GLYPH[flag.severity]
            label = "SCORING CONFLICTS" if flag.severity == "high" else "RUBRIC-ONLY"
            print(f"{glyph} {label}")

        print(
            f"    {_question_heading(flag):<18} {_KIND_HEADLINE[flag.kind]} "
            f"— {flag.member_count} students"
        )
        print(f"        {_answer_line(flag)}")
        for variant in flag.variants:
            names = ", ".join(member.label for member in variant.members)
            points = _format_points(variant.points_awarded)
            print(
                f"        {points:>6} pts  [{_format_labels(variant.rubric_labels)}]  "
                f"→ {names}"
            )
        print()


def _flag_to_dict(flag: ConsistencyFlag) -> dict[str, object]:
    return {
        "severity": flag.severity,
        "kind": flag.kind.value,
        "question_id": flag.question_id,
        "assignment_version": flag.assignment_version,
        "question_type": flag.question_type,
        "answer_key": flag.answer_key,
        "sample_response_text": flag.sample_response_text,
        "possible_points": flag.possible_points,
        "point_spread": flag.point_spread,
        "member_count": flag.member_count,
        "variants": [
            {
                "points_awarded": variant.points_awarded,
                "rubric_labels": list(variant.rubric_labels),
                "members": [
                    {
                        "student_id": member.student_id,
                        "student_name": member.student_name,
                        "source_pdf": member.source_pdf,
                    }
                    for member in variant.members
                ],
            }
            for variant in flag.variants
        ],
    }


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Audit a graded submission set for consistency — flag answer groups "
            "where students who gave the same answer received different grades."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        required=True,
        help="A .submission.md file or a directory of them.",
    )
    parser.add_argument(
        "--include-blank",
        action="store_true",
        help=(
            "Also audit the blank ('no answer') group. Off by default: a blank "
            "transcription often reflects work the OCR missed."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the flags as a JSON array instead of a human-readable list.",
    )
    parser.add_argument(
        "--fail-on-inconsistency",
        action="store_true",
        help="Exit non-zero if any inconsistency is found (for scripting/CI).",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input path not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    paths = _submission_paths(input_path)
    if not paths:
        print(f"Error: no .submission.md files found under {input_path}", file=sys.stderr)
        sys.exit(1)

    documents = []
    for path in paths:
        try:
            documents.append(load_student_submission_markdown(path))
        except ValueError as exc:
            print(f"Warning: could not read {path}: {exc}", file=sys.stderr)

    flags = build_consistency_flags(documents, include_blank=args.include_blank)

    if args.as_json:
        print(json.dumps([_flag_to_dict(flag) for flag in flags], indent=2))
    else:
        _print_human(flags)

    if args.fail_on_inconsistency and flags:
        sys.exit(2)
