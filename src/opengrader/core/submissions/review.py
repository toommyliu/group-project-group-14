from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal, Sequence

from opengrader.core.grading.llm_grader import (
    ai_analysis_confidence,
    ai_analysis_is_reviewed,
)
from opengrader.core.grading.solutions import MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE
from opengrader.core.question_types import FREE_RESPONSE
from opengrader.core.roster import RosterStudent
from opengrader.core.submissions.regrade import RegradeRequest
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.analytics.rubric_drift import StaleGrade

ReviewSeverity = Literal["high", "medium", "low"]

_SEVERITY_RANK: dict[ReviewSeverity, int] = {"high": 0, "medium": 1, "low": 2}

_EMPTY_RESPONSE_SENTINELS = {"", "none", "n/a", "na", "null", "-", "—", "–"}

_DEFAULT_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{5,}$")

DEFAULT_AI_CONFIDENCE_FLOOR = 80

DEFAULT_OCR_CONFIDENCE_FLOOR = int(MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE)

DEFAULT_IDENTITY_CONFIDENCE_FLOOR = 80

_ZERO_SWEEP_MIN_GRADED = 2


class ReviewReason(str, Enum):
    MISSING_STUDENT_ID = "missing_student_id"
    MALFORMED_STUDENT_ID = "malformed_student_id"
    MISSING_STUDENT_NAME = "missing_student_name"
    EMPTY_RESPONSE = "empty_response"
    LOW_CONFIDENCE_TRANSCRIPTION = "low_confidence_transcription"
    LOW_CONFIDENCE_IDENTITY = "low_confidence_identity"
    UNREADABLE_RESPONSE = "unreadable_response"
    MISSING_SOURCE_SEGMENTS = "missing_source_segments"
    LOW_CONFIDENCE_AI_GRADE = "low_confidence_ai_grade"
    ZERO_SCORE_SWEEP = "zero_score_sweep"
    UNGRADED = "ungraded"
    MISSING_SUBMISSION = "missing_submission"
    OPEN_REGRADE_REQUEST = "open_regrade_request"
    GRADED_BEFORE_RUBRIC_CHANGE = "graded_before_rubric_change"


@dataclass(frozen=True)
class ReviewFlag:
    student_id: str
    student_name: str
    source_pdf: str
    reason: ReviewReason
    severity: ReviewSeverity
    detail: str
    question_id: str = ""

    @property
    def severity_rank(self) -> int:
        return _SEVERITY_RANK[self.severity]


def _is_graded(question: StudentSubmissionQuestion) -> bool:
    return question.points_awarded is not None or bool(question.rubric_labels)


def _response_is_empty(text: str) -> bool:
    return text.strip().lower() in _EMPTY_RESPONSE_SENTINELS


def _ai_confidence(analysis: str) -> int | None:
    return ai_analysis_confidence(analysis)


