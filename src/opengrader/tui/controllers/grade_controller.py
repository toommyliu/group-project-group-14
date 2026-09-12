from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from opengrader.core.claims import claim_for
from opengrader.core.grading import (
    confirm_ai_analysis,
    override_ai_analysis,
    parse_ai_analysis,
)
from opengrader.core.grading.scoring import score_rubric_selection
from opengrader.core.history import current_actor
from opengrader.core.policy import read_policy
from opengrader.core.submissions import (
    AnswerGroup,
    GroupableResponse,
    SuggestedGroup,
    group_responses,
    suggest_group_merges,
)
from opengrader.core.traversal import shuffled_order, traversal_seed
from opengrader.tui.data import Provider, QuestionSeed, RubricOptionSeed
from opengrader.tui.formatting import format_question_label
from opengrader.tui.state import (
    PAGE_GRADE,
    PAGE_QA_PICKER,
    GradeRevert,
    GradingLogEntry,
    QuestionGrade,
    RubricOption,
    TuiWorkflowState,
)
from opengrader.tui.statistics import (
    QuestionStatistics,
    compute_question_statistics,
    seed_is_graded,
    selected_option_indices_for_seed,
)
from opengrader.tui.ui.screens import GradeScreen, QAPickerScreen
from opengrader.tui.ui.widgets.nav_item import NavItem


_CURRENT_STUDENT_ITEM = object()


@dataclass
class _PendingCommentEdit:
    """baseline precedes the entire coalesced edit, not the latest keystroke."""

    student_item_id: str
    question_id: str
    baseline: str
    text: str


class GradeApp(Protocol):
    def get_grade_screen(self) -> GradeScreen: ...

    def get_qa_picker_screen(self) -> QAPickerScreen: ...

    def switch_to(self, page_id: str) -> None: ...


