from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Literal

from opengrader.core.history.journal import GradeJournalEntry
from opengrader.core.history.restore import student_key
from opengrader.core.submissions.answer_groups import normalize_answer_key
from opengrader.core.submissions.student_file import StudentSubmissionDocument

OverlapBasis = Literal["same_work", "same_answer"]

DEFAULT_MIN_PAIR_SAMPLES = 12

DEFAULT_MIN_DRIFT_SAMPLES = 20

# Tolerance is in percentage points and absorbs floating-point noise only.
_AGREEMENT_EPSILON = 1e-6


@dataclass(frozen=True)
class GradedAnswer:
    actor: str
    student_key: str
    question_id: str
    points_awarded: float
    possible_points: float | None = None
    rubric_labels: tuple[str, ...] = ()
    answer_key: str = ""
    at: datetime | None = None
    student_label: str = ""

    @property
    def percent(self) -> float | None:
        if self.possible_points is None or self.possible_points <= 0:
            return None
        return (self.points_awarded / self.possible_points) * 100.0


@dataclass(frozen=True)
class DriftStats:
    samples: int
    early_residual: float
    late_residual: float

    @property
    def delta(self) -> float:
        return self.late_residual - self.early_residual


@dataclass(frozen=True)
class GraderStats:
    actor: str
    answers: int
    students: int
    questions: int
    mean_percent: float | None
    # Mean residual from the question's cohort mean; positive means more generous.
    mean_residual: float | None
    first_graded: datetime | None
    last_graded: datetime | None
    drift: DriftStats | None


@dataclass(frozen=True)
class PairAgreement:
    a: str
    b: str
    basis: OverlapBasis
    samples: int
    exact_agreement: float
    mean_absolute_gap: float
    # Signed a minus b; positive means a awarded more credit.
    mean_signed_gap: float
    max_absolute_gap: float
    sufficient: bool

    @property
    def more_generous(self) -> str:
        if abs(self.mean_signed_gap) <= _AGREEMENT_EPSILON:
            return ""
        return self.a if self.mean_signed_gap > 0 else self.b


@dataclass(frozen=True)
class QuestionNorm:
    question_id: str
    answers: int
    graders: int
    mean_percent: float


@dataclass(frozen=True)
class GraderAgreementReport:
    graders: tuple[GraderStats, ...]
    same_work_pairs: tuple[PairAgreement, ...]
    same_answer_pairs: tuple[PairAgreement, ...]
    questions: tuple[QuestionNorm, ...]
    total_answers: int
    unweighted_answers: int
    min_pair_samples: int

    @property
    def distinct_graders(self) -> int:
        return len(self.graders)

    @property
    def has_overlap(self) -> bool:
        return bool(self.same_work_pairs or self.same_answer_pairs)

def _weighted(answers: Iterable[GradedAnswer]) -> list[tuple[GradedAnswer, float]]:
    kept: list[tuple[GradedAnswer, float]] = []
    for answer in answers:
        percent = answer.percent
        if percent is not None:
            kept.append((answer, percent))
    return kept


def _question_means(weighted: Sequence[tuple[GradedAnswer, float]]) -> dict[str, float]:
    buckets: dict[str, list[float]] = {}
    for answer, percent in weighted:
        buckets.setdefault(answer.question_id, []).append(percent)
    return {question: mean(values) for question, values in buckets.items()}


def _latest_per_actor(
    answers: Sequence[GradedAnswer],
) -> dict[str, GradedAnswer]:
    chosen: dict[str, GradedAnswer] = {}
    for answer in answers:
        current = chosen.get(answer.actor)
        if current is None:
            chosen[answer.actor] = answer
            continue
        if answer.at is not None and current.at is not None:
            if answer.at >= current.at:
                chosen[answer.actor] = answer
        else:
            chosen[answer.actor] = answer
    return chosen