def flag_submission(
    document: StudentSubmissionDocument,
    *,
    id_pattern: re.Pattern[str] = _DEFAULT_ID_PATTERN,
    ai_confidence_floor: int = DEFAULT_AI_CONFIDENCE_FLOOR,
    ocr_confidence_floor: float = DEFAULT_OCR_CONFIDENCE_FLOOR,
    identity_confidence_floor: float = DEFAULT_IDENTITY_CONFIDENCE_FLOOR,
    include_ungraded: bool = False,
) -> list[ReviewFlag]:
    student_id = document.student_id.strip()
    student_name = document.student_name.strip()
    source_pdf = document.source_pdf

    def _flag(
        reason: ReviewReason,
        severity: ReviewSeverity,
        detail: str,
        *,
        question_id: str = "",
    ) -> ReviewFlag:
        return ReviewFlag(
            student_id=student_id,
            student_name=student_name,
            source_pdf=source_pdf,
            reason=reason,
            severity=severity,
            detail=detail,
            question_id=question_id,
        )

    flags: list[ReviewFlag] = []

    if not student_id:
        flags.append(
            _flag(
                ReviewReason.MISSING_STUDENT_ID,
                "high",
                "No student id extracted; Canvas export would be unmatched.",
            )
        )
    elif id_pattern.match(student_id) is None:
        flags.append(
            _flag(
                ReviewReason.MALFORMED_STUDENT_ID,
                "medium",
                f"Student id {student_id!r} looks like an OCR misread.",
            )
        )
    if not student_name:
        flags.append(
            _flag(
                ReviewReason.MISSING_STUDENT_NAME,
                "medium",
                "No student name extracted.",
            )
        )

    unsure: list[str] = []
    if (
        student_id
        and id_pattern.match(student_id) is not None
        and document.id_confidence is not None
        and document.id_confidence < identity_confidence_floor
    ):
        unsure.append(f"id {document.id_confidence:.0f}%")
    if (
        student_name
        and document.name_confidence is not None
        and document.name_confidence < identity_confidence_floor
    ):
        unsure.append(f"name {document.name_confidence:.0f}%")
    if unsure:
        flags.append(
            _flag(
                ReviewReason.LOW_CONFIDENCE_IDENTITY,
                "medium",
                f"Identity read with low confidence ({', '.join(unsure)}); "
                "confirm it against the scan before grades are returned.",
            )
        )

    graded_scores: list[float] = []
    for question in document.questions:
        response_empty = _response_is_empty(question.response_text)
        low_confidence = (
            question.response_confidence is not None
            and question.response_confidence < ocr_confidence_floor
        )
        if response_empty and low_confidence:
            flags.append(
                _flag(
                    ReviewReason.UNREADABLE_RESPONSE,
                    "high",
                    f"Response could not be resolved (confidence "
                    f"{question.response_confidence:.0f}%); check the scan — "
                    "a bubble sheet with two marks reads this way.",
                    question_id=question.question_id,
                )
            )
        elif response_empty and question.question_type == FREE_RESPONSE:
            flags.append(
                _flag(
                    ReviewReason.EMPTY_RESPONSE,
                    "low",
                    "Free-response transcription is empty; verify the scan.",
                    question_id=question.question_id,
                )
            )
        elif low_confidence:
            flags.append(
                _flag(
                    ReviewReason.LOW_CONFIDENCE_TRANSCRIPTION,
                    "medium",
                    f"OCR transcription confidence {question.response_confidence:.0f}%; "
                    "verify the text against the scan.",
                    question_id=question.question_id,
                )
            )
        if not question.source_segments:
            flags.append(
                _flag(
                    ReviewReason.MISSING_SOURCE_SEGMENTS,
                    "low",
                    "No source region; the PDF comment cannot be anchored.",
                    question_id=question.question_id,
                )
            )
        confidence = _ai_confidence(question.analysis)
        if (
            confidence is not None
            and confidence < ai_confidence_floor
            and not ai_analysis_is_reviewed(question.analysis)
        ):
            flags.append(
                _flag(
                    ReviewReason.LOW_CONFIDENCE_AI_GRADE,
                    "high",
                    f"AI-suggested grade at {confidence}% confidence; confirm it.",
                    question_id=question.question_id,
                )
            )
        if include_ungraded and not _is_graded(question):
            flags.append(
                _flag(
                    ReviewReason.UNGRADED,
                    "low",
                    "Not yet graded.",
                    question_id=question.question_id,
                )
            )
        if _is_graded(question) and question.points_awarded is not None:
            graded_scores.append(question.points_awarded)

    if len(graded_scores) >= _ZERO_SWEEP_MIN_GRADED and all(
        score == 0 for score in graded_scores
    ):
        flags.append(
            _flag(
                ReviewReason.ZERO_SCORE_SWEEP,
                "medium",
                f"All {len(graded_scores)} graded questions scored 0; "
                "check the version/alignment.",
            )
        )

    return flags


def _normalize_id(value: str) -> str:
    return value.strip().casefold()


def _normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def flag_missing_submissions(
    roster: list[RosterStudent],
    documents: list[StudentSubmissionDocument],
) -> list[ReviewFlag]:
    submitted_ids = {
        _normalize_id(doc.student_id)
        for doc in documents
        if doc.student_id.strip()
    }
    submitted_names = {
        _normalize_name(doc.student_name)
        for doc in documents
        if doc.student_name.strip()
    }

    roster_name_counts: dict[str, int] = {}
    for student in roster:
        roster_name_counts[_normalize_name(student.name)] = (
            roster_name_counts.get(_normalize_name(student.name), 0) + 1
        )

    flags: list[ReviewFlag] = []
    for student in roster:
        norm_id = _normalize_id(student.student_id)
        norm_name = _normalize_name(student.name)
        if not norm_id and not norm_name:
            continue
        if norm_id and norm_id in submitted_ids:
            continue
        if norm_name and roster_name_counts[norm_name] == 1 and norm_name in submitted_names:
            continue
        flags.append(
            ReviewFlag(
                student_id=student.student_id.strip(),
                student_name=student.name.strip(),
                source_pdf="",
                reason=ReviewReason.MISSING_SUBMISSION,
                severity="medium",
                detail="On the roster but no matching submission was found.",
            )
        )
    return flags


