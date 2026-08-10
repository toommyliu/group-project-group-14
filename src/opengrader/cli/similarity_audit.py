"""Audit a graded set for answer similarity: did students hand in the same work?

Two audits over one exam, because an instructor asking "did anyone copy?" asks
it once and the answer lives in two disjoint halves of the paper:

* **Free responses** are clustered by near-identical text. Deliberately
  conservative: only free responses (a text diff over "B" and "B" says 100% for
  every honest pair in the class), only answers long enough to carry a signal.
* **Objective answers** — with ``--solutions`` — are audited by the statistic
  that *does* work there: identical **wrong** choices, far more often than the
  class's own distractor spread can explain. Two students picking the same right
  answer is the exam working; two students repeatedly picking the same wrong one
  is the signal. This half needs the answer key, since "wrong" is undefined
  without it, which is why it is opt-in rather than always on.

Both report for a human to judge — neither accuses, grades, or changes anything.
Reads only the persisted ``.submission.md`` files; it never re-runs OCR.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.qa_file import load_qa_markdown
from opengrader.core.submissions import (
    SimilarityFlag,
    build_similarity_flags,
)
from opengrader.core.submissions.answer_patterns import (
    DEFAULT_MIN_SHARED_WRONG,
    DEFAULT_PATTERN_THRESHOLD,
    EXCLUSION_LABELS,
    AnswerPatternFlag,
    AnswerPatternReport,
    build_answer_pattern_report_for_documents,
)
from opengrader.core.submissions.similarity import (
    DEFAULT_MIN_LENGTH,
    DEFAULT_SIMILARITY_THRESHOLD,
)
from opengrader.core.submissions.student_file import load_student_submission_markdown

EPILOG = """
examples:
  uv run similarity-audit --input out/student-submissions/
  uv run similarity-audit --input out/student-submissions/ --threshold 0.9
  uv run similarity-audit --input out/student-submissions/ --json
  uv run similarity-audit --input out/student-submissions/ --fail-on-match

also audit the objective questions (needs the answer key):
  uv run similarity-audit --input out/student-submissions/ --solutions solutions.md
  uv run similarity-audit --input out/student-submissions/ --solutions solutions.md \\
    --pattern-threshold 4.5      # fewer, stronger objective flags
"""

_SEVERITY_GLYPH = {"high": "●", "medium": "◐"}

# How many of a pair's shared wrong answers to print. The full list is always in
# --json; on screen the rarest few are what make the case, and the rest is noise.
_MAX_SHARED_LINES = 8


def _submission_paths(input_path: Path) -> list[Path]:
    """Discover submission files, matching SubmissionFilesProvider's rules."""
    if input_path.is_file():
        return [input_path]
    direct = sorted(input_path.glob("*.submission.md"))
    nested = sorted(input_path.glob("*/*.submission.md"))
    return direct + nested


def _question_heading(flag: SimilarityFlag) -> str:
    version = f" (version {flag.assignment_version})" if flag.assignment_version else ""
    return f"{flag.question_id}{version}"


def _snippet(text: str, width: int = 70) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > width:
        return collapsed[: width - 1] + "…"
    return collapsed


def _print_human(flags: list[SimilarityFlag]) -> None:
    if not flags:
        print("No suspiciously-similar answers found. ✓")
        return

    high = sum(1 for flag in flags if flag.severity == "high")
    medium = len(flags) - high
    print(
        f"{len(flags)} similar-answer cluster(s): "
        f"{high} near-verbatim, {medium} strongly similar.\n"
    )

    current: str | None = None
    for flag in flags:
        if flag.severity != current:
            current = flag.severity
            glyph = _SEVERITY_GLYPH[flag.severity]
            label = "NEAR-VERBATIM" if flag.severity == "high" else "STRONGLY SIMILAR"
            print(f"{glyph} {label}")

        members = ", ".join(member.label for member in flag.members)
        print(
            f"    {_question_heading(flag):<18} {flag.member_count} students "
            f"— up to {flag.max_score * 100:.0f}% alike"
        )
        print(f"        students: {members}")
        for pair in flag.pairs:
            print(
                f"        {pair.score * 100:5.0f}%  {pair.a.label} ↔ {pair.b.label}"
            )
        # Echo one answer so the reviewer can eyeball what matched.
        print(f'        e.g. "{_snippet(flag.members[0].response_text)}"')
        print()


