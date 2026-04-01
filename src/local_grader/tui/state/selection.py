from dataclasses import dataclass


@dataclass
class GradingSelection:
    course_id: int | None = None
    assessment_id: int | None = None
    question_id: str | None = None
    student_id: int | None = None
