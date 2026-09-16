from __future__ import annotations

from dataclasses import dataclass

from .student_file import StudentSubmissionQuestion


@dataclass(frozen=True)
class FieldLocation:
    page: int  # 1-based.
    bbox: tuple[float, float, float, float]  # Normalized [x, y, w, h].


@dataclass(frozen=True)
class AssignmentTemplate:
    assignment_title: str
    name_field: FieldLocation | None
    student_id_field: FieldLocation | None
    questions: tuple[StudentSubmissionQuestion, ...]
    total_pages: int
    # False means question-start anchors; True means complete answer regions.
    exact_regions: bool = False
    # The blank page pixels are required to separate printed and student ink.
    reference_pdf_path: str | None = None
