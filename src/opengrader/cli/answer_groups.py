"""How much of this assignment is actually distinct work to grade?

Grouping is the biggest lever on grading time — students who wrote the same
answer can be marked once — but nothing said *in advance* how much it would
save, so an instructor deciding whether to grade question-major or hand the pile
to two TAs was guessing. This reports it: per question, how many responses came
in, how many *distinct* answers that is (the exact tier), and how many remain
once near-identical answers are merged (the suggested tier).

``--suggestions`` prints the proposed merges themselves, which is the same list
the TUI's Unify view offers — reading it here first is a cheap way to sanity-
check the threshold on a real cohort before grading against it.

Strictly read-only: no grade, comment, annotation or journal entry is written.
Nothing is ever merged by this tool — a merge is a proposal a grader accepts in
the TUI, and this is the report about them.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.question_types import humanize_question_label
from opengrader.core.submissions.answer_groups import (
    AnswerGroup,
    GroupableResponse,
    group_responses,
)
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    load_student_submission_markdown,
)
from opengrader.core.submissions.suggested_groups import (
    DEFAULT_SUGGESTION_MIN_LENGTH,
    DEFAULT_SUGGESTION_THRESHOLD,
    GroupingEffort,
    SuggestedGroup,
    diff_answer_spans,
    suggest_group_merges,
    summarize_grouping_effort,
)

EPILOG = """
examples:
  uv run answer-groups --input out/student-submissions/
  uv run answer-groups --input out/student-submissions/ --suggestions
  uv run answer-groups --input out/student-submissions/ --threshold 0.9
  uv run answer-groups --input out/student-submissions/ --question q3 --suggestions
  uv run answer-groups --input out/student-submissions/ --json
"""


def _floor_percent(score: float) -> int:
    """Render a similarity as a whole percent, rounding **down**.

    The label reads "at least N% alike", so rounding up would print 100% for a
    pair that is 99.5% alike — a claim of identical answers where there is a
    real difference. Understating is the only safe direction here.
    """
    return int(score * 100)


def _snippet(text: str, width: int = 68) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > width:
        return collapsed[: width - 1] + "…"
    return collapsed


def _ordered_question_ids(documents: list[StudentSubmissionDocument]) -> list[str]:
    """Union of question ids in first-seen order (multi-version safe).

    The same rule the gradebook export uses, so a cohort sitting two versions
    reports every question rather than only the first student's.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for document in documents:
        for question in document.questions:
            if question.question_id not in seen:
                seen.add(question.question_id)
                ordered.append(question.question_id)
    return ordered


def _groups_for_question(
    documents: list[StudentSubmissionDocument], question_id: str
) -> list[AnswerGroup]:
    """Exact answer groups for one question, keyed by the student's own id."""
    responses: list[GroupableResponse] = []
    for document in documents:
        for question in document.questions:
            if question.question_id != question_id:
                continue
            member = (
                document.student_id.strip()
                or document.student_name.strip()
                or document.source_pdf
            )
            responses.append(
                GroupableResponse(
                    member_id=member,
                    response_text=question.response_text,
                    question_type=question.question_type,
                )
            )
            break
    return group_responses(responses)


def _print_effort_table(rows: list[GroupingEffort]) -> None:
    print(f"{'question':<14}{'answers':>9}{'distinct':>10}{'merged':>9}{'to grade':>10}  saving")
    for row in rows:
        label = humanize_question_label(row.question_id)
        percent = row.reduction_percent
        saving = "—" if percent is None else f"{percent:5.0f}% less"
        print(
            f"{label:<14}{row.responses:>9}{row.exact_groups:>10}"
            f"{row.suggested_merges:>9}{row.groups_after_merges:>10}  {saving}"
        )


def _diff_text(reference: str, other: str) -> str:
    """Mark how one answer differs from the answer that would be graded.

    Words this answer adds are wrapped in ``{+ +}`` and words it omits in
    ``{- -}``. A similarity score cannot tell a rewording from a reversal — an
    inserted "not" scores 0.96 — so the difference has to be visible, not
    inferred from two paragraphs printed one above the other.
    """
    parts: list[str] = []
    for span in diff_answer_spans(reference, other):
        if span.kind == "added":
            parts.append("{+" + span.text + "+}")
        elif span.kind == "removed":
            parts.append("{-" + span.text + "-}")
        else:
            parts.append(span.text)
    return " ".join(parts)


