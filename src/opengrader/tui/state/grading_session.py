from dataclasses import dataclass, field


@dataclass(frozen=True)
class RubricOption:
    label: str
    points_delta: float


@dataclass
class QuestionGrade:
    question_id: str
    image_path: str
    rubric_options: list[RubricOption]
    points: float = 0.0
    prompt: str = ""
    expected_answers: list[str] = field(default_factory=list)
    response_text: str = ""
    points_awarded: float | None = None
    rubric_label: str = ""
    selected_option_index: int | None = None
    comment: str = ""

    @property
    def points_delta(self) -> float:
        if self.selected_option_index is None:
            return 0.0
        return self.rubric_options[self.selected_option_index].points_delta

    @property
    def status(self) -> str:
        if self.selected_option_index is None:
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
        return sum(
            1
            for question in self.questions
            if question.selected_option_index is not None
        )

    @property
    def total_questions(self) -> int:
        return len(self.questions)

    @property
    def total_score(self) -> float:
        return self.max_points + sum(
            question.points_delta for question in self.questions
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
        question = self.current_question
        if question is None:
            return
        if option_index < 0 or option_index >= len(question.rubric_options):
            return
        question.selected_option_index = option_index

    def update_comment(self, comment: str) -> None:
        question = self.current_question
        if question is None:
            return
        question.comment = comment
