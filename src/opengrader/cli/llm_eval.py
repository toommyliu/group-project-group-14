"""Measure an LLM grader against submissions a human already graded."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, ai_default, run_command
from opengrader.core import app_config
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
from opengrader.core.llm import LLMError
from opengrader.core.qa_file import load_qa_markdown
from opengrader.core.question_types import humanize_question_label
from opengrader.core.submissions import load_student_submission_markdown

EPILOG = """\
\b
Examples:
  opengrader llm-eval --solutions solutions.md --input out/student-submissions/
  opengrader llm-eval --solutions solutions.md --input out/student-submissions/ --llm-model llama3.1 --llm-model mistral
  opengrader llm-eval --solutions solutions.md --input out/student-submissions/ --target-agreement 0.95
  opengrader llm-eval --solutions solutions.md --input out/student-submissions/ --limit 40 --json

\b
Pool past classes of the same assignment for a bigger sample (one shared answer key):
  opengrader llm-eval --solutions solutions.md \\
      --input 2026-spring/student-submissions/ \\
      --input 2025-fall/student-submissions/ \\
      --input 2025-spring/student-submissions/

\b
Then grade with the number it suggests:
  opengrader automated-grading --solutions solutions.md --input out/student-submissions/ --llm --llm-min-confidence 0.85

