from dataclasses import dataclass


@dataclass
class GradingSelection:
    course_item_id: str | None = None
    course_id: int | None = None
    assignment_item_id: str | None = None
    assignment_id: int | None = None
    assignment_version: str | None = None
    submission_markdown_path: str | None = None
    question_id: str | None = None
    student_item_id: str | None = None
    student_id: int | None = None
