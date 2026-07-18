"""Automatically grade student submission files using a QA-format solutions file.

Exact-match grading: a response earns full credit when its stripped text
matches one of the QA question's correct answers (or the correct choice
label for multiple choice). Incorrect multiple-choice responses earn 0;
incorrect free responses keep their Points blank for manual grading.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from dataclasses import dataclass
from pathlib import Path

from opengrader.core.qa_file import QADocument, QAQuestion, load_qa_markdown
from opengrader.core.question_types import MULTIPLE_CHOICE
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_markdown,
)


EPILOG = """
examples:
  uv run automated-grading --solutions solutions.md --input student-0001.submission.md
  uv run automated-grading --solutions solutions.md --input ./student-submissions/
  uv run automated-grading --solutions Solutions/solutions.md --input samples/student_responses.md
  uv run automated-grading --solutions Solutions/final_exam_solutions.md --input ./Solutions/student_submissions/
"""


@dataclass(frozen=True)
class _SolutionEntry:
    points: float
    expected_answers: tuple[str, ...]


def _expected_answers(
    answers: list[str],
    correct_choice_label: str | None,
) -> tuple[str, ...]:
    if correct_choice_label:
        return (correct_choice_label,)
    return tuple(answers)


def _flatten_solution_entries(
    questions: list[QAQuestion],
) -> dict[str, _SolutionEntry]:
    """Map gradable question IDs to their points and correct answers.

    Questions with subquestions are graded per subquestion, matching how
    submission files record them (q2a, q2b, ...).
    """
    entries: dict[str, _SolutionEntry] = {}
    for question in questions:
        if question.subquestions:
            for subquestion in question.subquestions:
                entries[subquestion.id] = _SolutionEntry(
                    points=float(subquestion.points),
                    expected_answers=_expected_answers(
                        subquestion.answers, subquestion.correct_choice_label
                    ),
                )
            continue
        entries[question.id] = _SolutionEntry(
            points=float(question.points),
            expected_answers=_expected_answers(
                question.answers, question.correct_choice_label
            ),
        )
    return entries


def _solution_entries_for_submission(
    qa_doc: QADocument,
    document: StudentSubmissionDocument,
    submission_path: Path,
) -> dict[str, _SolutionEntry]:
    if not qa_doc.version_questions:
        return _flatten_solution_entries(qa_doc.questions)

    version = (document.assignment_version or "").strip()
    available = ", ".join(qa_doc.version_questions)
    if not version:
        raise ValueError(
            f"{submission_path} is missing assignment_version; "
            f"available QA versions: {available}"
        )

    version_by_normalized = {
        candidate.strip().lower(): candidate
        for candidate in qa_doc.version_questions
    }
    selected = version_by_normalized.get(version.lower())
    if selected is None:
        raise ValueError(
            f"{submission_path} has assignment_version {version!r}; "
            f"available QA versions: {available}"
        )
    return _flatten_solution_entries(qa_doc.version_questions[selected])


def grade_student_file(student_path: Path, qa_doc: QADocument) -> None:
    """Score a submission against the QA solutions and update its file."""
    document = load_student_submission_markdown(student_path)
    solutions = _solution_entries_for_submission(qa_doc, document, student_path)

    content = student_path.read_text()
    updated = content
    for question in document.questions:
        entry = solutions.get(question.question_id)
        if entry is None:
            continue

        response = question.response_text.strip()
        if not response:
            continue

        is_correct = response in entry.expected_answers
        points_awarded: float | None = None
        if question.question_type == MULTIPLE_CHOICE:
            points_awarded = entry.points if is_correct else 0.0
        elif is_correct:
            points_awarded = entry.points

        updated = update_student_submission_markdown(
            updated,
            StudentSubmissionGradeUpdate(
                question_id=question.question_id,
                possible_points=entry.points,
                points_awarded=points_awarded,
                analysis="Correct" if is_correct else "",
            ),
        )

    if updated != content:
        student_path.write_text(updated)
    print(f"Successfully graded and updated {student_path.name}")


def _submission_paths(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    # Match SubmissionFilesProvider discovery: submission files may sit
    # directly in the directory or one level down in per-student folders.
    direct = sorted(input_path.glob("*.submission.md"))
    nested = sorted(input_path.glob("*/*.submission.md"))
    return direct + nested


def setup_args() -> ArgumentParser:
    parser = ArgumentParser(
        description="Grade student markdown files using a solutions template.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--solutions", required=True, help="Path to the solutions markdown file"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a single student file or a directory of files",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> None:
    parser = setup_args()
    args = parser.parse_args(argv)

    solutions_path = Path(args.solutions)
    if not solutions_path.exists():
        print(f"Error: Solutions file not found at {solutions_path}")
        sys.exit(1)

    qa_doc = load_qa_markdown(solutions_path)

    input_path = Path(args.input)
    if not input_path.is_file() and not input_path.is_dir():
        print(f"Error: Input path '{args.input}' is not a file or directory.")
        sys.exit(1)

    failures = 0
    for md_file in _submission_paths(input_path):
        try:
            grade_student_file(md_file, qa_doc)
        except ValueError as exc:
            failures += 1
            print(f"Error grading {md_file}: {exc}", file=sys.stderr)

    if failures:
        print(f"{failures} file(s) could not be graded.", file=sys.stderr)


if __name__ == "__main__":
    run_cli()
