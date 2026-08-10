"""Are the graders on this assignment marking it the same way?

Two things an instructor running a TA team needs, and one engine underneath both
(:mod:`opengrader.core.analytics.grader_agreement`):

* ``pack`` cuts a **calibration set** — the same small sample of answers, blind
  and ungraded, to every grader. They mark it independently, hand the packets
  back, and ``report --set`` says where the rubric meant different things to
  different people. This is a norming session, and it is the cheap moment to
  find disagreement: the same finding after the pile is graded costs a regrade.

* ``report`` with no set reads the **audit journal** of grading that already
  happened. Overlap there is accidental rather than designed, so the numbers are
  weaker — but they need no meeting and no extra marking, and they are the only
  way to see *drift*, since a returned packet records the grade and not the hour
  it was made.

Leniency is always reported as a **residual** against how each question was
marked overall, never as a raw average: the TA who was handed questions 8-10 of
a hard exam has a low raw mean and may be the fairest person on the team.

Everything except ``pack`` is read-only — no grade, annotation or journal entry
is ever written by this tool, and ``pack`` writes only into the packet
directory and the assignment's own ``.opengrader/`` metadata.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.analytics.grader_agreement import (
    DEFAULT_MIN_DRIFT_SAMPLES,
    DEFAULT_MIN_PAIR_SAMPLES,
    GraderAgreementReport,
    OverlapBasis,
    PairAgreement,
    answers_from_graded_copies,
    answers_from_journal,
    build_grader_agreement_report,
)
from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.handoff.calibration import (
    DEFAULT_SAMPLE_SIZE,
    CalibrationError,
    build_calibration_set,
    collect_calibration_gradings,
    find_calibration_set,
    list_calibration_sets,
)
from opengrader.core.handoff.packet import default_packet_output_dir
from opengrader.core.history import read_journal, resolve_history_dir
from opengrader.core.submissions.student_file import load_student_submission_markdown

EPILOG = """
examples:
  # Before dividing the pile: everyone marks the same 8 answers, blind.
  uv run calibrate pack --submissions out/student-submissions/ \\
      --graders alice,bob,carol --sample 8 --qa-file qa.md
  uv run calibrate list --submissions out/student-submissions/

  # After the packets come back.
  uv run calibrate report --submissions out/student-submissions/ --set calibration-2026-08-05

  # No meeting: read the grading that already happened out of the journal.
  uv run calibrate report --submissions out/student-submissions/
  uv run calibrate report --submissions out/student-submissions/ --json
