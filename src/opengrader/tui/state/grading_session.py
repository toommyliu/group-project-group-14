from dataclasses import dataclass, field

from opengrader.core.qa_file.scoring import (
    RubricOptionKind,
    RubricSelectionMode,
    score_adjustment_for_rubric_selection,
    ungraded_score_adjustment,
)


@dataclass(frozen=True)
class RubricOption:
    """Runtime rubric option with selection semantics."""

    label: str
    points_delta: float
    kind: RubricOptionKind
    selection_mode: RubricSelectionMode


@dataclass
class QuestionGrade:
    """Mutable grading state for the active student's question."""

    question_id: str
    image_path: str
    rubric_options: list[RubricOption]
    points: float = 0.0
    prompt: str = ""
    expected_answers: list[str] = field(default_factory=list)
    response_text: str = ""
    points_awarded: float | None = None
    selected_option_indices: set[int] = field(default_factory=set)
    comment: str = ""

    @property
    def points_delta(self) -> float:
        return self.score_adjustment

    def selected_options(self) -> list[RubricOption]:
        """Return selected rubric options in display order."""
        return [
            option
            for index, option in enumerate(self.rubric_options)
            if index in self.selected_option_indices
        ]

    def selected_rubric_labels(self) -> tuple[str, ...]:
        """Return persisted labels for the selected rubric options."""
        return tuple(option.label for option in self.selected_options())

    @property
    def score_adjustment(self) -> float:
        rubric_values = [option.points_delta for option in self.rubric_options]
        if not self.selected_option_indices:
            return ungraded_score_adjustment(self.points, rubric_values)
        return score_adjustment_for_rubric_selection(
            self.points,
            [option.points_delta for option in self.selected_options()],
            rubric_values,
        )

    @property
    def status(self) -> str:
        if not self.selected_option_indices:
            return "ungraded"
        return "graded"


@dataclass
class StudentGradingRun:
    student_id: int | None
    student_name: str
    assignment_name: str
    questions: list[QuestionGrade] = field(default_factory=list)
    current_question_index: int = 0
    max_points: float = 100.0

    def reset(
        self, *, student_id: int | None, student_name: str, assignment_name: str
    ) -> None:
        self.student_id = student_id
        self.student_name = student_name
        self.assignment_name = assignment_name
        self.current_question_index = 0
        self.questions = []

    @property
    def current_question(self) -> QuestionGrade | None:
        if not self.questions:
            return None
        if self.current_question_index < 0:
            self.current_question_index = 0
        if self.current_question_index >= len(self.questions):
            self.current_question_index = len(self.questions) - 1
        return self.questions[self.current_question_index]

    @property
    def graded_count(self) -> int:
        return sum(1 for question in self.questions if question.selected_option_indices)

    @property
    def total_questions(self) -> int:
        return len(self.questions)

    @property
    def total_score(self) -> float:
        return self.max_points + sum(
            question.score_adjustment for question in self.questions
        )

    def select_question(self, question_index: int) -> None:
        if not self.questions:
            self.current_question_index = 0
            return
        if question_index < 0:
            self.current_question_index = 0
            return
        if question_index >= len(self.questions):
            self.current_question_index = len(self.questions) - 1
            return
        self.current_question_index = question_index

    def next_question(self) -> None:
        self.select_question(self.current_question_index + 1)

    def previous_question(self) -> None:
        self.select_question(self.current_question_index - 1)

    def apply_option(self, option_index: int) -> None:
        """Apply exclusive or checklist selection semantics for one rubric row."""
        question = self.current_question
        if question is None:
            return
        if option_index < 0 or option_index >= len(question.rubric_options):
            return
        option = question.rubric_options[option_index]
        # Exclusive rows are shortcuts for full-credit or zero-credit outcomes; checklist rows compose.
        if option.selection_mode == "exclusive":
            question.selected_option_indices = {option_index}
            return

        exclusive_indices = {
            index
            for index, candidate in enumerate(question.rubric_options)
            if candidate.selection_mode == "exclusive"
        }
        question.selected_option_indices.difference_update(exclusive_indices)
        if option_index in question.selected_option_indices:
            question.selected_option_indices.remove(option_index)
        else:
            question.selected_option_indices.add(option_index)

    def update_comment(self, comment: str) -> None:
        question = self.current_question
        if question is None:
            return
        question.comment = comment
