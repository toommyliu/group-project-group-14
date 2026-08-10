"""Item analysis: how well is each *question* doing its job?

Once a batch is graded, the score distribution (`tui/statistics.py`) tells you
how the *students* did. This module answers the complementary question an
instructor needs before reusing or fixing an assignment: how did each *question*
behave as a measurement instrument?

It reports two classic psychometrics per question, plus a per-choice breakdown
for multiple-choice items:

- **Difficulty** — the fraction of the available points the cohort earned
  (``mean_awarded / possible_points``). Higher means *easier*. This is the
  standard "p-value" of test theory; the name is about the item, not the class.

- **Separation** — how well the question separates stronger students from weaker
  ones. It is the *corrected item–total correlation* (a point-biserial-style
  Pearson r) between a student's score on this question and their total on the
  *rest* of the assignment. A question every strong student gets and every weak
  student misses has high separation; one that everybody — or nobody — gets, or
  one that the weaker students somehow do *better* on (negative separation, a
  red flag for a miskeyed or ambiguous item), has low or negative separation.
  We correlate against the *rest* total (total minus this item) rather than the
  whole total so a question can't inflate its own separation by being part of
  the number it's compared to.

  "Separation" is this codebase's name for what the measurement literature calls
  the *discrimination index* — the neutral word, because the statistic is about
  telling ability levels apart, nothing else.

- **Distractors** (MC only) — for each answer choice: how many students picked
  it, whether it looks like the answer key (its choosers earned full credit),
  and the average overall assignment performance of the students who chose it. A
  healthy distractor is chosen mostly by weaker students; one chosen by the
  strong students points at an ambiguous or miskeyed question.

Design, mirroring the sibling engines (:mod:`submissions.consistency`,
:mod:`submissions.review`): this is a **pure** function over the persisted
``.submission.md`` documents — no I/O, no OCR, no QA-file dependency —
returning frozen dataclasses in a deterministic order. It reuses
:func:`answer_groups.normalize_answer_key` so "the same choice" means exactly
what it means everywhere else in the app.

Partitioning: items are keyed by ``(question_id, assignment_version)``. On a
multi-version assignment the same ``q1`` is a different question per version, so —
exactly as the consistency audit does — mixing versions would compare
unrelated items. Single-version assignments (version ``None``) collapse to one item
per question.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from opengrader.core.qa_file.scoring import EPSILON
from opengrader.core.question_types import MULTIPLE_CHOICE, QuestionType
from opengrader.core.submissions.answer_groups import normalize_answer_key
from opengrader.core.submissions.student_file import StudentSubmissionDocument

# Separation needs a real cohort: a Pearson r over three or four points is
# noise. Below this many graded responses on an item we report the difficulty
# but leave separation as "insufficient data" rather than a misleading number.
_MIN_SEPARATION_SAMPLE = 8

# The overall-performance split for distractor analysis (high vs low group)
# needs enough students that each tail is more than one or two people; below
# this we still report per-choice counts but omit the chooser-performance stat.
_MIN_CHOOSER_STAT_SAMPLE = 5


class DifficultyBand(str, Enum):
    """Qualitative reading of an item's difficulty (fraction earned)."""

    VERY_EASY = "very_easy"  # p >= 0.90
    EASY = "easy"  # 0.75 <= p < 0.90
    MODERATE = "moderate"  # 0.50 <= p < 0.75  (the healthy target band)
    HARD = "hard"  # 0.30 <= p < 0.50
    VERY_HARD = "very_hard"  # p < 0.30


class SeparationBand(str, Enum):
    """Qualitative reading of an item's separation (item-total correlation).

    Cut points follow the conventional Ebel guidance for discrimination.
    """

    EXCELLENT = "excellent"  # r >= 0.40
    GOOD = "good"  # 0.30 <= r < 0.40
    FAIR = "fair"  # 0.20 <= r < 0.30
    WEAK = "weak"  # 0.00 <= r < 0.20
    NEGATIVE = "negative"  # r < 0.00  — review this item
    INSUFFICIENT_DATA = "insufficient_data"  # too few responses to judge


def difficulty_band(difficulty: float | None) -> DifficultyBand | None:
    """Bucket a difficulty (fraction earned, 0..1) into a qualitative band."""
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
    """Bucket a separation correlation into a qualitative band.

    ``None`` (not enough data to compute a stable correlation) maps to
    :attr:`SeparationBand.INSUFFICIENT_DATA`.
    """
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
    """One answer choice for a multiple-choice item, and who picked it."""

    choice: str
    count: int
    # Fraction of graded responses on this item that chose this option (0..1).
    share: float
    # True when this choice's choosers earned full credit — i.e. it looks like
    # the answer key. Inferred from awarded points (no QA file needed), so it is
    # a strong signal, not an authority.
    is_likely_key: bool
    # Average total-assignment performance (0..100%) of the students who chose this
    # option. ``None`` when the cohort is too small to be meaningful. A working
    # distractor sits well below the key here.
    mean_chooser_total_percent: float | None


