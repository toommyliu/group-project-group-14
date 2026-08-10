"""Print a prioritized "needs review" triage list for a set of submissions.

Turns the silent anomalies the extraction/grading pipeline already knows about
(unmatched student ids, empty handwriting transcriptions, low-confidence AI
grades, whole-assignment zero sweeps, …) into an explicit list a grader can work
through before scores reach Canvas. Reads only the persisted ``.submission.md``
files — it never re-runs OCR.
"""

from __future__ import annotations

import json
import re
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

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

EPILOG = """
examples:
  uv run review-queue --input out/student-submissions/
  uv run review-queue --input out/student-submissions/ --include-ungraded
  uv run review-queue --input out/student-submissions/ --ai-confidence 90
  uv run review-queue --input out/student-submissions/ --roster roster.tsv
  uv run review-queue --input out/student-submissions/ --json
"""

_SEVERITY_GLYPH = {"high": "●", "medium": "◐", "low": "○"}


def _submission_paths(input_path: Path) -> list[Path]:
    """Discover submission files, matching SubmissionFilesProvider's rules."""
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


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "List submissions that need human review — likely OCR/extraction "
            "errors and low-confidence AI grades — ranked most severe first."
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
        "--include-ungraded",
        action="store_true",
        help="Also list questions that are simply not graded yet (low priority).",
    )
    parser.add_argument(
        "--ai-confidence",
        type=int,
        default=DEFAULT_AI_CONFIDENCE_FLOOR,
        help=(
            "Flag AI-suggested grades below this confidence percent "
            "(default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--ocr-confidence",
        type=float,
        default=DEFAULT_OCR_CONFIDENCE_FLOOR,
        help=(
            "Flag responses whose OCR transcription confidence is below this "
            "percent (default: %(default)s). VLM-transcribed responses carry no "
            "confidence and are never flagged by this."
        ),
    )
    parser.add_argument(
        "--identity-confidence",
        type=float,
        default=DEFAULT_IDENTITY_CONFIDENCE_FLOOR,
        help=(
            "Flag submissions whose student name/id was read (or roster-matched) "
            "below this confidence percent (default: %(default)s). Stricter than "
            "--ocr-confidence on purpose: a shaky answer costs one question, a "
            "shaky identity posts the whole exam to the wrong person."
        ),
    )
    parser.add_argument(
        "--id-pattern",
        default=None,
        help=(
            "Regex a valid student id must fully match; ids that don't are "
            "flagged as OCR misreads. Default: at least 5 alphanumerics."
        ),
    )
    parser.add_argument(
        "--roster",
        default=None,
        help=(
            "Tab-separated roster file ('ID<TAB>NAME' per line, the same file "
            "passed to submission-extractor --id-file). Roster students with no "
            "matching submission are flagged, catching assignments that were never "
            "scanned or extracted before grades reach Canvas."
        ),
    )
    parser.add_argument(
        "--no-rubric-drift",
        action="store_true",
        help=(
            "Skip the check for grades awarded before their question's rubric "
            "was last edited. On by default (it reads the audit journal that is "
            "already in the folder); switch it off for a cohort where the "
            "rubric was edited deliberately after grading was finished."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the flags as a JSON array instead of a human-readable list.",
    )
    parser.add_argument(
        "--fail-on-high",
        action="store_true",
        help="Exit non-zero if any high-severity item is found (for scripting).",
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

    kwargs: dict[str, object] = {
        "ai_confidence_floor": args.ai_confidence,
        "ocr_confidence_floor": args.ocr_confidence,
        "identity_confidence_floor": args.identity_confidence,
        "include_ungraded": args.include_ungraded,
    }
    if args.id_pattern is not None:
        kwargs["id_pattern"] = re.compile(args.id_pattern)
    if args.roster is not None:
        roster_path = Path(args.roster)
        if not roster_path.exists():
            print(f"Error: roster file not found: {roster_path}", file=sys.stderr)
            sys.exit(1)
        kwargs["roster"] = load_roster_students(roster_path)

    # Open regrade requests (student grade disputes) live alongside the
    # submissions in .opengrader/regrades.jsonl; fold them into triage so a
    # dispute surfaces at the top of the queue automatically.
    regrade_requests = open_regrade_requests(input_path)
    if regrade_requests:
        kwargs["regrade_requests"] = regrade_requests

    # Grades awarded under a rubric that has since been edited. Read from the
    # audit journal alongside the submissions, and folded in automatically for
    # the same reason the disputes above are: a fairness problem nobody thought
    # to ask about is one nobody will pass a flag for. Absent journal ⇒ nothing
    # to compare ⇒ no flags, so a folder graded before journalling is silent
    # rather than wrong.
    if not args.no_rubric_drift:
        stale_grades = detect_rubric_drift(read_journal(resolve_history_dir(input_path)))
        if stale_grades:
            kwargs["stale_grades"] = stale_grades

    flags = build_review_flags(documents, **kwargs)  # type: ignore[arg-type]

    if args.as_json:
        print(json.dumps([_flag_to_dict(flag) for flag in flags], indent=2))
    else:
        _print_human(flags)

    if args.fail_on_high and any(flag.severity == "high" for flag in flags):
        sys.exit(2)
