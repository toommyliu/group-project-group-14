"""Evaluation harness for LLM free-response grading (pure; transport injected).

``automated-grading --llm`` writes a model's grade whenever its self-reported
confidence clears ``--llm-min-confidence``. That threshold is the single knob
deciding how much of an exam a model is trusted with — and until now there was
nothing to set it from. 0.7 was a guess, and a guess is a bad way to decide how
much of a class's marks an unverified model gets to award.

This module measures the guess. Given submissions a human has already graded, it
re-asks the model the *same* questions production would ask it, and compares:

* **agreement** — did the model award the same points as the human?
* **direction** — when it disagreed, was it *generous* (awarded more than the
  human, so a student keeps marks they did not earn) or *harsh*?
* **per confidence bucket** — is the model's confidence worth anything? A model
  whose 0.9+ answers agree 95% of the time and whose 0.6-0.7 answers agree 55%
  of the time has a *useful* confidence signal and a natural cutoff. One that
  agrees 70% at every confidence has none, and no threshold will save it.
* **per model / per question type** — which model, on which kind of question.

The output is a threshold curve: for each candidate ``--llm-min-confidence``,
how much of the exam the model would grade (coverage) and how often it would be
right (agreement). ``recommend_threshold`` reads that curve back as a single
number an instructor can paste onto the command line.

Design rules, all deliberate:

* **Measure exactly what production grades.** Case selection routes through
  ``solutions.needs_llm_grade``, the same predicate ``grade_student_file``
  uses. Answers settled by rule (multiple choice, numeric) are excluded — the
  model never sees them, so counting them would inflate every number here.
* **The human is the ground truth, and only a human counts.** A question whose
  Analysis carries the ``[AI · NN%]`` tag was graded by a previous model run;
  scoring a model against its own earlier verdict measures self-consistency and
  reports it as accuracy. Those are excluded and counted separately.
* **Read-only.** Nothing here writes a grade; the harness is a measurement of
  the grader, not an act of grading.
* **Transport injected** (``generate``), like ``llm_grader.py`` — so the whole
  engine unit-tests with no Ollama and no network.
"""

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

if TYPE_CHECKING:  # Type only; see the note in solutions.py about the cycle.
    from opengrader.core.submissions.student_file import StudentSubmissionDocument

# What the model did relative to the human on one answer.
EvalOutcome = Literal["agree", "generous", "harsh", "no_suggestion"]

# Default width of a confidence bucket in the "is this model's confidence worth
# anything?" breakdown. Deciles: fine enough to show a trend, coarse enough that
# a normal-sized class still puts several answers in a bucket.
DEFAULT_BUCKET_SIZE = 0.1

# Candidate --llm-min-confidence values are walked on this grid. 0.05 is finer
# than any instructor would tune by hand, which is the point: the recommendation
# lands on a number rather than a bucket.
DEFAULT_THRESHOLD_STEP = 0.05

# How often a threshold's grades must match the human's before it is
# recommendable. 0.9 says "nine in ten of the marks this setting awards
# unattended agree with the person who graded the sample".
DEFAULT_TARGET_AGREEMENT = 0.9

# A threshold that applies to fewer than this many answers is not evidence, it
# is an anecdote — 3/3 correct is a 100% agreement rate and means nothing. Such
# thresholds are ineligible for the recommendation (they are still printed, so
# the instructor can see the whole curve).
DEFAULT_MIN_SAMPLE = 5


# ── the population under test ────────────────────────────────────────────────