@dataclass(frozen=True)
class ItemAnalysis:
    """Psychometrics for one question on one assignment version."""

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
        """Whether this item is worth a second look before reuse.

        Negative separation is the clearest alarm (stronger students did worse);
        a very-hard *and* poorly-separating item is the next concern.
        """
        if self.separation_band is SeparationBand.NEGATIVE:
            return True
        return (
            self.difficulty_band is DifficultyBand.VERY_HARD
            and self.separation_band in (SeparationBand.WEAK, SeparationBand.NEGATIVE)
        )


@dataclass(frozen=True)
class ItemAnalysisReport:
    """The full per-item analysis for a graded cohort."""

    items: tuple[ItemAnalysis, ...]
    # Number of submissions considered (each contributes to the totals used for
    # separation), for context in the header.
    class_size: int

    @property
    def flagged_count(self) -> int:
        return sum(1 for item in self.items if item.needs_attention)


@dataclass(frozen=True)
class _GradedResponse:
    """Internal: one graded question response with its student context."""

    student_key: str
    question_id: str
    assignment_version: str | None
    question_type: QuestionType
    response_text: str
    points_awarded: float
    possible_points: float | None


_ItemKey = tuple[str, str | None]


def _student_key(document: StudentSubmissionDocument, index: int) -> str:
    """A stable per-submission identity for correlating across questions."""
    return (
        document.student_id.strip()
        or document.source_pdf
        or document.student_name.strip()
        or f"__submission_{index}"
    )


def _collect(
    documents: list[StudentSubmissionDocument],
) -> tuple[list[_GradedResponse], dict[str, float]]:
    """Flatten every graded response and total each student's earned points.

    A student's total is the sum of their *graded* questions' awarded points —
    the number separation correlates each item against (minus the item itself).
    Ungraded questions are simply not yet part of the measurement.
    """
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
    """Pearson correlation of two equal-length series, or None if undefined.

    Undefined (returns ``None``) when there are fewer than two points or either
    series has no spread — a constant series has no correlation to report.
    """
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
    """Corrected item–total correlation for one item's responses.

    Each student contributes (item points, rest-of-assignment total) where the rest
    total excludes this item, so an item is never correlated against itself.
    Returns ``None`` below the sample floor or when the correlation is undefined.
    """
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
    """Per-choice tally for a multiple-choice item, largest share first.

    Blank answers are folded into their own "(blank)" bucket. Chooser
    performance is each option's mean of its students' whole-assignment percentage,
    omitted (``None``) when the cohort is too small to mean much.
    """
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
    """Natural-sort key so "q2" precedes "q10".

    Splits the id into alternating text/number tokens and compares numbers
    numerically. Each token is tagged with a type rank so an int and a str can
    never be compared directly (which would raise on mixed ids).
    """
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
    """Point value for an item: the document's own, else the caller's override.

    Extraction often leaves ``Possible points`` blank in the ``.submission.md``
    (the QA file is the real source of truth), so a caller that has the QA file
    — the TUI provider — can supply the authoritative values keyed by item.
    """
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
    """Compute per-question psychometrics for a graded cohort.

    Returns one :class:`ItemAnalysis` per ``(question_id, assignment_version)``
    that has at least one graded response, ordered by version then natural
    question order (q2 before q10). Difficulty is always reported when the
    question carries a point value; separation is reported once the item has
    enough graded responses (:data:`_MIN_SEPARATION_SAMPLE`), otherwise its band
    is :attr:`SeparationBand.INSUFFICIENT_DATA`. Distractor tallies are produced
    for multiple-choice items only.

    ``possible_points_by_question`` (keyed by ``(question_id, assignment_version)``,
    with ``(question_id, None)`` as a fallback) supplies point values where a
    submission file left ``Possible points`` blank — the TUI passes the QA
    file's authoritative points so difficulty is always available on screen.
    """
    responses, totals = _collect(documents)

    order: list[_ItemKey] = []
    groups: dict[_ItemKey, list[_GradedResponse]] = {}
    for response in responses:
        key: _ItemKey = (response.question_id, response.assignment_version)
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(response)

    # Resolve each item's point value once (document value, else override) so
    # difficulty and the whole-assignment percentages share one consistent basis.
    possible_by_item: dict[_ItemKey, float | None] = {
        key: _resolve_possible(key[0], key[1], groups[key], possible_points_by_question)
        for key in order
    }

    # Each student's own attainable maximum (sum of resolved possible points
    # over the questions they were graded on), for a fair whole-assignment percentage.
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


