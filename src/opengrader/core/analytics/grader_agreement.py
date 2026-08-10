"""Do two graders mark the same work the same way? (pure, no I/O)

Opengrader can already tell an instructor that *the same answer got different
grades* (:mod:`~opengrader.core.submissions.consistency`) and *how fast each
grader worked* (:mod:`grading_session`). What sat unmeasured between them is the
question a professor running a TA team actually asks: **is one of us harder than
the other, and did either of us drift over the afternoon?** Every ingredient was
already on disk — the audit journal records the ``actor`` and the timestamp of
every grade — and nothing read them together.

This module is that reading. It is deliberately one engine with two front ends,
because the two things an instructor wants are the same computation over
different samples:

* **Calibration (norming).** Before the pile is divided, every grader marks the
  *same* small sample independently. Overlap is total and designed, so the
  numbers are clean. :mod:`opengrader.core.handoff.calibration` cuts that sample.
* **Production grading.** The pile has been divided, so overlap is accidental —
  two graders happen to have marked the same *answer* (or, where claims
  overlapped, the same student's work). The numbers are noisier and the engine
  says so, but they need no extra work from anybody.

Keeping one engine is the point: a calibration session that measured agreement
one way and a live audit that measured it another would eventually disagree
about which TA is the harsh one, and there would be no way to tell which was
right.

Three decisions carry the honesty of the output:

- **Residuals, not raw means.** A grader's raw average is confounded by *which*
  work they were given — the TA who took questions 8-10 of a hard exam looks
  brutal and may not be. Every comparison is therefore run on the **residual**:
  the answer's awarded percentage minus the mean percentage that question
  received across all graders. A residual of +6 means "six points a hundred more
  generous than this question was marked overall", which is comparable across
  questions in a way a raw mean is not. This is also what makes the drift figure
  meaningful, since graders work question-by-question and the questions change
  as the session goes on.

- **Overlap is separated from inference, and labelled.** Where two graders
  marked genuinely comparable work, that is reported as a *pair* statistic with
  its sample size attached. Where they did not, the residual is still reported
  but it is not evidence that they disagree — only that they were handed
  different work. The report never merges the two into a single "leniency
  score", because the strong measurement and the weak one would then be
  indistinguishable.

- **A sample size is a first-class part of every figure.** Two answers in common
  is not a finding. Pair statistics below ``min_pair_samples`` are computed and
  carried, but marked ``sufficient=False`` so a caller can present them as
  provisional instead of as a verdict about a colleague.

Nothing here reads a file or names a threshold as a rule. It reports numbers
about *graders*, which is sensitive in the same way the grade history is; the
TUI keeps it behind teacher mode for exactly that reason.
"""

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

# How work has to line up before two graders can be compared on it.
#
# ``same_work`` is the gold standard: both marked the *same student's* answer,
# so nothing but the grader differs. It is what a calibration set produces by
# construction, and what production grading produces only where two graders'
# claims overlapped.
#
# ``same_answer`` is the fallback that makes production grading measurable at
# all: two students wrote the same thing (by the exact-grouping normalizer, so
# "same" means the same thing it means everywhere else in the app) and two
# different graders marked it. Weaker — the handwriting and the page differ —
# but it is real overlap, and on a large cohort there is a lot of it.
OverlapBasis = Literal["same_work", "same_answer"]

# Below this many comparable answers a pair statistic is reported but flagged as
# provisional. Twelve is not a magic number; it is the point where one unusual
# answer stops moving the mean by a whole grade band.
DEFAULT_MIN_PAIR_SAMPLES = 12

# A grader needs at least this many graded answers before an early-vs-late split
# says anything: with fewer, the two halves are a handful of answers each and
# the "drift" is just which questions happened to fall in which half.
DEFAULT_MIN_DRIFT_SAMPLES = 20

# Two grades this close (in percentage points of the question) are treated as
# the same decision. Absorbs float noise in the points arithmetic without
# absorbing a real half-point difference on a 10-point question.
_AGREEMENT_EPSILON = 1e-6


@dataclass(frozen=True)
class GradedAnswer:
    """One grader's decision on one answer — the engine's only input.

    Deliberately flat and source-agnostic: the journal adapter, the calibration
    adapter and a test fixture all build the same record, which is what lets one
    engine serve all three.
    """

    actor: str
    student_key: str
    question_id: str
    points_awarded: float
    possible_points: float | None = None
    rubric_labels: tuple[str, ...] = ()
    # The normalized answer text, for same-answer overlap. Empty means unknown
    # or blank, and never participates in overlap: a blank answer is shared by
    # everyone who wrote nothing and would manufacture agreement out of absence.
    answer_key: str = ""
    at: datetime | None = None
    student_label: str = ""

    @property
    def percent(self) -> float | None:
        """Awarded share of the question, or ``None`` when it has no weight.

        A zero-point question cannot be scored generously or harshly, so it is
        excluded from every mean rather than counted as 0% or 100%.
        """
        if self.possible_points is None or self.possible_points <= 0:
            return None
        return (self.points_awarded / self.possible_points) * 100.0


