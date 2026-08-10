"""Measure an LLM grader against submissions a human already graded.

``automated-grading --llm`` hands a model authority over part of an exam, gated
by one number: ``--llm-min-confidence``. Its default of 0.7 was a guess, and so
is every value an instructor has picked since — there has been no way to find
out whether a given model, on a given assignment, is right 95% of the time above
0.8 or 60% of the time everywhere.

This tool answers that. It replays the *exact* grading path
``automated-grading --llm --dry-run`` takes — same answer-key lookup, same
candidate selection, same prompt, same rubric-constrained scoring — over answers
a human has already marked, and reports how often the model agreed, broken down
per model, per question type, and per confidence bucket. It then reads a
threshold off that evidence.

It is **strictly read-only**: no grade, comment, annotation or journal entry is
written. The only side effect is load on the Ollama server.

    uv run llm-eval --solutions solutions.md --input out/student-submissions/
    uv run llm-eval --solutions solutions.md --input out/student-submissions/ \\
        --llm-model llama3.1 --llm-model qwen2.5:7b

``--input`` repeats to **pool classes that share this answer key** — the same
exam given in past terms. That is the one kind of pooling worth doing: same
rubric, same point scale, same questions, just more students, so the agreement
rate describes exactly what it claims to. (Pooling *different* assignments would
average a 4-point short answer with a 20-point essay into a number that
describes neither, which is why the answer key stays singular.)

Engine: ``core/grading/evaluation.py`` (pure); candidate selection is shared with
production grading via ``core/grading/solutions.needs_llm_grade``.
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import urlparse

from opengrader.core import ai_config
from opengrader.core.grading.evaluation import (
    DEFAULT_BUCKET_SIZE,
    DEFAULT_MIN_SAMPLE,
    DEFAULT_TARGET_AGREEMENT,
    DEFAULT_THRESHOLD_STEP,
    AgreementStats,
    EvalCase,
    EvalTarget,
    LlmEvalReport,
    ModelEvaluation,
    SkipTally,
    build_llm_eval_report,
    evaluate_target,
    select_eval_targets,
    spread_targets,
)
from opengrader.core.grading.llm_grader import GRADE_SUGGESTION_SCHEMA, GenerateFn
from opengrader.core.grading.solutions import (
    MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
    solution_entries_for_submission,
)
from opengrader.core.llm import OllamaConfig, OllamaError, ensure_model_available, generate
from opengrader.core.qa_file import load_qa_markdown
from opengrader.core.question_types import humanize_question_label
from opengrader.core.submissions import load_student_submission_markdown

EPILOG = """
examples:
  uv run llm-eval --solutions solutions.md --input out/student-submissions/
  uv run llm-eval --solutions solutions.md --input out/student-submissions/ --llm-model llama3.1 --llm-model mistral
  uv run llm-eval --solutions solutions.md --input out/student-submissions/ --target-agreement 0.95
  uv run llm-eval --solutions solutions.md --input out/student-submissions/ --limit 40 --json

pool past classes of the same exam for a bigger sample (one shared answer key):
  uv run llm-eval --solutions solutions.md \\
      --input 2026-spring/student-submissions/ \\
      --input 2025-fall/student-submissions/ \\
      --input 2025-spring/student-submissions/

then grade with the number it suggests:
  uv run automated-grading --solutions solutions.md --input out/student-submissions/ --llm --llm-min-confidence 0.85

