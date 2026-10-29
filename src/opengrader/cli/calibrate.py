"""Compare graders using calibration packets or production grades."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, Group
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

EPILOG = """\
\b
Examples:
  # Before dividing the pile: everyone marks the same 8 answers, blind.
  opengrader calibrate pack --submissions out/student-submissions/ \\
      --graders alice,bob,carol --sample 8 --qa-file qa.md
  opengrader calibrate list --submissions out/student-submissions/

\b
  # After the packets come back.
  opengrader calibrate report --submissions out/student-submissions/ --set calibration-2026-08-05

\b
  # No meeting: read the grading that already happened out of the journal.
  opengrader calibrate report --submissions out/student-submissions/
  opengrader calibrate report --submissions out/student-submissions/ --json
"""

_BASIS_LABEL: dict[OverlapBasis, str] = {
    "same_work": "the same students' answers",
    "same_answer": "the same answers from different students",
}


def _fmt_signed(value: float | None) -> str:
    if value is None:
        return "  n/a"
    return f"{value:+5.1f}"


def _fmt_percent(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.1f}%"


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
    if not report.same_work_pairs:
        _print_pairs(report.same_answer_pairs, "same_answer")

    if not report.has_overlap:
        print(
            "\nNo two graders marked comparable work, so they cannot be compared.\n"
            "Each grader's residual above is relative to a norm they are the only\n"
            "contributor to, which is why it sits near zero. Run\n"
            "  opengrader calibrate pack --graders <names>\n"
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


def _load_documents(submissions: Path) -> list:
    documents = []
    for path in discover_submission_paths(submissions):
        try:
            documents.append(load_student_submission_markdown(path))
        except (OSError, ValueError) as exc:
            print(f"Warning: could not read {path}: {exc}", file=sys.stderr)
    return documents


def _submissions_option(function):
    return click.option(
        "--submissions",
        required=True,
        type=click.Path(path_type=Path),
        help="The assignment's submissions directory (or one .submission.md).",
    )(function)


def _check_submissions(submissions: Path) -> None:
    if not submissions.exists():
        print(f"Error: submissions path not found: {submissions}", file=sys.stderr)
        sys.exit(1)


@click.group("calibrate", cls=Group, context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
def command() -> None:
    """Measure whether a team of graders marks an assignment the same
    way — from a calibration (norming) round, or from grading that
    already happened.
    """


@command.command(
    "pack", short_help="Cut one blind, identical sample packet per grader."
)
@_submissions_option
@click.option(
    "--graders",
    required=True,
    help="Comma-separated grader names (at least two — agreement is a comparison).",
)
@click.option(
    "--sample",
    type=int,
    default=DEFAULT_SAMPLE_SIZE,
    help=(
        f"How many students each grader marks (default {DEFAULT_SAMPLE_SIZE}). "
        "Larger measures more precisely and gets skipped."
    ),
)
@click.option(
    "--qa-file",
    default="",
    help="Answer key to copy into each packet so the TUI opens it as-is.",
)
@click.option(
    "--output",
    default="",
    help=(
        "Where to write the packets (default: a grader-copies/ folder beside "
        "the submissions directory, never inside it)."
    ),
)
@click.option(
    "--name",
    "set_name",
    default="",
    help="Name for this calibration set (default: calibration-<date>).",
)
@click.option(
    "--seed",
    type=int,
    help="Seed for the sample draw, so a lost packet can be re-cut identically.",
)
def pack_command(
    *,
    submissions: Path,
    graders: str,
    sample: int,
    qa_file: str,
    output: str,
    set_name: str,
    seed: int | None,
) -> None:
    """Sample the cohort and cut one anonymous, ungraded packet per
    grader — every packet holding the same students, each under its
    own codes.
    """
    _check_submissions(submissions)
    grader_names = [name.strip() for name in graders.split(",") if name.strip()]
    output_dir = Path(output) if output else default_packet_output_dir(submissions)

    try:
        result = build_calibration_set(
            submissions,
            grader_names,
            output_dir,
            sample_size=sample,
            qa_file_path=qa_file or None,
            set_id=set_name or "",
            seed=seed,
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
        f"  opengrader calibrate report --submissions {submissions} --set {record.set_id}\n"
        "\nEach grader sees different codes for the same student, so comparing\n"
        "notes cannot anchor them to each other."
    )


@command.command("list")
@_submissions_option
def list_command(submissions: Path) -> None:
    """List the calibration sets recorded for this assignment."""
    _check_submissions(submissions)
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


@command.command(
    "report", short_help="Report leniency, drift and agreement between graders."
)
@_submissions_option
@click.option(
    "--set",
    "set_id",
    default="",
    help="Report on this calibration set's returned packets.",
)
@click.option(
    "--from-set",
    is_flag=True,
    help="Use the most recent calibration set (equivalent to --set with its name).",
)
@click.option(
    "--min-samples",
    type=int,
    default=DEFAULT_MIN_PAIR_SAMPLES,
    help=(
        "Shared answers a grader pair needs before its comparison counts as "
        f"evidence rather than provisional (default {DEFAULT_MIN_PAIR_SAMPLES})."
    ),
)
@click.option(
    "--min-drift-samples",
    type=int,
    default=DEFAULT_MIN_DRIFT_SAMPLES,
    help=(
        "Graded answers one grader needs before an early-vs-late split is "
        f"reported at all (default {DEFAULT_MIN_DRIFT_SAMPLES})."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the report as JSON instead of a human-readable summary.",
)
def report_command(
    *,
    submissions: Path,
    set_id: str,
    from_set: bool,
    min_samples: int,
    min_drift_samples: int,
    as_json: bool,
) -> None:
    """Report how each grader marked and how they compare. Reads the
    audit journal by default; --set reads a returned calibration round
    instead, which is the cleaner measurement.
    """
    _check_submissions(submissions)
    if set_id or from_set:
        record = find_calibration_set(submissions, set_id)
        if record is None:
            named = f" named {set_id}" if set_id else ""
            print(
                f"Error: no calibration set{named} for {submissions}. "
                "Run `opengrader calibrate list` to see what exists.",
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
        min_pair_samples=min_samples,
        min_drift_samples=min_drift_samples,
    )

    if as_json:
        print(json.dumps(_report_to_dict(report, source=source), indent=2))
        return
    _print_report(report, source=source)


if __name__ == "__main__":
    command()