@dataclass(frozen=True)
class DriftStats:
    """Whether a grader's marking moved between the start and end of their work.

    Both figures are mean *residuals* (see the module docstring), so the
    comparison is not confounded by which questions were graded when — which
    matters because graders work through a cohort question by question, so the
    late half is a different question from the early half almost by definition.
    """

    samples: int
    early_residual: float
    late_residual: float

    @property
    def delta(self) -> float:
        """Late minus early: positive means the grader loosened over time."""
        return self.late_residual - self.early_residual


@dataclass(frozen=True)
class GraderStats:
    """One grader's marking, summarized."""

    actor: str
    answers: int
    students: int
    questions: int
    # Raw mean awarded percentage. Honest but confounded by which work they were
    # given, so it is shown for context and never used for a comparison.
    mean_percent: float | None
    # Mean residual against each question's overall mean. This is the leniency
    # figure: positive = more generous than the cohort's marking of the same
    # questions. Note that the norm includes this grader's own answers, so a
    # sole grader necessarily scores ~0 — you cannot be lenient relative only to
    # yourself, and the number correctly says nothing rather than inventing a
    # verdict from one person's marking.
    mean_residual: float | None
    first_graded: datetime | None
    last_graded: datetime | None
    drift: DriftStats | None


@dataclass(frozen=True)
class PairAgreement:
    """How two graders compared on work they both marked."""

    a: str
    b: str
    basis: OverlapBasis
    samples: int
    # Share of comparable answers where both awarded the same points.
    exact_agreement: float
    # Mean absolute gap, in percentage points of the question.
    mean_absolute_gap: float
    # Mean signed gap, a minus b: positive means `a` marked more generously.
    mean_signed_gap: float
    max_absolute_gap: float
    sufficient: bool

    @property
    def more_generous(self) -> str:
        """Which grader marked higher on the shared work ("" if they tied)."""
        if abs(self.mean_signed_gap) <= _AGREEMENT_EPSILON:
            return ""
        return self.a if self.mean_signed_gap > 0 else self.b


@dataclass(frozen=True)
class QuestionNorm:
    """The reference every residual is measured against."""

    question_id: str
    answers: int
    graders: int
    mean_percent: float


@dataclass(frozen=True)
class GraderAgreementReport:
    """Leniency, drift and pairwise agreement across a set of graders."""

    graders: tuple[GraderStats, ...]
    # Pairs on genuinely identical work — the strong measurement.
    same_work_pairs: tuple[PairAgreement, ...]
    # Pairs on identical *answers* from different students — the weaker one that
    # a divided cohort can still supply.
    same_answer_pairs: tuple[PairAgreement, ...]
    questions: tuple[QuestionNorm, ...]
    total_answers: int
    # Answers excluded from every mean because their question carries no points.
    unweighted_answers: int
    min_pair_samples: int

    @property
    def distinct_graders(self) -> int:
        return len(self.graders)

    @property
    def has_overlap(self) -> bool:
        """Whether any two graders marked comparable work at all.

        ``False`` is the ordinary state of a cleanly divided cohort, and the
        reason calibration exists: without overlap the report can describe each
        grader but cannot compare them.
        """
        return bool(self.same_work_pairs or self.same_answer_pairs)

    def pairs_for(self, basis: OverlapBasis) -> tuple[PairAgreement, ...]:
        return self.same_work_pairs if basis == "same_work" else self.same_answer_pairs


def _weighted(answers: Iterable[GradedAnswer]) -> list[tuple[GradedAnswer, float]]:
    """Keep only answers whose question carries points, paired with the percent."""
    kept: list[tuple[GradedAnswer, float]] = []
    for answer in answers:
        percent = answer.percent
        if percent is not None:
            kept.append((answer, percent))
    return kept


def _question_means(weighted: Sequence[tuple[GradedAnswer, float]]) -> dict[str, float]:
    """Mean awarded percentage per question, pooled across every grader."""
    buckets: dict[str, list[float]] = {}
    for answer, percent in weighted:
        buckets.setdefault(answer.question_id, []).append(percent)
    return {question: mean(values) for question, values in buckets.items()}