@dataclass(frozen=True)
class SkipTally:
    """Why answers dropped out of the evaluation, so the sample size is legible.

    An instructor looking at "27 of 34 agreed" needs to know what the 34 is out
    of, and — more importantly — that a tiny sample is a tiny sample. The two
    interesting rows are ``ungraded`` (grade more of the exam by hand and the
    harness gets sharper) and ``ai_graded`` (already carrying a model's verdict,
    excluded to keep the comparison honest).
    """

    objective: int = 0  # settled by rule; a model is never asked
    numeric: int = 0  # numeric miss: goes to a human, never to a prose grader
    blank: int = 0  # nothing transcribed to grade
    no_solution: int = 0  # no matching question in the answer key
    ungraded: int = 0  # no human grade, so no ground truth to compare against
    ai_graded: int = 0  # a previous --llm run's grade; comparing is circular
    # Transcription confidence below the floor, so production would not send it
    # to a model either. Counted separately from `blank` because the two say
    # different things to an instructor: blank means the extractor found
    # nothing, this means it found something it does not trust — and a large
    # number here is a scan-quality problem, not a grading one.
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
    """One human-graded answer to re-ask a model about.

    Deliberately self-contained: it carries the whole grading prompt *and* the
    human's verdict, so ``evaluate_target`` needs no submission document, no QA
    file and no filesystem — and the same target can be replayed against several
    models without re-deriving anything.
    """

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
    # Which submissions folder this answer came from, when several *sharing one
    # answer key* are pooled (the same exam given in past terms). Empty for the
    # ordinary single-folder run. It is what keeps two different people who
    # happen to share a student id — a retake, or two classes both numbering
    # from s1 — from reading as one person in the disagreement list.
    class_label: str = ""

    @property
    def person(self) -> tuple[str, str]:
        """Identity for "spread the sample across people" purposes.

        A student id alone is not unique once classes are pooled, which is
        exactly when spreading matters most.
        """
        return (self.class_label, self.student_id)


def select_eval_targets(
    document: StudentSubmissionDocument,
    solutions: dict[str, SolutionEntry],
    *,
    class_label: str = "",
    min_transcription_confidence: float = MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
) -> tuple[list[EvalTarget], SkipTally]:
    """Pick the answers in one submission that can measure a model.

    The order of the checks is the point. The first four mirror
    ``grade_student_file`` exactly — an answer is only a candidate if production
    would have sent it to the model — and only then do the two ground-truth
    checks apply. Doing it the other way round would quietly change the
    population being measured.

    ``min_transcription_confidence`` is part of that mirror: it must be the
    value production runs with, or the harness would calibrate a threshold
    against answers the real run refuses to grade.
    """
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
                # The remaining way to fail the predicate: a transcription the
                # OCR does not trust. Production leaves it for a human, so the
                # harness must not measure a model against it either.
                tally = replace(tally, low_confidence=tally.low_confidence + 1)
            continue

        if question.points_awarded is None:
            # Nothing to compare against. This is the row an instructor can act
            # on: hand-grade more answers and the harness gets more evidence.
            tally = replace(tally, ungraded=tally.ungraded + 1)
            continue

        if ai_analysis_confidence(question.analysis or "") is not None:
            # A previous --llm run wrote this grade. Scoring the model against
            # it would measure how repeatable the model is and print the number
            # as if it were accuracy.
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
    """Round-robin across groups: one from each in turn, until all are spent.

    Groups keep their insertion order, and a group that runs out early is simply
    skipped, so an uneven split (one class with twice the students) still drains
    fairly rather than stopping at the shortest.
    """
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
    """Take ``limit`` targets spread across classes and people, not the first N.

    Targets arrive grouped by submission — every one of student 1's answers,
    then every one of student 2's — so a plain ``targets[:limit]`` measures the
    model on the first few students and nothing else. That is a bad sample for
    *any* limit, and a worse one once classes are pooled, where it would take
    everything from the first folder and nothing from the second.

    The spread is **two levels**, and both are load-bearing. Rotating over people
    alone is not enough: with 4 students per class and a limit of 4, the first
    four *people* are all in the first class, so the pooling would buy nothing.
    So each class is first flattened into its own people-rotation, and those
    per-class sequences are then rotated against each other. Every class is
    sampled before any class is sampled twice, and within a class every person
    is sampled before any person is sampled twice — and because a person's
    answers stay in question order, the questions rotate as it comes round again.

    People are identified by ``EvalTarget.person`` (class + student id), since a
    student id alone is not unique once classes are pooled.

    Deterministic — no shuffling — so two runs over the same folders measure the
    same answers and their agreement rates are comparable. The returned order is
    the rotation order rather than the original; nothing downstream depends on
    target order, and it means an interrupted run leaves a spread, not a prefix.
    """
    if limit >= len(targets):
        return list(targets)
    if limit <= 0:
        return []

    # class -> person -> that person's answers, all insertion-ordered.
    by_class: dict[str, dict[tuple[str, str], list[EvalTarget]]] = {}
    for target in targets:
        by_class.setdefault(target.class_label, {}).setdefault(target.person, []).append(
            target
        )

    per_class = [
        _interleave(list(people.values())) for people in by_class.values()
    ]
    return _interleave(per_class)[:limit]


