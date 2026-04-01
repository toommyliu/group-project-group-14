from dataclasses import dataclass


@dataclass
class GradingSelection:
    course_item_id: str | None = None
    course_id: int | None = None
    assessment_item_id: str | None = None
    assessment_id: int | None = None
    question_id: str | None = None
    student_item_id: str | None = None
    student_id: int | None = None
