from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from opengrader.tui.data import Provider, QuestionSeed
from opengrader.tui.state import (
    PAGE_GRADE,
    PAGE_QA_PICKER,
    QuestionGrade,
    RubricOption,
    TuiWorkflowState,
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
        self._state.selection_labels["student"] = student_item.label

        self._state.grading_run.reset(
            student_id=self._state.selection.student_id,
            student_name=student_item.label,
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

    def on_rubric_selected(self, option_index: int) -> None:
        self._state.grading_run.apply_option(option_index)
        status = self._persist_current_grade()
        self.refresh_grade_screen(status=status)

    def on_comment_updated(self, comment: str) -> None:
        self._state.grading_run.update_comment(comment)
        error = self._persist_current_analysis(comment)
        if error:
            self.refresh_grade_screen(status=error)

    def return_to_home(self) -> None:
        self.return_to_qa_picker()

    def return_to_qa_picker(self) -> None:
        self.refresh_qa_picker(status="")
        self._app.switch_to(PAGE_QA_PICKER)
        self._app.get_qa_picker_screen().focus_questions()

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

    def finalize_student(self) -> None:
        if not self._on_grade_page():
            return
        current = self._current_student_item()
        if current is None:
            return

        self._state.qa_student_status[self._qa_status_key(current.item_id)] = "graded"
        self._state.student_status[self._status_key(current.item_id)] = "graded"
        if self._refresh_students is not None:
            self._refresh_students()

        next_index = self._find_ungraded_student_index(
            start=self._state.active_student_index + 1,
            direction=1,
        )
        if next_index is not None:
            self.open_student_by_index(next_index)
            self.refresh_grade_screen(
                status="QA finalized. Loaded next ungraded student."
            )
            return

        self.refresh_qa_picker(status="All students finalized for this QA.")
        self._app.switch_to(PAGE_QA_PICKER)
        self._app.get_qa_picker_screen().focus_questions()

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
        )
        qa_screen.set_status(status)

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
        if current_question.selected_option_index is None:
            return ""

        selected_option = current_question.rubric_options[
            current_question.selected_option_index
        ]
        points_awarded = self._clamp_points(
            current_question.points + selected_option.points_delta,
            max_points=current_question.points,
        )
        current_question.points_awarded = points_awarded
        current_question.rubric_label = selected_option.label

        try:
            save_grade(
                student_item_id=current_student.item_id,
                question_id=current_question.question_id,
                possible_points=current_question.points,
                points_awarded=points_awarded,
                rubric_label=selected_option.label,
            )
        except Exception as exc:
            return f"Could not save grade: {exc}"
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

    def _status_key(self, student_item_id: str) -> tuple[str, str, str]:
        return (
            str(self._state.selection.course_id or ""),
            str(self._state.selection.assignment_id or ""),
            student_item_id,
        )

    def _qa_status_key(self, student_item_id: str) -> tuple[str, str, str, str]:
        return (
            str(self._state.selection.course_id or ""),
            str(self._state.selection.assignment_id or ""),
            str(self._state.selection.question_id or ""),
            student_item_id,
        )

    def _student_items_for_current_course(self) -> list[NavItem]:
        return [
            NavItem(
                item_id=f"s{student.id}",
                label=student.full_name,
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

    def _question_index(self, item_id: str) -> int | None:
        for index, question in enumerate(self._state.grading_run.questions):
            if question.question_id == item_id:
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
                        label=option.label,
                        points_delta=option.points_delta,
                    )
                    for option in seed.rubric_options
                ],
                points=seed.points,
                prompt=seed.prompt,
                expected_answers=list(seed.expected_answers),
                response_text=seed.response_text,
                points_awarded=seed.points_awarded,
                rubric_label=seed.rubric_label,
                selected_option_index=GradeController._selected_option_index(seed),
                comment=seed.analysis,
            )
            for seed in seeds
        ]

    @staticmethod
    def _max_points(questions: list[QuestionGrade]) -> float:
        total = sum(question.points for question in questions)
        return total if total > 0 else 100.0

    @staticmethod
    def _selected_option_index(seed: QuestionSeed) -> int | None:
        if seed.rubric_label:
            for index, option in enumerate(seed.rubric_options):
                if option.label == seed.rubric_label:
                    return index

        if seed.points_awarded is None:
            return None

        matching_indices = [
            index
            for index, option in enumerate(seed.rubric_options)
            if GradeController._clamp_points(
                seed.points + option.points_delta,
                max_points=seed.points,
            )
            == seed.points_awarded
        ]
        if len(matching_indices) == 1:
            return matching_indices[0]
        return None

    @staticmethod
    def _clamp_points(points: float, *, max_points: float) -> float:
        return max(0.0, min(points, max_points))

    def _question_items(self, questions: list[QuestionGrade]) -> list[NavItem]:
        items: list[NavItem] = []
        total_students = len(self._state.student_items)
        for question in questions:
            graded_count = sum(
                1
                for student in self._state.student_items
                if self._state.qa_student_status.get(
                    (
                        str(self._state.selection.course_id or ""),
                        str(self._state.selection.assignment_id or ""),
                        question.question_id,
                        student.item_id,
                    ),
                    "ungraded",
                )
                == "graded"
            )
            items.append(
                NavItem(
                    item_id=question.question_id,
                    label=question.question_id,
                    meta=f"{graded_count}/{total_students} graded",
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
                    (
                        str(self._state.selection.course_id or ""),
                        str(self._state.selection.assignment_id or ""),
                        question.question_id,
                        student.item_id,
                    ),
                    "ungraded",
                )
                == "graded"
            )
            items.append(
                NavItem(
                    item_id=student.item_id,
                    label=student.label,
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
                        (
                            str(self._state.selection.course_id or ""),
                            str(self._state.selection.assignment_id or ""),
                            question.question_id,
                            student.item_id,
                        ),
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
