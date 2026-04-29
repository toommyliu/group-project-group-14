from dataclasses import dataclass, field


@dataclass(frozen=True)
class CourseSummary:
    id: int
    code: str
    name: str
    term: str
    section: str


@dataclass(frozen=True)
class AssignmentSummary:
    id: int
    title: str
    assignment_type: str


@dataclass(frozen=True)
class AssignmentTemplateSummary:
    id: int
    course_id: int
    name: str
    template_pdf_relpath: str


@dataclass(frozen=True)
class StudentSummary:
    id: int
    full_name: str


@dataclass(frozen=True)
class StudentSubmissionSummary:
    name: str
    student_id: str
    course_code: str
    assignment_title: str
    assignment_version: str
    source_pdf: str
    score: str
    markdown_path: str


@dataclass(frozen=True)
class RubricOptionSeed:
    label: str
    points_delta: float


@dataclass(frozen=True)
class QuestionSeed:
    question_id: str
    prompt: str
    image_path: str
    rubric_options: list[RubricOptionSeed]
    answers: list[str] = field(default_factory=list)
    response_text: str = ""
