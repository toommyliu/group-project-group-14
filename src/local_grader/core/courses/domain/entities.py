from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal

from .errors import InvalidAssessmentTypeError, InvalidPointsError

AssessmentType = Literal["assignment", "quiz", "exam", "other"]
ALLOWED_ASSESSMENT_TYPES: tuple[str, ...] = (
    "assignment",
    "quiz",
    "exam",
    "other",
)

_POINTS_QUANTUM = Decimal("0.01")


def normalize_points(value: Decimal | float | int | str) -> Decimal:
    try:
        points = Decimal(str(value))
    except Exception as exc:
        raise InvalidPointsError(f"invalid points value: {value!r}") from exc

    if points < Decimal("0"):
        raise InvalidPointsError(f"points must be >= 0, got {value!r}")
    return points.quantize(_POINTS_QUANTUM, rounding=ROUND_HALF_UP)


def normalize_assessment_type(value: str) -> AssessmentType:
    candidate = value.strip().lower()
    if candidate not in ALLOWED_ASSESSMENT_TYPES:
        raise InvalidAssessmentTypeError(
            f"assessment type must be one of {ALLOWED_ASSESSMENT_TYPES}, got {value!r}"
        )
    return candidate  # type: ignore[return-value]


@dataclass(frozen=True)
class Course:
    # TODO: look into decomposing further
    id: int  # database PK or the class number
    code: str
    name: str
    term: str
    section: str
    created_at: datetime


@dataclass(frozen=True)
class Student:
    id: int
    sid: str
    first_name: str
    last_name: str
    middle_name: str | None
    email: str | None
    created_at: datetime

    @property
    def full_name(self) -> str:
        parts = [self.first_name]
        if self.middle_name:
            parts.append(self.middle_name)
        parts.append(self.last_name)
        return " ".join(parts)


@dataclass(frozen=True)
class Enrollment:
    course_id: int
    student_id: int
    created_at: datetime


@dataclass(frozen=True)
class Assessment:
    id: int
    course_id: int
    type: AssessmentType
    title: str
    max_points: Decimal
    due_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class Grade:
    assessment_id: int
    student_id: int
    points_awarded: Decimal
    updated_at: datetime
    version: int # optimistic offline concurrency control


@dataclass(frozen=True)
class StudentInput:
    sid: str
    first_name: str
    last_name: str
    middle_name: str | None = None
    email: str | None = None


@dataclass(frozen=True)
class GradeInput:
    assessment_id: int
    student_id: int
    points_awarded: Decimal
    expected_version: int | None = None


@dataclass(frozen=True)
class StudentGradeSummary:
    student: Student
    grades_by_assessment_id: dict[int, Grade]
    total_awarded: Decimal
    total_possible: Decimal
    percentage: Decimal | None


@dataclass(frozen=True)
class CourseGradebook:
    course: Course
    assessments: list[Assessment]
    rows: list[StudentGradeSummary]
