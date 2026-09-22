"""Typed grading records shared by workspace operations and their clients."""

from pydantic import BaseModel
from typing import Literal

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
    assignment_id: str
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


class GradeSaveResult(BaseModel):
    answer: Answer


class WorkspaceInfo(BaseModel):
    id: str
    actor: str
    event_cursor: int


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
