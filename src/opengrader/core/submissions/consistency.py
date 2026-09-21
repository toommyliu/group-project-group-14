from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from opengrader.core.question_types import QuestionType
from opengrader.core.submissions.answer_groups import normalize_answer_key
from opengrader.core.submissions.student_file import StudentSubmissionDocument

ConsistencySeverity = Literal["high", "low"]

_SEVERITY_RANK: dict[ConsistencySeverity, int] = {"high": 0, "low": 1}

_POINTS_DECIMALS = 6


class ConsistencyKind(str, Enum):
    POINTS_DIVERGENCE = "points_divergence"
    RUBRIC_DIVERGENCE = "rubric_divergence"


@dataclass(frozen=True)
class ResponseRef:
    student_id: str
    student_name: str
    source_pdf: str

    @property
    def label(self) -> str:
        return self.student_name or self.student_id or self.source_pdf or "unknown"


@dataclass(frozen=True)
class GradeVariant:
    points_awarded: float
    rubric_labels: tuple[str, ...]
    members: tuple[ResponseRef, ...]

    @property
    def size(self) -> int:
        return len(self.members)


@dataclass(frozen=True)
class ConsistencyFlag:
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
        return self.answer_key == ""

    @property
    def member_count(self) -> int:
        return sum(variant.size for variant in self.variants)

    @property
    def point_spread(self) -> float:
        points = [variant.points_awarded for variant in self.variants]
        return max(points) - min(points)


@dataclass(frozen=True)
class _Response:
    ref: ResponseRef
    question_id: str
    assignment_version: str | None
    question_type: QuestionType
    response_text: str
    points_awarded: float
    possible_points: float | None
    rubric_labels: tuple[str, ...]


_GroupKey = tuple[str, str | None, str]


def _points_bucket(points: float) -> float:
    return round(points, _POINTS_DECIMALS)


def _collect_graded_responses(
    documents: list[StudentSubmissionDocument],
) -> list[_Response]:
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
            continue

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
