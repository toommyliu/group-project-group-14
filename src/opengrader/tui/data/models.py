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