def _print_pattern_scope(report: AnswerPatternReport) -> None:
    """State what the objective audit could and could not look at, first.

    Printed before any finding (or any "nothing found"), following the same rule
    as ``scan-check``: a report that quietly analyzed four questions of a fifty
    question exam and found nothing reads exactly like a clean bill of health.
    """
    analyzed = len(report.analyzed_questions)
    excluded = len(report.excluded_questions)
    print(
        f"Objective answers: {analyzed} of {analyzed + excluded} rule-graded "
        "question(s) could be analyzed."
    )
    if excluded:
        by_reason: dict[str, list[str]] = {}
        for item in report.excluded_questions:
            label = EXCLUSION_LABELS[item.reason]
            by_reason.setdefault(label, []).append(item.question_id)
        for label, question_ids in by_reason.items():
            print(f"    not analyzed ({label}): {', '.join(question_ids)}")
    if not report.analyzed:
        # Not a pass — there was nothing to test. Say which, since the two look
        # identical from the outside and only one of them is reassuring.
        print(
            "    Nothing to compare: this exam has no rule-graded question with a "
            "spread of wrong answers.\n"
            "    (True/False questions are excluded by design — with one wrong "
            "answer available, every\n"
            "     wrong student matches every other by construction.)\n"
        )
        return
    print(
        f"    {report.pairs_compared} student pair(s) compared; about "
        f"{report.expected_by_chance:.2f} flag(s) would be expected from chance "
        f"alone at z ≥ {report.threshold:g}.\n"
    )


def _print_pattern_flag(flag: AnswerPatternFlag) -> None:
    version = f" (version {flag.assignment_version})" if flag.assignment_version else ""
    print(
        f"    {flag.a.label} ↔ {flag.b.label}{version}\n"
        f"        {flag.shared_wrong_count} identical wrong answers "
        f"({flag.expected_shared:.1f} expected) · z = {flag.z_score:.1f}"
    )
    index = flag.harpp_hogan_index
    index_text = "n/a (they never differed)" if index is None else f"{index:.2f}"
    print(
        f"        both wrong on {flag.both_wrong_count}, answered differently on "
        f"{flag.difference_count} · Harpp-Hogan index {index_text}"
    )
    # Rarest agreements first (the engine's order), and only the leading few:
    # a wholesale copier can share twenty answers, and printing all of them
    # buries the next pair rather than telling the reader anything more.
    for shared in flag.shared_wrong[:_MAX_SHARED_LINES]:
        print(
            f'        {shared.question_id:<8} both "{shared.answer}" '
            f'(correct: {shared.expected_answer or "?"}) '
            f"— {shared.match_probability * 100:.0f}% of wrong pairs agree here"
        )
    remaining = flag.shared_wrong_count - _MAX_SHARED_LINES
    if remaining > 0:
        print(f"        … and {remaining} more identical wrong answer(s)")
    print()


def _print_patterns_human(report: AnswerPatternReport) -> None:
    _print_pattern_scope(report)
    if not report.analyzed:
        return
    if not report.flags:
        print("No implausible objective answer patterns found. ✓\n")
        return

    high = sum(1 for flag in report.flags if flag.severity == "high")
    medium = len(report.flags) - high
    print(
        f"{len(report.flags)} objective answer-pattern flag(s): "
        f"{high} extreme, {medium} notable.\n"
    )
    current: str | None = None
    for flag in report.flags:
        if flag.severity != current:
            current = flag.severity
            glyph = _SEVERITY_GLYPH[flag.severity]
            label = "EXTREME" if flag.severity == "high" else "NOTABLE"
            print(f"{glyph} {label}")
        _print_pattern_flag(flag)
    print(
        "These are pairs to look at, not conclusions: the statistic cannot tell "
        "copying from two\nstudents who revised together from the same wrong "
        "notes."
    )


def _flag_to_dict(flag: SimilarityFlag) -> dict[str, object]:
    return {
        "severity": flag.severity,
        "kind": flag.kind.value,
        "question_id": flag.question_id,
        "assignment_version": flag.assignment_version,
        "max_score": flag.max_score,
        "member_count": flag.member_count,
        "members": [
            {
                "student_id": member.student_id,
                "student_name": member.student_name,
                "source_pdf": member.source_pdf,
                "response_text": member.response_text,
            }
            for member in flag.members
        ],
        "pairs": [
            {
                "score": pair.score,
                "a": {"student_id": pair.a.student_id, "student_name": pair.a.student_name},
                "b": {"student_id": pair.b.student_id, "student_name": pair.b.student_name},
            }
            for pair in flag.pairs
        ],
    }


