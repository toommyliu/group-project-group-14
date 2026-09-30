"""Compare machine transcriptions with human corrections."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS
from opengrader.core.analytics.transcription_eval import (
    DEFAULT_ERROR_TOLERANCE,
    DEFAULT_MIN_SAMPLE,
    DEFAULT_TARGET_CATCH,
    AccuracyStats,
    CoverageStats,
    FloorPoint,
    SampleTally,
    TranscriptionEvalReport,
    TranscriptionSample,
    build_coverage,
    build_report_from_samples,
    collapse_curve,
    collect_transcription_samples,
    format_percent,
)
from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.history import read_journal, resolve_history_dir
from opengrader.core.submissions import load_student_submission_markdown

EPILOG = """\
\b
Examples:
  uv run transcribe-eval --input out/student-submissions/
  uv run transcribe-eval --input out/student-submissions/ --target-catch 0.95
  uv run transcribe-eval --input out/student-submissions/ --ignore-case
  uv run transcribe-eval --input past-term/subs/ --input this-term/subs/
  uv run transcribe-eval --input out/student-submissions/ --json

\b
What it measures:
  Only answers a human corrected in the grade screen ('x'). That is a
  deliberately biased sample - graders check what the review queue flagged -
  so the report leads with how much of the cohort it covers. An answer nobody
  checked is not evidence that it was read correctly.
