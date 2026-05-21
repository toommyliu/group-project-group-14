from .student_file import (
    AssignmentTitleMetadata,
    StudentSubmissionBuildResult,
    StudentSubmissionDocument,
    StudentSubmissionGradeUpdate,
    StudentSubmissionQuestion,
    build_student_submission_document,
    load_student_submission_markdown,
    parse_student_submission_markdown,
    parse_assignment_metadata_from_title,
    render_student_submission_markdown,
    update_student_submission_file,
    update_student_submission_markdown,
)
from .splitter import (
    SubmissionPageRange,
    plan_submission_ranges,
    split_scanned_submission_pdf,
)
from .reorder import reorder_pdf_pages, _load_page_number_layout
from .processor import (
    _BlankExamTemplate,
    build_student_submission_document_unified,
    _load_blank_exam_template,
    _load_roster, # Added for roster loading
    _RosterEntry,
)
from opengrader.core.source_location import QuestionSourceSegment

__all__ = [
    "SubmissionPageRange",
    "plan_submission_ranges",
    "split_scanned_submission_pdf",
    "QuestionSourceSegment",
    "reorder_pdf_pages",
    "_load_page_number_layout",
    "AssignmentTitleMetadata",
    "StudentSubmissionBuildResult",
    "StudentSubmissionDocument",
    "StudentSubmissionGradeUpdate",
    "StudentSubmissionQuestion",
    "build_student_submission_document",
    "load_student_submission_markdown",
    "_load_roster", # Added for roster loading
    "_BlankExamTemplate",
    "_RosterEntry",
    "build_student_submission_document_unified",
    "_load_blank_exam_template",
    "parse_student_submission_markdown",
    "parse_assignment_metadata_from_title",
    "render_student_submission_markdown",
    "update_student_submission_file",
    "update_student_submission_markdown",
]
