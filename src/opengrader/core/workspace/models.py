"""Typed grading records shared by workspace operations and their clients."""

import re
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    StringConstraints,
    model_validator,
)
from pydantic_core import PydanticCustomError

from opengrader.core.qa_file.scoring import RubricOptionKind, RubricSelectionMode
from opengrader.core.submissions.student_file import IdentityCandidate
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
    name_image_url: str | None
    id_image_url: str | None
    # Set when the name was filled in from the roster and no grader has confirmed it.
    match_percent: float | None
    candidates: list[IdentityCandidate]
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


class ReplaceSubmissions(BaseModel):
    """Submissions whose files were read again (two pieces of one paper the
    splitter cut apart, now joined): drop them and publish what the folder
    holds now. Keys are `pipeline:<path in the submissions folder>`."""

    source_keys: list[str]


class ReplacedSubmissions(BaseModel):
    # The submission each key now names; None when its file is gone.
    submission_ids: dict[str, int | None]
    problems: list[str] = []


class RubricEditResult(BaseModel):
    """What saving a question's rubric — or answers — from the browser did.

    `regraded` counts the saved grades that followed a renamed or re-pointed
    item. `problems` names any of those grades whose `.submission.md` could
    not be written yet; the answer key file itself is always written before
    anything else changes, so a result means it holds the new rubric.
    """

    changed: bool
    regraded: int = 0
    problems: list[str] = []


class FolderRoster(BaseModel):
    """The class folder's roster file, as a folder session last read it.

    `editable` is false when the roster is several files (one per section):
    a student added in the browser would have no one file to go into.
    """

    files: list[str]
    format: Literal["tsv", "csv"]
    editable: bool
    has_email: bool
    students: int
    skipped: int = 0


class FolderSessionInfo(BaseModel):
    assignment_id: str
    assignment_title: str
    folder: str
    # None when the class folder has no roster; the People page is then hidden.
    roster: FolderRoster | None = None


class FolderSyncResult(BaseModel):
    imported: int = 0
    updated: int = 0
    written: int = 0
    failed: int = 0
    problems: list[str] = []
    # The roster file changed, appeared or went away since the last sync.
    roster_changed: bool = False


class RegradeResult(BaseModel):
    """What grading a class folder again with its answer key did.

    `updated` counts the grades that changed; `problems` names anything that
    could not be read, graded or brought back into the browser.
    """

    files: int = 0
    updated: int = 0
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

    def __str__(self) -> str:
        return " ".join(
            str(part)
            for part in (self.number, self.term and self.term.title(), self.year)
            if part
        )


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def _roster_text(label: str, limit: int) -> AfterValidator:
    def check(value: str) -> str:
        value = value.strip()
        if len(value) > limit:
            raise PydanticCustomError(
                "too_long", f"{label} can be at most {limit} characters."
            )
        if _CONTROL_CHARACTERS.search(value):
            raise PydanticCustomError(
                "control_character", f"{label} can't contain line breaks or tabs."
            )
        return value

    return AfterValidator(check)


def _required_sid(value: str) -> str:
    if not value:
        raise PydanticCustomError("missing_sid", "Enter an SID.")
    return value


def _email(value: object) -> object:
    if not isinstance(value, str):
        return value
    value = value.strip().lower()
    if not value:
        return None
    if len(value) > 254:
        raise PydanticCustomError("too_long", "Email can be at most 254 characters.")
    if not _EMAIL.fullmatch(value):
        raise PydanticCustomError(
            "email", "{email} isn't a valid email address.", {"email": value}
        )
    return value


class StudentDetails(BaseModel):
    sid: Annotated[str, _roster_text("SID", 64), AfterValidator(_required_sid)]
    first_name: Annotated[str, _roster_text("First name", 200)] = ""
    last_name: Annotated[str, _roster_text("Last name", 200)] = ""
    email: Annotated[str | None, BeforeValidator(_email)] = None

    @model_validator(mode="after")
    def _named(self) -> "StudentDetails":
        if not (self.first_name or self.last_name):
            raise PydanticCustomError("missing_name", "Enter a first or last name.")
        return self

    @property
    def name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part)

    def __str__(self) -> str:
        return f"{self.name} (SID {self.sid})"


class Student(StudentDetails):
    id: str
    revision: int


class StudentChange(Student):
    """A roster student after an edit, and what the edit did beyond the roster.

    Submissions already matched to the student (they carry the student's ID)
    follow a changed name or ID, so the roster and the graded work never
    name the same person two ways. `renamed` counts them. `problems` is for
    a folder session, where each one is also a file that has to be written.
    """

    renamed: int = 0
    problems: list[str] = []


class StudentEdit(StudentDetails):
    expected_revision: int


class RosterProblem(BaseModel):
    row: int | None = None
    message: str


class RosterUpdate(BaseModel):
    before: StudentDetails
    after: StudentDetails


class RosterImport(BaseModel):
    added: list[StudentDetails]
    updated: list[RosterUpdate]
    unchanged: int
    problems: list[RosterProblem]
    fingerprint: str


StaffRole = Literal["instructor", "grader"]


class WorkspaceInfo(BaseModel):
    id: str
    course: CourseDetails
    actor: str
    event_cursor: int
    folder_session: FolderSessionInfo | None = None


class WorkspaceEvent(BaseModel):
    kind: Literal["grade", "assignment", "submission", "feedback", "roster"] = "grade"
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


class AccountRole(BaseModel):
    """An account on this computer that can open the workspace, and its role.

    The account that runs the service owns the workspace and is always an
    instructor. Others join through it as graders unless made instructors.
    """

    login: str
    role: StaffRole
    # A grader allowed to change grades other people gave, without asking.
    may_change_others: bool = False


class SharingInfo(BaseModel):
    owner: str
    # The group whose members may join; None when the workspace is not shared.
    group: str | None
    accounts: list[AccountRole]


class SharingUpdate(BaseModel):
    group: str | None = None
    # Turn sharing off (`group` is then ignored).
    off: bool = False
    accounts: list[AccountRole] = []


class Viewer(BaseModel):
    """Who this request comes from, as the service sees it."""

    login: str
    role: StaffRole
    owner: str
    may_change_others: bool


class ChangeRequest(BaseModel):
    """A grader's change to a grade someone else gave, waiting for an instructor."""

    id: int
    assignment_id: str
    submission_id: int
    question_id: str
    requested_by: str
    graded_by: str
    created: str
    status: Literal["pending", "approved", "dismissed"]
    rubric_labels: list[str]
    points_awarded: float | None
    score_mode: Literal["rubric", "manual"]
    analysis: str
    # The grade as it stands now, to compare with.
    current_rubric_labels: list[str] = []
    current_points: float | None = None
    decided_by: str | None = None