"""


def cohort_labels(roots: list[Path]) -> dict[Path, str]:
    if len(roots) < 2:
        return {root: "" for root in roots}

    labels: dict[Path, str] = {}
    for root in roots:
        parts = root.resolve().parts
        for depth in range(1, len(parts) + 1):
            candidate = "/".join(parts[-depth:])
            if sum(1 for other in roots if "/".join(other.resolve().parts[-depth:]) == candidate) == 1:
                labels[root] = candidate
                break
        else:
            labels[root] = str(root)
    return labels


def _load_cohorts(
    inputs: Sequence[str], *, ignore_actors: Sequence[str]
) -> tuple[list[TranscriptionSample], CoverageStats, SampleTally, list[str]]:
    problems: list[str] = []
    roots: list[Path] = []
    seen: set[Path] = set()
    for raw in inputs:
        root = Path(raw)
        try:
            resolved = root.resolve()
        except OSError:
            resolved = root
        if resolved in seen:
            problems.append(f"{root}: named more than once, counted once")
            continue
        seen.add(resolved)
        if not root.exists():
            problems.append(f"{root}: not found")
            continue
        roots.append(root)

    labels = cohort_labels(roots)
    samples: list[TranscriptionSample] = []
    coverage = CoverageStats()
    tally = SampleTally()

    for root in roots:
        cohort_samples, cohort_tally = collect_transcription_samples(
            read_journal(resolve_history_dir(root)),
            ignore_actors=ignore_actors,
            cohort=labels[root],
        )
        tally = tally.merge(cohort_tally)

        documents = []
        try:
            paths = discover_submission_paths(root)
        except FileNotFoundError as exc:
            problems.append(f"{root}: {exc}")
            paths = []
        for path in paths:
            try:
                documents.append(load_student_submission_markdown(path))
            except (OSError, ValueError) as exc:
                problems.append(f"{path.name}: {exc}")

        samples.extend(cohort_samples)
        coverage = coverage.merge(build_coverage(documents, cohort_samples))

    return samples, coverage, tally, problems


def _accuracy_row(stats: AccuracyStats) -> str:
    return (
        f"  {stats.label:<24} {stats.samples:>4} checked   "
        f"correct {format_percent(stats.exact_rate):>4}   "
        f"CER {stats.mean_cer:.3f}   WER {stats.mean_wer:.3f}"
        + (f"   blank {stats.blank_readings}" if stats.blank_readings else "")
    )


def _floor_row(point: FloorPoint) -> str:
    return (
        f"  {point.floor:>5.0f}%   escalate {point.escalated:>4} "
        f"({format_percent(point.escalation_rate):>4})   "
        f"catches {point.caught:>3}/{point.errors} errors "
        f"({format_percent(point.catch_rate):>4})"
    )


def _print_human(report: TranscriptionEvalReport, *, worst: int) -> None:
    tally = report.tally
    if not report.has_samples:
        print("No corrected transcriptions found, so accuracy cannot be measured.")
        print(
            f"  {tally.transcription_entries} transcription event(s) in the journal; "
            f"{tally.tool_only} answer(s) only ever re-read by a tool."
        )
        print(
            "  Corrections are recorded when a grader fixes an answer with 'x' "
            "on the grade screen."
        )
        return

    coverage = report.coverage
    if coverage.answers_with_text:
        print(
            f"Checked {coverage.answers_corrected} of {coverage.answers_with_text} "
            f"transcribed answers ({format_percent(coverage.sampled_share)}). "
            "Rates below describe those, not the rest."
        )
    else:
        print(
            f"Measured {report.overall.samples} correction(s). "
            "No submissions were readable alongside the journal, so the "
            "sampled share of the cohort is unknown."
        )
    if tally.excluded:
        parts = []
        if tally.tool_only:
            parts.append(f"{tally.tool_only} only re-read by a tool")
        if tally.unattributable:
            parts.append(f"{tally.unattributable} unattributable")
        if tally.oversized:
            parts.append(f"{tally.oversized} too long to compare")
        print(f"  Excluded: {', '.join(parts)}.")
    print()

    overall = report.overall
    print(
        f"Of the {overall.samples} reading(s) a human checked, "
        f"{overall.exact} needed no change ({format_percent(overall.exact_rate)})."
    )
    print(
        f"  Mean character error rate {overall.mean_cer:.3f}  ·  "
        f"word error rate {overall.mean_wer:.3f}  ·  "
        f"median CER {overall.median_cer:.3f}"
    )
    if overall.blank_readings:
        print(
            f"  {overall.blank_readings} of them read *nothing* at all — a crop, "
            "resolution or reader-choice problem rather than a bad reading."
        )
    print()

    if report.by_source:
        print("By reader:")
        for stats in report.by_source:
            print(_accuracy_row(stats))
        print()

    if report.by_confidence:
        print("By the confidence the reader claimed:")
        for stats in report.by_confidence:
            print(_accuracy_row(stats))
        print()

    if report.curve and report.curve[0].total:
        print("Escalation floor (--escalate-confidence), in hindsight:")
        for point in collapse_curve(report.curve):
            print(_floor_row(point))
        print()

    if report.recommended_floor is not None:
        print(
            f"Recommended: submission-extractor --llm-escalate "
            f"--escalate-confidence {report.recommended_floor:g}"
        )
        print(
            f"  The lowest floor that re-reads {format_percent(report.target_catch)} "
            f"of the {report.curve[0].errors} misreads measured above — the same "
            "catch at the fewest model calls."
        )
    else:
        print(
            "No floor recommended: too few errors were measured to tell one "
            "setting from another. Grade more, or lower --min-sample knowingly."
        )

    if worst and report.worst:
        print()
        print("Worst misreads:")
        for sample in report.worst[:worst]:
            rate = sample.character_error_rate(ignore_case=report.ignore_case)
            confidence = (
                f"{sample.confidence:.0f}%" if sample.confidence is not None else "unmeasured"
            )
            where = f"{sample.cohort} · " if sample.cohort else ""
            print(
                f"  {where}{sample.student_label} · {sample.question_id} "
                f"(CER {rate:.2f}, claimed {confidence})"
            )
            print(f"      read: {_excerpt(sample.machine_text)}")
            print(f"      was:  {_excerpt(sample.human_text)}")


def _excerpt(text: str, limit: int = 70) -> str:
    collapsed = " ".join(text.split())
    if not collapsed:
        return "(nothing)"
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _as_json(report: TranscriptionEvalReport) -> dict:
    def stats_json(stats: AccuracyStats) -> dict:
        return {
            "label": stats.label,
            "samples": stats.samples,
            "exact": stats.exact,
            "exact_rate": stats.exact_rate,
            "blank_readings": stats.blank_readings,
            "mean_cer": stats.mean_cer,
            "median_cer": stats.median_cer,
            "mean_wer": stats.mean_wer,
        }

    return {
        "overall": stats_json(report.overall),
        "by_source": [stats_json(stats) for stats in report.by_source],
        "by_confidence": [stats_json(stats) for stats in report.by_confidence],
        "floor_curve": [
            {
                "floor": point.floor,
                "escalated": point.escalated,
                "escalation_rate": point.escalation_rate,
                "caught": point.caught,
                "errors": point.errors,
                "catch_rate": point.catch_rate,
                "precision": point.precision,
                "total": point.total,
            }
            for point in report.curve
        ],
        "recommended_floor": report.recommended_floor,
        "coverage": {
            "answers_with_text": report.coverage.answers_with_text,
            "answers_corrected": report.coverage.answers_corrected,
            "sampled_share": report.coverage.sampled_share,
        },
        "excluded": {
            "transcription_entries": report.tally.transcription_entries,
            "tool_only": report.tally.tool_only,
            "unattributable": report.tally.unattributable,
            "oversized": report.tally.oversized,
        },
        "worst": [
            {
                "cohort": sample.cohort,
                "student": sample.student_label,
                "question_id": sample.question_id,
                "machine_text": sample.machine_text,
                "human_text": sample.human_text,
                "confidence": sample.confidence,
                "source": sample.source,
                "cer": sample.character_error_rate(ignore_case=report.ignore_case),
                "wer": sample.word_error_rate(ignore_case=report.ignore_case),
            }
            for sample in report.worst
        ],
        "settings": {
            "ignore_case": report.ignore_case,
            "tolerance": report.tolerance,
        },
    }


@click.command("transcribe-eval", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--input",
    "inputs",
    multiple=True,
    required=True,
    metavar="PATH",
    help=(
        "A submissions folder (or one .submission.md). Repeat to pool "
        "cohorts read by the same pipeline."
    ),
)
@click.option(
    "--ignore-case",
    is_flag=True,
    help=(
        "Treat a case difference as correct. Off by default: a lost "
        "capital is a real misread."
    ),
)
@click.option(
    "--tolerance",
    type=float,
    default=DEFAULT_ERROR_TOLERANCE,
    metavar="RATE",
    help=(
        "Character error rate below which a reading counts as correct "
        f"(default: {DEFAULT_ERROR_TOLERANCE:g}, i.e. exact)."
    ),
)
@click.option(
    "--target-catch",
    type=float,
    default=DEFAULT_TARGET_CATCH,
    metavar="RATE",
    help=(
        "Share of measured errors a recommended escalation floor must "
        f"catch (0-1, default: {DEFAULT_TARGET_CATCH:g})."
    ),
)
@click.option(
    "--min-sample",
    type=int,
    default=DEFAULT_MIN_SAMPLE,
    metavar="N",
    help=(
        "Recommend nothing when fewer than this many errors were measured "
        f"(default: {DEFAULT_MIN_SAMPLE})."
    ),
)
@click.option(
    "--ignore-actor",
    "ignore_actors",
    multiple=True,
    metavar="NAME",
    help=(
        "Exclude a grader's corrections (repeatable). Tool actors "
        "(name:role) are always excluded — a machine's re-read is not "
        "ground truth."
    ),
)
@click.option(
    "--worst",
    type=int,
    default=5,
    metavar="N",
    help="How many worst misreads to print (default: 5).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the whole report as JSON.")
def command(
    *,
    inputs: tuple[str, ...],
    ignore_case: bool,
    tolerance: float,
    target_catch: float,
    min_sample: int,
    ignore_actors: tuple[str, ...],
    worst: int,
    as_json: bool,
) -> TranscriptionEvalReport:
    """Measure OCR/vision-model transcription accuracy from the
    corrections graders have already made.
    """
    if not 0.0 < target_catch <= 1.0:
        print("error: --target-catch must be between 0 and 1.", file=sys.stderr)
        sys.exit(1)
    if tolerance < 0.0:
        print("error: --tolerance cannot be negative.", file=sys.stderr)
        sys.exit(1)

    samples, coverage, tally, problems = _load_cohorts(
        inputs, ignore_actors=ignore_actors
    )
    for problem in problems:
        print(f"warning: {problem}", file=sys.stderr)

    report = build_report_from_samples(
        samples,
        coverage=coverage,
        tally=tally,
        ignore_case=ignore_case,
        tolerance=tolerance,
        target_catch=target_catch,
        min_sample=min_sample,
        worst_count=max(worst, 0),
    )

    if as_json:
        print(json.dumps(_as_json(report), indent=2))
    else:
        _print_human(report, worst=worst)
    return report


if __name__ == "__main__":  # pragma: no cover
    command()