def _print_suggestions(question_id: str, suggestions: list[SuggestedGroup]) -> None:
    if not suggestions:
        return
    print(f"\n  {humanize_question_label(question_id)} — {len(suggestions)} proposed merge(s)")
    for suggestion in suggestions:
        print(
            f"    ● {suggestion.size} students across {suggestion.group_count} answers "
            f"— at least {_floor_percent(suggestion.min_score)}% alike"
        )
        reference = suggestion.representative.sample_response_text
        for group in suggestion.groups:
            lead = group is suggestion.representative
            marker = "grade this" if lead else f"+{group.size}"
            body = (
                _snippet(reference)
                if lead
                else _diff_text(reference, group.sample_response_text)
            )
            print(f"        [{marker:>10}] {body}")


def _report_to_dict(
    rows: list[GroupingEffort],
    suggestions_by_question: dict[str, list[SuggestedGroup]],
) -> dict[str, object]:
    return {
        "questions": [
            {
                "question_id": row.question_id,
                "responses": row.responses,
                "blank_responses": row.blank_responses,
                "exact_groups": row.exact_groups,
                "suggested_merges": row.suggested_merges,
                "groups_after_merges": row.groups_after_merges,
                "reduction_percent": row.reduction_percent,
                "suggestions": [
                    {
                        "size": suggestion.size,
                        "added_size": suggestion.added_size,
                        "min_score": suggestion.min_score,
                        "max_score": suggestion.max_score,
                        "answers": [
                            {
                                "sample_response_text": group.sample_response_text,
                                "members": list(group.member_ids),
                                "representative": group is suggestion.representative,
                            }
                            for group in suggestion.groups
                        ],
                    }
                    for suggestion in suggestions_by_question.get(row.question_id, [])
                ],
            }
            for row in rows
        ],
        "totals": {
            "responses": sum(row.responses for row in rows),
            "exact_groups": sum(row.exact_groups for row in rows),
            "groups_after_merges": sum(row.groups_after_merges for row in rows),
        },
    }


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Report how much distinct work a graded set actually contains: "
            "responses, distinct answers, and what remains once near-identical "
            "answers are merged."
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
        "--question",
        default="",
        help="Report only this question id (e.g. q3). Default: every question.",
    )
    parser.add_argument(
        "--suggestions",
        action="store_true",
        help="Also print each proposed merge and the answers it would join.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_SUGGESTION_THRESHOLD,
        help=(
            "Similarity (0-1) two answers need before a merge is proposed "
            f"(default {DEFAULT_SUGGESTION_THRESHOLD}). Every pair inside a "
            "proposal must clear it, not just one."
        ),
    )
    parser.add_argument(
        "--min-length",
        type=int,
        default=DEFAULT_SUGGESTION_MIN_LENGTH,
        help=(
            "Minimum answer length, in characters of normalized text, to be "
            f"considered for a merge (default {DEFAULT_SUGGESTION_MIN_LENGTH})."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the report as JSON instead of a human-readable table.",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    if not 0.0 < args.threshold <= 1.0:
        print("Error: --threshold must be between 0 and 1.", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input path not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    paths = discover_submission_paths(input_path)
    if not paths:
        print(f"Error: no .submission.md files found under {input_path}", file=sys.stderr)
        sys.exit(1)

    documents: list[StudentSubmissionDocument] = []
    for path in paths:
        try:
            documents.append(load_student_submission_markdown(path))
        except (OSError, ValueError) as exc:
            print(f"Warning: could not read {path}: {exc}", file=sys.stderr)

    question_ids = _ordered_question_ids(documents)
    if args.question:
        if args.question not in question_ids:
            print(
                f"Error: question {args.question} not found "
                f"(have: {', '.join(question_ids) or 'none'})",
                file=sys.stderr,
            )
            sys.exit(1)
        question_ids = [args.question]

    rows: list[GroupingEffort] = []
    suggestions_by_question: dict[str, list[SuggestedGroup]] = {}
    for question_id in question_ids:
        groups = _groups_for_question(documents, question_id)
        suggestions = suggest_group_merges(
            groups, threshold=args.threshold, min_length=args.min_length
        )
        suggestions_by_question[question_id] = suggestions
        rows.append(summarize_grouping_effort(question_id, groups, suggestions))

    if args.as_json:
        print(json.dumps(_report_to_dict(rows, suggestions_by_question), indent=2))
        return

    responses = sum(row.responses for row in rows)
    exact = sum(row.exact_groups for row in rows)
    after = sum(row.groups_after_merges for row in rows)
    print(
        f"{len(documents)} submission(s), {len(rows)} question(s): "
        f"{responses} responses → {exact} distinct answers → {after} after "
        f"suggested merges.\n"
    )
    _print_effort_table(rows)

    if args.suggestions:
        total = sum(len(items) for items in suggestions_by_question.values())
        if not total:
            print("\nNo merges proposed at this threshold.")
        for question_id in question_ids:
            _print_suggestions(question_id, suggestions_by_question[question_id])
        if total:
            print(
                "\nNothing here is applied. Accept a merge in the TUI "
                "(Unify Answers) to grade one as a group."
            )
