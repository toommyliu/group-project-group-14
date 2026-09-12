from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from opengrader.core.qa_file.scoring import EPSILON
from opengrader.core.question_types import MULTIPLE_CHOICE, QuestionType
from opengrader.core.submissions.answer_groups import normalize_answer_key
from opengrader.core.submissions.student_file import StudentSubmissionDocument

_MIN_SEPARATION_SAMPLE = 8

_MIN_CHOOSER_STAT_SAMPLE = 5


class DifficultyBand(str, Enum):
    VERY_EASY = "very_easy"
    EASY = "easy"
    MODERATE = "moderate"
    HARD = "hard"
    VERY_HARD = "very_hard"


class SeparationBand(str, Enum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    WEAK = "weak"
    NEGATIVE = "negative"
    INSUFFICIENT_DATA = "insufficient_data"


def difficulty_band(difficulty: float | None) -> DifficultyBand | None:
    if difficulty is None:
        return None
    if difficulty >= 0.90:
        return DifficultyBand.VERY_EASY
    if difficulty >= 0.75:
        return DifficultyBand.EASY
    if difficulty >= 0.50:
        return DifficultyBand.MODERATE
    if difficulty >= 0.30:
        return DifficultyBand.HARD
    return DifficultyBand.VERY_HARD


def separation_band(separation: float | None) -> SeparationBand:
    if separation is None:
        return SeparationBand.INSUFFICIENT_DATA
    if separation >= 0.40:
        return SeparationBand.EXCELLENT
    if separation >= 0.30:
        return SeparationBand.GOOD
    if separation >= 0.20:
        return SeparationBand.FAIR
    if separation >= 0.0:
        return SeparationBand.WEAK
    return SeparationBand.NEGATIVE


@dataclass(frozen=True)
class DistractorTally:
    choice: str
    count: int
    share: float
    # Awarded points suggest a key but cannot establish the correct answer.
    is_likely_key: bool
    mean_chooser_total_percent: float | None


@dataclass(frozen=True)
class ItemAnalysis:
    question_id: str
    assignment_version: str | None
    question_type: QuestionType
    possible_points: float | None
    responses_count: int
    mean_points: float | None
    difficulty: float | None
    difficulty_band: DifficultyBand | None
    separation: float | None
    separation_band: SeparationBand
    distractors: tuple[DistractorTally, ...]

    @property
    def needs_attention(self) -> bool:
        if self.separation_band is SeparationBand.NEGATIVE:
            return True
        return (
            self.difficulty_band is DifficultyBand.VERY_HARD
            and self.separation_band in (SeparationBand.WEAK, SeparationBand.NEGATIVE)
        )


@dataclass(frozen=True)
class ItemAnalysisReport:
    items: tuple[ItemAnalysis, ...]
    class_size: int

    @property
    def flagged_count(self) -> int:
        return sum(1 for item in self.items if item.needs_attention)


@dataclass(frozen=True)
class _GradedResponse:
    student_key: str
    question_id: str
    assignment_version: str | None
    question_type: QuestionType
    response_text: str
    points_awarded: float
    possible_points: float | None


_ItemKey = tuple[str, str | None]


def _student_key(document: StudentSubmissionDocument, index: int) -> str:
    return (
        document.student_id.strip()
        or document.source_pdf
        or document.student_name.strip()
        or f"__submission_{index}"
    )


def _collect(
    documents: list[StudentSubmissionDocument],
) -> tuple[list[_GradedResponse], dict[str, float]]:
    responses: list[_GradedResponse] = []
    totals: dict[str, float] = {}
    for index, document in enumerate(documents):
        key = _student_key(document, index)
        totals.setdefault(key, 0.0)
        for question in document.questions:
            if question.points_awarded is None:
                continue
            totals[key] += question.points_awarded
            responses.append(
                _GradedResponse(
                    student_key=key,
                    question_id=question.question_id,
                    assignment_version=document.assignment_version,
                    question_type=question.question_type,
                    response_text=question.response_text,
                    points_awarded=question.points_awarded,
                    possible_points=question.possible_points,
                )
            )
    return responses, totals


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    dxs = [x - mean_x for x in xs]
    dys = [y - mean_y for y in ys]
    cov = sum(dx * dy for dx, dy in zip(dxs, dys, strict=True))
    var_x = sum(dx * dx for dx in dxs)
    var_y = sum(dy * dy for dy in dys)
    if var_x <= EPSILON or var_y <= EPSILON:
        return None
    return cov / (var_x**0.5 * var_y**0.5)


def _separation(
    group: list[_GradedResponse],
    totals: dict[str, float],
) -> float | None:
    if len(group) < _MIN_SEPARATION_SAMPLE:
        return None
    item_scores = [r.points_awarded for r in group]
    rest_totals = [totals[r.student_key] - r.points_awarded for r in group]
    return _pearson(item_scores, rest_totals)


def _possible_points(group: list[_GradedResponse]) -> float | None:
    for response in group:
        if response.possible_points is not None:
            return response.possible_points
    return None


def _difficulty(mean_points: float, possible: float | None) -> float | None:
    if possible is None or possible <= 0:
        return None
    return max(0.0, min(1.0, mean_points / possible))


def _distractors(
    group: list[_GradedResponse],
    totals: dict[str, float],
    possible: float | None,
    assignment_max_by_student: dict[str, float],
) -> tuple[DistractorTally, ...]:
    total = len(group)
    if total == 0:
        return ()

    order: list[str] = []
    members: dict[str, list[_GradedResponse]] = {}
    display: dict[str, str] = {}
    for response in group:
        key = normalize_answer_key(response.response_text, response.question_type)
        if key not in members:
            order.append(key)
            members[key] = []
            display[key] = " ".join(response.response_text.split()) or "(blank)"
        members[key].append(response)

    can_stat = len(group) >= _MIN_CHOOSER_STAT_SAMPLE

    tallies: list[DistractorTally] = []
    for key in order:
        choosers = members[key]
        count = len(choosers)
        mean_points = sum(r.points_awarded for r in choosers) / count
        is_key = possible is not None and possible > 0 and mean_points >= possible - EPSILON

        chooser_percent: float | None = None
        if can_stat:
            percents = [
                (totals[r.student_key] / assignment_max_by_student[r.student_key]) * 100.0
                for r in choosers
                if assignment_max_by_student.get(r.student_key, 0.0) > 0
            ]
            if percents:
                chooser_percent = sum(percents) / len(percents)

        tallies.append(
            DistractorTally(
                choice=display[key],
                count=count,
                share=count / total,
                is_likely_key=is_key,
                mean_chooser_total_percent=chooser_percent,
            )
        )

    tallies.sort(key=lambda t: (-t.count, t.choice))
    return tuple(tallies)


_NATURAL_TOKEN_RE = re.compile(r"\d+")


def _natural_key(question_id: str) -> tuple[tuple[int, object], ...]:
    tokens = _NATURAL_TOKEN_RE.split(question_id)
    numbers = _NATURAL_TOKEN_RE.findall(question_id)
    parts: list[str] = []
    for index, text in enumerate(tokens):
        parts.append(text)
        if index < len(numbers):
            parts.append(numbers[index])
    return tuple(
        (0, int(token)) if token.isdigit() else (1, token)
        for token in parts
        if token != ""
    )


def _resolve_possible(
    question_id: str,
    version: str | None,
    group: list[_GradedResponse],
    override: dict[_ItemKey, float] | None,
) -> float | None:
    possible = _possible_points(group)
    if possible is not None:
        return possible
    if override is None:
        return None
    if (question_id, version) in override:
        return override[(question_id, version)]
    return override.get((question_id, None))


def build_item_analysis(
    documents: list[StudentSubmissionDocument],
    *,
    possible_points_by_question: dict[_ItemKey, float] | None = None,
) -> ItemAnalysisReport:
    """Missing point values fall back to (question_id, version), then (question_id, None)."""
    responses, totals = _collect(documents)

    order: list[_ItemKey] = []
    groups: dict[_ItemKey, list[_GradedResponse]] = {}
    for response in responses:
        key: _ItemKey = (response.question_id, response.assignment_version)
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(response)

    possible_by_item: dict[_ItemKey, float | None] = {
        key: _resolve_possible(key[0], key[1], groups[key], possible_points_by_question)
        for key in order
    }

    assignment_max: dict[str, float] = {}
    for response in responses:
        possible = possible_by_item.get((response.question_id, response.assignment_version))
        if possible is not None:
            assignment_max[response.student_key] = (
                assignment_max.get(response.student_key, 0.0) + possible
            )

    items: list[ItemAnalysis] = []
    for key in order:
        question_id, version = key
        group = groups[key]
        possible = possible_by_item[key]
        mean_points = sum(r.points_awarded for r in group) / len(group)
        difficulty = _difficulty(mean_points, possible)
        separation = _separation(group, totals)
        question_type = group[0].question_type
        distractors = (
            _distractors(group, totals, possible, assignment_max)
            if question_type == MULTIPLE_CHOICE
            else ()
        )
        items.append(
            ItemAnalysis(
                question_id=question_id,
                assignment_version=version,
                question_type=question_type,
                possible_points=possible,
                responses_count=len(group),
                mean_points=mean_points,
                difficulty=difficulty,
                difficulty_band=difficulty_band(difficulty),
                separation=separation,
                separation_band=separation_band(separation),
                distractors=distractors,
            )
        )

    items.sort(key=lambda item: (item.assignment_version or "", _natural_key(item.question_id)))
    return ItemAnalysisReport(items=tuple(items), class_size=len(documents))


DEFAULT_MASTERY_THRESHOLD = 0.7


@dataclass(frozen=True)
class StudentOutcomeMastery:
    student_key: str
    student_id: str
    student_name: str
    outcome: str
    earned_points: float
    possible_points: float
    questions_graded: int

    @property
    def mastery(self) -> float | None:
        if self.possible_points <= EPSILON:
            return None
        return self.earned_points / self.possible_points

    def has_mastered(self, threshold: float = DEFAULT_MASTERY_THRESHOLD) -> bool | None:
        mastery = self.mastery
        return None if mastery is None else mastery >= threshold


@dataclass(frozen=True)
class OutcomeMastery:
    outcome: str
    question_ids: tuple[str, ...]
    possible_points: float
    assignment_possible_points: float
    students_measured: int
    students_mastered: int
    mean_mastery: float | None
    students: tuple[StudentOutcomeMastery, ...]

    @property
    def mastery_rate(self) -> float | None:
        if self.students_measured == 0:
            return None
        return self.students_mastered / self.students_measured

    @property
    def assignment_weight(self) -> float | None:
        if self.assignment_possible_points <= EPSILON:
            return None
        return self.possible_points / self.assignment_possible_points


@dataclass(frozen=True)
class OutcomeReport:
    outcomes: tuple[OutcomeMastery, ...]
    class_size: int
    mastery_threshold: float
    untagged_points: float
    assignment_possible_points: float

    @property
    def tagged_share(self) -> float | None:
        if self.assignment_possible_points <= EPSILON:
            return None
        return 1.0 - (self.untagged_points / self.assignment_possible_points)

    def outcome(self, name: str) -> OutcomeMastery | None:
        return next((o for o in self.outcomes if o.outcome == name), None)


def build_outcome_report(
    documents: list[StudentSubmissionDocument],
    outcomes_by_question: dict[str, tuple[str, ...]],
    *,
    possible_points_by_question: dict[str, float] | None = None,
    mastery_threshold: float = DEFAULT_MASTERY_THRESHOLD,
) -> OutcomeReport:
    """Ungraded answers contribute neither earned nor possible points to outcome mastery."""
    responses, _ = _collect(documents)

    identity: dict[str, tuple[str, str]] = {}
    for index, document in enumerate(documents):
        identity[_student_key(document, index)] = (
            document.student_id.strip(),
            document.student_name.strip(),
        )

    earned: dict[tuple[str, str], float] = {}
    possible: dict[tuple[str, str], float] = {}
    graded_count: dict[tuple[str, str], int] = {}
    outcome_points: dict[str, float] = {}
    outcome_questions: dict[str, list[str]] = {}
    question_points: dict[str, float] = {}

    for response in responses:
        points = response.possible_points
        if points is None and possible_points_by_question is not None:
            points = possible_points_by_question.get(response.question_id)
        if points is None:
            continue
        question_points.setdefault(response.question_id, float(points))

        for outcome in outcomes_by_question.get(response.question_id, ()):
            key = (response.student_key, outcome)
            earned[key] = earned.get(key, 0.0) + response.points_awarded
            possible[key] = possible.get(key, 0.0) + float(points)
            graded_count[key] = graded_count.get(key, 0) + 1
            outcome_questions.setdefault(outcome, [])
            if response.question_id not in outcome_questions[outcome]:
                outcome_questions[outcome].append(response.question_id)

    for question_id, points in question_points.items():
        for outcome in outcomes_by_question.get(question_id, ()):
            outcome_points[outcome] = outcome_points.get(outcome, 0.0) + points

    assignment_points = sum(question_points.values())
    tagged_points = sum(
        points
        for question_id, points in question_points.items()
        if outcomes_by_question.get(question_id)
    )

    masteries: list[OutcomeMastery] = []
    for outcome in sorted(outcome_questions, key=outcome_sort_key):
        students = [
            StudentOutcomeMastery(
                student_key=student_key,
                student_id=identity.get(student_key, ("", ""))[0],
                student_name=identity.get(student_key, ("", ""))[1],
                outcome=outcome,
                earned_points=earned[(student_key, outcome)],
                possible_points=possible[(student_key, outcome)],
                questions_graded=graded_count[(student_key, outcome)],
            )
            for (student_key, tagged) in sorted(earned)
            if tagged == outcome
        ]
        measured = [s for s in students if s.mastery is not None]
        mastered = sum(1 for s in measured if s.has_mastered(mastery_threshold))
        masteries.append(
            OutcomeMastery(
                outcome=outcome,
                question_ids=tuple(
                    sorted(outcome_questions[outcome], key=_natural_key)
                ),
                possible_points=outcome_points.get(outcome, 0.0),
                assignment_possible_points=assignment_points,
                students_measured=len(measured),
                students_mastered=mastered,
                mean_mastery=(
                    sum(s.mastery for s in measured) / len(measured)  # type: ignore[misc]
                    if measured
                    else None
                ),
                students=tuple(students),
            )
        )

    return OutcomeReport(
        outcomes=tuple(masteries),
        class_size=len(documents),
        mastery_threshold=mastery_threshold,
        untagged_points=assignment_points - tagged_points,
        assignment_possible_points=assignment_points,
    )


def outcome_sort_key(outcome: str) -> tuple[tuple[int, object], ...]:
    return _natural_key(outcome)
