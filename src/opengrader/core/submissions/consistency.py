"""Grading-consistency audit: did the same answer get the same grade?

The single fairness guarantee an instructor most wants from a batch of graded
assignments is that two students who wrote *the same answer* to a question received
the *same* score. Opengrader already leans on this: the TUI's answer-grouping
workflow (the ``g`` hotkey, :mod:`answer_groups`) lets a grader apply one rubric
selection to everyone who gave an answer. But grades drift — a group graded
before a rubric tweak, two TAs splitting a question, an early hand-grade
overridden later — and nothing today catches it before the scores reach Canvas.

This module is that catch. It is a **pure** function over the persisted
``.submission.md`` data (no I/O, no OCR) that clusters every graded response by
its answer and reports each cluster whose members did *not* all receive the same
grade.

It reuses :func:`answer_groups.normalize_answer_key` — the one place that
decides "same answer" — so the two features can never disagree, and so a flag
always means genuinely-identical answers were graded differently (the
normalizer only collapses cosmetic differences, never fuzzy/semantic ones).

Why this lives beside :mod:`review` rather than inside it: a review flag is a
property of a *single* submission (:func:`review.flag_submission` takes one
document), whereas consistency is inherently *cross-submission* — it can only be
judged with the whole cohort for a question in hand. Forcing it into the
per-submission shape would break that module's design, so it gets its own
cohort-level engine with the same conventions (frozen dataclasses, deterministic
ordering, severity-ranked output).

Partitioning note: responses are compared only within the same
``assignment_version``. On a multi-version assignment the answer key differs per
version, so "42" can be correct on version A and wrong on version B — comparing
across versions would manufacture false conflicts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from opengrader.core.qa_file.scoring import EPSILON
from opengrader.core.question_types import QuestionType
from opengrader.core.submissions.answer_groups import normalize_answer_key
from opengrader.core.submissions.student_file import StudentSubmissionDocument

ConsistencySeverity = Literal["high", "low"]

_SEVERITY_RANK: dict[ConsistencySeverity, int] = {"high": 0, "low": 1}

# Points are bucketed to this many decimals so float noise never splits an
# otherwise-identical score. Mirrors scoring.EPSILON (1e-6).
_POINTS_DECIMALS = 6


class ConsistencyKind(str, Enum):
    """Why an answer group is inconsistent."""

    # Members of one answer group received different point totals — the direct
    # fairness violation (different scores for the same answer).
    POINTS_DIVERGENCE = "points_divergence"
    # Members received the same points but via different rubric selections — the
    # score is fair, but the rubric was applied inconsistently (so PDF comments
    # and any later re-tally would diverge). Worth a look, not a scoring bug.
    RUBRIC_DIVERGENCE = "rubric_divergence"


@dataclass(frozen=True)
class ResponseRef:
    """Identifies the student behind one response in a variant."""

    student_id: str
    student_name: str
    source_pdf: str

    @property
    def label(self) -> str:
        """A human handle for this student, preferring the name."""
        return self.student_name or self.student_id or self.source_pdf or "unknown"


@dataclass(frozen=True)
class GradeVariant:
    """One distinct grade outcome within an answer group, and who got it."""

    points_awarded: float
    rubric_labels: tuple[str, ...]
    members: tuple[ResponseRef, ...]

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass(frozen=True)
class ConsistencyFlag:
    """An answer group whose members were graded differently.

    Anchored to one question on one version, it carries the shared answer and
    the two-or-more :class:`GradeVariant`s the group split into (largest first).
    """

    question_id: str
    assignment_version: str | None
    question_type: QuestionType
    answer_key: str
    sample_response_text: str
    possible_points: float | None
    kind: ConsistencyKind
    severity: ConsistencySeverity
    variants: tuple[GradeVariant, ...]

    @property
    def severity_rank(self) -> int:
        return _SEVERITY_RANK[self.severity]

    @property
    def is_blank(self) -> bool:
        """Whether the shared answer is the "no answer" bucket."""
        return self.answer_key == ""

    @property
    def member_count(self) -> int:
        return sum(variant.size for variant in self.variants)

    @property
    def point_spread(self) -> float:
        """The largest gap in awarded points across the group (0 for rubric-only)."""
        points = [variant.points_awarded for variant in self.variants]
        return max(points) - min(points)


@dataclass(frozen=True)
class _Response:
    """Internal: one graded response with everything grouping needs."""

    ref: ResponseRef
    question_id: str
    assignment_version: str | None
    question_type: QuestionType
    response_text: str
    points_awarded: float
    possible_points: float | None
    rubric_labels: tuple[str, ...]


# Grouping key: same question, same version, same normalized answer.
_GroupKey = tuple[str, str | None, str]


def _points_bucket(points: float) -> float:
    """Canonicalize a score so values within EPSILON compare equal."""
    return round(points, _POINTS_DECIMALS)


def _collect_graded_responses(
    documents: list[StudentSubmissionDocument],
) -> list[_Response]:
    """Flatten every *graded* response across submissions.

    A response participates only when it has an awarded point value — that is
    the Canvas-facing number the audit compares. A question with rubric labels
    but no points is malformed and left to the needs-review queue.
    """
    responses: list[_Response] = []
    for document in documents:
        ref = ResponseRef(
            student_id=document.student_id.strip(),
            student_name=document.student_name.strip(),
            source_pdf=document.source_pdf,
        )
        for question in document.questions:
            if question.points_awarded is None:
                continue
            responses.append(
                _Response(
                    ref=ref,
                    question_id=question.question_id,
                    assignment_version=document.assignment_version,
                    question_type=question.question_type,
                    response_text=question.response_text,
                    points_awarded=question.points_awarded,
                    possible_points=question.possible_points,
                    rubric_labels=tuple(question.rubric_labels),
                )
            )
    return responses


def _build_variants(group: list[_Response]) -> list[GradeVariant]:
    """Split a group of same-answer responses into distinct grade outcomes.

    Two members share a variant when their awarded points and their *set* of
    rubric labels match (label order is not significant — a checklist selection
    is a set). Variants are ordered largest-first, then by points, then labels,
    for a stable, reviewer-friendly presentation.
    """
    order: list[tuple[float, frozenset[str]]] = []
    members_by_outcome: dict[tuple[float, frozenset[str]], list[ResponseRef]] = {}
    display_by_outcome: dict[tuple[float, frozenset[str]], tuple[float, tuple[str, ...]]] = {}

    for response in group:
        outcome = (_points_bucket(response.points_awarded), frozenset(response.rubric_labels))
        if outcome not in members_by_outcome:
            order.append(outcome)
            members_by_outcome[outcome] = []
            display_by_outcome[outcome] = (response.points_awarded, response.rubric_labels)
        members_by_outcome[outcome].append(response.ref)

    variants = [
        GradeVariant(
            points_awarded=display_by_outcome[outcome][0],
            rubric_labels=display_by_outcome[outcome][1],
            members=tuple(
                sorted(
                    members_by_outcome[outcome],
                    key=lambda ref: (ref.student_name.lower(), ref.student_id, ref.source_pdf),
                )
            ),
        )
        for outcome in order
    ]

    variants.sort(
        key=lambda variant: (
            -variant.size,
            variant.points_awarded,
            variant.rubric_labels,
        )
    )
    return variants


def build_consistency_flags(
    documents: list[StudentSubmissionDocument],
    *,
    include_blank: bool = False,
) -> list[ConsistencyFlag]:
    """Return every answer group that was graded inconsistently, worst first.

    Two responses are "the same answer" when
    :func:`answer_groups.normalize_answer_key` returns the same key for them,
    within the same question and ``assignment_version``. A group is flagged when
    its graded members did not all receive the same grade:

    - different awarded points  → :attr:`ConsistencyKind.POINTS_DIVERGENCE`
      (``severity="high"`` — students got different scores for the same answer);
    - same points, different rubric selection → :attr:`ConsistencyKind.RUBRIC_DIVERGENCE`
      (``severity="low"`` — fair score, inconsistent rubric).

    Groups with a single graded member, or fully consistent groups, are not
    returned. The blank ("no answer") bucket is skipped unless ``include_blank``
    is set, because a blank transcription most often reflects work the OCR
    missed, making it the noisiest signal.

    The ordering is total and deterministic: severity, then widest point spread,
    then largest group, then question id, version, and answer key — so the same
    inputs always produce the same audit.
    """
    responses = _collect_graded_responses(documents)

    order: list[_GroupKey] = []
    groups: dict[_GroupKey, list[_Response]] = {}
    samples: dict[_GroupKey, str] = {}
    for response in responses:
        answer_key = normalize_answer_key(response.response_text, response.question_type)
        key: _GroupKey = (response.question_id, response.assignment_version, answer_key)
        if key not in groups:
            order.append(key)
            groups[key] = []
            samples[key] = ""
        groups[key].append(response)
        # Keep the first non-blank sample so the report can echo the answer.
        if not samples[key] and response.response_text.strip():
            samples[key] = response.response_text.strip()

    flags: list[ConsistencyFlag] = []
    for key in order:
        question_id, version, answer_key = key
        if answer_key == "" and not include_blank:
            continue
        group = groups[key]
        if len(group) < 2:
            continue

        variants = _build_variants(group)
        if len(variants) < 2:
            continue  # every member graded identically

        distinct_points = {_points_bucket(v.points_awarded) for v in variants}
        if len(distinct_points) > 1:
            kind = ConsistencyKind.POINTS_DIVERGENCE
            severity: ConsistencySeverity = "high"
        else:
            kind = ConsistencyKind.RUBRIC_DIVERGENCE
            severity = "low"

        first = group[0]
        flags.append(
            ConsistencyFlag(
                question_id=question_id,
                assignment_version=version,
                question_type=first.question_type,
                answer_key=answer_key,
                sample_response_text=samples[key],
                possible_points=first.possible_points,
                kind=kind,
                severity=severity,
                variants=tuple(variants),
            )
        )

    flags.sort(
        key=lambda flag: (
            flag.severity_rank,
            -flag.point_spread,
            -flag.member_count,
            flag.question_id,
            flag.assignment_version or "",
            flag.answer_key,
        )
    )
    return flags
