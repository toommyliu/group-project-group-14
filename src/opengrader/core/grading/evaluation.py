from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal

from opengrader.core.grading.llm_grader import (
    GenerateFn,
    RubricChoice,
    ai_analysis_confidence,
    suggest_free_response_grade,
)
from opengrader.core.grading.solutions import (
    MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
    SolutionEntry,
    needs_llm_grade,
    objective_correctness,
)
from opengrader.core.qa_file.scoring import EPSILON

if TYPE_CHECKING:
    from opengrader.core.submissions.student_file import StudentSubmissionDocument

EvalOutcome = Literal["agree", "generous", "harsh", "no_suggestion"]

DEFAULT_BUCKET_SIZE = 0.1

DEFAULT_THRESHOLD_STEP = 0.05

DEFAULT_TARGET_AGREEMENT = 0.9

DEFAULT_MIN_SAMPLE = 5


@dataclass(frozen=True)
class SkipTally:
    objective: int = 0
    numeric: int = 0
    blank: int = 0
    no_solution: int = 0
    ungraded: int = 0
    ai_graded: int = 0
    low_confidence: int = 0

    def __add__(self, other: "SkipTally") -> "SkipTally":
        return SkipTally(
            objective=self.objective + other.objective,
            numeric=self.numeric + other.numeric,
            blank=self.blank + other.blank,
            no_solution=self.no_solution + other.no_solution,
            ungraded=self.ungraded + other.ungraded,
            ai_graded=self.ai_graded + other.ai_graded,
            low_confidence=self.low_confidence + other.low_confidence,
        )

    @property
    def total(self) -> int:
        return (
            self.objective
            + self.numeric
            + self.blank
            + self.no_solution
            + self.ungraded
            + self.ai_graded
            + self.low_confidence
        )


@dataclass(frozen=True)
class EvalTarget:
    student_id: str
    student_name: str
    question_id: str
    question_type: str
    prompt: str
    expected_answers: tuple[str, ...]
    response_text: str
    rubric_options: tuple[RubricChoice, ...]
    possible_points: float
    human_points: float
    human_rubric: tuple[str, ...]
    class_label: str = ""

    @property
    def person(self) -> tuple[str, str]:
        return (self.class_label, self.student_id)


def select_eval_targets(
    document: StudentSubmissionDocument,
    solutions: dict[str, SolutionEntry],
    *,
    class_label: str = "",
    min_transcription_confidence: float = MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
) -> tuple[list[EvalTarget], SkipTally]:
    targets: list[EvalTarget] = []
    tally = SkipTally()

    for question in document.questions:
        entry = solutions.get(question.question_id)
        if entry is None:
            tally = replace(tally, no_solution=tally.no_solution + 1)
            continue

        response = question.response_text.strip()
        if not response:
            tally = replace(tally, blank=tally.blank + 1)
            continue

        objective = objective_correctness(entry, response, question)
        if not needs_llm_grade(
            entry,
            objective,
            question,
            min_transcription_confidence=min_transcription_confidence,
        ):
            if objective is not None:
                tally = replace(tally, objective=tally.objective + 1)
            elif entry.is_numeric:
                tally = replace(tally, numeric=tally.numeric + 1)
            else:
                tally = replace(tally, low_confidence=tally.low_confidence + 1)
            continue

        if question.points_awarded is None:
            tally = replace(tally, ungraded=tally.ungraded + 1)
            continue

        if ai_analysis_confidence(question.analysis or "") is not None:
            tally = replace(tally, ai_graded=tally.ai_graded + 1)
            continue

        targets.append(
            EvalTarget(
                student_id=document.student_id,
                student_name=document.student_name,
                question_id=question.question_id,
                question_type=question.question_type,
                prompt=entry.prompt,
                expected_answers=entry.expected_answers,
                response_text=response,
                rubric_options=entry.rubric_options,
                possible_points=entry.points,
                human_points=float(question.points_awarded),
                human_rubric=tuple(question.rubric_labels),
                class_label=class_label,
            )
        )

    return targets, tally


def _interleave(groups: Sequence[list[EvalTarget]]) -> list[EvalTarget]:
    merged: list[EvalTarget] = []
    depth = 0
    while True:
        progressed = False
        for group in groups:
            if depth < len(group):
                merged.append(group[depth])
                progressed = True
        if not progressed:
            return merged
        depth += 1


