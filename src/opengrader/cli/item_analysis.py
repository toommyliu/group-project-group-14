"""Report question difficulty, separation, and outcome mastery."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.analytics import (
    DEFAULT_MASTERY_THRESHOLD,
    DifficultyBand,
    ItemAnalysis,
    SeparationBand,
    build_item_analysis,
    build_outcome_report,
)
from opengrader.core.qa_file import (
    load_qa_markdown,
    outcomes_by_question,
    possible_points_by_question,
)
from opengrader.core.submissions.student_file import load_student_submission_markdown

EPILOG = """
examples:
  uv run item-analysis --input out/student-submissions/
  uv run item-analysis --input out/student-submissions/ --json
  uv run item-analysis --input out/student-submissions/ --flagged-only

learning-outcome mastery (needs the answer key, where the **Outcome** tags live):
  uv run item-analysis --input out/student-submissions/ --solutions solutions.md
"""

_DIFFICULTY_LABELS = {
    DifficultyBand.VERY_EASY: "very easy",
    DifficultyBand.EASY: "easy",
    DifficultyBand.MODERATE: "moderate",
    DifficultyBand.HARD: "hard",
    DifficultyBand.VERY_HARD: "very hard",
}

_SEPARATION_LABELS = {
    SeparationBand.EXCELLENT: "excellent",
    SeparationBand.GOOD: "good",
    SeparationBand.FAIR: "fair",
    SeparationBand.WEAK: "weak",
    SeparationBand.NEGATIVE: "NEGATIVE",
    SeparationBand.INSUFFICIENT_DATA: "n/a (too few responses)",
}


def _submission_paths(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    direct = sorted(input_path.glob("*.submission.md"))
    nested = sorted(input_path.glob("*/*.submission.md"))
    return direct + nested


def _heading(item: ItemAnalysis) -> str:
    version = f" (version {item.assignment_version})" if item.assignment_version else ""
    return f"{item.question_id}{version}"


def _difficulty_text(item: ItemAnalysis) -> str:
    if item.difficulty is None:
        return "difficulty: n/a"
    band = _DIFFICULTY_LABELS.get(item.difficulty_band, "")
    return f"difficulty: {item.difficulty * 100:.0f}% ({band})"


def _separation_text(item: ItemAnalysis) -> str:
    label = _SEPARATION_LABELS.get(item.separation_band, "")
    if item.separation is None:
        return f"separation: {label}"
    return f"separation: {item.separation:+.2f} ({label})"


def _print_human(items: list[ItemAnalysis], class_size: int, *, flagged_only: bool) -> None:
    shown = [item for item in items if item.needs_attention] if flagged_only else items
    if not shown:
        if flagged_only:
            print("No items flagged for attention. ✓")
        else:
            print("No graded items to analyze.")
        return

    flagged = sum(1 for item in items if item.needs_attention)
    print(
        f"Item analysis over {class_size} submission(s): "
        f"{len(items)} question(s), {flagged} flagged for attention.\n"
    )

    for item in shown:
        marker = "⚠ " if item.needs_attention else "  "
        print(f"{marker}{_heading(item):<16} {item.responses_count} responses")
        print(f"      {_difficulty_text(item)}")
        print(f"      {_separation_text(item)}")
        for distractor in item.distractors:
            key = " ← key" if distractor.is_likely_key else ""
            chooser = (
                f", choosers avg {distractor.mean_chooser_total_percent:.0f}%"
                if distractor.mean_chooser_total_percent is not None
                else ""
            )
            print(
                f"        {distractor.choice:<20} {distractor.count:>3}"
                f"  ({distractor.share * 100:.0f}%{chooser}){key}"
            )
        print()


def _item_to_dict(item: ItemAnalysis) -> dict[str, object]:
    return {
        "question_id": item.question_id,
        "assignment_version": item.assignment_version,
        "question_type": item.question_type,
        "possible_points": item.possible_points,
        "responses_count": item.responses_count,
        "mean_points": item.mean_points,
        "difficulty": item.difficulty,
        "difficulty_band": item.difficulty_band.value if item.difficulty_band else None,
        "separation": item.separation,
        "separation_band": item.separation_band.value,
        "needs_attention": item.needs_attention,
        "distractors": [
            {
                "choice": distractor.choice,
                "count": distractor.count,
                "share": distractor.share,
                "is_likely_key": distractor.is_likely_key,
                "mean_chooser_total_percent": distractor.mean_chooser_total_percent,
            }
            for distractor in item.distractors
        ],
    }


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Analyze a graded submission set item-by-item: difficulty, "
            "separation (discrimination), and multiple-choice distractors."
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
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the analysis as a JSON array instead of a human-readable list.",
    )
    parser.add_argument(
        "--flagged-only",
        action="store_true",
        help="Show only items flagged for attention (negative or weak separation).",
    )
    parser.add_argument(
        "--solutions",
        default=None,
        help=(
            "QA answer key. Optional: supply it to add a learning-outcome "
            "mastery section (the **Outcome** tags live in the answer key, not "
            "in the submissions)."
        ),
    )
    parser.add_argument(
        "--mastery-threshold",
        type=float,
        default=DEFAULT_MASTERY_THRESHOLD,
        metavar="RATE",
        help=(
            "Share of an outcome's points a student must earn to count as "
            "having mastered it, 0-1 (default: %(default)s)."
        ),
    )
    return parser


def _outcomes_to_dict(report) -> dict[str, object]:  # noqa: ANN001
    return {
        "mastery_threshold": report.mastery_threshold,
        "class_size": report.class_size,
        "tagged_share": report.tagged_share,
        "untagged_points": report.untagged_points,
        "outcomes": [
            {
                "outcome": o.outcome,
                "question_ids": list(o.question_ids),
                "possible_points": o.possible_points,
                "assignment_weight": o.assignment_weight,
                "students_measured": o.students_measured,
                "students_mastered": o.students_mastered,
                "mastery_rate": o.mastery_rate,
                "mean_mastery": o.mean_mastery,
                "students": [
                    {
                        "student_id": s.student_id,
                        "student_name": s.student_name,
                        "earned_points": s.earned_points,
                        "possible_points": s.possible_points,
                        "questions_graded": s.questions_graded,
                        "mastery": s.mastery,
                        "mastered": s.has_mastered(report.mastery_threshold),
                    }
                    for s in o.students
                ],
            }
            for o in report.outcomes
        ],
    }


def _print_outcomes(report) -> None:  # noqa: ANN001
    print("\nLearning outcomes")
    tagged = report.tagged_share
    if tagged is not None and tagged < 1.0:
        print(
            f"  {tagged:.0%} of this assignment's points carry an outcome tag "
            f"({report.untagged_points:g} point(s) untagged)."
        )
    print(f"  Mastery bar: {report.mastery_threshold:.0%}\n")
    for outcome in report.outcomes:
        rate = outcome.mastery_rate
        weight = outcome.assignment_weight
        print(f"  {outcome.outcome}")
        print(
            f"    questions: {', '.join(outcome.question_ids)}"
            f"   ({outcome.possible_points:g} pts"
            + (f", {weight:.0%} of the assignment)" if weight is not None else ")")
        )
        print(
            f"    mastered:  {outcome.students_mastered}/{outcome.students_measured}"
            + (f"  ({rate:.0%})" if rate is not None else "")
            + (
                f"   mean {outcome.mean_mastery:.0%}"
                if outcome.mean_mastery is not None
                else ""
            )
        )


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

    report = build_item_analysis(documents)

    outcome_report = None
    if args.solutions:
        qa_path = Path(args.solutions)
        if not qa_path.exists():
            print(f"Error: solutions file not found: {qa_path}", file=sys.stderr)
            sys.exit(1)
        qa_doc = load_qa_markdown(qa_path)
        tags = outcomes_by_question(qa_doc)
        if not tags:
            print(
                "Note: the answer key carries no **Outcome** tags, so there is "
                "no outcome analysis to show.",
                file=sys.stderr,
            )
        else:
            outcome_report = build_outcome_report(
                documents,
                tags,
                possible_points_by_question=possible_points_by_question(qa_doc),
                mastery_threshold=args.mastery_threshold,
            )

    if args.as_json:
        payload: dict[str, object] = {
            "items": [_item_to_dict(item) for item in report.items],
        }
        if outcome_report is not None:
            payload["outcomes"] = _outcomes_to_dict(outcome_report)
        print(json.dumps(payload["items"] if outcome_report is None else payload, indent=2))
    else:
        _print_human(list(report.items), report.class_size, flagged_only=args.flagged_only)
        if outcome_report is not None:
            _print_outcomes(outcome_report)