def _pair_stats(
    buckets: Mapping[tuple[str, ...], list[GradedAnswer]],
    *,
    basis: OverlapBasis,
    min_pair_samples: int,
) -> list[PairAgreement]:
    gaps: dict[tuple[str, str], list[float]] = {}
    agreements: dict[tuple[str, str], list[bool]] = {}

    for answers in buckets.values():
        by_actor = _latest_per_actor(answers)
        if len(by_actor) < 2:
            continue
        actors = sorted(by_actor)
        for index, first in enumerate(actors):
            for second in actors[index + 1 :]:
                a_percent = by_actor[first].percent
                b_percent = by_actor[second].percent
                if a_percent is None or b_percent is None:
                    continue
                key = (first, second)
                gaps.setdefault(key, []).append(a_percent - b_percent)
                agreements.setdefault(key, []).append(
                    abs(by_actor[first].points_awarded - by_actor[second].points_awarded)
                    <= _AGREEMENT_EPSILON
                )

    pairs: list[PairAgreement] = []
    for (first, second), values in gaps.items():
        matches = agreements[(first, second)]
        pairs.append(
            PairAgreement(
                a=first,
                b=second,
                basis=basis,
                samples=len(values),
                exact_agreement=sum(matches) / len(matches),
                mean_absolute_gap=mean(abs(value) for value in values),
                mean_signed_gap=mean(values),
                max_absolute_gap=max(abs(value) for value in values),
                sufficient=len(values) >= min_pair_samples,
            )
        )

    pairs.sort(
        key=lambda pair: (
            not pair.sufficient,
            -pair.mean_absolute_gap,
            pair.a,
            pair.b,
        )
    )
    return pairs


def _drift_for(
    weighted: Sequence[tuple[GradedAnswer, float]],
    question_means: Mapping[str, float],
    *,
    min_samples: int,
) -> DriftStats | None:
    timed = [
        (answer.at, answer, percent)
        for answer, percent in weighted
        if answer.at is not None
    ]
    if len(timed) < min_samples:
        return None
    timed.sort(key=lambda row: row[0])
    ordered = [(answer, percent) for _, answer, percent in timed]

    half = len(ordered) // 2
    early = ordered[:half]
    late = ordered[len(ordered) - half :]

    def residuals(rows: Sequence[tuple[GradedAnswer, float]]) -> list[float]:
        return [percent - question_means[answer.question_id] for answer, percent in rows]

    return DriftStats(
        samples=len(ordered),
        early_residual=mean(residuals(early)),
        late_residual=mean(residuals(late)),
    )


def build_grader_agreement_report(
    answers: Iterable[GradedAnswer],
    *,
    min_pair_samples: int = DEFAULT_MIN_PAIR_SAMPLES,
    min_drift_samples: int = DEFAULT_MIN_DRIFT_SAMPLES,
) -> GraderAgreementReport:
    all_answers = list(answers)
    weighted = _weighted(all_answers)
    question_means = _question_means(weighted)

    by_actor: dict[str, list[tuple[GradedAnswer, float]]] = {}
    for answer, percent in weighted:
        by_actor.setdefault(answer.actor, []).append((answer, percent))
    for answer in all_answers:
        by_actor.setdefault(answer.actor, [])

    graders: list[GraderStats] = []
    for actor, rows in by_actor.items():
        times = sorted(row[0].at for row in rows if row[0].at is not None)
        percents = [percent for _, percent in rows]
        residuals = [
            percent - question_means[answer.question_id] for answer, percent in rows
        ]
        graders.append(
            GraderStats(
                actor=actor,
                answers=len(rows),
                students=len({answer.student_key for answer, _ in rows}),
                questions=len({answer.question_id for answer, _ in rows}),
                mean_percent=mean(percents) if percents else None,
                mean_residual=mean(residuals) if residuals else None,
                first_graded=times[0] if times else None,
                last_graded=times[-1] if times else None,
                drift=_drift_for(rows, question_means, min_samples=min_drift_samples),
            )
        )
    graders.sort(key=lambda stats: (-stats.answers, stats.actor))

    same_work: dict[tuple[str, ...], list[GradedAnswer]] = {}
    same_answer: dict[tuple[str, ...], list[GradedAnswer]] = {}
    for answer, _ in weighted:
        if answer.student_key:
            same_work.setdefault((answer.question_id, answer.student_key), []).append(answer)
        if answer.answer_key:
            same_answer.setdefault((answer.question_id, answer.answer_key), []).append(answer)

    counts: dict[str, int] = {}
    actors_per_question: dict[str, set[str]] = {}
    for answer, _ in weighted:
        counts[answer.question_id] = counts.get(answer.question_id, 0) + 1
        actors_per_question.setdefault(answer.question_id, set()).add(answer.actor)

    question_norms = [
        QuestionNorm(
            question_id=question_id,
            answers=counts[question_id],
            graders=len(actors_per_question[question_id]),
            mean_percent=value,
        )
        for question_id, value in sorted(question_means.items())
    ]

    return GraderAgreementReport(
        graders=tuple(graders),
        same_work_pairs=tuple(
            _pair_stats(same_work, basis="same_work", min_pair_samples=min_pair_samples)
        ),
        same_answer_pairs=tuple(
            _pair_stats(same_answer, basis="same_answer", min_pair_samples=min_pair_samples)
        ),
        questions=tuple(question_norms),
        total_answers=len(all_answers),
        unweighted_answers=len(all_answers) - len(weighted),
        min_pair_samples=min_pair_samples,
    )


