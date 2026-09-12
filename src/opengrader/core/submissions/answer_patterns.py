from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations
from typing import Iterable, Literal, Mapping, Sequence

from opengrader.core.grading.solutions import (
    normalize_choice_response,
    objective_correctness,
    solution_entries_for_submission,
)
from opengrader.core.qa_file import QADocument
from opengrader.core.submissions.student_file import StudentSubmissionDocument

PatternSeverity = Literal["high", "medium"]

_SEVERITY_RANK: dict[PatternSeverity, int] = {"high": 0, "medium": 1}

# 4-sigma accounts for comparisons across every student pair.
DEFAULT_PATTERN_THRESHOLD = 4.0

STRONG_PATTERN_THRESHOLD = 6.0

DEFAULT_MIN_SHARED_WRONG = 3

# Agreement is inevitable when only one wrong choice exists.
MIN_DISTINCT_WRONG_ANSWERS = 2

MIN_WRONG_RESPONDENTS = 3


class PatternKind(str, Enum):
    SHARED_WRONG_ANSWERS = "shared_wrong_answers"


class ExclusionReason(str, Enum):
    NO_WRONG_ANSWERS = "no_wrong_answers"
    SINGLE_DISTRACTOR = "single_distractor"
    TOO_FEW_WRONG = "too_few_wrong"


@dataclass(frozen=True)
class PatternMember:
    student_id: str
    student_name: str
    source_pdf: str

    @property
    def label(self) -> str:
        return self.student_name or self.student_id or self.source_pdf or "unknown"


@dataclass(frozen=True)
class ObjectiveResponse:
    member: PatternMember
    question_id: str
    assignment_version: str | None
    answer: str
    correct: bool
    expected_answer: str = ""


@dataclass(frozen=True)
class SharedWrongAnswer:
    question_id: str
    answer: str
    expected_answer: str
    match_probability: float


@dataclass(frozen=True)
class AnswerPatternFlag:
    assignment_version: str | None
    kind: PatternKind
    severity: PatternSeverity
    a: PatternMember
    b: PatternMember
    shared_wrong: tuple[SharedWrongAnswer, ...]
    expected_shared: float
    z_score: float
    both_wrong_count: int
    difference_count: int
    compared_questions: int

    @property
    def members(self) -> tuple[PatternMember, PatternMember]:
        return (self.a, self.b)

    @property
    def shared_wrong_count(self) -> int:
        return len(self.shared_wrong)

    @property
    def harpp_hogan_index(self) -> float | None:
        if self.difference_count <= 0:
            return None
        return self.shared_wrong_count / self.difference_count

    @property
    def severity_rank(self) -> int:
        return _SEVERITY_RANK[self.severity]


@dataclass(frozen=True)
class QuestionExclusion:
    question_id: str
    assignment_version: str | None
    reason: ExclusionReason
    wrong_count: int
    distinct_wrong_answers: int


@dataclass(frozen=True)
class AnswerPatternReport:
    flags: tuple[AnswerPatternFlag, ...] = ()
    analyzed_questions: tuple[str, ...] = ()
    excluded_questions: tuple[QuestionExclusion, ...] = ()
    pairs_compared: int = 0
    threshold: float = DEFAULT_PATTERN_THRESHOLD
    min_shared_wrong: int = DEFAULT_MIN_SHARED_WRONG

    @property
    def expected_by_chance(self) -> float:
        if self.pairs_compared <= 0:
            return 0.0
        return self.pairs_compared * _normal_tail(self.threshold)

    @property
    def analyzed(self) -> bool:
        return bool(self.analyzed_questions)


def _normal_tail(z: float) -> float:
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def objective_responses_from_documents(
    documents: Sequence[StudentSubmissionDocument],
    qa_doc: QADocument,
) -> list[ObjectiveResponse]:
    responses: list[ObjectiveResponse] = []
    for document in documents:
        member = PatternMember(
            student_id=document.student_id.strip(),
            student_name=document.student_name.strip(),
            source_pdf=document.source_pdf,
        )
        entries = solution_entries_for_submission(qa_doc, document)
        for question in document.questions:
            entry = entries.get(question.question_id)
            if entry is None:
                continue
            raw = question.response_text or ""
            answer = normalize_choice_response(raw)
            if not answer:
                continue
            correct = objective_correctness(entry, raw, question)
            if correct is None:
                continue
            responses.append(
                ObjectiveResponse(
                    member=member,
                    question_id=question.question_id,
                    assignment_version=document.assignment_version,
                    answer=answer,
                    correct=correct,
                    expected_answer=", ".join(entry.expected_answers),
                )
            )
    return responses


def _member_sort_key(member: PatternMember) -> tuple[str, str, str]:
    return (member.student_name.lower(), member.student_id, member.source_pdf)


