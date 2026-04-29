from dataclasses import dataclass, field


@dataclass(frozen=True)
class StudentSummary:
    id: int
    full_name: str


@dataclass(frozen=True)
class RubricOptionSeed:
    label: str
    points_delta: float


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
    question_id: str
    image_path: str
    rubric_options: list[RubricOptionSeed]
    points: float = 0.0
    prompt: str = ""
    expected_answers: list[str] = field(default_factory=list)
    response_text: str = ""
    points_awarded: float | None = None
    rubric_label: str = ""
    analysis: str = ""
