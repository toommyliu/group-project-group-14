"""List submissions and answers that need review."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS
from opengrader.core.roster import load_roster_students
from opengrader.core.submissions import ReviewFlag, build_review_flags
from opengrader.core.submissions.regrade import open_regrade_requests
from opengrader.core.analytics.rubric_drift import detect_rubric_drift
from opengrader.core.history import read_journal, resolve_history_dir
from opengrader.core.submissions.review import (
    DEFAULT_AI_CONFIDENCE_FLOOR,
    DEFAULT_IDENTITY_CONFIDENCE_FLOOR,
    DEFAULT_OCR_CONFIDENCE_FLOOR,
)
from opengrader.core.submissions.student_file import load_student_submission_markdown

EPILOG = """\
\b
Examples:
  uv run review-queue --input out/student-submissions/
  uv run review-queue --input out/student-submissions/ --include-ungraded
  uv run review-queue --input out/student-submissions/ --ai-confidence 90
  uv run review-queue --input out/student-submissions/ --roster roster.tsv
  uv run review-queue --input out/student-submissions/ --json
"""

_SEVERITY_GLYPH = {"high": "●", "medium": "◐", "low": "○"}


def _submission_paths(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    direct = sorted(input_path.glob("*.submission.md"))
    nested = sorted(input_path.glob("*/*.submission.md"))
    return direct + nested


def _flag_label(flag: ReviewFlag) -> str:
    who = flag.student_name or flag.student_id or flag.source_pdf or "unknown"
    where = f" · {flag.question_id}" if flag.question_id else ""
    return f"{who}{where}"


def _print_human(flags: list[ReviewFlag]) -> None:
    if not flags:
        print("No submissions need review. ✓")
        return

    counts = {"high": 0, "medium": 0, "low": 0}
    for flag in flags:
        counts[flag.severity] += 1
    print(
        f"{len(flags)} item(s) need review: "
        f"{counts['high']} high, {counts['medium']} medium, {counts['low']} low.\n"
    )

    current: str | None = None
    for flag in flags:
        if flag.severity != current:
            current = flag.severity
            glyph = _SEVERITY_GLYPH[flag.severity]
            print(f"{glyph} {flag.severity.upper()}")
        print(f"    {_flag_label(flag):<40} {flag.reason.value:<24} {flag.detail}")


def _flag_to_dict(flag: ReviewFlag) -> dict[str, str]:
    return {
        "severity": flag.severity,
        "reason": flag.reason.value,
        "student_id": flag.student_id,
        "student_name": flag.student_name,
        "question_id": flag.question_id,
        "source_pdf": flag.source_pdf,
        "detail": flag.detail,
    }


@click.command("review-queue", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--input",
    "input_path",
    required=True,
    type=click.Path(path_type=Path),
    help="A .submission.md file or a directory of them.",
)
@click.option(
    "--include-ungraded",
    is_flag=True,
    help="Also list questions that are simply not graded yet (low priority).",
)
@click.option(
    "--ai-confidence",
    type=int,
    default=DEFAULT_AI_CONFIDENCE_FLOOR,
    show_default=True,
    help="Flag AI-suggested grades below this confidence percent.",
)
@click.option(
    "--ocr-confidence",
    type=float,
    default=DEFAULT_OCR_CONFIDENCE_FLOOR,
    show_default=True,
    help=(
        "Flag responses whose OCR transcription confidence is below this "
        "percent. VLM-transcribed responses carry no confidence and are never "
        "flagged by this."
    ),
)
@click.option(
    "--identity-confidence",
    type=float,
    default=DEFAULT_IDENTITY_CONFIDENCE_FLOOR,
    show_default=True,
    help=(
        "Flag submissions whose student name/id was read (or roster-matched) "
        "below this confidence percent. Stricter than --ocr-confidence on "
        "purpose: a shaky answer costs one question, a shaky identity posts "
        "the whole assignment to the wrong person."
    ),
)
@click.option(
    "--id-pattern",
    help=(
        "Regex a valid student id must fully match; ids that don't are "
        "flagged as OCR misreads. Default: at least 5 alphanumerics."
    ),
)
@click.option(
    "--roster",
    help=(
        "Tab-separated roster file ('ID<TAB>NAME' per line, the same file "
        "passed to submission-extractor --id-file). Roster students with no "
        "matching submission are flagged, catching assignments that were never "
        "scanned or extracted before grades reach Canvas."
    ),
)
@click.option(
    "--solutions",
    help=(
        "The answer key (QA markdown). With it, a paper whose version cannot "
        "be told from a key with several versions is flagged: it is left "
        "ungraded until its version is picked."
    ),
)
@click.option(
    "--no-rubric-drift",
    is_flag=True,
    help=(
        "Skip the check for grades awarded before their question's rubric "
        "was last edited. On by default (it reads the audit journal that is "
        "already in the folder); switch it off for a cohort where the "
        "rubric was edited deliberately after grading was finished."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the flags as a JSON array instead of a human-readable list.",
)
@click.option(
    "--fail-on-high",
    is_flag=True,
    help="Exit non-zero if any high-severity item is found (for scripting).",
)
def command(
    *,
    input_path: Path,
    include_ungraded: bool,
    ai_confidence: int,
    ocr_confidence: float,
    identity_confidence: float,
    id_pattern: str | None,
    roster: str | None,
    solutions: str | None,
    no_rubric_drift: bool,
    as_json: bool,
    fail_on_high: bool,
) -> None:
    """List submissions that need human review — likely OCR/extraction
    errors and low-confidence AI grades — ranked most severe first.
    """
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

    kwargs: dict[str, object] = {
        "ai_confidence_floor": ai_confidence,
        "ocr_confidence_floor": ocr_confidence,
        "identity_confidence_floor": identity_confidence,
        "include_ungraded": include_ungraded,
    }
    if id_pattern is not None:
        kwargs["id_pattern"] = re.compile(id_pattern)
    if roster is not None:
        roster_path = Path(roster)
        if not roster_path.exists():
            print(f"Error: roster file not found: {roster_path}", file=sys.stderr)
            sys.exit(1)
        kwargs["roster"] = load_roster_students(roster_path)

    if solutions is not None:
        from opengrader.core.qa_file import load_qa_markdown

        try:
            kwargs["qa_document"] = load_qa_markdown(Path(solutions))
        except (OSError, ValueError) as exc:
            print(f"Error: could not read the answer key {solutions}: {exc}", file=sys.stderr)
            sys.exit(1)

    regrade_requests = open_regrade_requests(input_path)
    if regrade_requests:
        kwargs["regrade_requests"] = regrade_requests

    if not no_rubric_drift:
        stale_grades = detect_rubric_drift(read_journal(resolve_history_dir(input_path)))
        if stale_grades:
            kwargs["stale_grades"] = stale_grades

    flags = build_review_flags(documents, **kwargs)  # type: ignore[arg-type]

    if as_json:
        print(json.dumps([_flag_to_dict(flag) for flag in flags], indent=2))
    else:
        _print_human(flags)

    if fail_on_high and any(flag.severity == "high" for flag in flags):
        sys.exit(2)


if __name__ == "__main__":
    command()