def flag_regrade_requests(
    regrade_requests: list[RegradeRequest],
    documents: list[StudentSubmissionDocument],
) -> list[ReviewFlag]:
    """An empty question ID disputes the whole assignment."""
    doc_by_id = {
        _normalize_id(doc.student_id): doc
        for doc in documents
        if doc.student_id.strip()
    }

    flags: list[ReviewFlag] = []
    for request in regrade_requests:
        if not request.is_open:
            continue
        doc = doc_by_id.get(_normalize_id(request.student_id))
        student_name = doc.student_name.strip() if doc is not None else ""
        source_pdf = doc.source_pdf if doc is not None else ""
        reason = request.reason.strip()
        detail = (
            f"Student is disputing this grade: {reason}"
            if reason
            else "Student is disputing this grade."
        )
        flags.append(
            ReviewFlag(
                student_id=request.student_id.strip(),
                student_name=student_name,
                source_pdf=source_pdf,
                reason=ReviewReason.OPEN_REGRADE_REQUEST,
                severity="high",
                detail=detail,
                question_id=request.question_id.strip(),
            )
        )
    return flags


def flag_rubric_drift(
    stale_grades: "Sequence[StaleGrade]",
    documents: list[StudentSubmissionDocument],
) -> list[ReviewFlag]:
    doc_by_id = {
        _normalize_id(doc.student_id): doc
        for doc in documents
        if doc.student_id.strip()
    }

    flags: list[ReviewFlag] = []
    for stale in stale_grades:
        doc = doc_by_id.get(_normalize_id(stale.student_id))
        flags.append(
            ReviewFlag(
                student_id=stale.student_id.strip(),
                student_name=(
                    doc.student_name.strip() if doc is not None else stale.student_name.strip()
                ),
                source_pdf=doc.source_pdf if doc is not None else "",
                reason=ReviewReason.GRADED_BEFORE_RUBRIC_CHANGE,
                severity="medium",
                detail=(
                    f"Graded before the rubric changed ({stale.change.describe()}); "
                    "confirm the grade still stands under the current options."
                ),
                question_id=stale.question_id,
            )
        )
    return flags


def build_review_flags(
    documents: list[StudentSubmissionDocument],
    *,
    id_pattern: re.Pattern[str] = _DEFAULT_ID_PATTERN,
    ai_confidence_floor: int = DEFAULT_AI_CONFIDENCE_FLOOR,
    ocr_confidence_floor: float = DEFAULT_OCR_CONFIDENCE_FLOOR,
    identity_confidence_floor: float = DEFAULT_IDENTITY_CONFIDENCE_FLOOR,
    include_ungraded: bool = False,
    roster: list[RosterStudent] | None = None,
    regrade_requests: list[RegradeRequest] | None = None,
    stale_grades: "Sequence[StaleGrade] | None" = None,
) -> list[ReviewFlag]:
    flags: list[ReviewFlag] = []
    for document in documents:
        flags.extend(
            flag_submission(
                document,
                id_pattern=id_pattern,
                ai_confidence_floor=ai_confidence_floor,
                ocr_confidence_floor=ocr_confidence_floor,
                identity_confidence_floor=identity_confidence_floor,
                include_ungraded=include_ungraded,
            )
        )
    if roster is not None:
        flags.extend(flag_missing_submissions(roster, documents))
    if regrade_requests is not None:
        flags.extend(flag_regrade_requests(regrade_requests, documents))
    if stale_grades is not None:
        flags.extend(flag_rubric_drift(stale_grades, documents))
    flags.sort(
        key=lambda flag: (
            flag.severity_rank,
            flag.student_name.lower(),
            flag.student_id,
            flag.question_id,
        )
    )
    return flags