# ── learning-outcome mastery ─────────────────────────────────────────────────
#
# The item analysis above asks "is this question doing its job?". This asks the
# question an accreditation review asks instead: "did the students learn the
# thing we said we would teach them?" — which no per-question number answers,
# because an outcome is spread across several questions and several assignments.
#
# The engine stays pure and deliberately knows nothing about QA files: the
# caller passes the question -> outcomes map it derived from wherever it has one
# (``QAQuestion.outcomes_for`` today). That keeps this reusable by the
# course-level roll-up, which merges maps from several answer keys.

# Below this share of the available points an outcome is not yet mastered. 0.7
# is the conventional "C" line and the most common default in programme
# assessment; every consumer takes it as a parameter so a department with a
# different bar can say so rather than being told what its standard is.
DEFAULT_MASTERY_THRESHOLD = 0.7


@dataclass(frozen=True)
class StudentOutcomeMastery:
    """One student's performance on one learning outcome."""

    student_key: str
    student_id: str
    student_name: str
    outcome: str
    earned_points: float
    possible_points: float
    questions_graded: int

    @property
    def mastery(self) -> float | None:
        """Share of the outcome's available points earned, or ``None``.

        ``None`` when the student has no *graded* question carrying this
        outcome — different from scoring zero, and the distinction is the whole
        point: a partially-graded assignment must not report a student as having
        failed an outcome nobody has marked yet.
        """
        if self.possible_points <= EPSILON:
            return None
        return self.earned_points / self.possible_points

    def has_mastered(self, threshold: float = DEFAULT_MASTERY_THRESHOLD) -> bool | None:
        mastery = self.mastery
        return None if mastery is None else mastery >= threshold


@dataclass(frozen=True)
class OutcomeMastery:
    """How a whole class did on one learning outcome."""

    outcome: str
    # Every graded id tagged with this outcome, in natural question order —
    # this is the *coverage* half: which questions actually measure it.
    question_ids: tuple[str, ...]
    possible_points: float  # points tagged with this outcome, per student
    assignment_possible_points: float  # the assignment's total, for weight
    students_measured: int
    students_mastered: int
    mean_mastery: float | None
    students: tuple[StudentOutcomeMastery, ...]

    @property
    def mastery_rate(self) -> float | None:
        """Share of measured students at or above the mastery threshold."""
        if self.students_measured == 0:
            return None
        return self.students_mastered / self.students_measured

    @property
    def assignment_weight(self) -> float | None:
        """How much of the assignment's points this outcome accounts for.

        The coverage number an accreditation reviewer asks for: an outcome
        carried by 2 of 100 points is being *claimed*, not *assessed*.
        """
        if self.assignment_possible_points <= EPSILON:
            return None
        return self.possible_points / self.assignment_possible_points


@dataclass(frozen=True)
class OutcomeReport:
    """Per-outcome mastery for one graded assignment, plus its coverage."""

    outcomes: tuple[OutcomeMastery, ...]
    class_size: int
    mastery_threshold: float
    # Points on this assignment carrying no outcome tag at all. Reported rather
    # than hidden: an assignment where most points are untagged produces
    # outcome numbers that look authoritative and describe a fraction of it.
    untagged_points: float
    assignment_possible_points: float

    @property
    def tagged_share(self) -> float | None:
        """Share of the assignment's points carrying at least one outcome."""
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
    """Aggregate graded submissions into per-outcome mastery (pure, no I/O).

    ``outcomes_by_question`` maps a *graded* question id (``q1``, ``q5a``) to
    the outcomes it measures; ids absent from it are untagged and contribute
    only to the coverage figures. ``possible_points_by_question`` supplies point
    values where the submission files left ``Possible points`` blank, exactly as
    :func:`build_item_analysis` does.

    Two rules make the output honest:

    * **A question counts toward an outcome only where a human graded it.** An
      ungraded answer adds neither earned nor possible points, so a half-marked
      assignment reports mastery over what has actually been marked instead of
      quietly scoring the rest as zero.
    * **Points, not questions, are the unit.** An outcome carried by one
      20-point essay and one 2-point definition is dominated by the essay, which
      is what the instructor's own weighting already said.
    """
    responses, _ = _collect(documents)

    identity: dict[str, tuple[str, str]] = {}
    for index, document in enumerate(documents):
        identity[_student_key(document, index)] = (
            document.student_id.strip(),
            document.student_name.strip(),
        )

    # Per-student, per-outcome running totals; and the assignment-level point
    # pool, which is per *question* rather than per response (every student sits
    # the same assignment, so summing responses would multiply it by the class).
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

    # The per-student point pool for an outcome, and the assignment's total,
    # both counted once per question.
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
    """Order outcomes naturally so CLO2 precedes CLO10.

    Reuses the question-id natural sort: outcome names are the same shape
    (a prefix plus a number) often enough that plain alphabetical ordering would
    put CLO10 second and look like a bug in every report.
    """
    return _natural_key(outcome)