def spread_targets(targets: Sequence[EvalTarget], limit: int) -> list[EvalTarget]:
    """Rotates across classes, then students within each class; partial runs retain that spread."""
    if limit >= len(targets):
        return list(targets)
    if limit <= 0:
        return []

    by_class: dict[str, dict[tuple[str, str], list[EvalTarget]]] = {}
    for target in targets:
        by_class.setdefault(target.class_label, {}).setdefault(target.person, []).append(
            target
        )

    per_class = [
        _interleave(list(people.values())) for people in by_class.values()
    ]
    return _interleave(per_class)[:limit]


@dataclass(frozen=True)
class EvalCase:
    model: str
    target: EvalTarget
    llm_points: float | None
    llm_rubric: tuple[str, ...]
    llm_confidence: float | None
    rationale: str

    @property
    def points_error(self) -> float | None:
        if self.llm_points is None:
            return None
        return self.llm_points - self.target.human_points

    def outcome(self, tolerance: float = 0.0) -> EvalOutcome:
        error = self.points_error
        if error is None:
            return "no_suggestion"
        # EPSILON absorbs float noise; tolerance is the user's allowed grading difference.
        if abs(error) <= max(tolerance, EPSILON):
            return "agree"
        return "generous" if error > 0 else "harsh"

    @property
    def rubric_matches(self) -> bool | None:
        if not self.target.human_rubric or self.llm_points is None:
            return None
        return frozenset(self.llm_rubric) == frozenset(self.target.human_rubric)


def evaluate_target(
    target: EvalTarget,
    *,
    model: str,
    generate: GenerateFn,
) -> EvalCase:
    suggestion = suggest_free_response_grade(
        prompt=target.prompt,
        expected_answers=list(target.expected_answers),
        response_text=target.response_text,
        rubric_options=list(target.rubric_options),
        possible_points=target.possible_points,
        generate=generate,
    )
    if suggestion is None:
        return EvalCase(
            model=model,
            target=target,
            llm_points=None,
            llm_rubric=(),
            llm_confidence=None,
            rationale="",
        )
    return EvalCase(
        model=model,
        target=target,
        llm_points=suggestion.points_awarded,
        llm_rubric=suggestion.rubric_labels,
        llm_confidence=suggestion.confidence,
        rationale=suggestion.rationale,
    )


@dataclass(frozen=True)
class AgreementStats:
    cases: int
    scored: int
    agreements: int
    generous: int
    harsh: int
    no_suggestion: int
    rubric_comparable: int
    rubric_matches: int
    mean_absolute_error: float | None
    mean_signed_error: float | None
    max_absolute_error: float | None

    @property
    def agreement_rate(self) -> float | None:
        if self.scored == 0:
            return None
        return self.agreements / self.scored

    @property
    def suggestion_rate(self) -> float | None:
        if self.cases == 0:
            return None
        return self.scored / self.cases

    @property
    def rubric_match_rate(self) -> float | None:
        if self.rubric_comparable == 0:
            return None
        return self.rubric_matches / self.rubric_comparable

    @property
    def bias(self) -> Literal["generous", "harsh", "balanced"]:
        if self.generous > self.harsh:
            return "generous"
        if self.harsh > self.generous:
            return "harsh"
        return "balanced"


def summarize(cases: Sequence[EvalCase], *, tolerance: float = 0.0) -> AgreementStats:
    outcomes = [case.outcome(tolerance) for case in cases]
    errors = [
        case.points_error for case in cases if case.points_error is not None
    ]
    rubric_verdicts = [
        case.rubric_matches for case in cases if case.rubric_matches is not None
    ]
    return AgreementStats(
        cases=len(cases),
        scored=len(errors),
        agreements=outcomes.count("agree"),
        generous=outcomes.count("generous"),
        harsh=outcomes.count("harsh"),
        no_suggestion=outcomes.count("no_suggestion"),
        rubric_comparable=len(rubric_verdicts),
        rubric_matches=sum(1 for matched in rubric_verdicts if matched),
        mean_absolute_error=(
            sum(abs(e) for e in errors) / len(errors) if errors else None
        ),
        mean_signed_error=sum(errors) / len(errors) if errors else None,
        max_absolute_error=max((abs(e) for e in errors), default=None),
    )


@dataclass(frozen=True)
class ConfidenceBucket:
    lower: float
    upper: float
    stats: AgreementStats

    @property
    def label(self) -> str:
        return f"{self.lower:.2f}-{self.upper:.2f}"