# ── one measurement ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EvalCase:
    """What one model said about one human-graded answer."""

    model: str
    target: EvalTarget
    llm_points: float | None  # None: the model returned nothing usable
    llm_rubric: tuple[str, ...]
    llm_confidence: float | None
    rationale: str

    @property
    def points_error(self) -> float | None:
        """Model minus human, in points. Positive = the model was generous."""
        if self.llm_points is None:
            return None
        return self.llm_points - self.target.human_points

    def outcome(self, tolerance: float = 0.0) -> EvalOutcome:
        error = self.points_error
        if error is None:
            return "no_suggestion"
        # EPSILON keeps float arithmetic from turning an exact match into a
        # disagreement; `tolerance` is the instructor's own "close enough".
        if abs(error) <= max(tolerance, EPSILON):
            return "agree"
        return "generous" if error > 0 else "harsh"

    @property
    def rubric_matches(self) -> bool | None:
        """Did the model pick the same rubric options the human did?

        ``None`` when the human recorded no rubric selection (points only) —
        there is nothing to compare, which is different from disagreeing.
        """
        if not self.target.human_rubric or self.llm_points is None:
            return None
        return frozenset(self.llm_rubric) == frozenset(self.target.human_rubric)


def evaluate_target(
    target: EvalTarget,
    *,
    model: str,
    generate: GenerateFn,
) -> EvalCase:
    """Ask one model to grade one answer, and record what it said.

    A model that returns nothing usable (unparseable JSON, invented rubric
    labels) yields a case with ``llm_points=None``. That is a real and
    reportable result — in production the answer would simply be left for a
    human — and it is deliberately not counted as a disagreement.
    """
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


# ── aggregation ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AgreementStats:
    """How a set of cases came out. Rates are ``None`` on an empty denominator."""

    cases: int
    scored: int  # cases where the model produced a usable suggestion
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
        """Share of *scored* answers where the model matched the human.

        Scored, not total: a refusal to answer isn't a wrong answer. The
        ``suggestion_rate`` below is what says how often the model answered at
        all, and the two are meant to be read together.
        """
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
        """Which way the model leans when it disagrees.

        Worth separating from raw accuracy: a harsh model costs a grader review
        time, a generous one hands out marks nobody checked.
        """
        if self.generous > self.harsh:
            return "generous"
        if self.harsh > self.generous:
            return "harsh"
        return "balanced"


def summarize(cases: Sequence[EvalCase], *, tolerance: float = 0.0) -> AgreementStats:
    """Aggregate a set of cases into one agreement summary."""
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
    """One slice of the confidence axis, with how the model did inside it."""

    lower: float
    upper: float
    stats: AgreementStats

    @property
    def label(self) -> str:
        return f"{self.lower:.2f}-{self.upper:.2f}"


def _bucket_index(confidence: float, bucket_size: float, bucket_count: int) -> int:
    # min() keeps confidence == 1.0 in the top bucket rather than one past it.
    return min(int(confidence / bucket_size), bucket_count - 1)


def bucket_by_confidence(
    cases: Sequence[EvalCase],
    *,
    bucket_size: float = DEFAULT_BUCKET_SIZE,
    tolerance: float = 0.0,
) -> tuple[ConfidenceBucket, ...]:
    """Group scored cases by the model's self-reported confidence.

    Cases with no suggestion carry no confidence and so appear in no bucket;
    they are still counted in the overall summary. Empty buckets are dropped —
    an all-zero row tells the reader nothing and lengthens the table.
    """
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
    """Break the cases down by question type, most-measured type first."""
    grouped: dict[str, list[EvalCase]] = {}
    for case in cases:
        grouped.setdefault(case.target.question_type, []).append(case)
    summaries = [
        (question_type, summarize(group, tolerance=tolerance))
        for question_type, group in grouped.items()
    ]
    summaries.sort(key=lambda item: (-item[1].cases, item[0]))
    return tuple(summaries)