Read-only: this never writes a grade. It only asks the model to re-grade answers
a human already marked, and compares.
"""


# ── collecting the evidence ──────────────────────────────────────────────────


def discover_submission_paths(input_path: Path) -> list[Path]:
    """Find submission files, flat or one directory down.

    Matches `SubmissionFilesProvider` and every other CLI, so "the folder I
    graded" means the same thing here as everywhere else.
    """
    if input_path.is_file():
        return [input_path]
    direct = sorted(input_path.glob("*.submission.md"))
    nested = sorted(input_path.glob("*/*.submission.md"))
    return direct + nested


def resolve_classes(inputs: Sequence[str]) -> list[Path]:
    """Turn the ``--input`` values into distinct class roots, order preserved.

    De-duplicated by resolved path: naming the same folder twice would double
    every one of its answers in the sample, quietly halving the weight of every
    other class. Silently dropping the repeat is right — the user's intent is
    unambiguous and there is nothing for them to decide.
    """
    seen: set[Path] = set()
    roots: list[Path] = []
    for raw in inputs:
        path = Path(raw).expanduser()
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen.add(key)
        roots.append(path)
    return roots


def class_labels(roots: Sequence[Path]) -> list[str]:
    """Name each class by the shortest tail of its path that is still unique.

    The expected on-disk layout is one folder per term, each containing an
    identically-named `student-submissions/` — so a bare basename would label
    every class "student-submissions" and tell the reader nothing. This walks
    up a component at a time until the labels differ: `2026-spring` and
    `2025-fall` when those are the folder names, `2026-spring/student-
    submissions` when the distinguishing part is one level up.

    Callers pass de-duplicated roots (`resolve_classes`), so the loop always
    terminates on a difference rather than exhausting the paths.
    """
    tails = [list(reversed(Path(root).parts)) for root in roots]
    longest = max((len(tail) for tail in tails), default=0)
    for depth in range(1, longest + 1):
        labels = ["/".join(reversed(tail[:depth])) for tail in tails]
        if len(set(labels)) == len(labels):
            return labels
    return ["/".join(reversed(tail)) for tail in tails]


def collect_targets(
    roots: Sequence[Path],
    qa_doc,  # noqa: ANN001 — QADocument
    *,
    warn: Callable[[str], None] | None = None,
    label_classes: bool = False,
    min_transcription_confidence: float = MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
) -> tuple[list[EvalTarget], SkipTally]:
    """Gather every human-graded answer a model would be asked to grade.

    Done once for all models, so each model is measured on an identical set of
    answers — otherwise a comparison between two models would partly be a
    comparison between two samples. A submission that fails to parse is reported
    and skipped rather than aborting the run: one bad file should not cost an
    instructor the whole calibration.

    Several ``roots`` pool classes that share the answer key — the same exam
    given in past terms. Nothing about the aggregation changes, because pooling
    answers is what this already does across students; these just come from more
    folders. ``label_classes`` stamps each target with its folder name, worth
    doing only when there is more than one (otherwise every row would carry the
    same redundant label).
    """
    targets: list[EvalTarget] = []
    tally = SkipTally()
    labels = class_labels(roots) if label_classes else [""] * len(roots)
    for root, class_label in zip(roots, labels):
        for path in discover_submission_paths(root):
            try:
                document = load_student_submission_markdown(path)
            except ValueError as exc:
                if warn is not None:
                    warn(f"skipped {path.name}: {exc}")
                continue
            solutions = solution_entries_for_submission(qa_doc, document)
            file_targets, file_tally = select_eval_targets(
                document,
                solutions,
                class_label=class_label,
                min_transcription_confidence=min_transcription_confidence,
            )
            targets.extend(file_targets)
            tally = tally + file_tally
    return targets, tally


def run_models_over_targets(
    targets: Sequence[EvalTarget],
    models: Sequence[str],
    *,
    generate_for: Callable[[str], GenerateFn],
    progress: Callable[[str, int, int, EvalTarget], None] | None = None,
    warn: Callable[[str], None] | None = None,
) -> list[EvalCase]:
    """Ask every model about every target.

    A transport failure (server died mid-run) is reported and the answer is
    recorded as "no suggestion" rather than aborting: a partial measurement of a
    slow, expensive pass is worth far more than none, and "no suggestion" is
    exactly what production would do with that answer anyway.
    """
    cases: list[EvalCase] = []
    for model in models:
        generate_fn = generate_for(model)
        for index, target in enumerate(targets, start=1):
            if progress is not None:
                progress(model, index, len(targets), target)
            try:
                cases.append(evaluate_target(target, model=model, generate=generate_fn))
            except OllamaError as exc:
                if warn is not None:
                    warn(
                        f"{model}: grading {target.student_id}/{target.question_id} "
                        f"failed: {exc}"
                    )
                cases.append(
                    EvalCase(
                        model=model,
                        target=target,
                        llm_points=None,
                        llm_rubric=(),
                        llm_confidence=None,
                        rationale="",
                    )
                )
    return cases


# ── rendering ────────────────────────────────────────────────────────────────


def _pct(value: float | None) -> str:
    return f"{value:.0%}" if value is not None else "—"


def _points(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "—"


def _print_skips(tally: SkipTally, target_count: int, classes: Sequence[str] = ()) -> None:
    pooled = (
        f" pooled from {len(classes)} classes ({', '.join(classes)})"
        if len(classes) > 1
        else ""
    )
    print(f"Evaluating {target_count} human-graded answer(s) per model{pooled}.")
    if tally.total == 0:
        return
    print(
        "  Not evaluated: "
        f"{tally.objective} graded by rule, "
        f"{tally.numeric} numeric, "
        f"{tally.blank} blank, "
        f"{tally.no_solution} not in the answer key, "
        f"{tally.ungraded} not yet graded by a human, "
        f"{tally.ai_graded} already AI-graded, "
        f"{tally.low_confidence} transcription too weak to grade"
    )
    if tally.low_confidence:
        # Not a grading problem and not fixable by tuning a threshold — say so,
        # or it reads as answers the model declined.
        print(
            f"  ({tally.low_confidence} answer(s) were not sent to any model "
            "because the OCR does not trust the transcription; production "
            "skips these too. A large number here is a scan-quality problem.)"
        )
    if tally.ungraded:
        # The one row an instructor can act on to make the answer sharper.
        print(
            f"  ({tally.ungraded} answer(s) have no human grade to compare "
            "against — grading more of them by hand sharpens this report.)"
        )


def _print_stats(stats: AgreementStats, *, indent: str) -> None:
    print(
        f"{indent}Agreed with the human: {stats.agreements}/{stats.scored}"
        f"  {_pct(stats.agreement_rate)}"
    )
    print(
        f"{indent}Disagreed: {stats.generous} generous (model gave more), "
        f"{stats.harsh} harsh (model gave less)"
        + (
            f", {stats.no_suggestion} no usable answer"
            if stats.no_suggestion
            else ""
        )
    )
    print(
        f"{indent}Points off by: {_points(stats.mean_absolute_error)} average, "
        f"{_points(stats.max_absolute_error)} worst"
        f"  ·  leans {stats.bias} ({_points(stats.mean_signed_error)} signed)"
    )
    if stats.rubric_comparable:
        print(
            f"{indent}Picked the same rubric option(s): "
            f"{stats.rubric_matches}/{stats.rubric_comparable}"
            f"  {_pct(stats.rubric_match_rate)}"
        )


def _print_model(evaluation: ModelEvaluation, report: LlmEvalReport) -> None:
    print(f"\n── {evaluation.model} " + "─" * max(0, 60 - len(evaluation.model)))
    _print_stats(evaluation.overall, indent="  ")

    if len(evaluation.by_question_type) > 1:
        print("\n  By question type")
        for question_type, stats in evaluation.by_question_type:
            print(
                f"    {question_type:<18} {stats.agreements}/{stats.scored}"
                f"  {_pct(stats.agreement_rate)}"
                f"  ±{_points(stats.mean_absolute_error)} pts"
            )

    if evaluation.by_confidence:
        print("\n  By the model's own confidence")
        print(f"    {'confidence':<12}{'n':>4}  {'agreed':>7}  {'±pts':>6}")
        for bucket in evaluation.by_confidence:
            print(
                f"    {bucket.label:<12}{bucket.stats.scored:>4}"
                f"  {_pct(bucket.stats.agreement_rate):>7}"
                f"  {_points(bucket.stats.mean_absolute_error):>6}"
            )

    print("\n  What each --llm-min-confidence would have done")
    print(
        f"    {'min-conf':<9}{'graded':>7}{'coverage':>10}{'agreed':>8}"
        f"{'±pts':>7}{'left to you':>13}"
    )
    # Consecutive thresholds that catch the same answers produce identical rows.
    # Only the first (loosest) of each run is printed: it is the one that would
    # be recommended, and a dozen repeats of it would bury the shape of the
    # curve — which is the only thing this table exists to show.
    previous_applied: int | None = None
    for row in evaluation.thresholds:
        if not row.has_sample and row.threshold > 0:
            # Every stricter threshold is empty too; stop rather than print a
            # tail of dashes.
            break
        if row.applied == previous_applied:
            continue
        previous_applied = row.applied
        print(
            f"    {row.threshold:<9.2f}{row.applied:>7}{_pct(row.coverage):>10}"
            f"{_pct(row.agreement_rate):>8}{_points(row.mean_absolute_error):>7}"
            f"{row.deferred:>13}"
        )

    recommendation = evaluation.recommendation
    print()
    if recommendation.threshold is not None:
        row = recommendation.row
        assert row is not None  # set together with `threshold`
        print(
            f"  → --llm-min-confidence {recommendation.threshold:.2f}"
            f"   grades {row.applied}/{report.cases_per_model} "
            f"({_pct(row.coverage)}) at {_pct(row.agreement_rate)} agreement, "
            f"leaving {row.deferred} for you"
        )
        print(f"    {recommendation.reason}")
    else:
        print(f"  → no recommended threshold: {recommendation.reason}")

    # Printed in both cases, and deliberately so: when no threshold is
    # recommendable, these are the answers that say why.
    if evaluation.worst_disagreements:
        print("\n  Biggest disagreements (read these yourself)")
        for case in evaluation.worst_disagreements:
            who = case.target.student_name or case.target.student_id or "?"
            # Two pooled classes can both number their students from s1, so a
            # bare id would point at two different people.
            if case.target.class_label:
                who = f"{who} [{case.target.class_label}]"
            error = case.points_error or 0.0
            print(
                f"    {who} · {humanize_question_label(case.target.question_id)}: "
                f"human {case.target.human_points:g}, model "
                f"{(case.llm_points or 0.0):g} of "
                f"{case.target.possible_points:g} ({error:+g}) "
                f"@ {_pct(case.llm_confidence)} confidence"
            )
            if case.rationale:
                print(f"      model said: {case.rationale}")


def print_report(report: LlmEvalReport) -> None:
    _print_skips(report.skipped, report.cases_per_model, report.classes)
    if not report.models:
        print("\nNothing to evaluate. Grade some free responses by hand first.")
        return
    for evaluation in report.models:
        _print_model(evaluation, report)

    if len(report.models) > 1:
        best = report.best_model
        print("\n── verdict " + "─" * 53)
        if best is None:
            print(
                "  No model reaches "
                f"{report.target_agreement:.0%} agreement on this assignment. "
                "Grade its free responses by hand."
            )
        else:
            row = best.recommendation.row
            assert row is not None
            print(
                f"  Best: {best.model} at "
                f"--llm-min-confidence {best.recommendation.threshold:.2f} — "
                f"grades {_pct(row.coverage)} of the free responses at "
                f"{_pct(row.agreement_rate)} agreement."
            )


# ── JSON ─────────────────────────────────────────────────────────────────────


def _stats_to_dict(stats: AgreementStats) -> dict[str, object]:
    return {
        "cases": stats.cases,
        "scored": stats.scored,
        "agreements": stats.agreements,
        "agreement_rate": stats.agreement_rate,
        "generous": stats.generous,
        "harsh": stats.harsh,
        "no_suggestion": stats.no_suggestion,
        "suggestion_rate": stats.suggestion_rate,
        "rubric_comparable": stats.rubric_comparable,
        "rubric_matches": stats.rubric_matches,
        "rubric_match_rate": stats.rubric_match_rate,
        "mean_absolute_error": stats.mean_absolute_error,
        "mean_signed_error": stats.mean_signed_error,
        "max_absolute_error": stats.max_absolute_error,
        "bias": stats.bias,
    }


def report_to_dict(report: LlmEvalReport) -> dict[str, object]:
    return {
        "cases_per_model": report.cases_per_model,
        "classes": list(report.classes),
        "points_tolerance": report.points_tolerance,
        "target_agreement": report.target_agreement,
        "min_sample": report.min_sample,
        "bucket_size": report.bucket_size,
        "skipped": {
            "objective": report.skipped.objective,
            "numeric": report.skipped.numeric,
            "blank": report.skipped.blank,
            "no_solution": report.skipped.no_solution,
            "ungraded": report.skipped.ungraded,
            "ai_graded": report.skipped.ai_graded,
            "low_confidence": report.skipped.low_confidence,
        },
        "best_model": report.best_model.model if report.best_model else None,
        "models": [
            {
                "model": evaluation.model,
                "overall": _stats_to_dict(evaluation.overall),
                "by_question_type": {
                    question_type: _stats_to_dict(stats)
                    for question_type, stats in evaluation.by_question_type
                },
                "by_confidence": [
                    {
                        "lower": bucket.lower,
                        "upper": bucket.upper,
                        **_stats_to_dict(bucket.stats),
                    }
                    for bucket in evaluation.by_confidence
                ],
                "thresholds": [
                    {
                        "threshold": row.threshold,
                        "applied": row.applied,
                        "deferred": row.deferred,
                        "coverage": row.coverage,
                        "agreements": row.agreements,
                        "agreement_rate": row.agreement_rate,
                        "generous": row.generous,
                        "harsh": row.harsh,
                        "mean_absolute_error": row.mean_absolute_error,
                    }
                    for row in evaluation.thresholds
                ],
                "recommendation": {
                    "llm_min_confidence": evaluation.recommendation.threshold,
                    "target_agreement": evaluation.recommendation.target_agreement,
                    "min_sample": evaluation.recommendation.min_sample,
                    "reason": evaluation.recommendation.reason,
                },
                "worst_disagreements": [
                    {
                        "student_id": case.target.student_id,
                        "student_name": case.target.student_name,
                        "class": case.target.class_label,
                        "question_id": case.target.question_id,
                        "possible_points": case.target.possible_points,
                        "human_points": case.target.human_points,
                        "human_rubric": list(case.target.human_rubric),
                        "llm_points": case.llm_points,
                        "llm_rubric": list(case.llm_rubric),
                        "llm_confidence": case.llm_confidence,
                        "points_error": case.points_error,
                        "rationale": case.rationale,
                    }
                    for case in evaluation.worst_disagreements
                ],
            }
            for evaluation in report.models
        ],
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def _build_parser() -> ArgumentParser:
    # Resolved once, here, rather than as a module-level constant: a teacher's
    # AI Setup choice can change between runs, and reading it only when the
    # parser is actually built keeps a `--help` of some other tool from ever
    # touching the settings file.
    ai = ai_config.resolve()
    parser = ArgumentParser(
        description=(
            "Measure how well an LLM grades free responses, by re-grading "
            "answers a human already marked. Reports agreement per model, per "
            "question type and per confidence bucket, then recommends an "
            "evidence-based --llm-min-confidence for automated-grading. "
            "Read-only: no grade is ever written."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--solutions", required=True, help="Path to the QA solutions markdown file."
    )
    parser.add_argument(
        "--input",
        required=True,
        action="append",
        dest="inputs",
        metavar="PATH",
        help=(
            "A graded submission file, or a directory of them. The more free "
            "responses a human has graded, the sharper the answer — so repeat "
            "this to pool several classes that share this answer key (the same "
            "exam given in past terms). Same rubric and same point scale, just "
            "more students."
        ),
    )
    parser.add_argument(
        "--llm-model",
        action="append",
        dest="llm_models",
        metavar="MODEL",
        help=(
            "Ollama model to evaluate. Repeat to compare several "
            f"(default: {ai.text_model}, from AI Setup)."
        ),
    )
    parser.add_argument(
        "--llm-host",
        default=ai.host,
        help="Ollama server URL (default: %(default)s, from AI Setup).",
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=OllamaConfig.timeout_seconds,
        help="Per-request timeout in seconds (default: %(default)s).",
    )
    parser.add_argument(
        "--auto-ollama",
        action="store_true",
        help=(
            "Start 'ollama serve' if nothing is listening and pull each model "
            "first. Off by default so a scripted run fails fast rather than "
            "downloading several GB."
        ),
    )
    parser.add_argument(
        "--target-agreement",
        type=float,
        default=DEFAULT_TARGET_AGREEMENT,
        metavar="RATE",
        help=(
            "How often a recommended threshold's grades must match the human's, "
            "0-1 (default: %(default)s). Raise it for a high-stakes exam."
        ),
    )
    parser.add_argument(
        "--points-tolerance",
        type=float,
        default=0.0,
        metavar="POINTS",
        help=(
            "Count a grade as agreeing when it is within this many points of "
            "the human's (default: %(default)s — exact agreement only)."
        ),
    )
    parser.add_argument(
        "--min-sample",
        type=int,
        default=DEFAULT_MIN_SAMPLE,
        metavar="N",
        help=(
            "A threshold covering fewer than N answers is too small to "
            "recommend from (default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--bucket-size",
        type=float,
        default=DEFAULT_BUCKET_SIZE,
        metavar="WIDTH",
        help="Width of a confidence bucket in the breakdown (default: %(default)s).",
    )
    parser.add_argument(
        "--threshold-step",
        type=float,
        default=DEFAULT_THRESHOLD_STEP,
        metavar="STEP",
        help="Spacing of the candidate thresholds tried (default: %(default)s).",
    )
    parser.add_argument(
        "--show-disagreements",
        type=int,
        default=5,
        metavar="N",
        help="How many of the worst disagreements to print (default: %(default)s).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Evaluate at most N answers per model — a total across every "
            "class, not per folder — for a quick first look before committing "
            "to a full pass. The N are spread across students (and classes) "
            "rather than taken from the first few, so a small sample still "
            "describes the whole set."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the report as JSON on stdout instead of a readable summary.",
    )
    parser.add_argument(
        "--llm-min-transcription-confidence",
        type=float,
        default=MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
        metavar="PERCENT",
        help=(
            "Exclude answers whose OCR transcription confidence is below this "
            "(0-100, default: %(default)s). Must match what "
            "'automated-grading' runs with, since a threshold is only valid "
            "for the population production actually grades."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Do not print per-answer progress to stderr.",
    )
    return parser


def _start_ollama_server_if_needed(
    config: OllamaConfig, emit: Callable[[str], None]
) -> None:
    """Start ``ollama serve`` if nothing is listening on the configured host.

    A copy of the helper in the split/extract/answer-key/grading CLIs, per the
    repo's self-contained-CLI convention.
    """
    parsed = urlparse(config.host)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 11434
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex((host, port)) == 0:
            return
    emit(f"Starting Ollama server at {config.host}…")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "'ollama' binary not found. Please install Ollama first."
        ) from exc
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex((host, port)) == 0:
                return
    emit("Warning: timed out waiting for Ollama to start.")


def _default_generate_factory(
    host: str, timeout_seconds: float, *, auto_ollama: bool
) -> Callable[[str], GenerateFn]:
    """Build the per-model transport, checking each model is actually there.

    The check is up front for the same reason ``automated-grading`` does it: a
    missing model should cost one clear error, not one warning per answer over a
    pass that then reports a meaningless 0% agreement.
    """
    started = False

    def factory(model: str) -> GenerateFn:
        nonlocal started
        config = OllamaConfig(model=model, host=host, timeout_seconds=timeout_seconds)
        if auto_ollama:
            if not started:
                _start_ollama_server_if_needed(config, lambda msg: print(msg, file=sys.stderr))
                started = True
            print(
                f"Pulling model '{model}' if needed — first run may download "
                "several GB…",
                file=sys.stderr,
            )
            subprocess.run(
                ["ollama", "pull", model],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        ensure_model_available(config)
        return lambda prompt: generate(
            prompt=prompt, config=config, response_format=GRADE_SUGGESTION_SCHEMA
        )

    return factory


def run_cli(
    argv: list[str] | None = None,
    *,
    generate_factory: Callable[[str], GenerateFn] | None = None,
) -> LlmEvalReport | None:
    """Run the evaluation harness.

    ``generate_factory`` injects the transport (model name -> generate function)
    so the whole CLI is testable without an Ollama server.

    Returns the finished report. The console entry point ignores it, but the
    TUI's calibration modal reads the recommended threshold off it **as a
    number** rather than scraping it back out of the printed text — the number
    is the entire point of the tool, and it should not survive or fail on a
    change to a `print` format string.
    """
    args = _build_parser().parse_args(argv)

    solutions_path = Path(args.solutions)
    if not solutions_path.exists():
        print(f"Error: solutions file not found at {solutions_path}", file=sys.stderr)
        sys.exit(1)
    qa_doc = load_qa_markdown(solutions_path)

    roots = resolve_classes(args.inputs)
    for root in roots:
        if not root.exists():
            print(f"Error: input path not found: {root}", file=sys.stderr)
            sys.exit(1)
    if not any(discover_submission_paths(root) for root in roots):
        where = ", ".join(str(root) for root in roots)
        print(f"Error: no .submission.md files found under {where}", file=sys.stderr)
        sys.exit(1)

    def warn(message: str) -> None:
        print(f"Warning: {message}", file=sys.stderr)

    classes = class_labels(roots)
    targets, tally = collect_targets(
        roots,
        qa_doc,
        warn=warn,
        label_classes=len(roots) > 1,
        min_transcription_confidence=args.llm_min_transcription_confidence,
    )
    if args.limit is not None:
        # Spread rather than truncate: see `spread_targets`. Applied to the
        # pooled set, so --limit is a total budget across every class.
        targets = spread_targets(targets, args.limit)

    # `--llm-model` is repeatable (to compare several), so it can't carry a
    # fixed argparse default the way a single-value flag can; falling back to
    # the resolved AI Setup model here (rather than OllamaConfig's literal)
    # keeps this in step with every other tool's default.
    models = args.llm_models or [ai_config.resolve().text_model]

    if not targets:
        # Report the tally anyway: it says *why* there is nothing to measure,
        # which is the actionable part.
        report = build_llm_eval_report(
            [],
            skipped=tally,
            cases_per_model=0,
            target_agreement=args.target_agreement,
            min_sample=args.min_sample,
            classes=classes,
        )
        if args.as_json:
            print(json.dumps(report_to_dict(report), indent=2))
        else:
            print_report(report)
        sys.exit(1)

    factory = generate_factory or _default_generate_factory(
        args.llm_host, args.llm_timeout, auto_ollama=args.auto_ollama
    )

    calls = len(targets) * len(models)
    print(
        f"Evaluating {len(models)} model(s) over {len(targets)} human-graded "
        f"answer(s) — {calls} model call(s). This takes a while.",
        file=sys.stderr,
    )

    def progress(model: str, index: int, total: int, target: EvalTarget) -> None:
        if args.quiet:
            return
        print(
            f"  [{model} {index}/{total}] "
            f"{target.student_id or '?'} {target.question_id}",
            file=sys.stderr,
        )

    try:
        cases = run_models_over_targets(
            targets,
            models,
            generate_for=factory,
            progress=progress,
            warn=warn,
        )
    except (OllamaError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    report = build_llm_eval_report(
        cases,
        skipped=tally,
        cases_per_model=len(targets),
        points_tolerance=args.points_tolerance,
        bucket_size=args.bucket_size,
        threshold_step=args.threshold_step,
        target_agreement=args.target_agreement,
        min_sample=args.min_sample,
        disagreement_samples=args.show_disagreements,
        classes=classes,
    )

    if args.as_json:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        print_report(report)
    return report


def main(argv: list[str] | None = None) -> None:
    """Console entry point.

    A ``console_scripts`` wrapper calls ``sys.exit(main())``, so pointing the
    entry point straight at :func:`run_cli` made a *successful* run print the
    report's ``repr`` and exit 1. The return value exists for the TUI's
    calibration modal, which reads the recommended threshold off it as a number
    rather than scraping stdout; the console never wants it.
    """
    run_cli(argv)


if __name__ == "__main__":
    main()