def _bucket_index(confidence: float, bucket_size: float, bucket_count: int) -> int:
    return min(int(confidence / bucket_size), bucket_count - 1)


def bucket_by_confidence(
    cases: Sequence[EvalCase],
    *,
    bucket_size: float = DEFAULT_BUCKET_SIZE,
    tolerance: float = 0.0,
) -> tuple[ConfidenceBucket, ...]:
    bucket_count = max(1, int(round(1.0 / bucket_size)))
    grouped: dict[int, list[EvalCase]] = {}
    for case in cases:
        if case.llm_confidence is None:
            continue
        index = _bucket_index(case.llm_confidence, bucket_size, bucket_count)
        grouped.setdefault(index, []).append(case)

    return tuple(
        ConfidenceBucket(
            lower=index * bucket_size,
            upper=min(1.0, (index + 1) * bucket_size),
            stats=summarize(grouped[index], tolerance=tolerance),
        )
        for index in sorted(grouped)
    )


def group_by_question_type(
    cases: Sequence[EvalCase], *, tolerance: float = 0.0
) -> tuple[tuple[str, AgreementStats], ...]:
    grouped: dict[str, list[EvalCase]] = {}
    for case in cases:
        grouped.setdefault(case.target.question_type, []).append(case)
    summaries = [
        (question_type, summarize(group, tolerance=tolerance))
        for question_type, group in grouped.items()
    ]
    summaries.sort(key=lambda item: (-item[1].cases, item[0]))
    return tuple(summaries)


@dataclass(frozen=True)
class ThresholdRow:
    threshold: float
    applied: int
    deferred: int
    coverage: float
    agreements: int
    agreement_rate: float | None
    generous: int
    harsh: int
    mean_absolute_error: float | None

    @property
    def has_sample(self) -> bool:
        return self.applied > 0


def threshold_curve(
    cases: Sequence[EvalCase],
    *,
    step: float = DEFAULT_THRESHOLD_STEP,
    tolerance: float = 0.0,
) -> tuple[ThresholdRow, ...]:
    total = len(cases)
    steps = max(1, int(round(1.0 / step)))
    rows: list[ThresholdRow] = []
    for index in range(steps + 1):
        threshold = round(min(1.0, index * step), 6)
        applied = [
            case
            for case in cases
            if case.llm_confidence is not None and case.llm_confidence >= threshold
        ]
        stats = summarize(applied, tolerance=tolerance)
        rows.append(
            ThresholdRow(
                threshold=threshold,
                applied=len(applied),
                deferred=total - len(applied),
                coverage=(len(applied) / total) if total else 0.0,
                agreements=stats.agreements,
                agreement_rate=stats.agreement_rate,
                generous=stats.generous,
                harsh=stats.harsh,
                mean_absolute_error=stats.mean_absolute_error,
            )
        )
    return tuple(rows)


@dataclass(frozen=True)
class ThresholdRecommendation:
    threshold: float | None
    row: ThresholdRow | None
    target_agreement: float
    min_sample: int
    reason: str


def recommend_threshold(
    rows: Sequence[ThresholdRow],
    *,
    target_agreement: float = DEFAULT_TARGET_AGREEMENT,
    min_sample: int = DEFAULT_MIN_SAMPLE,
) -> ThresholdRecommendation:
    """Agreement is not monotonic. A threshold qualifies only if every stricter eligible
    threshold also meets the target.
    """
    eligible = sorted(
        (
            row
            for row in rows
            if row.applied >= min_sample and row.agreement_rate is not None
        ),
        key=lambda row: row.threshold,
        reverse=True,
    )
    if not eligible:
        return ThresholdRecommendation(
            threshold=None,
            row=None,
            target_agreement=target_agreement,
            min_sample=min_sample,
            reason=(
                f"not enough evidence: no confidence cutoff covers {min_sample} "
                "or more human-graded answers. Grade more of the assignment by hand, "
                "or lower --min-sample if you accept a smaller sample."
            ),
        )

    chosen: ThresholdRow | None = None
    for row in eligible:
        if row.agreement_rate is not None and row.agreement_rate >= target_agreement:
            chosen = row
        else:
            break

    if chosen is None:
        best = max(eligible, key=lambda row: (row.agreement_rate or 0.0, -row.threshold))
        return ThresholdRecommendation(
            threshold=None,
            row=best,
            target_agreement=target_agreement,
            min_sample=min_sample,
            reason=(
                "no confidence cutoff reaches "
                f"{target_agreement:.0%} agreement — the best is "
                f"{(best.agreement_rate or 0.0):.0%} at {best.threshold:.2f}. "
                "Grade this assignment's free responses by hand, or try a "
                "different model."
            ),
        )

    return ThresholdRecommendation(
        threshold=chosen.threshold,
        row=chosen,
        target_agreement=target_agreement,
        min_sample=min_sample,
        reason=(
            f"agreement stays at or above {target_agreement:.0%} for every "
            f"cutoff from {chosen.threshold:.2f} up; this is the loosest of "
            f"them, so it grades the most ({chosen.coverage:.0%} of the "
            "free responses) without giving that up."
        ),
    )