# ── the threshold curve (the actual deliverable) ─────────────────────────────


@dataclass(frozen=True)
class ThresholdRow:
    """What one candidate ``--llm-min-confidence`` would have done to this exam."""

    threshold: float
    applied: int  # suggestions this setting would have written
    deferred: int  # answers it would have left for a human
    coverage: float  # applied / all evaluated answers
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
    """Replay every candidate threshold over the measured cases.

    This is a pure re-slicing of results already collected — the model is not
    consulted again — so the whole curve costs one grading pass, not one pass
    per threshold. Rows run low to high, which is also cheapest-to-strictest.
    """
    total = len(cases)
    steps = max(1, int(round(1.0 / step)))
    rows: list[ThresholdRow] = []
    for index in range(steps + 1):
        # Rounded because `17 * 0.05` is 0.8500000000000001 in binary floating
        # point, and this number is printed, compared and pasted onto a command
        # line by a human.
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
    """The number to paste onto ``--llm-min-confidence``, and why."""

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
    """Read a threshold off the curve: the loosest setting that still holds up.

    Agreement is **not** monotonic in the threshold — with a real class's worth
    of answers, one unlucky high-confidence miss can make 0.95 look worse than
    0.80 — so "the lowest threshold that hits the target" would happily
    recommend a lucky dip underneath a bad stretch. Instead this walks *down*
    from the strictest end and stops at the first threshold that misses the
    target, recommending the last one that held. The result is the loosest
    setting from which agreement stays above target all the way up: maximum
    coverage, with no bad stretch above it.

    Thresholds applying to fewer than ``min_sample`` answers are ineligible (a
    100% agreement rate over three answers is not evidence), which also means
    the walk starts at the strictest threshold that still has a real sample
    rather than at a near-empty 1.0.
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
                "or more human-graded answers. Grade more of the exam by hand, "
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
        # On a tie prefer the *lower* threshold: consecutive cutoffs that catch
        # the same answers are the same row, and naming the loosest of them
        # keeps the number in this message one the printed curve actually shows.
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


# ── the report ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelEvaluation:
    """One model's whole story: how it did, sliced every way that matters."""

    model: str
    overall: AgreementStats
    by_question_type: tuple[tuple[str, AgreementStats], ...]
    by_confidence: tuple[ConfidenceBucket, ...]
    thresholds: tuple[ThresholdRow, ...]
    recommendation: ThresholdRecommendation
    worst_disagreements: tuple[EvalCase, ...]


@dataclass(frozen=True)
class LlmEvalReport:
    """The harness's output: every model measured against the same answers."""

    models: tuple[ModelEvaluation, ...]
    cases_per_model: int
    skipped: SkipTally
    points_tolerance: float
    bucket_size: float
    target_agreement: float
    min_sample: int
    # The submissions folders behind the sample, in the order given. More than
    # one means several classes *sharing one answer key* were pooled — the same
    # exam across terms — which is the only pooling that is sound: same rubric,
    # same point scale, same questions, just more students. Consumers use it to
    # decide whether a class is worth naming in the output at all.
    classes: tuple[str, ...] = ()

    @property
    def is_pooled(self) -> bool:
        return len(self.classes) > 1

    @property
    def best_model(self) -> ModelEvaluation | None:
        """The model to actually use, or ``None`` if none is usable.

        "Best" means: it has a recommendable threshold at all, and at that
        threshold it grades the most of the exam. Coverage rather than agreement
        decides, because every recommended threshold already clears the same
        agreement target — so the remaining question is which model does the
        most work at that quality. Ties go to the higher agreement, then to the
        name, so the answer is stable across runs.
        """
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
    """Turn raw cases into the per-model report. Pure; safe on an empty input."""
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

    # Alphabetical: the models were named on the command line, and a stable,
    # predictable order makes two runs diffable.
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
    """The biggest point gaps, worst first — the answers to go read yourself.

    A rate tells an instructor whether to trust the model; these tell them
    *why* it went wrong, which is what decides whether the fix is a threshold,
    a clearer rubric, or a different model. High-confidence misses break ties,
    since a confident wrong answer is the one a threshold cannot catch.
    """
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