def _question_index(
    documents: Iterable[StudentSubmissionDocument],
) -> dict[tuple[str, str], tuple[float | None, str, str]]:
    index: dict[tuple[str, str], tuple[float | None, str, str]] = {}
    for document in documents:
        key = student_key(document.student_id, document.student_name)
        if not key:
            continue
        label = document.student_name.strip() or document.student_id.strip() or key
        for question in document.questions:
            index[(key, question.question_id)] = (
                question.possible_points,
                normalize_answer_key(question.response_text, question.question_type),
                label,
            )
    return index


@dataclass(frozen=True)
class JournalAdaptation:
    answers: tuple[GradedAnswer, ...]
    unmatched: int


def answers_from_journal(
    entries: Iterable[GradeJournalEntry],
    documents: Iterable[StudentSubmissionDocument],
) -> JournalAdaptation:
    index = _question_index(documents)
    answers: list[GradedAnswer] = []
    unmatched = 0

    for entry in entries:
        if entry.kind != "grade":
            continue
        points = entry.new.get("points")
        if not isinstance(points, (int, float)):
            continue
        key = student_key(entry.student_id, entry.student_name)
        found = index.get((key, entry.question_id))
        if found is None:
            unmatched += 1
            continue
        possible, answer_key, label = found
        rubric = entry.new.get("rubric")
        answers.append(
            GradedAnswer(
                actor=entry.actor,
                student_key=key,
                question_id=entry.question_id,
                points_awarded=float(points),
                possible_points=possible,
                rubric_labels=tuple(str(item) for item in rubric)
                if isinstance(rubric, list)
                else (),
                answer_key=answer_key,
                at=_parse_timestamp(entry.timestamp),
                student_label=label,
            )
        )

    return JournalAdaptation(answers=tuple(answers), unmatched=unmatched)


def answers_from_graded_copies(
    copies: Iterable[tuple[str, Mapping[str, StudentSubmissionDocument]]],
) -> list[GradedAnswer]:
    answers: list[GradedAnswer] = []
    for actor, documents_by_key in copies:
        for key, document in documents_by_key.items():
            label = document.student_name.strip() or key
            for question in document.questions:
                if question.points_awarded is None:
                    continue
                answers.append(
                    GradedAnswer(
                        actor=actor,
                        student_key=key,
                        question_id=question.question_id,
                        points_awarded=question.points_awarded,
                        possible_points=question.possible_points,
                        rubric_labels=tuple(question.rubric_labels),
                        answer_key=normalize_answer_key(
                            question.response_text, question.question_type
                        ),
                        student_label=label,
                    )
                )
    return answers


def _parse_timestamp(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