def match_probability(wrong_answers: Sequence[str]) -> float:
    total = len(wrong_answers)
    if total < 2:
        return 0.0
    counts: dict[str, int] = {}
    for answer in wrong_answers:
        counts[answer] = counts.get(answer, 0) + 1
    pairs_matching = sum(count * (count - 1) for count in counts.values())
    return pairs_matching / (total * (total - 1))


@dataclass(frozen=True)
class _EligibleQuestion:
    question_id: str
    expected_answer: str
    match_probability: float


def _bit_indices(mask: int) -> Iterable[int]:
    while mask:
        lowest = mask & -mask
        yield lowest.bit_length() - 1
        mask ^= lowest


def _partition_by_version(
    responses: Sequence[ObjectiveResponse],
) -> dict[str | None, list[ObjectiveResponse]]:
    groups: dict[str | None, list[ObjectiveResponse]] = {}
    for response in responses:
        groups.setdefault(response.assignment_version, []).append(response)
    return groups


def _eligible_questions(
    responses: Sequence[ObjectiveResponse],
    version: str | None,
) -> tuple[list[_EligibleQuestion], list[QuestionExclusion]]:
    wrong_by_question: dict[str, list[str]] = {}
    expected_by_question: dict[str, str] = {}
    order: list[str] = []
    for response in responses:
        if response.question_id not in wrong_by_question:
            wrong_by_question[response.question_id] = []
            order.append(response.question_id)
        expected_by_question.setdefault(response.question_id, response.expected_answer)
        if not response.correct:
            wrong_by_question[response.question_id].append(response.answer)

    eligible: list[_EligibleQuestion] = []
    excluded: list[QuestionExclusion] = []
    for question_id in order:
        wrong = wrong_by_question[question_id]
        distinct = len(set(wrong))
        reason: ExclusionReason | None = None
        if not wrong:
            reason = ExclusionReason.NO_WRONG_ANSWERS
        elif distinct < MIN_DISTINCT_WRONG_ANSWERS:
            reason = ExclusionReason.SINGLE_DISTRACTOR
        elif len(wrong) < MIN_WRONG_RESPONDENTS:
            reason = ExclusionReason.TOO_FEW_WRONG
        if reason is not None:
            excluded.append(
                QuestionExclusion(
                    question_id=question_id,
                    assignment_version=version,
                    reason=reason,
                    wrong_count=len(wrong),
                    distinct_wrong_answers=distinct,
                )
            )
            continue
        eligible.append(
            _EligibleQuestion(
                question_id=question_id,
                expected_answer=expected_by_question.get(question_id, ""),
                match_probability=match_probability(wrong),
            )
        )
    return eligible, excluded


@dataclass
class _StudentProfile:
    member: PatternMember
    answered_mask: int = 0
    wrong_mask: int = 0
    correct_mask: int = 0
    wrong_answers: dict[int, str] = field(default_factory=dict)


def _build_profiles(
    responses: Sequence[ObjectiveResponse],
    eligible: Sequence[_EligibleQuestion],
) -> list[_StudentProfile]:
    bit_of = {question.question_id: index for index, question in enumerate(eligible)}
    profiles: dict[PatternMember, _StudentProfile] = {}
    for response in responses:
        bit = bit_of.get(response.question_id)
        if bit is None:
            continue
        profile = profiles.get(response.member)
        if profile is None:
            profile = _StudentProfile(member=response.member)
            profiles[response.member] = profile
        flag = 1 << bit
        profile.answered_mask |= flag
        if response.correct:
            profile.correct_mask |= flag
        else:
            profile.wrong_mask |= flag
            profile.wrong_answers[bit] = response.answer
    return sorted(profiles.values(), key=lambda profile: _member_sort_key(profile.member))


def _shared_wrong_index(
    profiles: Sequence[_StudentProfile],
    eligible: Sequence[_EligibleQuestion],
) -> dict[tuple[int, int], list[int]]:
    shared: dict[tuple[int, int], list[int]] = {}
    for bit in range(len(eligible)):
        by_answer: dict[str, list[int]] = {}
        for index, profile in enumerate(profiles):
            answer = profile.wrong_answers.get(bit)
            if answer is not None:
                by_answer.setdefault(answer, []).append(index)
        for indices in by_answer.values():
            if len(indices) < 2:
                continue
            for first, second in combinations(indices, 2):
                shared.setdefault((first, second), []).append(bit)
    return shared


