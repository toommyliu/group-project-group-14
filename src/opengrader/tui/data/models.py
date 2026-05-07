from dataclasses import dataclass, field

from opengrader.core.qa_file.scoring import RubricOptionKind, RubricSelectionMode


@dataclass(frozen=True)
class StudentSummary:
    id: int
    full_name: str
    student_id: str = ""


@dataclass(frozen=True)
class RubricOptionSeed:
    """A flattened rubric option prepared for grading UI and persistence."""

    label: str
    points_delta: float
    kind: RubricOptionKind
    selection_mode: RubricSelectionMode


@dataclass(frozen=True)
class RubricOptionUpdate:
    old_label: str
    old_points_delta: float
    new_label: str
    new_points_delta: float


@dataclass(frozen=True)
class RubricEditResult:
    propagated_count: int
    deleted_blocked_count: int = 0


@dataclass(frozen=True)
class QuestionSeed:
    """Question data prepared by providers for a grading session."""

    question_id: str
    image_path: str
    rubric_options: list[RubricOptionSeed]
    points: float = 0.0
    prompt: str = ""
    expected_answers: list[str] = field(default_factory=list)
    response_text: str = ""
    points_awarded: float | None = None
    selected_rubric_labels: tuple[str, ...] = ()
    analysis: str = ""