def _latest_per_actor(
    answers: Sequence[GradedAnswer],
) -> dict[str, GradedAnswer]:
    """One decision per grader for a comparison bucket — their last one.

    A grader who regraded an answer has two entries in the journal; the pair
    statistics must compare the grade that stands, not the one that was
    replaced. Timestamps decide when present, otherwise the last record wins,
    which matches the journal's own oldest-first ordering.
    """
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
    """Compare every pair of graders over the buckets they both marked."""
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
                # Signed as first-minus-second, and `actors` is sorted, so the
                # sign always means the same thing for a given pair.
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

    # Widest disagreement first, but only among pairs with enough evidence:
    # a two-sample pair with a huge gap is noise, and leading with it would
    # point an instructor at the wrong colleague.
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
    """Compare a grader's first and second half, on residuals.

    Returns ``None`` rather than a number whenever the split cannot mean
    anything: too few answers, or no timestamps to order them by. An unknown
    drift has to stay visibly unknown — a 0.0 would read as "measured, and
    steady", which is the one thing it does not say.
    """
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
    """Summarize how a set of graders marked, and how they compare.

    Read-only and total: it takes decisions and returns statistics, and is safe
    on an empty input (every figure ``None`` or zero rather than an exception).

    Leniency is reported as a mean **residual** against each question's overall
    mean, so it is not confounded by which questions a grader was given.
    Agreement is reported only where two graders marked comparable work, split
    by :data:`OverlapBasis` so the strong measurement (identical work) is never
    silently blended with the weaker one (identical answers from different
    students), and every pair carries its sample size and a ``sufficient`` flag.
    """
    all_answers = list(answers)
    weighted = _weighted(all_answers)
    question_means = _question_means(weighted)

    by_actor: dict[str, list[tuple[GradedAnswer, float]]] = {}
    for answer, percent in weighted:
        by_actor.setdefault(answer.actor, []).append((answer, percent))
    # A grader whose every answer was unweighted still graded: list them with
    # empty statistics rather than dropping them out of the report entirely.
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
    # Most work first, then name: the same ordering rule the session report uses.
    graders.sort(key=lambda stats: (-stats.answers, stats.actor))

    same_work: dict[tuple[str, ...], list[GradedAnswer]] = {}
    same_answer: dict[tuple[str, ...], list[GradedAnswer]] = {}
    for answer, _ in weighted:
        if answer.student_key:
            same_work.setdefault((answer.question_id, answer.student_key), []).append(answer)
        # A blank/unknown answer key is not overlap: everyone who wrote nothing
        # would otherwise be compared against everyone else who wrote nothing.
        if answer.answer_key:
            same_answer.setdefault((answer.question_id, answer.answer_key), []).append(answer)

    # Counted in one pass rather than re-scanning per question: a 40-question
    # cohort would otherwise walk every answer forty times.
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


# --------------------------------------------------------------------------- #
# Adapters: the two sources that feed the engine. Both are pure — they take
# parsed documents and entries, never paths — so the whole feature unit-tests
# with no folder on disk.
# --------------------------------------------------------------------------- #


def _question_index(
    documents: Iterable[StudentSubmissionDocument],
) -> dict[tuple[str, str], tuple[float | None, str, str]]:
    """(student key, question id) → (possible points, answer key, label).

    The journal records what a grade *changed to*, not what the question was
    worth or what the student wrote, so those come from the submission files —
    which is also why a journal entry for a submission that has since been
    deleted is simply not comparable and gets dropped.
    """
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
    """What the journal yielded, and what it could not.

    ``unmatched`` is reported rather than swallowed: journal entries with no
    surviving submission mean the report describes less grading than the folder
    actually saw, and an instructor comparing two TAs deserves to know that.
    """

    answers: tuple[GradedAnswer, ...]
    unmatched: int


def answers_from_journal(
    entries: Iterable[GradeJournalEntry],
    documents: Iterable[StudentSubmissionDocument],
) -> JournalAdaptation:
    """Read grade events out of the audit journal as comparable decisions.

    Only ``grade`` events that recorded a points value are usable: a comment or
    a rubric edit says nothing about severity, and a grade cleared back to
    ungraded is not a mark. The question's weight and the student's answer come
    from the submission files, since the journal never carried them.
    """
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
    """Read one graded copy per grader as comparable decisions.

    Each copy is a mapping of *our* student key → that grader's document, so the
    caller has already undone any packet aliasing. Every graded question becomes
    one decision; ungraded ones are simply absent, which is what makes a partly
    finished calibration packet still usable.

    There are no timestamps here — a returned copy records the grade, not when
    it was made — so drift is not computed from a calibration set.
    """
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
