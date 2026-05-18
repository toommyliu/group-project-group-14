from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from opengrader.core.qa_file.scoring import awarded_points_for_rubric_selection
from opengrader.tui.data import Provider, QuestionSeed, RubricOptionSeed
from opengrader.tui.state import (
    PAGE_GRADE,
    PAGE_QA_PICKER,
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

    def attach_student_refresh(self, callback: Callable[[], None]) -> None:
        self._refresh_students = callback

    def open_question_picker_for_assignment(self) -> None:
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

        # Progress counters are derived from saved submission grades, not
        # session-only finalize state.
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

        self.refresh_qa_picker(status="Select a QA to grade for all students.")
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

    def open_question_by_index(self, index: int) -> None:
        if index < 0 or index >= len(self._state.grading_run.questions):
            return

        self._state.active_question_index = index
        self._state.grading_run.select_question(index)
        current = self._state.grading_run.current_question
        if current is None:
            return

        self._state.selection.question_id = current.question_id
        self._state.selection_labels["question"] = current.question_id

        first_ungraded = self._find_ungraded_student_index(start=0, direction=1)
        student_index = first_ungraded if first_ungraded is not None else 0
        self.open_student_by_index(student_index)

    def open_student_by_index(self, index: int) -> None:
        if index < 0 or index >= len(self._state.student_items):
            return

        question_index = self._state.active_question_index
        if question_index < 0:
            question_index = self._state.grading_run.current_question_index

        self._state.active_student_index = index
        student_item = self._state.student_items[index]
        self._state.selection.student_item_id = student_item.item_id
        self._state.selection.student_id = self._numeric_id(student_item.item_id)
        self._state.selection_labels["student"] = student_item.display_id

        self._state.grading_run.reset(
            student_id=self._state.selection.student_id,
            student_name=student_item.display_id,
            assignment_name=self._state.selection_labels.get("assignment")
            or "Assignment",
        )
        self._state.grading_run.questions = self._question_grades(
            self._build_question_seeds_for_selection()
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
        self._state.grading_run.apply_option(option_index)
        status = self._persist_current_grade()
        if finalize:
            self.finalize_student()
        else:
            self.refresh_grade_screen(status=status)

    def on_comment_updated(self, comment: str) -> None:
        self._state.grading_run.update_comment(comment)
        error = self._persist_current_analysis(comment)
        if not error:
            error = self._persist_current_comment_annotation(comment)
        if error:
            self.refresh_grade_screen(status=error)

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

        # Rubric edits can propagate saved grades, so rebuild progress before
        # reloading the active student's grading context.
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
                    f"Jumped to {question.question_id} ({graded_count}/{total_students} graded)"
                )
                return

        self._app.get_qa_picker_screen().set_status(
            "All questions are fully graded for all students!"
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
        next_index = self._state.active_student_index + 1
        if next_index >= len(self._state.student_items):
            self.refresh_grade_screen(status="Already on last student.")
            return
        self.open_student_by_index(next_index)

    def next_ungraded_student(self) -> None:
        if not self._on_grade_page():
            return
        index = self._find_ungraded_student_index(
            start=self._state.active_student_index + 1,
            direction=1,
        )
        if index is None:
            self.refresh_grade_screen(status="No later ungraded students for this QA.")
            return
        self.open_student_by_index(index)

    def previous_ungraded_student(self) -> None:
        if not self._on_grade_page():
            return
        index = self._find_ungraded_student_index(
            start=self._state.active_student_index - 1,
            direction=-1,
        )
        if index is None:
            self.refresh_grade_screen(
                status="No earlier ungraded students for this QA."
            )
            return
        self.open_student_by_index(index)

    def previous_student(self) -> None:
        if not self._on_grade_page():
            return
        previous_index = self._state.active_student_index - 1
        if previous_index < 0:
            self.refresh_grade_screen(status="Already on first student.")
            return
        self.open_student_by_index(previous_index)

    def next_question(self) -> None:
        if not self._on_grade_page():
            return
        next_index = self._state.active_question_index + 1
        if next_index >= len(self._state.grading_run.questions):
            self.refresh_grade_screen(status="Already on last question.")
            return
        self._state.active_question_index = next_index
        self.open_student_by_index(self._state.active_student_index)

    def previous_question(self) -> None:
        if not self._on_grade_page():
            return
        prev_index = self._state.active_question_index - 1
        if prev_index < 0:
            self.refresh_grade_screen(status="Already on first question.")
            return
        self._state.active_question_index = prev_index
        self.open_student_by_index(self._state.active_student_index)

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

        next_index = self._find_ungraded_student_index(
            start=self._state.active_student_index + 1,
            direction=1,
        )
        status = "QA finalized. Loaded next ungraded student."

        if next_index is None:
            # Wrap around to check for any earlier ungraded students for this QA
            next_index = self._find_ungraded_student_index(start=0, direction=1)
            status = "QA finalized. Wrapped to find ungraded student."

        if next_index is not None:
            self.open_student_by_index(next_index)
            self.refresh_grade_screen(status=status)
            return

        self.refresh_qa_picker(status="All students finalized for this QA.")
        self._app.switch_to(PAGE_QA_PICKER)
        self._focus_qa_picker_after_grade()

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
        """Toggle between side-by-side and top-bottom grading layouts."""
        if not self._on_grade_page():
            return
        self._app.get_grade_screen().toggle_layout()

    def refresh_grade_screen(self, status: str) -> None:
        grade_screen = self._app.get_grade_screen()
        grade_screen.bind_run(
            self._state.grading_run,
            breadcrumb_labels=self._state.breadcrumb_labels(),
        )
        set_progress = getattr(grade_screen, "set_progress", None)
        if callable(set_progress):
            set_progress(self._qa_student_progress_label())
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
        qa_screen.set_status(status)

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

    def _persist_current_grade(self) -> str:
        save_grade = getattr(self._data_provider, "save_question_grade", None)
        if not callable(save_grade):
            return ""

        current_student = self._current_student_item()
        current_question = self._state.grading_run.current_question
        if current_student is None or current_question is None:
            return ""
        selected_options = current_question.selected_options()
        rubric_labels = current_question.selected_rubric_labels()

        standard_selected = [o for o in selected_options if o.kind != "bonus"]
        bonus_selected = [o for o in selected_options if o.kind == "bonus"]

        points_awarded = (
            awarded_points_for_rubric_selection(
                current_question.points or 0.0,
                [o.points_delta for o in standard_selected],
                [o.points_delta for o in current_question.rubric_options if o.kind != "bonus"],
                bonus_points_values=[o.points_delta for o in bonus_selected],
            )
            if selected_options
            else None
        )
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
        self._set_qa_student_status(
            current_question.question_id,
            current_student.item_id,
            graded=bool(selected_options),
        )
        return "Saved grade to submission file."

    def _persist_current_analysis(self, analysis: str) -> str:
        save_analysis = getattr(self._data_provider, "save_question_analysis", None)
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

    def _persist_current_comment_annotation(self, comment: str) -> str:
        save_annotation = getattr(
            self._data_provider,
            "save_question_comment_annotation",
            None,
        )
        if not callable(save_annotation):
            return ""

        current_student = self._current_student_item()
        current_question = self._state.grading_run.current_question
        if current_student is None or current_question is None:
            return ""

        try:
            save_annotation(
                student_item_id=current_student.item_id,
                question_id=current_question.question_id,
                comment=comment,
            )
        except Exception as exc:
            return f"Could not annotate PDF: {exc}"
        return ""

    def _status_key(self, student_item_id: str) -> tuple[str, str, str]:
        return (
            str(self._state.selection.course_id or ""),
            str(self._state.selection.assignment_id or ""),
            student_item_id,
        )

    def _qa_status_key(self, student_item_id: str) -> tuple[str, str, str, str]:
        """Return the progress key for the currently selected QA/student."""
        return self._qa_status_key_for(
            str(self._state.selection.question_id or ""),
            student_item_id,
        )

    def _qa_status_key_for(
        self,
        question_id: str,
        student_item_id: str,
    ) -> tuple[str, str, str, str]:
        """Return the progress key for an explicit QA/student pair."""
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
        """Store or clear one QA/student progress marker."""
        key = self._qa_status_key_for(question_id, student_item_id)
        if graded:
            self._state.qa_student_status[key] = "graded"
            return
        self._state.qa_student_status.pop(key, None)

    def _hydrate_saved_grading_status(self) -> None:
        """Rebuild QA progress for the active assignment from persisted grades.

        The submission markdown is the source of truth across TUI sessions.
        Only resolvable saved grades are marked graded; ambiguous or missing
        grade fields remain absent so existing ungraded defaults apply.
        """
        course_id = str(self._state.selection.course_id or "")
        assignment_id = str(self._state.selection.assignment_id or "")
        self._state.qa_student_status = {
            key: status
            for key, status in self._state.qa_student_status.items()
            if key[:2] != (course_id, assignment_id)
        }

        for student in self._state.student_items:
            # Use each student's provider context so versioned QA files and
            # student-specific submission markdown are interpreted correctly.
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
        return [
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

    def _find_ungraded_student_index(
        self,
        *,
        start: int,
        direction: int,
    ) -> int | None:
        if direction == 0:
            return None
        index = start
        while 0 <= index < len(self._state.student_items):
            student = self._state.student_items[index]
            if (
                self._state.qa_student_status.get(
                    self._qa_status_key(student.item_id),
                    "ungraded",
                )
                != "graded"
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
        return f"Progress: {graded_students} / {total_students} students"

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
            )
            for seed in seeds
        ]

    @staticmethod
    def _max_points(questions: list[QuestionGrade]) -> float:
        total = sum(question.points for question in questions)
        return total if total > 0 else 100.0

    @staticmethod
    def _selected_option_indices(seed: QuestionSeed) -> set[int]:
        """Resolve persisted rubric state to runtime option indices.

        Labels are authoritative because they preserve checklist selections.
        Point-only grades are supported as a fallback only when they map to one
        rubric row, avoiding false progress on ambiguous saved scores.
        """
        return selected_option_indices_for_seed(seed)

    @staticmethod
    def _seed_is_graded(seed: QuestionSeed) -> bool:
        """Return whether a persisted question seed has a resolvable grade."""
        return seed_is_graded(seed)

    def _question_items(self, questions: list[QuestionGrade]) -> list[NavItem]:
        items: list[NavItem] = []
        total_students = len(self._state.student_items)
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
            items.append(
                NavItem(
                    item_id=question.question_id,
                    label=question.question_id,
                    meta=f"{graded_count}/{total_students} graded",
                    description=question.prompt or "",
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