Read-only: this never writes a grade. It only asks the model to re-grade answers
a human already marked, and compares.
"""


def discover_submission_paths(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    direct = sorted(input_path.glob("*.submission.md"))
    nested = sorted(input_path.glob("*/*.submission.md"))
    return direct + nested


def resolve_classes(inputs: Sequence[str]) -> list[Path]:
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
    tails = [list(reversed(Path(root).parts)) for root in roots]
    longest = max((len(tail) for tail in tails), default=0)
    for depth in range(1, longest + 1):
        labels = ["/".join(reversed(tail[:depth])) for tail in tails]
        if len(set(labels)) == len(labels):
            return labels
    return ["/".join(reversed(tail)) for tail in tails]


def collect_targets(
    roots: Sequence[Path],
    qa_doc,  # noqa: ANN001
    *,
    warn: Callable[[str], None] | None = None,
    label_classes: bool = False,
    min_transcription_confidence: float = MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
) -> tuple[list[EvalTarget], SkipTally]:
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
    cases: list[EvalCase] = []
    for model in models:
        generate_fn = generate_for(model)
        for index, target in enumerate(targets, start=1):
            if progress is not None:
                progress(model, index, len(targets), target)
            try:
                cases.append(evaluate_target(target, model=model, generate=generate_fn))
            except LLMError as exc:
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
        print(
            f"  ({tally.low_confidence} answer(s) were not sent to any model "
            "because the OCR does not trust the transcription; production "
            "skips these too. A large number here is a scan-quality problem.)"
        )
    if tally.ungraded:
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
    previous_applied: int | None = None
    for row in evaluation.thresholds:
        if not row.has_sample and row.threshold > 0:
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
        assert row is not None
        print(
            f"  → --llm-min-confidence {recommendation.threshold:.2f}"
            f"   grades {row.applied}/{report.cases_per_model} "
            f"({_pct(row.coverage)}) at {_pct(row.agreement_rate)} agreement, "
            f"leaving {row.deferred} for you"
        )
        print(f"    {recommendation.reason}")
    else:
        print(f"  → no recommended threshold: {recommendation.reason}")

    if evaluation.worst_disagreements:
        print("\n  Biggest disagreements (read these yourself)")
        for case in evaluation.worst_disagreements:
            who = case.target.student_name or case.target.student_id or "?"
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


def _default_generate_factory(
    host: str, timeout_seconds: float
) -> Callable[[str], GenerateFn]:
    def factory(model: str) -> GenerateFn:
        resolved = app_config.with_overrides(
            host=host, text_model=model, timeout_seconds=timeout_seconds
        )
        try:
            app_config.ensure_ready(resolved, purpose="text")
        except (app_config.CloudConsentRequired, app_config.CloudDisabledError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            print(
                f"Fix: run 'opengrader providers consent {resolved.provider.name}' "
                "(or open AI Setup), then retry.",
                file=sys.stderr,
            )
            sys.exit(2)
        return app_config.text_generate(resolved, response_format=GRADE_SUGGESTION_SCHEMA)

    return factory


@click.command("llm-eval", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--solutions", required=True, help="Path to the QA solutions markdown file."
)
@click.option(
    "--input",
    "inputs",
    required=True,
    multiple=True,
    metavar="PATH",
    help=(
        "A graded submission file, or a directory of them. The more free "
        "responses a human has graded, the sharper the answer — so repeat "
        "this to pool several classes that share this answer key (the same "
        "assignment given in past terms). Same rubric and same point scale, just "
        "more students."
    ),
)
@click.option(
    "--llm-model",
    "llm_models",
    multiple=True,
    metavar="MODEL",
    show_default="from AI Setup",
    help="Ollama model to evaluate. Repeat to compare several.",
)
@click.option(
    "--llm-host",
    default=ai_default("host"),
    show_default="from AI Setup",
    help="Ollama server URL.",
)
@click.option(
    "--llm-timeout",
    type=float,
    default=app_config.DEFAULT_TIMEOUT_SECONDS,
    show_default=True,
    help="Per-request timeout in seconds.",
)
@click.option(
    "--target-agreement",
    type=float,
    default=DEFAULT_TARGET_AGREEMENT,
    show_default=True,
    metavar="RATE",
    help=(
        "How often a recommended threshold's grades must match the human's, "
        "0-1. Raise it for a high-stakes assignment."
    ),
)
@click.option(
    "--points-tolerance",
    type=float,
    default=0.0,
    show_default=True,
    metavar="POINTS",
    help=(
        "Count a grade as agreeing when it is within this many points of "
        "the human's (0 means exact agreement only)."
    ),
)
@click.option(
    "--min-sample",
    type=int,
    default=DEFAULT_MIN_SAMPLE,
    show_default=True,
    metavar="N",
    help=(
        "A threshold covering fewer than N answers is too small to "
        "recommend from."
    ),
)
@click.option(
    "--bucket-size",
    type=float,
    default=DEFAULT_BUCKET_SIZE,
    show_default=True,
    metavar="WIDTH",
    help="Width of a confidence bucket in the breakdown.",
)
@click.option(
    "--threshold-step",
    type=float,
    default=DEFAULT_THRESHOLD_STEP,
    show_default=True,
    metavar="STEP",
    help="Spacing of the candidate thresholds tried.",
)
@click.option(
    "--show-disagreements",
    type=int,
    default=5,
    show_default=True,
    metavar="N",
    help="How many of the worst disagreements to print.",
)
@click.option(
    "--limit",
    type=int,
    metavar="N",
    help=(
        "Evaluate at most N answers per model — a total across every "
        "class, not per folder — for a quick first look before committing "
        "to a full pass. The N are spread across students (and classes) "
        "rather than taken from the first few, so a small sample still "
        "describes the whole set."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the report as JSON on stdout instead of a readable summary.",
)
@click.option(
    "--llm-min-transcription-confidence",
    type=float,
    default=MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
    show_default=True,
    metavar="PERCENT",
    help=(
        "Exclude answers whose OCR transcription confidence is below this "
        "(0-100). Must match what "
        "'opengrader automated-grading' runs with, since a threshold is only valid "
        "for the population production actually grades."
    ),
)
@click.option(
    "--quiet",
    is_flag=True,
    help="Do not print per-answer progress to stderr.",
)
@click.option(
    "--yes-send-to-cloud",
    is_flag=True,
    help=(
        "Confirm sending these already-graded answers to a cloud AI "
        "provider for evaluation. The scale and destination are shown "
        "first; nothing is sent without this or an interactive yes."
    ),
)
@click.pass_obj
def command(
    generate_factory: Callable[[str], GenerateFn] | None,
    *,
    solutions: str,
    inputs: tuple[str, ...],
    llm_models: tuple[str, ...],
    llm_host: str,
    llm_timeout: float,
    target_agreement: float,
    points_tolerance: float,
    min_sample: int,
    bucket_size: float,
    threshold_step: float,
    show_disagreements: int,
    limit: int | None,
    as_json: bool,
    llm_min_transcription_confidence: float,
    quiet: bool,
    yes_send_to_cloud: bool,
) -> LlmEvalReport:
    """Measure how well an LLM grades free responses, by re-grading answers
    a human already marked. Reports agreement per model, per question type
    and per confidence bucket, then recommends an evidence-based
    --llm-min-confidence for opengrader automated-grading. Read-only: no grade is ever
    written.
    """
    solutions_path = Path(solutions)
    if not solutions_path.exists():
        print(f"Error: solutions file not found at {solutions_path}", file=sys.stderr)
        sys.exit(1)
    qa_doc = load_qa_markdown(solutions_path)

    roots = resolve_classes(inputs)
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
        min_transcription_confidence=llm_min_transcription_confidence,
    )
    if limit is not None:
        targets = spread_targets(targets, limit)

    models = llm_models or [app_config.resolve().text_model]

    if not targets:
        report = build_llm_eval_report(
            [],
            skipped=tally,
            cases_per_model=0,
            target_agreement=target_agreement,
            min_sample=min_sample,
            classes=classes,
        )
        if as_json:
            print(json.dumps(report_to_dict(report), indent=2))
        else:
            print_report(report)
        sys.exit(1)

    from opengrader.core import egress

    # Scoped to this process; recorded next to the first input folder given.
    egress.activate(roots[0])

    calls = len(targets) * len(models)

    if generate_factory is None:
        probe = app_config.with_overrides(
            host=llm_host, text_model=models[0], timeout_seconds=llm_timeout
        )
        if probe.kind == "cloud":
            from opengrader.core.cloud_preflight import CloudPreflight, cli_confirm_cloud_send

            cli_confirm_cloud_send(
                probe,
                CloudPreflight(
                    provider_label=probe.provider.label,
                    host=probe.host,
                    counts={"graded-text": calls} if calls else {},
                    students=len(targets),
                ),
                yes_send_to_cloud=yes_send_to_cloud,
                emit=lambda message: print(message, file=sys.stderr),
            )

    factory = generate_factory or _default_generate_factory(
        llm_host, llm_timeout
    )
    print(
        f"Evaluating {len(models)} model(s) over {len(targets)} human-graded "
        f"answer(s) — {calls} model call(s). This takes a while.",
        file=sys.stderr,
    )

    def progress(model: str, index: int, total: int, target: EvalTarget) -> None:
        if quiet:
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
    except (LLMError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    report = build_llm_eval_report(
        cases,
        skipped=tally,
        cases_per_model=len(targets),
        points_tolerance=points_tolerance,
        bucket_size=bucket_size,
        threshold_step=threshold_step,
        target_agreement=target_agreement,
        min_sample=min_sample,
        disagreement_samples=show_disagreements,
        classes=classes,
    )

    if as_json:
        print(json.dumps(report_to_dict(report), indent=2))
    else:
        print_report(report)
    return report


def run_cli(
    argv: Sequence[str],
    *,
    generate_factory: Callable[[str], GenerateFn] | None = None,
) -> LlmEvalReport | None:
    return run_command(command, argv, obj=generate_factory)


if __name__ == "__main__":
    command()