class GradeController:
    def __init__(
        self,
        app: GradeApp,
        state: TuiWorkflowState,
        data_provider: Provider,
    ) -> None:
        self._app = app
        self._state = state
        self._data_provider = data_provider
        self._refresh_students: Callable[[], None] | None = None
        self._qa_picker_return_list_id = "qa-list"
        self._qa_picker_return_question_item_id: str | None = None
        self._qa_picker_return_student_item_id: str | None = None
        self._answer_group_cache: dict[str, list[AnswerGroup]] = {}
        self._suggestion_cache: dict[str, list[SuggestedGroup]] = {}
        self._unconfirmed_ai_cache: dict[str, list[int]] = {}
        self._pending_comment: _PendingCommentEdit | None = None
        self._accepted_merges: dict[str, set[int]] = {}
        self._traversal_shuffled: bool = False
        self._pending_new_submissions: int = 0
        self._extraction_active: bool = False

    def attach_student_refresh(self, callback: Callable[[], None]) -> None:
        self._refresh_students = callback


    def note_extraction_progress(self, *, added: int, appears_active: bool) -> None:
        self._pending_new_submissions += added
        self._extraction_active = appears_active

    def _extraction_hint(self) -> str:
        if self._pending_new_submissions:
            noun = "submission" if self._pending_new_submissions == 1 else "submissions"
            return f"{self._pending_new_submissions} more {noun} finished extracting"
        if self._extraction_active:
            return "extraction still running"
        return ""

    def _extraction_hint_sentence(self) -> str:
        hint = self._extraction_hint()
        if not hint:
            return ""
        return hint[:1].upper() + hint[1:] + "."

    def _append_extraction_hint(self, status: str) -> str:
        hint = self._extraction_hint()
        if not hint:
            return status
        if not status:
            return self._extraction_hint_sentence()
        return f"{status} · {hint}"

    def _fold_in_pending_submissions(self, *, restore_grade_position: bool = True) -> None:
        if self._pending_new_submissions == 0:
            return
        self._pending_new_submissions = 0

        current_question_id = self._state.selection.question_id
        current_student_item_id = self._state.selection.student_item_id
        should_restore = (
            restore_grade_position and self._state.current_page == PAGE_GRADE
        )

        self._answer_group_cache.clear()
        self._suggestion_cache.clear()
        self._unconfirmed_ai_cache.clear()
        self._accepted_merges.clear()

        self._state.student_items = self._student_items_for_current_course()
        self._state.grading_run.questions = self._question_grades(
            self._build_question_seeds_for_selection(student_item_id=None)
        )
        self._state.grading_run.max_points = self._max_points(
            self._state.grading_run.questions
        )
        self._hydrate_saved_grading_status()
        self._state.question_items = self._question_items(
            self._state.grading_run.questions
        )

        if should_restore and current_question_id:
            question_index = self._question_index(current_question_id)
            if question_index is not None:
                student_index = (
                    self._student_index(current_student_item_id)
                    if current_student_item_id
                    else None
                )
                self.open_question_by_index(question_index, student_index=student_index)

    def open_question_picker_for_assignment(self) -> None:
        self._answer_group_cache.clear()
        self._suggestion_cache.clear()
        self._unconfirmed_ai_cache.clear()
        self._accepted_merges.clear()
        students = self._student_items_for_current_course()
        self._state.student_items = students
        if not students:
            self._app.switch_to(PAGE_QA_PICKER)
            self._app.get_qa_picker_screen().set_status(
                "No students found for this grading queue."
            )
            return

        self._state.grading_run.questions = self._question_grades(
            self._build_question_seeds_for_selection(student_item_id=None)
        )
        self._state.grading_run.max_points = self._max_points(
            self._state.grading_run.questions
        )

        self._hydrate_saved_grading_status()
        self._state.question_items = self._question_items(
            self._state.grading_run.questions
        )
        if not self._state.question_items:
            self._app.switch_to(PAGE_QA_PICKER)
            self._app.get_qa_picker_screen().set_status(
                "No questions found for this assignment."
            )
            return

        status = "Select a QA to grade for all students."
        if self._traversal_shuffled:
            status += " · randomized order"
        self.refresh_qa_picker(status=status)
        self._app.switch_to(PAGE_QA_PICKER)
        self._app.get_qa_picker_screen().focus_questions()

    def on_qa_highlighted(self, item: NavItem) -> None:
        self._app.get_qa_picker_screen().show_question_details(item)

    def on_qa_selected(self, item: NavItem) -> None:
        index = self._question_index(item.item_id)
        if index is None:
            self._app.get_qa_picker_screen().set_status("Select a valid QA.")
            return
        self._remember_qa_picker_return_state()
        self.open_question_by_index(index)

    def open_question_by_index(
        self,
        index: int,
        *,
        student_index: int | None = None,
    ) -> None:
        if index < 0 or index >= len(self._state.grading_run.questions):
            return

        self.commit_pending_comment()

        self._state.active_question_index = index
        self._state.grading_run.select_question(index)
        current = self._state.grading_run.current_question
        if current is None:
            return

        self._state.selection.question_id = current.question_id
        self._state.selection_labels["question"] = format_question_label(
            current.question_id
        )

        if student_index is None:
            first_ungraded = self._find_ungraded_student_index(start=0, direction=1)
            student_index = first_ungraded if first_ungraded is not None else 0
        self.open_student_by_index(student_index)

    def on_grading_log_entry_selected(self, entry: GradingLogEntry) -> None:
        question_index = self._question_index(entry.question_id)
        if question_index is None:
            self._app.get_qa_picker_screen().set_status(
                "That question is not part of the current assignment."
            )
            return

        student_index = (
            self._student_index(entry.student_item_id)
            if entry.student_item_id
            else None
        )
        self._remember_qa_picker_return_state()
        self.open_question_by_index(question_index, student_index=student_index)

    def open_review_target(self, student_item_id: str, question_id: str) -> None:
        if not self._state.student_items:
            return
        student_index = self._student_index(student_item_id)
        question_index = self._question_index(question_id) if question_id else None
        if question_index is None or not (
            0 <= question_index < len(self._state.grading_run.questions)
        ):
            question_index = 0
        self._remember_qa_picker_return_state()
        self.open_question_by_index(question_index, student_index=student_index)

    def open_student_by_index(self, index: int) -> None:
        if index < 0 or index >= len(self._state.student_items):
            return
        self.commit_pending_comment()

        question_index = self._state.active_question_index
        if question_index < 0:
            question_index = self._state.grading_run.current_question_index

        self._state.active_student_index = index
        student_item = self._state.student_items[index]
        self._state.selection.student_item_id = student_item.item_id
        self._state.selection.student_id = self._numeric_id(student_item.item_id)
        self._state.selection_labels["student"] = self._student_display_label(
            student_item
        )

        self._state.grading_run.reset(
            student_id=self._state.selection.student_id,
            student_name=self._student_display_label(student_item),
            assignment_name=self._state.selection_labels.get("assignment")
            or "Assignment",
        )
        seeds = self._build_question_seeds_for_selection()
        self._state.grading_run.questions = self._question_grades(seeds)
        self._state.grading_run.source_pdf_name = (
            seeds[0].source_pdf_name if seeds else ""
        )
        self._state.grading_run.max_points = self._max_points(
            self._state.grading_run.questions
        )
        self._state.grading_run.select_question(question_index)
        self._state.active_question_index = (
            self._state.grading_run.current_question_index
        )

        self.refresh_grade_screen(status="")
        self._app.switch_to(PAGE_GRADE)

    def on_rubric_selected(self, option_index: int, finalize: bool = False) -> None:
        revert = self._grade_revert_before_change()
        self._state.grading_run.apply_option(option_index)
        status = self._persist_current_grade(revert=revert)
        self._auto_insert_rubric_comment(option_index)
        if finalize:
            self.finalize_student()
        else:
            self.refresh_grade_screen(status=status)

    def _auto_insert_rubric_comment(self, option_index: int) -> None:
        question = self._state.grading_run.current_question
        if question is None:
            return
        if not 0 <= option_index < len(question.rubric_options):
            return
        if option_index not in question.selected_option_indices:
            return
        comment_text = (question.rubric_options[option_index].default_comment or "").strip()
        if not comment_text:
            return
        existing = (question.comment or "").strip()
        if comment_text in existing:
            return
        merged = f"{existing} {comment_text}".strip() if existing else comment_text
        self.on_comment_updated(merged)
        self.commit_pending_comment()

    def on_comment_updated(self, comment: str) -> None:
        current_question = self._state.grading_run.current_question
        previous_comment = (
            current_question.comment if current_question is not None else ""
        ) or ""
        if comment == previous_comment:
            # Input.Changed also fires when navigation sets the saved value.
            return

        current_student = self._current_student_item()
        if self._pending_comment is not None and not self._pending_matches(
            current_student, current_question
        ):
            self.commit_pending_comment()

        self._state.grading_run.update_comment(comment)
        error = self._persist_current_analysis(comment)
        if error:
            self.refresh_grade_screen(status=error)
            return
        if current_student is None or current_question is None:
            return
        if self._pending_comment is None:
            self._pending_comment = _PendingCommentEdit(
                student_item_id=current_student.item_id,
                question_id=current_question.question_id,
                baseline=previous_comment,
                text=comment,
            )
        else:
            self._pending_comment.text = comment

    def _pending_matches(
        self,
        student: "NavItem | None",
        question: "QuestionGrade | None",
    ) -> bool:
        pending = self._pending_comment
        if pending is None or student is None or question is None:
            return False
        return (
            pending.student_item_id == student.item_id
            and pending.question_id == question.question_id
        )

    def commit_pending_comment(self) -> str:
        pending = self._pending_comment
        if pending is None:
            return ""
        self._pending_comment = None

        error = ""
        commit = getattr(self._data_provider, "commit_question_analysis", None)
        if callable(commit):
            try:
                commit(
                    student_item_id=pending.student_item_id,
                    question_id=pending.question_id,
                    analysis=pending.text,
                    previous_analysis=pending.baseline,
                )
            except Exception as exc:
                error = f"Could not annotate PDF: {exc}"
        self._record_comment_log_entry(
            pending.text,
            previous_comment=pending.baseline,
            student_item_id=pending.student_item_id,
            question_id=pending.question_id,
        )
        if error:
            self._set_active_status(error)
        return error

    def on_transcription_updated(self, response_text: str) -> None:
        current_question = self._state.grading_run.current_question
        current_student = self._current_student_item()
        if current_question is None or current_student is None:
            return
        previous_text = current_question.response_text
        if response_text == previous_text:
            return

        revert = GradeRevert(
            kind="transcription",
            student_item_id=current_student.item_id,
            question_id=current_question.question_id,
            response_text=previous_text,
            response_confidence=current_question.response_confidence,
            response_source=current_question.response_source,
        )
        self._state.grading_run.update_response_text(response_text)
        error = self._persist_current_transcription(response_text)
        if error:
            self.refresh_grade_screen(status=error)
            return
        self._record_transcription_log_entry(response_text, revert=revert)
        self.refresh_grade_screen(status="Saved corrected transcription.")

    def _persist_current_transcription(self, response_text: str) -> str:
        save_transcription = getattr(
            self._data_provider, "save_question_transcription", None
        )
        if not callable(save_transcription):
            return ""

        current_student = self._current_student_item()
        current_question = self._state.grading_run.current_question
        if current_student is None or current_question is None:
            return ""
        try:
            save_transcription(
                student_item_id=current_student.item_id,
                question_id=current_question.question_id,
                response_text=response_text,
            )
        except Exception as exc:
            return f"Could not save transcription: {exc}"
        return ""

    def _record_transcription_log_entry(
        self,
        response_text: str,
        *,
        revert: GradeRevert | None = None,
    ) -> None:
        if not callable(
            getattr(self._data_provider, "save_question_transcription", None)
        ):
            return
        current_student = self._current_student_item()
        current_question = self._state.grading_run.current_question
        if current_student is None or current_question is None:
            return
        excerpt = self._excerpt(response_text)
        self._record_log_entry(
            GradingLogEntry(
                kind="transcription",
                question_id=current_question.question_id,
                summary=f"Fixed: “{excerpt}”" if excerpt else "Cleared transcription",
                revert=revert,
                **self._student_log_fields(current_student),
            )
        )

    def open_rubric_editor(self) -> None:
        if not self._on_grade_page():
            return

        current_question = self._state.grading_run.current_question
        if current_question is None:
            self.refresh_grade_screen(status="No question selected.")
            return

        save_rubric = getattr(self._data_provider, "save_rubric_options", None)
        if not callable(save_rubric):
            self.refresh_grade_screen(
                status="Rubric editing is not supported by this data provider."
            )
            return

        original_options = [
            RubricOptionSeed(
                label=option.label,
                points_delta=option.points_delta,
                kind=option.kind,
                selection_mode=option.selection_mode,
                default_comment=option.default_comment,
            )
            for option in current_question.rubric_options
        ]

        present_editor = getattr(
            self._app.get_grade_screen(),
            "present_rubric_editor",
            None,
        )
        if not callable(present_editor):
            self.refresh_grade_screen(status="Rubric editor is not available.")
            return

        present_editor(
            self._app,
            current_question,
            lambda options: self.on_rubric_options_saved(
                options,
                original_options=original_options,
            ),
        )

    def on_rubric_options_saved(
        self,
        options: list[RubricOptionSeed] | None,
        *,
        original_options: list[RubricOptionSeed],
    ) -> None:
        if options is None:
            return

        save_rubric = getattr(self._data_provider, "save_rubric_options", None)
        if not callable(save_rubric):
            self.refresh_grade_screen(
                status="Rubric editing is not supported by this data provider."
            )
            return

        current_student = self._current_student_item()
        current_question = self._state.grading_run.current_question
        if current_student is None or current_question is None:
            self.refresh_grade_screen(status="No active grade to update.")
            return

        active_student_index = self._state.active_student_index
        try:
            result = save_rubric(
                student_item_id=current_student.item_id,
                question_id=current_question.question_id,
                options=options,
                original_options=original_options,
            )
        except Exception as exc:
            self.refresh_grade_screen(status=f"Could not save rubric: {exc}")
            return

        self._record_log_entry(
            GradingLogEntry(
                kind="rubric",
                question_id=current_question.question_id,
                summary=(
                    f"Rubric edited · {result.propagated_count} "
                    f"grade{'s' if result.propagated_count != 1 else ''} updated"
                ),
            )
        )

        self._hydrate_saved_grading_status()
        if 0 <= active_student_index < len(self._state.student_items):
            self.open_student_by_index(active_student_index)

        self.refresh_grade_screen(
            status=(
                "Rubric saved. Updated "
                f"{result.propagated_count} matching "
                f"grade{'s' if result.propagated_count != 1 else ''}."
            )
        )

    def return_to_home(self) -> None:
        self.return_to_qa_picker()

    def return_to_qa_picker(self) -> None:
        self.commit_pending_comment()
        self._fold_in_pending_submissions(restore_grade_position=False)
        self.refresh_qa_picker(status="")
        self._app.switch_to(PAGE_QA_PICKER)
        self._focus_qa_picker_after_grade()

    def qa_move_up(self) -> None:
        if self._state.current_page != PAGE_QA_PICKER:
            return
        self._app.get_qa_picker_screen().move_cursor(-1)

    def qa_move_down(self) -> None:
        if self._state.current_page != PAGE_QA_PICKER:
            return
        self._app.get_qa_picker_screen().move_cursor(1)

    def qa_jump_ungraded_question(self) -> None:
        if self._state.current_page != PAGE_QA_PICKER:
            return

        total_students = len(self._state.student_items)
        if total_students == 0:
            return

        for index, question in enumerate(self._state.grading_run.questions):
            graded_count = sum(
                1
                for student in self._state.student_items
                if self._state.qa_student_status.get(
                    (
                        str(self._state.selection.course_id or ""),
                        str(self._state.selection.assignment_id or ""),
                        question.question_id,
                        student.item_id,
                    )
                )
                == "graded"
            )
            if graded_count < total_students:
                qa_screen = self._app.get_qa_picker_screen()
                qa_screen.move_to_index(index)
                qa_screen.set_status(
                    self._append_extraction_hint(
                        f"Jumped to {format_question_label(question.question_id)} "
                        f"({graded_count}/{total_students} graded)"
                    )
                )
                return

        self._app.get_qa_picker_screen().set_status(
            self._append_extraction_hint(
                "All questions are fully graded for all students!"
            )
        )

    def qa_confirm(self) -> None:
        if self._state.current_page != PAGE_QA_PICKER:
            return
        item = self._app.get_qa_picker_screen().selected_question()
        if item is None:
            self._app.get_qa_picker_screen().set_status("Select a QA first.")
            return
        self.on_qa_selected(item)

    def next_student(self) -> None:
        if not self._on_grade_page():
            return
        if self._state.group_mode:
            self._move_to_group(direction=1, ungraded_only=False)
            return
        if self._state.ai_review_mode:
            self._move_to_unconfirmed_ai(direction=1)
            return
        next_index = self._next_available_student_index(
            start=self._state.active_student_index + 1, direction=1
        )
        if next_index is None:
            self.refresh_grade_screen(status=self._end_of_students_status(forward=True))
            return
        self.open_student_by_index(next_index)

    def next_ungraded_student(self) -> None:
        if not self._on_grade_page():
            return
        if self._state.group_mode:
            self._move_to_group(direction=1, ungraded_only=True)
            return
        if self._state.ai_review_mode:
            self._move_to_unconfirmed_ai(direction=1)
            return
        index = self._find_ungraded_student_index(
            start=self._state.active_student_index + 1,
            direction=1,
        )
        if index is None:
            self.refresh_grade_screen(
                status=self._append_extraction_hint(
                    "No later ungraded students for this QA."
                )
            )
            return
        self.open_student_by_index(index)

    def previous_ungraded_student(self) -> None:
        if not self._on_grade_page():
            return
        if self._state.group_mode:
            self._move_to_group(direction=-1, ungraded_only=True)
            return
        if self._state.ai_review_mode:
            self._move_to_unconfirmed_ai(direction=-1)
            return
        index = self._find_ungraded_student_index(
            start=self._state.active_student_index - 1,
            direction=-1,
        )
        if index is None:
            self.refresh_grade_screen(
                status=self._append_extraction_hint(
                    "No earlier ungraded students for this QA."
                )
            )
            return
        self.open_student_by_index(index)

    def previous_student(self) -> None:
        if not self._on_grade_page():
            return
        if self._state.group_mode:
            self._move_to_group(direction=-1, ungraded_only=False)
            return
        if self._state.ai_review_mode:
            self._move_to_unconfirmed_ai(direction=-1)
            return
        previous_index = self._next_available_student_index(
            start=self._state.active_student_index - 1, direction=-1
        )
        if previous_index is None:
            self.refresh_grade_screen(
                status=self._end_of_students_status(forward=False)
            )
            return
        self.open_student_by_index(previous_index)


    def toggle_group_mode(self) -> None:
        if not self._on_grade_page():
            return
        self._state.group_mode = not self._state.group_mode
        if self._state.group_mode:
            self._state.ai_review_mode = False
        if not self._state.group_mode:
            self.refresh_grade_screen(
                status="Grading student by student again."
            )
            return

        groups = self._grading_groups(self._current_question_id())
        if not groups:
            self.refresh_grade_screen(
                status="Grading by answer. No answers to group on this question yet."
            )
            return
        remaining = sum(
            1 for group in groups if not self._group_is_graded(group)
        )
        self._open_first_ungraded_group()
        self.refresh_grade_screen(
            status=(
                f"Grading by answer: {len(groups)} distinct answer(s), "
                f"{remaining} still to grade, most-given first. "
                "Move between answers as usual; press g to grade everyone "
                "who gave this one."
            )
        )


    def toggle_ai_review_mode(self) -> None:
        if not self._on_grade_page():
            return

        if self._state.ai_review_mode:
            self._state.ai_review_mode = False
            self.refresh_grade_screen(status="Grading student by student again.")
            return

        question_id = self._current_question_id()
        pending = self._unconfirmed_ai_indices(question_id)
        if not pending:
            self.refresh_grade_screen(
                status=(
                    "No unconfirmed AI grades on this question. "
                    "Nothing to confirm."
                )
            )
            return

        self._state.ai_review_mode = True
        self._state.group_mode = False
        current = self._state.active_student_index
        if current not in pending:
            self.open_student_by_index(pending[0])
        self.refresh_grade_screen(
            status=(
                f"Confirming AI grades: {len(pending)} on this question. "
                "Move between them as usual; press y to confirm this one."
            )
        )

    def _unconfirmed_ai_indices(self, question_id: str) -> list[int]:
        cached = self._unconfirmed_ai_cache.get(question_id)
        if cached is not None:
            return cached
        indices: list[int] = []
        for index, student in enumerate(self._state.student_items):
            if self._is_skipped_by_claim(question_id, student):
                continue
            if self._is_unconfirmed_ai_grade(question_id, student.item_id):
                indices.append(index)
        self._unconfirmed_ai_cache[question_id] = indices
        return indices

    def _is_unconfirmed_ai_grade(self, question_id: str, student_item_id: str) -> bool:
        seed = self._question_seed_for_student(question_id, student_item_id)
        if seed is None:
            return False
        tag = parse_ai_analysis(seed.analysis or "")
        return tag is not None and not tag.reviewed

    def _move_to_unconfirmed_ai(self, *, direction: int) -> None:
        question_id = self._current_question_id()
        index = self._state.active_student_index + direction
        while 0 <= index < len(self._state.student_items):
            student = self._state.student_items[index]
            if not self._is_skipped_by_claim(
                question_id, student
            ) and self._is_unconfirmed_ai_grade(question_id, student.item_id):
                self.open_student_by_index(index)
                return
            index += direction

        remaining = len(self._unconfirmed_ai_indices(question_id))
        if remaining:
            edge = "later" if direction > 0 else "earlier"
            self.refresh_grade_screen(
                status=(
                    f"No {edge} AI grades to confirm on this question "
                    f"({remaining} still waiting the other way)."
                )
            )
        else:
            self.refresh_grade_screen(
                status=(
                    "Every AI grade on this question is confirmed. "
                    "Move to another question, or press v to leave the mode."
                )
            )

    def confirm_ai_grade(self) -> None:
        if not self._on_grade_page():
            return
        current_question = self._state.grading_run.current_question
        current_student = self._current_student_item()
        if current_question is None or current_student is None:
            return

        analysis = current_question.comment or ""
        tag = parse_ai_analysis(analysis)
        if tag is None:
            self.refresh_grade_screen(
                status="This grade was not AI-suggested — nothing to confirm."
            )
            return
        if tag.overridden:
            self.refresh_grade_screen(
                status="You replaced this grade; it is already recorded as yours."
            )
            return
        if tag.confirmed:
            self.refresh_grade_screen(status="Already confirmed.")
            return

        confirmed = confirm_ai_analysis(analysis)
        self.on_comment_updated(confirmed)
        self.commit_pending_comment()
        self._unconfirmed_ai_cache.pop(current_question.question_id, None)

        question_label = format_question_label(current_question.question_id)
        student_label = self._student_display_label(current_student)
        remaining = self._unconfirmed_ai_indices(current_question.question_id)
        status = f"Confirmed the AI grade on {question_label} for {student_label}."
        if self._state.ai_review_mode and remaining:
            self._move_to_unconfirmed_ai(direction=1)
            self._set_active_status(f"{status} {len(remaining)} left.")
            return
        if remaining:
            status += f" {len(remaining)} more on this question (v to sweep them)."
        self.refresh_grade_screen(status=status)

    def _grading_groups(self, question_id: str) -> list[tuple[str, ...]]:
        if not question_id:
            return []
        groups: list[tuple[str, ...]] = []
        seen: set[str] = set()
        for exact in self._answer_groups_for_question(question_id):
            representative = next(
                (member for member in exact.member_ids if member not in seen), None
            )
            if representative is None:
                continue
            widened = self._answer_group_for_student(question_id, representative)
            members = tuple(
                member
                for member in (widened.member_ids if widened else exact.member_ids)
                if member not in seen
            )
            if not members:
                continue
            seen.update(members)
            groups.append(members)
        return groups

    def _group_is_graded(self, members: tuple[str, ...]) -> bool:
        question_id = self._qa_status_question_id()
        by_id = {student.item_id: student for student in self._state.student_items}
        for member_id in members:
            student = by_id.get(member_id)
            if student is None:
                continue
            if self._is_skipped_by_claim(question_id, student):
                continue
            if (
                self._state.qa_student_status.get(
                    self._qa_status_key(member_id), "ungraded"
                )
                != "graded"
            ):
                return False
        return True

    def _group_representative_index(self, members: tuple[str, ...]) -> int | None:
        question_id = self._qa_status_question_id()
        for index, student in enumerate(self._state.student_items):
            if student.item_id in members and not self._is_skipped_by_claim(
                question_id, student
            ):
                return index
        return None

    def _current_group_index(self, groups: list[tuple[str, ...]]) -> int | None:
        current = self._current_student_item()
        if current is None:
            return None
        for index, members in enumerate(groups):
            if current.item_id in members:
                return index
        return None

    def _move_to_group(self, *, direction: int, ungraded_only: bool) -> None:
        question_id = self._current_question_id()
        groups = self._grading_groups(question_id)
        if not groups:
            self.refresh_grade_screen(
                status="No answers to group on this question."
            )
            return

        start = self._current_group_index(groups)
        index = 0 if start is None else start + direction
        while 0 <= index < len(groups):
            members = groups[index]
            if not ungraded_only or not self._group_is_graded(members):
                student_index = self._group_representative_index(members)
                if student_index is not None:
                    self.open_student_by_index(student_index)
                    return
            index += direction

        forward = direction > 0
        where = "later" if forward else "earlier"
        what = "ungraded answers" if ungraded_only else "answers"
        self.refresh_grade_screen(status=f"No {where} {what} for this question.")

    def _current_question_id(self) -> str:
        current_question = self._state.grading_run.current_question
        return current_question.question_id if current_question is not None else ""

    def next_question(self) -> None:
        if not self._on_grade_page():
            return
        next_index = self._state.active_question_index + 1
        if next_index >= len(self._state.grading_run.questions):
            self.refresh_grade_screen(status="Already on last question.")
            return
        self._state.active_question_index = next_index
        self.open_student_by_index(self._state.active_student_index)
        self._land_on_first_ungraded_group()

    def previous_question(self) -> None:
        if not self._on_grade_page():
            return
        prev_index = self._state.active_question_index - 1
        if prev_index < 0:
            self.refresh_grade_screen(status="Already on first question.")
            return
        self._state.active_question_index = prev_index
        self.open_student_by_index(self._state.active_student_index)
        self._land_on_first_ungraded_group()

    def _land_on_first_ungraded_group(self) -> None:
        if not self._state.group_mode:
            return
        groups = self._grading_groups(self._current_question_id())
        current = self._current_group_index(groups)
        if current is not None and not self._group_is_graded(groups[current]):
            return
        self._open_first_ungraded_group()

    def _open_first_ungraded_group(self) -> bool:
        for members in self._grading_groups(self._current_question_id()):
            if self._group_is_graded(members):
                continue
            student_index = self._group_representative_index(members)
            if student_index is not None:
                self.open_student_by_index(student_index)
                return True
        return False

    def finalize_student(self) -> None:
        if not self._on_grade_page():
            return
        current = self._current_student_item()
        current_question = self._state.grading_run.current_question
        if current is None or current_question is None:
            return

        self._set_qa_student_status(
            current_question.question_id,
            current.item_id,
            graded=True,
        )
        self._state.student_status[self._status_key(current.item_id)] = "graded"
        if self._refresh_students is not None:
            self._refresh_students()

        self._fold_in_pending_submissions()

        next_index = self._find_ungraded_student_index(
            start=self._state.active_student_index + 1,
            direction=1,
        )
        status = "QA finalized. Loaded next ungraded student."

        if next_index is None:
            next_index = self._find_ungraded_student_index(start=0, direction=1)
            status = "QA finalized. Wrapped to find ungraded student."

        if next_index is not None:
            self.open_student_by_index(next_index)
            self.refresh_grade_screen(status=status)
            return

        self.refresh_qa_picker(
            status=self._append_extraction_hint("All students finalized for this QA.")
        )
        self._app.switch_to(PAGE_QA_PICKER)
        self._focus_qa_picker_after_grade()

    def apply_grade_to_answer_group(self) -> None:
        if not self._on_grade_page():
            return
        save_grade = getattr(self._data_provider, "save_question_grade", None)
        if not callable(save_grade):
            self.refresh_grade_screen(
                status="Group grading is not supported by this data provider."
            )
            return

        current_student = self._current_student_item()
        current_question = self._state.grading_run.current_question
        if current_student is None or current_question is None:
            return

        rubric_labels = current_question.selected_rubric_labels()
        points_awarded = self._points_for_selection(current_question)
        if not rubric_labels and points_awarded is None:
            self.refresh_grade_screen(
                status="Select a rubric option before grading the answer group."
            )
            return

        group = self._answer_group_for_student(
            current_question.question_id, current_student.item_id
        )
        other_members = (
            [mid for mid in group.member_ids if mid != current_student.item_id]
            if group is not None
            else []
        )
        if not other_members:
            if self._state.group_mode:
                self._advance_after_group_grade("Graded.")
                return
            self.refresh_grade_screen(
                status="No other students share this answer."
            )
            return

        applied = 0
        failed = 0
        skipped = 0
        for member_id in other_members:
            member_seed = self._question_seed_for_student(
                current_question.question_id, member_id
            )
            member_points = self._points_for_seed_labels(member_seed, rubric_labels)
            if member_seed is None or member_points is None:
                skipped += 1
                continue
            try:
                save_grade(
                    student_item_id=member_id,
                    question_id=current_question.question_id,
                    possible_points=member_seed.points,
                    points_awarded=member_points,
                    rubric_labels=rubric_labels,
                )
            except Exception:
                failed += 1
                continue
            self._set_qa_student_status(
                current_question.question_id,
                member_id,
                graded=bool(rubric_labels),
            )
            self._retag_member_ai_note(
                student_item_id=member_id,
                question_id=current_question.question_id,
                analysis=member_seed.analysis or "",
                changed=(
                    member_seed.points_awarded != member_points
                    or tuple(member_seed.selected_rubric_labels or ())
                    != tuple(rubric_labels)
                ),
            )
            applied += 1

        awarded = self._format_points(points_awarded or 0.0)
        possible = self._format_points(current_question.points or 0.0)
        self._record_log_entry(
            GradingLogEntry(
                kind="grade",
                question_id=current_question.question_id,
                summary=(
                    f"Group grade · {applied + 1} students · "
                    f"{awarded}/{possible} pts"
                ),
            )
        )
        if self._refresh_students is not None:
            self._refresh_students()

        status = (
            f"Applied this grade to {applied} other student(s) "
            "with the same answer."
        )
        extras = []
        if skipped:
            extras.append(f"{skipped} skipped (different rubric)")
        if failed:
            extras.append(f"{failed} could not be saved")
        if extras:
            status += " · " + ", ".join(extras) + "."

        if self._state.group_mode:
            self._advance_after_group_grade(status)
            return
        self.refresh_grade_screen(status=status)

    def _advance_after_group_grade(self, status: str) -> None:
        question_id = self._current_question_id()
        groups = self._grading_groups(question_id)
        remaining = [
            members for members in groups if not self._group_is_graded(members)
        ]
        if not remaining:
            self.refresh_grade_screen(
                status=f"{status} Every distinct answer on this question is graded."
            )
            return

        self._move_to_group(direction=1, ungraded_only=True)
        left = len(remaining)
        self._app.get_grade_screen().set_status(
            f"{status} {left} answer(s) left on this question."
        )

    def grade_open_external(self) -> None:
        if not self._on_grade_page():
            return
        self._app.get_grade_screen().open_response_externally()

    def grade_zoom_in(self) -> None:
        if not self._on_grade_page():
            return
        self._app.get_grade_screen().zoom_in()

    def grade_zoom_out(self) -> None:
        if not self._on_grade_page():
            return
        self._app.get_grade_screen().zoom_out()

    def grade_reset_zoom(self) -> None:
        if not self._on_grade_page():
            return
        self._app.get_grade_screen().reset_zoom()

    def grade_toggle_layout(self) -> None:
        if not self._on_grade_page():
            return
        self._app.get_grade_screen().toggle_layout()

    def first_student_for_question(self) -> None:
        if not self._on_grade_page():
            return
        first = self._next_available_student_index(start=0, direction=1)
        if first is None:
            self.refresh_grade_screen(status=self._end_of_students_status(forward=True))
            return
        self._state.active_student_index = first
        self.open_student_by_index(first)

    def _student_display_label(self, student_item: NavItem) -> str:
        if self._state.teacher_mode:
            return student_item.label
        return student_item.display_id

    def toggle_teacher_mode(self) -> None:
        self._state.teacher_mode = not self._state.teacher_mode
        status = (
            "Teacher mode on: showing student names."
            if self._state.teacher_mode
            else "Teacher mode off: anonymous grading by student ID."
        )

        current_student = self._current_student_item()
        if current_student is not None:
            label = self._student_display_label(current_student)
            self._state.selection_labels["student"] = label
            self._state.grading_run.student_name = label

        qa_screen = self._app.get_qa_picker_screen()
        apply_teacher_mode = getattr(qa_screen, "apply_teacher_mode", None)
        if callable(apply_teacher_mode):
            apply_teacher_mode()

        if self._on_grade_page():
            self.refresh_grade_screen(status=status)
            return

        student = qa_screen.selected_student()
        if student is not None and qa_screen.students_have_focus():
            qa_screen.show_student_details(student)
        qa_screen.set_status(status)

    def refresh_grade_screen(self, status: str) -> None:
        grade_screen = self._app.get_grade_screen()
        self._state.grading_run.redact_identity = not self._state.teacher_mode
        self._state.grading_run.claim_slice = self.my_claim_summary()
        status = status or self._foreign_slice_warning()
        grade_screen.bind_run(
            self._state.grading_run,
            breadcrumb_labels=self._state.breadcrumb_labels(),
        )
        set_progress = getattr(grade_screen, "set_progress", None)
        if callable(set_progress):
            set_progress(self._qa_student_progress_label())
        set_answer_group = getattr(grade_screen, "set_answer_group", None)
        if callable(set_answer_group):
            set_answer_group(self._answer_group_label())
        grade_screen.set_status(status)

    def refresh_qa_picker(self, status: str) -> None:
        qa_screen = self._app.get_qa_picker_screen()
        labels = [
            self._state.selection_labels.get("course") or "Course",
            self._state.selection_labels.get("assignment") or "Assignment",
        ]
        qa_screen.bind_assignment(
            self._question_items(self._state.grading_run.questions),
            students=self._student_progress_items(),
            breadcrumb_labels=labels,
            assignment_name=self._state.selection_labels.get("assignment")
            or "Assignment",
            student_question_statuses=self._student_question_statuses(),
            question_statistics=self._question_statistics_by_id(),
        )
        self._restore_qa_picker_question_selection()
        self._restore_qa_picker_student_selection()
        qa_screen.set_status(status or self._extraction_hint_sentence())

    def _restore_qa_picker_question_selection(self) -> None:
        question_item_id = self._qa_picker_return_question_item_id
        if question_item_id is not None:
            saved_index = self._question_index(question_item_id)
            if saved_index is not None:
                self._app.get_qa_picker_screen().move_to_index(saved_index)
                return

        active_index = self._state.active_question_index
        if 0 <= active_index < len(self._state.grading_run.questions):
            self._app.get_qa_picker_screen().move_to_index(active_index)
            return

        question_id = self._state.selection.question_id
        if question_id is None:
            return

        fallback_index = self._question_index(question_id)
        if fallback_index is not None:
            self._app.get_qa_picker_screen().move_to_index(fallback_index)

    def _restore_qa_picker_student_selection(self) -> None:
        student_item_id = self._qa_picker_return_student_item_id
        if student_item_id is not None:
            saved_index = self._student_index(student_item_id)
            if saved_index is not None:
                self._app.get_qa_picker_screen().move_student_to_index(saved_index)
                return

        selection_item_id = self._state.selection.student_item_id
        if selection_item_id is None:
            return

        fallback_index = self._student_index(selection_item_id)
        if fallback_index is not None:
            self._app.get_qa_picker_screen().move_student_to_index(fallback_index)

    def _focus_qa_picker_after_grade(self) -> None:
        qa_screen = self._app.get_qa_picker_screen()
        if self._qa_picker_return_list_id == "student-list":
            qa_screen.focus_students()
            return
        qa_screen.focus_questions()

    def _remember_qa_picker_return_state(self) -> None:
        qa_screen = self._app.get_qa_picker_screen()
        self._qa_picker_return_list_id = (
            "student-list" if qa_screen.students_have_focus() else "qa-list"
        )

        question = qa_screen.selected_question()
        self._qa_picker_return_question_item_id = (
            question.item_id if question is not None else None
        )

        student = qa_screen.selected_student()
        self._qa_picker_return_student_item_id = (
            student.item_id if student is not None else None
        )

    def _current_student_item(self) -> NavItem | None:
        if self._state.active_student_index < 0:
            return None
        if self._state.active_student_index >= len(self._state.student_items):
            return None
        return self._state.student_items[self._state.active_student_index]

    @staticmethod
    def _points_for_selection(question: QuestionGrade) -> float | None:
        return score_rubric_selection(
            question.points or 0.0,
            question.rubric_options,
            question.selected_options(),
        )

    def _persist_current_grade(self, *, revert: GradeRevert | None = None) -> str:
        save_grade = getattr(self._data_provider, "save_question_grade", None)
        if not callable(save_grade):
            return ""

        current_student = self._current_student_item()
        current_question = self._state.grading_run.current_question
        if current_student is None or current_question is None:
            return ""
        selected_options = current_question.selected_options()
        rubric_labels = current_question.selected_rubric_labels()
        points_awarded = self._points_for_selection(current_question)
        current_question.points_awarded = points_awarded

        try:
            save_grade(
                student_item_id=current_student.item_id,
                question_id=current_question.question_id,
                possible_points=current_question.points,
                points_awarded=points_awarded,
                rubric_labels=rubric_labels,
            )
        except Exception as exc:
            return f"Could not save grade: {exc}"
        self._record_grade_log_entry(
            current_question,
            current_student,
            points_awarded=points_awarded,
            rubric_labels=rubric_labels,
            revert=revert,
        )
        self._set_qa_student_status(
            current_question.question_id,
            current_student.item_id,
            graded=bool(selected_options),
        )
        if revert is not None:
            self._record_human_review_of_ai(
                changed=(
                    revert.points_awarded != points_awarded
                    or tuple(revert.rubric_labels or ()) != tuple(rubric_labels)
                )
            )
        return "Saved grade to submission file."

    def _retag_member_ai_note(
        self,
        *,
        student_item_id: str,
        question_id: str,
        analysis: str,
        changed: bool,
    ) -> None:
        if parse_ai_analysis(analysis) is None:
            return
        retagged = (
            override_ai_analysis(analysis)
            if changed
            else confirm_ai_analysis(analysis)
        )
        if retagged == analysis:
            return
        save_analysis = getattr(self._data_provider, "save_question_analysis", None)
        if not callable(save_analysis):
            return
        try:
            save_analysis(
                student_item_id=student_item_id,
                question_id=question_id,
                analysis=retagged,
            )
        except Exception:
            return
        self._unconfirmed_ai_cache.pop(question_id, None)

    def _record_human_review_of_ai(self, *, changed: bool) -> None:
        current_question = self._state.grading_run.current_question
        if current_question is None:
            return
        analysis = current_question.comment or ""
        if parse_ai_analysis(analysis) is None:
            return
        retagged = (
            override_ai_analysis(analysis)
            if changed
            else confirm_ai_analysis(analysis)
        )
        if retagged == analysis:
            return
        self.on_comment_updated(retagged)
        self.commit_pending_comment()
        self._unconfirmed_ai_cache.pop(current_question.question_id, None)

    def _persist_current_analysis(self, analysis: str) -> str:
        save_analysis = getattr(
            self._data_provider, "stage_question_analysis", None
        ) or getattr(self._data_provider, "save_question_analysis", None)
        if not callable(save_analysis):
            return ""

        current_student = self._current_student_item()
        current_question = self._state.grading_run.current_question
        if current_student is None or current_question is None:
            return ""

        try:
            save_analysis(
                student_item_id=current_student.item_id,
                question_id=current_question.question_id,
                analysis=analysis,
            )
        except Exception as exc:
            return f"Could not save comment: {exc}"
        return ""

    def _record_grade_log_entry(
        self,
        question: QuestionGrade,
        student: NavItem,
        *,
        points_awarded: float | None,
        rubric_labels: tuple[str, ...],
        revert: GradeRevert | None = None,
    ) -> None:
        if points_awarded is None and not rubric_labels:
            summary = "Grade cleared"
        else:
            awarded = self._format_points(points_awarded or 0.0)
            possible = self._format_points(question.points or 0.0)
            summary = f"{awarded}/{possible} pts"
            if rubric_labels:
                summary += f" · {self._excerpt(', '.join(rubric_labels), limit=48)}"
        self._record_log_entry(
            GradingLogEntry(
                kind="grade",
                question_id=question.question_id,
                summary=summary,
                revert=revert,
                **self._student_log_fields(student),
            )
        )

    def _record_comment_log_entry(
        self,
        comment: str,
        *,
        previous_comment: str = "",
        student_item_id: str = "",
        question_id: str = "",
    ) -> None:
        if not callable(
            getattr(self._data_provider, "stage_question_analysis", None)
            or getattr(self._data_provider, "save_question_analysis", None)
        ):
            return
        student = (
            self._student_item_by_id(student_item_id)
            if student_item_id
            else self._current_student_item()
        )
        current_question = self._state.grading_run.current_question
        resolved_question_id = question_id or (
            "" if current_question is None else current_question.question_id
        )
        if student is None or not resolved_question_id:
            return
        excerpt = self._excerpt(comment)
        revert = GradeRevert(
            kind="comment",
            student_item_id=student.item_id,
            question_id=resolved_question_id,
            analysis=previous_comment,
        )
        self._record_log_entry(
            GradingLogEntry(
                kind="comment",
                question_id=resolved_question_id,
                summary=f"“{excerpt}”" if excerpt else "Comment cleared",
                revert=revert,
                **self._student_log_fields(student),
            )
        )

    def _student_item_by_id(self, student_item_id: str) -> "NavItem | None":
        for student in self._state.student_items:
            if student.item_id == student_item_id:
                return student
        return None

    def _grade_revert_before_change(self) -> GradeRevert | None:
        student = self._current_student_item()
        question = self._state.grading_run.current_question
        if student is None or question is None:
            return None
        return GradeRevert(
            kind="grade",
            student_item_id=student.item_id,
            question_id=question.question_id,
            possible_points=question.points,
            points_awarded=question.points_awarded,
            rubric_labels=question.selected_rubric_labels(),
        )

    def undo_last_grade_change(self) -> None:
        log = self._state.grading_log
        entry = log.peek_last_undoable()
        if entry is None or entry.revert is None:
            self._set_active_status("Nothing to undo.")
            return

        error = self._apply_grade_revert(entry.revert)
        if error:
            self._set_active_status(error)
            return

        log.drop_entry(entry)
        self._hydrate_saved_grading_status()

        label = format_question_label(entry.revert.question_id)
        kind_word = {
            "grade": "grade",
            "transcription": "transcription",
        }.get(entry.revert.kind, "comment")
        self._after_undo_refresh(f"Undid {kind_word} · {label}.")

    def _apply_grade_revert(self, revert: GradeRevert) -> str:
        if revert.kind == "grade":
            save_grade = getattr(self._data_provider, "save_question_grade", None)
            if not callable(save_grade):
                return "Undo is not supported by this data provider."
            rubric_labels = revert.rubric_labels or ()
            try:
                save_grade(
                    student_item_id=revert.student_item_id,
                    question_id=revert.question_id,
                    possible_points=revert.possible_points,
                    points_awarded=revert.points_awarded,
                    rubric_labels=rubric_labels,
                )
            except Exception as exc:
                return f"Could not undo grade: {exc}"
            self._set_qa_student_status(
                revert.question_id,
                revert.student_item_id,
                graded=bool(rubric_labels),
            )
            return ""

        if revert.kind == "transcription":
            save_transcription = getattr(
                self._data_provider, "save_question_transcription", None
            )
            if not callable(save_transcription):
                return "Undo is not supported by this data provider."
            try:
                save_transcription(
                    student_item_id=revert.student_item_id,
                    question_id=revert.question_id,
                    response_text=revert.response_text or "",
                    response_confidence=revert.response_confidence,
                    response_source=revert.response_source,
                )
            except Exception as exc:
                return f"Could not undo transcription: {exc}"
            return ""

        save_analysis = getattr(self._data_provider, "save_question_analysis", None)
        if not callable(save_analysis):
            return "Undo is not supported by this data provider."
        analysis = revert.analysis or ""
        try:
            save_analysis(
                student_item_id=revert.student_item_id,
                question_id=revert.question_id,
                analysis=analysis,
            )
        except Exception as exc:
            return f"Could not undo comment: {exc}"
        return ""

    def _after_undo_refresh(self, status: str) -> None:
        if self._on_grade_page():
            active = self._state.active_student_index
            if 0 <= active < len(self._state.student_items):
                self.open_student_by_index(active)
            self.refresh_grade_screen(status=status)
        else:
            self.refresh_qa_picker(status=status)
        self._refresh_grading_log()

    def _set_active_status(self, status: str) -> None:
        if self._on_grade_page():
            self._app.get_grade_screen().set_status(status)
        else:
            self._app.get_qa_picker_screen().set_status(status)

    def _record_log_entry(self, entry: GradingLogEntry) -> None:
        self._state.grading_log.record(entry)
        self._refresh_grading_log()

    def _refresh_grading_log(self) -> None:
        refresh_log = getattr(
            self._app.get_qa_picker_screen(),
            "refresh_grading_log",
            None,
        )
        if callable(refresh_log):
            refresh_log()

    @staticmethod
    def _student_log_fields(student: NavItem) -> dict[str, str]:
        return {
            "student_item_id": student.item_id,
            "student_name": student.label,
            "student_display_id": student.display_id or student.item_id,
        }

    @staticmethod
    def _format_points(value: float) -> str:
        return f"{value:g}"

    @staticmethod
    def _excerpt(text: str, limit: int = 60) -> str:
        collapsed = " ".join(text.split())
        if len(collapsed) <= limit:
            return collapsed
        return collapsed[: limit - 1].rstrip() + "…"

    def _status_key(self, student_item_id: str) -> tuple[str, str, str]:
        return (
            str(self._state.selection.course_id or ""),
            str(self._state.selection.assignment_id or ""),
            student_item_id,
        )

    def _qa_status_question_id(self) -> str:
        return str(self._state.selection.question_id or "")

    def _qa_status_key(self, student_item_id: str) -> tuple[str, str, str, str]:
        return self._qa_status_key_for(
            str(self._state.selection.question_id or ""),
            student_item_id,
        )

    def _qa_status_key_for(
        self,
        question_id: str,
        student_item_id: str,
    ) -> tuple[str, str, str, str]:
        return (
            str(self._state.selection.course_id or ""),
            str(self._state.selection.assignment_id or ""),
            question_id,
            student_item_id,
        )

    def _set_qa_student_status(
        self,
        question_id: str,
        student_item_id: str,
        *,
        graded: bool,
    ) -> None:
        key = self._qa_status_key_for(question_id, student_item_id)
        if graded:
            self._state.qa_student_status[key] = "graded"
            return
        self._state.qa_student_status.pop(key, None)

    def _hydrate_saved_grading_status(self) -> None:
        course_id = str(self._state.selection.course_id or "")
        assignment_id = str(self._state.selection.assignment_id or "")
        self._state.qa_student_status = {
            key: status
            for key, status in self._state.qa_student_status.items()
            if key[:2] != (course_id, assignment_id)
        }

        for student in self._state.student_items:
            for seed in self._build_question_seeds_for_selection(
                student_item_id=student.item_id
            ):
                if self._seed_is_graded(seed):
                    self._set_qa_student_status(
                        seed.question_id,
                        student.item_id,
                        graded=True,
                    )

    def _student_items_for_current_course(self) -> list[NavItem]:
        items = [
            NavItem(
                item_id=f"s{student.id}",
                label=student.full_name,
                display_id=student.student_id.strip() or f"s{student.id}",
                meta="",
            )
            for student in self._data_provider.list_students(
                self._state.selection.course_id
            )
        ]

        self._traversal_shuffled = False
        submissions_path = getattr(self._data_provider, "submissions_path", None)
        if submissions_path is not None:
            try:
                policy = read_policy(submissions_path)
                if policy.traversal_shuffle:
                    grader_actor_method = getattr(
                        self._data_provider, "grader_actor", None
                    )
                    actor = (
                        grader_actor_method()
                        if callable(grader_actor_method)
                        else current_actor()
                    )
                    seed = traversal_seed(actor, str(submissions_path))
                    items = shuffled_order(items, seed)
                    self._traversal_shuffled = True
            except Exception:
                pass

        return items

    def _build_question_seeds_for_selection(
        self,
        *,
        student_item_id: str | None | object = _CURRENT_STUDENT_ITEM,
    ) -> list[QuestionSeed]:
        if student_item_id is _CURRENT_STUDENT_ITEM:
            student_item_id = self._state.selection.student_item_id
        provider_student_item_id = (
            student_item_id if isinstance(student_item_id, str) else None
        )
        return self._data_provider.build_question_seeds(
            assignment_id=self._state.selection.assignment_id,
            assignment_title=self._state.selection_labels.get("assignment") or None,
            student_item_id=provider_student_item_id,
        )

    def _answer_groups_for_question(self, question_id: str) -> list[AnswerGroup]:
        cached = self._answer_group_cache.get(question_id)
        if cached is not None:
            return cached

        responses: list[GroupableResponse] = []
        for student in self._state.student_items:
            for seed in self._build_question_seeds_for_selection(
                student_item_id=student.item_id
            ):
                if seed.question_id == question_id:
                    responses.append(
                        GroupableResponse(
                            member_id=student.item_id,
                            response_text=seed.response_text,
                            question_type=seed.question_type,
                        )
                    )
                    break

        groups = group_responses(responses)
        self._answer_group_cache[question_id] = groups
        return groups

    def _question_seed_for_student(
        self,
        question_id: str,
        student_item_id: str,
    ) -> QuestionSeed | None:
        for seed in self._build_question_seeds_for_selection(
            student_item_id=student_item_id
        ):
            if seed.question_id == question_id:
                return seed
        return None

    @staticmethod
    def _points_for_seed_labels(
        seed: QuestionSeed | None,
        rubric_labels: tuple[str, ...],
    ) -> float | None:
        if seed is None:
            return None
        options_by_label = {option.label: option for option in seed.rubric_options}
        if not all(label in options_by_label for label in rubric_labels):
            return None
        return score_rubric_selection(
            seed.points or 0.0,
            seed.rubric_options,
            [options_by_label[label] for label in rubric_labels],
        )

    def suggested_merges_for_question(self, question_id: str) -> list[SuggestedGroup]:
        cached = self._suggestion_cache.get(question_id)
        if cached is not None:
            return cached
        suggestions = suggest_group_merges(self._answer_groups_for_question(question_id))
        self._suggestion_cache[question_id] = suggestions
        return suggestions

    def accepted_merge_indices(self, question_id: str) -> frozenset[int]:
        return frozenset(self._accepted_merges.get(question_id, ()))

    def accept_suggested_merge(self, question_id: str, index: int) -> str:
        suggestions = self.suggested_merges_for_question(question_id)
        if not 0 <= index < len(suggestions):
            return "That merge is no longer available."
        self._accepted_merges.setdefault(question_id, set()).add(index)
        suggestion = suggestions[index]
        return (
            f"Merged {suggestion.group_count} similar answers "
            f"({suggestion.size} students). Grade one and press g to apply it "
            "to all of them."
        )

    def _accepted_merge_members(
        self, question_id: str, student_item_id: str
    ) -> tuple[str, ...] | None:
        indices = self._accepted_merges.get(question_id)
        if not indices:
            return None
        suggestions = self.suggested_merges_for_question(question_id)
        for index in sorted(indices):
            if index >= len(suggestions):
                continue
            members = suggestions[index].member_ids
            if student_item_id in members:
                return members
        return None

    def _answer_group_for_student(
        self,
        question_id: str,
        student_item_id: str,
    ) -> AnswerGroup | None:
        exact = next(
            (
                group
                for group in self._answer_groups_for_question(question_id)
                if student_item_id in group.member_ids
            ),
            None,
        )
        merged = self._accepted_merge_members(question_id, student_item_id)
        if merged is None or exact is None:
            return exact
        return AnswerGroup(
            key=exact.key,
            member_ids=merged,
            sample_response_text=exact.sample_response_text,
            question_type=exact.question_type,
        )

    def _answer_group_label(self) -> str:
        current_student = self._current_student_item()
        current_question = self._state.grading_run.current_question
        if current_student is None or current_question is None:
            return ""
        question_id = current_question.question_id
        if self._state.group_mode:
            return self._group_mode_label(question_id, current_student.item_id)
        if self._state.ai_review_mode:
            return self._ai_review_label(question_id, current_question.comment or "")
        group = self._answer_group_for_student(question_id, current_student.item_id)
        if group is None or group.size <= 1:
            return self._suggestion_hint(question_id)
        qualifier = " (blank)" if group.is_blank else ""

        merged = self._accepted_merge_members(question_id, current_student.item_id)
        if merged is not None:
            exact_size = next(
                (
                    exact.size
                    for exact in self._answer_groups_for_question(question_id)
                    if current_student.item_id in exact.member_ids
                ),
                group.size,
            )
            similar = group.size - exact_size
            if similar > 0:
                return (
                    f"Answer group: {group.size} students "
                    f"({exact_size} identical + {similar} merged as similar)"
                )
        label = f"Answer group: {group.size} students share this answer{qualifier}"
        hint = self._suggestion_hint(question_id)
        return f"{label} · {hint}" if hint else label

    def _group_mode_label(self, question_id: str, student_item_id: str) -> str:
        groups = self._grading_groups(question_id)
        if not groups:
            return "By answer · no answers to group yet"

        position = self._current_group_index(groups)
        remaining = sum(1 for members in groups if not self._group_is_graded(members))
        where = (
            f"answer {position + 1} of {len(groups)}"
            if position is not None
            else f"{len(groups)} distinct answer(s)"
        )

        members = groups[position] if position is not None else ()
        if len(members) > 1:
            covers = f"{len(members)} students share this answer"
        else:
            covers = "only this student gave it"

        label = f"By answer · {where} · {covers}"
        if remaining:
            label += f" · {remaining} left"
        else:
            label += " · all graded"
        hint = self._suggestion_hint(question_id)
        return f"{label} · {hint}" if hint else label

    def _ai_review_label(self, question_id: str, analysis: str) -> str:
        remaining = len(self._unconfirmed_ai_indices(question_id))
        tag = parse_ai_analysis(analysis)
        if tag is None:
            here = "this answer was graded by a human"
        elif tag.confirmed:
            here = "confirmed"
        else:
            confidence = f"{tag.confidence}%" if tag.confidence is not None else "?"
            model = f" · {tag.model}" if tag.model else ""
            here = f"AI {confidence}{model} · press y to confirm"
        left = f"{remaining} left" if remaining else "all confirmed"
        return f"Confirming AI grades · {here} · {left}"

    def _suggestion_hint(self, question_id: str) -> str:
        suggestions = self.suggested_merges_for_question(question_id)
        pending = len(suggestions) - len(self._accepted_merges.get(question_id, ()))
        if pending <= 0:
            return ""
        return f"U: {pending} similar-answer merge(s) available"


    def refresh_after_external_change(self) -> None:
        question_index = self._question_index_in_run()
        self.open_question_picker_for_assignment()
        if question_index is not None:
            self.open_question_by_index(question_index)

    def _question_index_in_run(self) -> int | None:
        if self._state.current_page != PAGE_GRADE:
            return None
        questions = self._state.grading_run.questions if self._state.grading_run else []
        current = self._state.selection.question_id
        for index, question in enumerate(questions):
            if question.question_id == current:
                return index
        return None

    def refresh_after_claim_change(self) -> None:
        self._unconfirmed_ai_cache.clear()
        if self._state.current_page == PAGE_GRADE:
            self.refresh_grade_screen(status=self._claim_filter_status())
        else:
            self.refresh_qa_picker(status=self._claim_filter_status())

    def _foreign_slice_warning(self) -> str:
        student = self._current_student_item()
        if student is None:
            return ""
        question_id = self._qa_status_question_id()
        owner = self._claim_owner(question_id, student)
        if owner is None:
            return ""
        return (
            f"{question_id} is {owner.actor}'s slice — your changes still save, "
            "but they may be grading it too."
        )

    def _claim_filter_status(self) -> str:
        if not self._state.active_claims:
            return "No work claims: every grader sees the whole assignment."
        if self._state.claim_filter_enabled:
            mine = self.my_claim_summary()
            scope = f"your slice ({mine})" if mine else "unclaimed work only"
            return f"Showing {scope}. Shift+D to see the whole class."
        return "Showing the whole class, including other graders' slices."

    def _end_of_students_status(self, *, forward: bool) -> str:
        edge = "last" if forward else "first"
        if self._state.claim_filter_enabled and self._state.active_claims:
            return (
                f"Already on the {edge} student in your slice "
                "(Shift+D to see the whole class)."
            )
        return f"Already on {edge} student."

    def _claim_owner(self, question_id: str, student_item: NavItem):
        if not self._state.active_claims:
            return None
        student_id = (student_item.display_id or "").strip()
        if not student_id:
            return None
        owner = claim_for(self._state.active_claims, question_id, student_id)
        if owner is None or owner.actor == self._state.grader_actor:
            return None
        return owner

    def _is_skipped_by_claim(self, question_id: str, student_item: NavItem) -> bool:
        if not self._state.claim_filter_enabled:
            return False
        return self._claim_owner(question_id, student_item) is not None

    def _next_available_student_index(self, *, start: int, direction: int) -> int | None:
        if direction == 0:
            return None
        question_id = self._qa_status_question_id()
        index = start
        while 0 <= index < len(self._state.student_items):
            if not self._is_skipped_by_claim(
                question_id, self._state.student_items[index]
            ):
                return index
            index += direction
        return None

    def claimed_question_owners(self, question_ids) -> dict[str, str]:
        if not self._state.active_claims or not self._state.student_items:
            return {}
        owners: dict[str, str] = {}
        for question_id in question_ids:
            actors = {
                getattr(self._claim_owner(question_id, student), "actor", "")
                for student in self._state.student_items
            }
            if len(actors) == 1:
                only = actors.pop()
                if only:
                    owners[question_id] = only
        return owners

    def my_claim_summary(self) -> str:
        mine = [
            claim
            for claim in self._state.active_claims
            if claim.actor == self._state.grader_actor
        ]
        if not mine:
            return ""
        return " · ".join(claim.work.describe() for claim in mine)

    def _find_ungraded_student_index(
        self,
        *,
        start: int,
        direction: int,
    ) -> int | None:
        if direction == 0:
            return None
        index = start
        question_id = self._qa_status_question_id()
        while 0 <= index < len(self._state.student_items):
            student = self._state.student_items[index]
            if (
                self._state.qa_student_status.get(
                    self._qa_status_key(student.item_id),
                    "ungraded",
                )
                != "graded"
                and not self._is_skipped_by_claim(question_id, student)
            ):
                return index
            index += direction
        return None

    def _qa_student_progress_label(self) -> str:
        total_students = len(self._state.student_items)
        graded_students = sum(
            1
            for student in self._state.student_items
            if self._state.qa_student_status.get(
                self._qa_status_key(student.item_id),
                "ungraded",
            )
            == "graded"
        )
        return self._append_extraction_hint(
            f"Progress: {graded_students} / {total_students} students"
        )

    def _question_statistics_by_id(self) -> dict[str, QuestionStatistics]:
        if not self._state.student_items or not self._state.grading_run.questions:
            return {}

        seeds_by_question_id: dict[str, list[QuestionSeed]] = {
            question.question_id: [] for question in self._state.grading_run.questions
        }
        for student in self._state.student_items:
            for seed in self._build_question_seeds_for_selection(
                student_item_id=student.item_id
            ):
                if seed.question_id in seeds_by_question_id:
                    seeds_by_question_id[seed.question_id].append(seed)

        total_submissions = len(self._state.student_items)
        return {
            question_id: compute_question_statistics(
                question_id,
                seeds,
                total_submissions=total_submissions,
            )
            for question_id, seeds in seeds_by_question_id.items()
        }

    def _question_index(self, item_id: str) -> int | None:
        for index, question in enumerate(self._state.grading_run.questions):
            if question.question_id == item_id:
                return index
        return None

    def _student_index(self, item_id: str) -> int | None:
        for index, student in enumerate(self._state.student_items):
            if student.item_id == item_id:
                return index
        return None

    def _on_grade_page(self) -> bool:
        return self._state.current_page == PAGE_GRADE

    @staticmethod
    def _question_grades(seeds: list[QuestionSeed]) -> list[QuestionGrade]:
        return [
            QuestionGrade(
                question_id=seed.question_id,
                image_path=seed.image_path,
                rubric_options=[
                    RubricOption(
                        label=(
                            f"Bonus - {option.label}"
                            if option.kind == "bonus" and not option.label.startswith("Bonus - ")
                            else f"Partially Correct - {option.label}"
                            if option.kind == "partial" and not (option.label.startswith("Partially Correct - ") or option.label == "Partially Correct")
                            else option.label
                        ),
                        points_delta=option.points_delta,
                        kind=option.kind,
                        selection_mode=option.selection_mode,
                        default_comment=option.default_comment,
                    )
                    for option in seed.rubric_options
                ],
                points=seed.points,
                prompt=seed.prompt,
                expected_answers=list(seed.expected_answers),
                response_text=seed.response_text,
                points_awarded=seed.points_awarded,
                selected_option_indices=GradeController._selected_option_indices(seed),
                comment=seed.analysis,
                question_type=seed.question_type,
                response_confidence=seed.response_confidence,
                response_source=seed.response_source,
                source_pdf_path=seed.source_pdf_path,
                page_context=seed.page_context,
            )
            for seed in seeds
        ]

    @staticmethod
    def _max_points(questions: list[QuestionGrade]) -> float:
        total = sum(question.points for question in questions)
        return total if total > 0 else 100.0

    @staticmethod
    def _selected_option_indices(seed: QuestionSeed) -> set[int]:
        return selected_option_indices_for_seed(seed)

    @staticmethod
    def _seed_is_graded(seed: QuestionSeed) -> bool:
        return seed_is_graded(seed)

    def _question_items(self, questions: list[QuestionGrade]) -> list[NavItem]:
        items: list[NavItem] = []
        total_students = len(self._state.student_items)
        owners = self.claimed_question_owners(
            question.question_id for question in questions
        )
        for question in questions:
            graded_count = sum(
                1
                for student in self._state.student_items
                if self._state.qa_student_status.get(
                    self._qa_status_key_for(question.question_id, student.item_id),
                    "ungraded",
                )
                == "graded"
            )
            owner = owners.get(question.question_id, "")
            meta = f"{graded_count}/{total_students} graded"
            if owner:
                meta = f"{meta} · {owner}"
            items.append(
                NavItem(
                    item_id=question.question_id,
                    label=format_question_label(question.question_id),
                    meta=meta,
                    description=question.prompt or "",
                    question_type=question.question_type,
                )
            )
        return items

    def _student_progress_items(self) -> list[NavItem]:
        items: list[NavItem] = []
        total_questions = len(self._state.grading_run.questions)
        for student in self._state.student_items:
            graded_count = sum(
                1
                for question in self._state.grading_run.questions
                if self._state.qa_student_status.get(
                    self._qa_status_key_for(question.question_id, student.item_id),
                    "ungraded",
                )
                == "graded"
            )
            items.append(
                NavItem(
                    item_id=student.item_id,
                    label=student.label,
                    display_id=student.display_id,
                    meta=f"{graded_count}/{total_questions} graded",
                )
            )
        return items

    def _student_question_statuses(self) -> dict[tuple[str, str], str]:
        statuses: dict[tuple[str, str], str] = {}
        for question in self._state.grading_run.questions:
            for student in self._state.student_items:
                statuses[(question.question_id, student.item_id)] = (
                    self._state.qa_student_status.get(
                        self._qa_status_key_for(question.question_id, student.item_id),
                        "ungraded",
                    )
                )
        return statuses

    @staticmethod
    def _numeric_id(raw_id: str) -> int | None:
        digits = "".join(ch for ch in raw_id if ch.isdigit())
        if not digits:
            return None
        return int(digits)