@dataclass(frozen=True)
class ModelEvaluation:
    model: str
    overall: AgreementStats
    by_question_type: tuple[tuple[str, AgreementStats], ...]
    by_confidence: tuple[ConfidenceBucket, ...]
    thresholds: tuple[ThresholdRow, ...]
    recommendation: ThresholdRecommendation
    worst_disagreements: tuple[EvalCase, ...]


@dataclass(frozen=True)
class LlmEvalReport:
    models: tuple[ModelEvaluation, ...]
    cases_per_model: int
    skipped: SkipTally
    points_tolerance: float
    bucket_size: float
    target_agreement: float
    min_sample: int
    classes: tuple[str, ...] = ()

    @property
    def is_pooled(self) -> bool:
        return len(self.classes) > 1

    @property
    def best_model(self) -> ModelEvaluation | None:
        usable = [
            model for model in self.models if model.recommendation.row is not None
            and model.recommendation.threshold is not None
        ]
        if not usable:
            return None
        return max(
            usable,
            key=lambda model: (
                model.recommendation.row.coverage,  # type: ignore[union-attr]
                model.recommendation.row.agreement_rate or 0.0,  # type: ignore[union-attr]
                model.model,
            ),
        )


def build_llm_eval_report(
    cases: Sequence[EvalCase],
    *,
    skipped: SkipTally | None = None,
    cases_per_model: int | None = None,
    points_tolerance: float = 0.0,
    bucket_size: float = DEFAULT_BUCKET_SIZE,
    threshold_step: float = DEFAULT_THRESHOLD_STEP,
    target_agreement: float = DEFAULT_TARGET_AGREEMENT,
    min_sample: int = DEFAULT_MIN_SAMPLE,
    disagreement_samples: int = 5,
    classes: Sequence[str] = (),
) -> LlmEvalReport:
    by_model: dict[str, list[EvalCase]] = {}
    for case in cases:
        by_model.setdefault(case.model, []).append(case)

    evaluations: list[ModelEvaluation] = []
    for model, model_cases in by_model.items():
        rows = threshold_curve(
            model_cases, step=threshold_step, tolerance=points_tolerance
        )
        evaluations.append(
            ModelEvaluation(
                model=model,
                overall=summarize(model_cases, tolerance=points_tolerance),
                by_question_type=group_by_question_type(
                    model_cases, tolerance=points_tolerance
                ),
                by_confidence=bucket_by_confidence(
                    model_cases, bucket_size=bucket_size, tolerance=points_tolerance
                ),
                thresholds=rows,
                recommendation=recommend_threshold(
                    rows, target_agreement=target_agreement, min_sample=min_sample
                ),
                worst_disagreements=_worst_disagreements(
                    model_cases,
                    tolerance=points_tolerance,
                    limit=disagreement_samples,
                ),
            )
        )

    evaluations.sort(key=lambda evaluation: evaluation.model)

    return LlmEvalReport(
        models=tuple(evaluations),
        cases_per_model=(
            cases_per_model
            if cases_per_model is not None
            else max((len(group) for group in by_model.values()), default=0)
        ),
        skipped=skipped or SkipTally(),
        points_tolerance=points_tolerance,
        bucket_size=bucket_size,
        target_agreement=target_agreement,
        min_sample=min_sample,
        classes=tuple(classes),
    )


def _worst_disagreements(
    cases: Sequence[EvalCase], *, tolerance: float, limit: int
) -> tuple[EvalCase, ...]:
    disagreements = [
        case
        for case in cases
        if case.outcome(tolerance) in ("generous", "harsh")
    ]
    disagreements.sort(
        key=lambda case: (
            -abs(case.points_error or 0.0),
            -(case.llm_confidence or 0.0),
            case.target.student_id,
            case.target.question_id,
        )
    )
    return tuple(disagreements[:limit])