def _flags_for_version(
    version: str | None,
    responses: Sequence[ObjectiveResponse],
    *,
    threshold: float,
    min_shared_wrong: int,
) -> tuple[list[AnswerPatternFlag], list[_EligibleQuestion], list[QuestionExclusion], int]:
    eligible, excluded = _eligible_questions(responses, version)
    if not eligible:
        return [], [], excluded, 0

    profiles = _build_profiles(responses, eligible)
    if len(profiles) < 2:
        return [], eligible, excluded, 0

    pairs_compared = sum(
        1
        for first, second in combinations(range(len(profiles)), 2)
        if profiles[first].wrong_mask & profiles[second].wrong_mask
    )

    probability = [question.match_probability for question in eligible]
    flags: list[AnswerPatternFlag] = []
    for (first, second), bits in _shared_wrong_index(profiles, eligible).items():
        if len(bits) < min_shared_wrong:
            continue
        a, b = profiles[first], profiles[second]

        both_wrong = a.wrong_mask & b.wrong_mask
        expected = 0.0
        variance = 0.0
        both_wrong_count = 0
        for bit in _bit_indices(both_wrong):
            chance = probability[bit]
            expected += chance
            variance += chance * (1.0 - chance)
            both_wrong_count += 1
        if variance <= 0.0:
            continue

        z_score = (len(bits) - expected) / math.sqrt(variance)
        if z_score < threshold:
            continue

        both_answered = a.answered_mask & b.answered_mask
        agreements = len(bits) + (a.correct_mask & b.correct_mask).bit_count()
        differences = both_answered.bit_count() - agreements

        shared_wrong = tuple(
            SharedWrongAnswer(
                question_id=eligible[bit].question_id,
                answer=a.wrong_answers[bit],
                expected_answer=eligible[bit].expected_answer,
                match_probability=probability[bit],
            )
            for bit in sorted(bits, key=lambda index: (probability[index], index))
        )
        severity: PatternSeverity = (
            "high" if z_score >= STRONG_PATTERN_THRESHOLD else "medium"
        )
        member_a, member_b = sorted((a.member, b.member), key=_member_sort_key)
        flags.append(
            AnswerPatternFlag(
                assignment_version=version,
                kind=PatternKind.SHARED_WRONG_ANSWERS,
                severity=severity,
                a=member_a,
                b=member_b,
                shared_wrong=shared_wrong,
                expected_shared=expected,
                z_score=z_score,
                both_wrong_count=both_wrong_count,
                difference_count=differences,
                compared_questions=(a.answered_mask | b.answered_mask).bit_count(),
            )
        )
    return flags, eligible, excluded, pairs_compared


def build_answer_pattern_report(
    responses: Sequence[ObjectiveResponse],
    *,
    threshold: float = DEFAULT_PATTERN_THRESHOLD,
    min_shared_wrong: int = DEFAULT_MIN_SHARED_WRONG,
) -> AnswerPatternReport:
    flags: list[AnswerPatternFlag] = []
    analyzed: list[str] = []
    excluded: list[QuestionExclusion] = []
    pairs_compared = 0

    for version, group in _partition_by_version(responses).items():
        version_flags, eligible, version_excluded, tested = _flags_for_version(
            version,
            group,
            threshold=threshold,
            min_shared_wrong=min_shared_wrong,
        )
        flags.extend(version_flags)
        excluded.extend(version_excluded)
        pairs_compared += tested
        for question in eligible:
            if question.question_id not in analyzed:
                analyzed.append(question.question_id)

    flags.sort(
        key=lambda flag: (
            flag.severity_rank,
            -flag.z_score,
            -flag.shared_wrong_count,
            flag.assignment_version or "",
            _member_sort_key(flag.a),
            _member_sort_key(flag.b),
        )
    )
    excluded.sort(
        key=lambda item: (item.assignment_version or "", item.question_id)
    )
    return AnswerPatternReport(
        flags=tuple(flags),
        analyzed_questions=tuple(analyzed),
        excluded_questions=tuple(excluded),
        pairs_compared=pairs_compared,
        threshold=threshold,
        min_shared_wrong=min_shared_wrong,
    )


def build_answer_pattern_report_for_documents(
    documents: Sequence[StudentSubmissionDocument],
    qa_doc: QADocument,
    *,
    threshold: float = DEFAULT_PATTERN_THRESHOLD,
    min_shared_wrong: int = DEFAULT_MIN_SHARED_WRONG,
) -> AnswerPatternReport:
    return build_answer_pattern_report(
        objective_responses_from_documents(documents, qa_doc),
        threshold=threshold,
        min_shared_wrong=min_shared_wrong,
    )


EXCLUSION_LABELS: Mapping[ExclusionReason, str] = {
    ExclusionReason.NO_WRONG_ANSWERS: "nobody answered it incorrectly",
    ExclusionReason.SINGLE_DISTRACTOR: "only one wrong answer was ever given",
    ExclusionReason.TOO_FEW_WRONG: "too few wrong answers to model",
}
