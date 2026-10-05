"""Typed grading records shared by workspace operations and their clients."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from opengrader.core.qa_file.scoring import RubricOptionKind, RubricSelectionMode
from opengrader.core.question_types import QuestionType


class RubricOption(BaseModel):
    label: str
    points_delta: float
    kind: RubricOptionKind
    selection_mode: RubricSelectionMode
    default_comment: str | None


class Question(BaseModel):
    id: str
    prompt: str
    possible_points: float
    question_type: QuestionType
    expected_answers: list[str]
    rubric: list[RubricOption]


class Assignment(BaseModel):
    id: str
    title: str


class AssignmentSummary(Assignment):
    question_count: int | None
    possible_points: float | None
    version_count: int
    submission_count: int
    graded_submission_count: int
    response_count: int
    graded_response_count: int
    average_percent: float | None


class AssignmentInputs(BaseModel):
    id: str
    folder: str
    scan: str
    output: str
    submissions: str | None = None
    qa: str | None = None
    roster: str | None = None
    blank_template: str | None = None
    pages_per_submission: int | None = None
    input_state: str = ""


class PipelineOutput(BaseModel):
    qa_markdown: str
    submissions_dir: str


class PipelinePublication(BaseModel):
    assignment: Assignment
    submission_count: int


class Submission(BaseModel):
    student_name: str = ""
    student_id: str = ""
    id: int
    version: str | None
    question_scores: dict[str, float | None]


class Answer(BaseModel):
    workspace_id: str
    context_revision: str
    revision: str
    submission_id: int
    version: str | None
    question: Question
    response_text: str
    response_image_url: str | None
    response_image_width: int | None
    response_image_height: int | None
    score_mode: Literal["rubric", "manual"]
    points_awarded: float | None
    rubric_labels: tuple[str, ...]
    analysis: str


class FolderWrite(BaseModel):
    """What happened to the class folder file when a folder session saved a grade.

    The grade itself is committed either way; a failed file write only means
    the `.submission.md` still has the older value until the next sync.
    """

    status: Literal["written", "unchanged", "failed"]
    path: str | None = None
    message: str | None = None


class GradeSaveResult(BaseModel):
    answer: Answer
    # None outside a folder session: nothing is written to the class files.
    folder: FolderWrite | None = None


class FolderSessionInfo(BaseModel):
    assignment_id: str
    assignment_title: str
    folder: str


class FolderSyncResult(BaseModel):
    imported: int = 0
    updated: int = 0
    written: int = 0
    failed: int = 0
    problems: list[str] = []


CourseText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)
]
Term = Literal["winter", "spring", "summer", "fall"]


class CourseDetails(BaseModel):
    number: CourseText
    name: CourseText | None = None
    term: Term | None = None
    year: Annotated[int, Field(ge=2000, le=2100)] | None = None


class WorkspaceInfo(BaseModel):
    id: str
    course: CourseDetails | None = None
    actor: str
    event_cursor: int
    folder_session: FolderSessionInfo | None = None


class WorkspaceEvent(BaseModel):
    kind: Literal["grade", "assignment", "submission", "feedback"] = "grade"
    cursor: int
    assignment_id: str
    submission_id: int
    question_id: str
    revision: str


class FeedbackJob(BaseModel):
    assignment_id: str
    submission_id: int
    generation: int
    status: Literal["pending", "running", "ready", "failed"]
    error: str
    artifact: str | None