"""

_BASIS_LABEL: dict[OverlapBasis, str] = {
    "same_work": "the same students' answers",
    "same_answer": "the same answers from different students",
}


def _fmt_signed(value: float | None) -> str:
    """A residual/gap in percentage points, always carrying its sign."""
    if value is None:
        return "  n/a"
    return f"{value:+5.1f}"


def _fmt_percent(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.1f}%"


# ── report rendering ──────────────────────────────────────────────────────


def _print_graders(report: GraderAgreementReport) -> None:
    print("GRADERS")
    print(
        f"  {'grader':<18}{'answers':>8}{'students':>10}"
        f"{'mean':>8}{'vs others':>11}{'drift':>8}"
    )
    for grader in report.graders:
        drift = "  n/a" if grader.drift is None else _fmt_signed(grader.drift.delta)
        print(
            f"  {grader.actor:<18}{grader.answers:>8}{grader.students:>10}"
            f"{_fmt_percent(grader.mean_percent):>8}"
            f"{_fmt_signed(grader.mean_residual):>11}{drift:>8}"
        )
    print(
        "\n  mean       = raw average awarded (confounded by which work they got)\n"
        "  vs others  = points per 100 above/below how those questions were marked overall\n"
        "  drift      = late minus early in their own session, same normalization"
    )


def _print_pairs(pairs: tuple[PairAgreement, ...], basis: OverlapBasis) -> None:
    if not pairs:
        return
    print(f"\nAGREEMENT on {_BASIS_LABEL[basis]}")
    # The pair is padded as one field: padding only the second name leaves the
    # columns ragged, because the first name varies in width too.
    width = max((len(f"{pair.a} <-> {pair.b}") for pair in pairs), default=0)
    for pair in pairs:
        provisional = "" if pair.sufficient else "  (provisional — small sample)"
        label = f"{pair.a} ↔ {pair.b}".ljust(width)
        print(
            f"  {label}{pair.samples:>4} shared  "
            f"{pair.exact_agreement * 100:5.0f}% identical  "
            f"avg gap {pair.mean_absolute_gap:4.1f} pts/100{provisional}"
        )
        if pair.more_generous:
            print(
                f"      {pair.more_generous} marks "
                f"{abs(pair.mean_signed_gap):.1f} points per 100 more generously "
                f"(worst single gap {pair.max_absolute_gap:.0f})"
            )


def _print_report(report: GraderAgreementReport, *, source: str) -> None:
    print(f"Source: {source}")
    print(
        f"{report.total_answers} graded answer(s) by {report.distinct_graders} grader(s)"
        f" across {len(report.questions)} question(s).\n"
    )
    if not report.graders:
        print("Nothing to report: no graded answers were found.")
        return

    _print_graders(report)
    _print_pairs(report.same_work_pairs, "same_work")
    # Same-answer overlap is a strict superset of same-work in a calibration
    # set, so showing both there would say the same thing twice, more weakly.
    if not report.same_work_pairs:
        _print_pairs(report.same_answer_pairs, "same_answer")

    if not report.has_overlap:
        print(
            "\nNo two graders marked comparable work, so they cannot be compared.\n"
            "Each grader's residual above is relative to a norm they are the only\n"
            "contributor to, which is why it sits near zero. Run\n"
            "  uv run calibrate pack --graders <names>\n"
            "to have everyone mark the same sample."
        )
    if report.unweighted_answers:
        print(
            f"\n{report.unweighted_answers} answer(s) excluded: their question "
            "carries no points, so it cannot be marked generously or harshly."
        )


def _report_to_dict(report: GraderAgreementReport, *, source: str) -> dict[str, object]:
    def pair_dict(pair: PairAgreement) -> dict[str, object]:
        return {
            "a": pair.a,
            "b": pair.b,
            "basis": pair.basis,
            "samples": pair.samples,
            "sufficient": pair.sufficient,
            "exact_agreement": pair.exact_agreement,
            "mean_absolute_gap": pair.mean_absolute_gap,
            "mean_signed_gap": pair.mean_signed_gap,
            "max_absolute_gap": pair.max_absolute_gap,
            "more_generous": pair.more_generous,
        }

    return {
        "source": source,
        "total_answers": report.total_answers,
        "unweighted_answers": report.unweighted_answers,
        "has_overlap": report.has_overlap,
        "graders": [
            {
                "actor": grader.actor,
                "answers": grader.answers,
                "students": grader.students,
                "questions": grader.questions,
                "mean_percent": grader.mean_percent,
                "mean_residual": grader.mean_residual,
                "first_graded": grader.first_graded.isoformat()
                if grader.first_graded
                else None,
                "last_graded": grader.last_graded.isoformat()
                if grader.last_graded
                else None,
                "drift": None
                if grader.drift is None
                else {
                    "samples": grader.drift.samples,
                    "early_residual": grader.drift.early_residual,
                    "late_residual": grader.drift.late_residual,
                    "delta": grader.drift.delta,
                },
            }
            for grader in report.graders
        ],
        "same_work_pairs": [pair_dict(pair) for pair in report.same_work_pairs],
        "same_answer_pairs": [pair_dict(pair) for pair in report.same_answer_pairs],
        "questions": [
            {
                "question_id": norm.question_id,
                "answers": norm.answers,
                "graders": norm.graders,
                "mean_percent": norm.mean_percent,
            }
            for norm in report.questions
        ],
    }


# ── subcommands ───────────────────────────────────────────────────────────


def _load_documents(submissions: Path) -> list:
    documents = []
    for path in discover_submission_paths(submissions):
        try:
            documents.append(load_student_submission_markdown(path))
        except (OSError, ValueError) as exc:
            print(f"Warning: could not read {path}: {exc}", file=sys.stderr)
    return documents


def _run_pack(args: Namespace, submissions: Path) -> None:
    graders = [name.strip() for name in args.graders.split(",") if name.strip()]
    output = Path(args.output) if args.output else default_packet_output_dir(submissions)

    try:
        result = build_calibration_set(
            submissions,
            graders,
            output,
            sample_size=args.sample,
            qa_file_path=args.qa_file or None,
            set_id=args.name or "",
            seed=args.seed,
        )
    except CalibrationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    record = result.calibration_set
    print(
        f"Calibration set '{record.set_id}': {record.sample_size} student(s) "
        f"to {len(record.packets)} grader(s), ungraded and anonymous.\n"
    )
    for packet in result.packets:
        print(f"  {packet.packet_path}")
    print(f"\nRecord: {result.record_path}")
    print(
        "\nHand each grader their own folder. They grade it with the ordinary TUI\n"
        "(see the README.txt inside), then hand it back and you run:\n"
        f"  uv run calibrate report --submissions {submissions} --set {record.set_id}\n"
        "\nEach grader sees different codes for the same student, so comparing\n"
        "notes cannot anchor them to each other."
    )


def _run_list(submissions: Path) -> None:
    sets = list_calibration_sets(submissions)
    if not sets:
        print("No calibration sets recorded for this assignment.")
        return
    print(f"{len(sets)} calibration set(s), newest first:\n")
    for record in sets:
        print(
            f"  {record.set_id:<28}{record.sample_size:>3} students  "
            f"{len(record.packets)} graders  {record.created_at[:10]}"
        )
        print(f"      {', '.join(record.graders)}")


def _run_report(args: Namespace, submissions: Path) -> None:
    if args.set_id or args.from_set:
        record = find_calibration_set(submissions, args.set_id)
        if record is None:
            named = f" named {args.set_id}" if args.set_id else ""
            print(
                f"Error: no calibration set{named} for {submissions}. "
                "Run `calibrate list` to see what exists.",
                file=sys.stderr,
            )
            sys.exit(1)

        collection = collect_calibration_gradings(submissions, record)
        for grader, reason in collection.missing:
            print(f"Warning: {grader}'s packet is not usable — {reason}", file=sys.stderr)
        for grading in collection.gradings:
            if grading.untranslated:
                print(
                    f"Warning: {len(grading.untranslated)} submission(s) in "
                    f"{grading.grader}'s packet could not be matched to a student.",
                    file=sys.stderr,
                )
        if collection.returned < 2:
            print(
                f"Error: only {collection.returned} grader(s) returned a graded "
                "packet — agreement needs at least two.",
                file=sys.stderr,
            )
            sys.exit(1)

        answers = answers_from_graded_copies(
            (grading.grader, grading.documents) for grading in collection.gradings
        )
        source = (
            f"calibration set '{record.set_id}' — "
            f"{collection.returned} grader(s) on {record.sample_size} identical answers"
        )
    else:
        entries = read_journal(resolve_history_dir(submissions))
        if not entries:
            print(
                f"Error: no grading history under {resolve_history_dir(submissions)}. "
                "Grade something first, or use --set after a calibration round.",
                file=sys.stderr,
            )
            sys.exit(1)
        adaptation = answers_from_journal(entries, _load_documents(submissions))
        if adaptation.unmatched:
            print(
                f"Warning: {adaptation.unmatched} journal entr(ies) have no "
                "surviving submission and were skipped.",
                file=sys.stderr,
            )
        answers = list(adaptation.answers)
        source = "the audit journal — overlap between graders is incidental"

    report = build_grader_agreement_report(
        answers,
        min_pair_samples=args.min_samples,
        min_drift_samples=args.min_drift_samples,
    )

    if args.as_json:
        print(json.dumps(_report_to_dict(report, source=source), indent=2))
        return
    _print_report(report, source=source)


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Measure whether a team of graders marks an assignment the same "
            "way — from a calibration (norming) round, or from grading that "
            "already happened."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_submissions(sub: ArgumentParser) -> None:
        sub.add_argument(
            "--submissions",
            required=True,
            help="The assignment's submissions directory (or one .submission.md).",
        )

    pack = subparsers.add_parser(
        "pack",
        help="Cut one blind, identical sample packet per grader.",
        description=(
            "Sample the cohort and cut one anonymous, ungraded packet per "
            "grader — every packet holding the same students, each under its "
            "own codes."
        ),
    )
    add_submissions(pack)
    pack.add_argument(
        "--graders",
        required=True,
        help="Comma-separated grader names (at least two — agreement is a comparison).",
    )
    pack.add_argument(
        "--sample",
        type=int,
        default=DEFAULT_SAMPLE_SIZE,
        help=(
            f"How many students each grader marks (default {DEFAULT_SAMPLE_SIZE}). "
            "Larger measures more precisely and gets skipped."
        ),
    )
    pack.add_argument(
        "--qa-file",
        default="",
        help="Answer key to copy into each packet so the TUI opens it as-is.",
    )
    pack.add_argument(
        "--output",
        default="",
        help=(
            "Where to write the packets (default: a grader-copies/ folder beside "
            "the submissions directory, never inside it)."
        ),
    )
    pack.add_argument(
        "--name",
        default="",
        help="Name for this calibration set (default: calibration-<date>).",
    )
    pack.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed for the sample draw, so a lost packet can be re-cut identically.",
    )

    listing = subparsers.add_parser(
        "list", help="List the calibration sets recorded for this assignment."
    )
    add_submissions(listing)

    report = subparsers.add_parser(
        "report",
        help="Report leniency, drift and agreement between graders.",
        description=(
            "Report how each grader marked and how they compare. Reads the "
            "audit journal by default; --set reads a returned calibration round "
            "instead, which is the cleaner measurement."
        ),
    )
    add_submissions(report)
    report.add_argument(
        "--set",
        dest="set_id",
        default="",
        help="Report on this calibration set's returned packets.",
    )
    report.add_argument(
        "--from-set",
        dest="from_set",
        action="store_true",
        help="Use the most recent calibration set (equivalent to --set with its name).",
    )
    report.add_argument(
        "--min-samples",
        type=int,
        default=DEFAULT_MIN_PAIR_SAMPLES,
        help=(
            "Shared answers a grader pair needs before its comparison counts as "
            f"evidence rather than provisional (default {DEFAULT_MIN_PAIR_SAMPLES})."
        ),
    )
    report.add_argument(
        "--min-drift-samples",
        type=int,
        default=DEFAULT_MIN_DRIFT_SAMPLES,
        help=(
            "Graded answers one grader needs before an early-vs-late split is "
            f"reported at all (default {DEFAULT_MIN_DRIFT_SAMPLES})."
        ),
    )
    report.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the report as JSON instead of a human-readable summary.",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)

    submissions = Path(args.submissions)
    if not submissions.exists():
        print(f"Error: submissions path not found: {submissions}", file=sys.stderr)
        sys.exit(1)

    if args.command == "pack":
        _run_pack(args, submissions)
    elif args.command == "list":
        _run_list(submissions)
    else:
        _run_report(args, submissions)
