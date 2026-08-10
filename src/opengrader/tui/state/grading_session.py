from dataclasses import dataclass, field

from opengrader.core.qa_file.scoring import (
    RubricOptionKind,
    RubricSelectionMode,
    score_adjustment_for_rubric_selection,
    ungraded_score_adjustment,
)
from opengrader.core.question_types import FREE_RESPONSE, QuestionType
from opengrader.core.submissions.page_context import PageContextEntry
from opengrader.core.submissions.transcription import SOURCE_HUMAN


@dataclass(frozen=True)
class RubricOption:
    """Runtime rubric option with selection semantics."""

    label: str
    points_delta: float
    kind: RubricOptionKind
    selection_mode: RubricSelectionMode
    # Feedback auto-inserted into the comment box when this option is selected,
    # sourced from the QA file's ``**Comment**`` bullet. ``None`` when unset.
    default_comment: str | None = None


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
    question_type: QuestionType = FREE_RESPONSE
    # Transcription confidence (0-100) for response_text, or None when
    # unverified (VLM-transcribed / pre-confidence extraction).
    response_confidence: float | None = None
    # Which reader produced response_text; None when never recorded.
    response_source: str | None = None
    # Whole-page context: the student's scanned PDF and the pages this question
    # can page through (its own source page(s) first, then the submission's
    # extra work pages). Empty when there is no PDF to render from.
    source_pdf_path: str = ""
    page_context: tuple[PageContextEntry, ...] = ()

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
        # Bonus deltas are additive extra credit: they never take part in
        # base-score clamping or in inferring the rubric's scoring mode.
        base_values = [
            option.points_delta
            for option in self.rubric_options
            if option.kind != "bonus"
        ]
        selected = self.selected_options()
        selected_base = [o.points_delta for o in selected if o.kind != "bonus"]
        selected_bonus = [o.points_delta for o in selected if o.kind == "bonus"]
        if not selected:
            return ungraded_score_adjustment(self.points, base_values)
        if not selected_base:
            # Only bonus picked: the base outcome is still ungraded.
            return ungraded_score_adjustment(self.points, base_values) + sum(
                selected_bonus
            )
        return score_adjustment_for_rubric_selection(
            self.points,
            selected_base,
            base_values,
            bonus_points_values=selected_bonus,
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
    source_pdf_name: str = ""
    # Human summary of the work slice this grader claimed ("q1-q4"), or ""
    # when the assignment was never divided. Display only.
    claim_slice: str = ""
    # Anonymity carried into the whole-page view: while this is set the scan's
    # Name/Student-ID header is painted out of every rendered page, so paging to
    # a student's sheet cannot reveal what `student_name` is already hiding. Set
    # from teacher mode by `GradeController.refresh_grade_screen`; unlike the
    # other fields here it survives `reset()`, because it belongs to the session
    # rather than to whichever student is open.
    redact_identity: bool = False

    def reset(
        self, *, student_id: int | None, student_name: str, assignment_name: str
    ) -> None:
        self.student_id = student_id
        self.student_name = student_name
        self.assignment_name = assignment_name
        self.current_question_index = 0
        self.questions = []
        self.source_pdf_name = ""

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
        """Apply selection semantics for one rubric row.

        Bonus rows toggle independently — extra credit stacks on top of the
        base outcome. Base rows never disturb bonus picks: Correct/Incorrect
        replace the other base rows (exclusive shortcuts), while
        partial-credit rows compose as a checklist.
        """
        question = self.current_question
        if question is None:
            return
        if option_index < 0 or option_index >= len(question.rubric_options):
            return
        options = question.rubric_options
        option = options[option_index]

        if option.kind == "bonus":
            if option_index in question.selected_option_indices:
                question.selected_option_indices.remove(option_index)
            else:
                question.selected_option_indices.add(option_index)
            return

        if option.selection_mode == "exclusive":
            kept_bonus = {
                index
                for index in question.selected_option_indices
                if options[index].kind == "bonus"
            }
            question.selected_option_indices = kept_bonus | {option_index}
            return

        exclusive_base_indices = {
            index
            for index, candidate in enumerate(options)
            if candidate.selection_mode == "exclusive" and candidate.kind != "bonus"
        }
        question.selected_option_indices.difference_update(exclusive_base_indices)
        if option_index in question.selected_option_indices:
            question.selected_option_indices.remove(option_index)
        else:
            question.selected_option_indices.add(option_index)

    def update_comment(self, comment: str) -> None:
        question = self.current_question
        if question is None:
            return
        question.comment = comment

    def update_response_text(
        self,
        response_text: str,
        *,
        confidence: float | None = 100.0,
        source: str | None = SOURCE_HUMAN,
    ) -> None:
        """Apply a grader's corrected transcription to the active question.

        A human-verified reading is recorded at full confidence by default so
        the confidence chip turns green and the low-confidence review flag
        clears; the in-memory update keeps the grade screen in step before the
        submission file is reloaded.
        """
        question = self.current_question
        if question is None:
            return
        question.response_text = response_text
        if confidence is not None:
            question.response_confidence = confidence
        # The grader is now the reader, and the screen has to say so before the
        # file is reloaded — otherwise a second edit in the same session would
        # snapshot the *machine* as the previous reader and undo to a lie.
        question.response_source = source
