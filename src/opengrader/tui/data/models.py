from dataclasses import dataclass


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