def _pattern_report_to_dict(report: AnswerPatternReport) -> dict[str, object]:
    return {
        "analyzed": report.analyzed,
        "threshold": report.threshold,
        "min_shared_wrong": report.min_shared_wrong,
        "analyzed_questions": list(report.analyzed_questions),
        "excluded_questions": [
            {
                "question_id": item.question_id,
                "assignment_version": item.assignment_version,
                "reason": item.reason.value,
                "wrong_count": item.wrong_count,
                "distinct_wrong_answers": item.distinct_wrong_answers,
            }
            for item in report.excluded_questions
        ],
        "pairs_compared": report.pairs_compared,
        "expected_by_chance": report.expected_by_chance,
        "flags": [
            {
                "severity": flag.severity,
                "kind": flag.kind.value,
                "assignment_version": flag.assignment_version,
                "z_score": flag.z_score,
                "expected_shared": flag.expected_shared,
                "shared_wrong_count": flag.shared_wrong_count,
                "both_wrong_count": flag.both_wrong_count,
                "difference_count": flag.difference_count,
                "compared_questions": flag.compared_questions,
                "harpp_hogan_index": flag.harpp_hogan_index,
                "members": [
                    {
                        "student_id": member.student_id,
                        "student_name": member.student_name,
                        "source_pdf": member.source_pdf,
                    }
                    for member in flag.members
                ],
                "shared_wrong": [
                    {
                        "question_id": shared.question_id,
                        "answer": shared.answer,
                        "expected_answer": shared.expected_answer,
                        "match_probability": shared.match_probability,
                    }
                    for shared in flag.shared_wrong
                ],
            }
            for flag in report.flags
        ],
    }


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Audit a graded submission set for answer similarity — cluster "
            "students whose free-response answers are near-identical, and (with "
            "--solutions) flag pairs whose incorrect objective answers agree far "
            "more often than chance allows."
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
        "--solutions",
        help=(
            "QA answer-key file. Enables the objective (multiple-choice) half of "
            "the audit: pairs of students who gave identical *wrong* answers far "
            "more often than the class's own spread of wrong answers predicts. "
            "The key is required because 'shared wrong answer' is undefined "
            "without it."
        ),
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_SIMILARITY_THRESHOLD,
        help=(
            "Free-response similarity (0-1) at or above which a pair is flagged "
            f"(default {DEFAULT_SIMILARITY_THRESHOLD}). Raise it for fewer, "
            "tighter matches."
        ),
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=DEFAULT_MIN_LENGTH,
        help=(
            "Minimum answer length, in characters of normalized text, to be "
            f"compared (default {DEFAULT_MIN_LENGTH}). Shorter answers are "
            "skipped: a whole class legitimately gives the same short answer."
        ),
    )
    parser.add_argument(
        "--pattern-threshold",
        type=float,
        default=DEFAULT_PATTERN_THRESHOLD,
        help=(
            "Objective audit: standard deviations above the expected number of "
            "identical wrong answers at which a pair is flagged (default "
            f"{DEFAULT_PATTERN_THRESHOLD:g}). The test runs over every pair in "
            "the class, so this is deliberately stricter than the usual 3.0 — "
            "raise it further on a very large class. Needs --solutions."
        ),
    )
    parser.add_argument(
        "--min-shared-wrong",
        type=int,
        default=DEFAULT_MIN_SHARED_WRONG,
        help=(
            "Objective audit: how many identical wrong answers a pair must share "
            f"before its score is considered at all (default "
            f"{DEFAULT_MIN_SHARED_WRONG}). One agreement on a rare wrong answer "
            "scores enormously and is a coincidence. Needs --solutions."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help=(
            "Emit the clusters as a JSON array instead of a human-readable list. "
            "With --solutions the output is instead an object with "
            "'free_response' and 'objective' keys, so the existing array shape is "
            "unchanged for callers that don't pass an answer key."
        ),
    )
    parser.add_argument(
        "--fail-on-match",
        action="store_true",
        help=(
            "Exit non-zero if anything is flagged — a similar-answer cluster or, "
            "with --solutions, an objective answer-pattern pair (for "
            "scripting/CI)."
        ),
    )
    return parser


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    if not 0.0 < args.threshold <= 1.0:
        print("Error: --threshold must be between 0 and 1.", file=sys.stderr)
        sys.exit(1)
    if args.min_shared_wrong < 1:
        print("Error: --min-shared-wrong must be at least 1.", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input path not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    qa_doc = None
    if args.solutions:
        qa_path = Path(args.solutions)
        if not qa_path.exists():
            print(f"Error: solutions file not found: {qa_path}", file=sys.stderr)
            sys.exit(1)
        qa_doc = load_qa_markdown(qa_path)

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

    flags = build_similarity_flags(
        documents, threshold=args.threshold, min_length=args.min_length
    )
    pattern_report = (
        build_answer_pattern_report_for_documents(
            documents,
            qa_doc,
            threshold=args.pattern_threshold,
            min_shared_wrong=args.min_shared_wrong,
        )
        if qa_doc is not None
        else None
    )

    if args.as_json:
        free_response = [_flag_to_dict(flag) for flag in flags]
        if pattern_report is None:
            print(json.dumps(free_response, indent=2))
        else:
            print(
                json.dumps(
                    {
                        "free_response": free_response,
                        "objective": _pattern_report_to_dict(pattern_report),
                    },
                    indent=2,
                )
            )
    else:
        _print_human(flags)
        if pattern_report is not None:
            print()
            _print_patterns_human(pattern_report)

    found = bool(flags) or bool(pattern_report and pattern_report.flags)
    if args.fail_on_match and found:
        sys.exit(2)
