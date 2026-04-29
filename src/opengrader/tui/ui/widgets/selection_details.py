from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from rich.markup import escape
from textual.widget import Widget

from opengrader.tui.data.models import StudentSubmissionSummary

from .nav_item import NavItem
from .tabbed_pane import TabbedPaneTab, TabbedPane
from .selection_detail_content import (
    AssignmentDetail,
    CourseDetail,
    StudentDetail,
    StudentSubmissionDetail,
)
from .template_preview import TemplatePreview


def make_title(prefix: str, label: str | None = None) -> str:
    title = f"[0] {escape(prefix)}"
    if label:
        title += f" · {escape(label)}"
    return title


class SelectionDetails(TabbedPane):
    can_focus = True

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(make_title("Selection"), show_single_tab=False, id=id)
        self._title_prefix: str = "Selection"
        self._title_label: str | None = None
        self._context_subtitle: str = ""

    def on_mount(self) -> None:
        super().on_mount()
        self._sync_context_chrome()

    def show_course(
        self,
        course: NavItem,
        *,
        assignment_count: int,
        student_count: int,
    ) -> None:
        self._set_context("Course", course.label)

        def _build_details() -> Widget:
            return CourseDetail(
                course,
                assignment_count=assignment_count,
                student_count=student_count,
            )

        self.set_tabs([TabbedPaneTab(label="", build=_build_details)])

    def show_assignment(
        self,
        assignment: NavItem,
        *,
        template_hint: str,
        template_tabs: list[tuple[str, Path | None]] | None = None,
    ) -> None:
        self._set_context("Assignment", assignment.label)

        def _build_details() -> Widget:
            return AssignmentDetail(assignment, template_hint=template_hint)

        tabs: list[TabbedPaneTab] = [TabbedPaneTab(label="", build=_build_details)]

        if template_tabs:
            for tab_label, pdf_path in template_tabs:
                tabs.append(
                    TabbedPaneTab(
                        label=tab_label,
                        build=self._make_template_tab_builder(pdf_path),
                        supports_paging=True,
                    )
                )

        self.set_tabs(tabs)

    def show_student(self, student: NavItem, *, submission_count: int) -> None:
        self._set_context("Student", student.label)

        def _build_details() -> Widget:
            return StudentDetail(student, submission_count=submission_count)

        self.set_tabs([TabbedPaneTab(label="", build=_build_details)])

    def show_student_submission(
        self,
        student: NavItem,
        assignment: NavItem,
        *,
        submission: StudentSubmissionSummary | None = None,
    ) -> None:
        self._set_context("Submission", student.label, subtitle="[g] Grade")

        def _build_details() -> Widget:
            return StudentSubmissionDetail(
                student,
                assignment,
                submission=submission,
            )

        self.set_tabs([TabbedPaneTab(label="", build=_build_details)])

    def _set_context(
        self,
        title_prefix: str,
        title_label: str | None = None,
        *,
        subtitle: str = "",
    ) -> None:
        self._title_prefix = title_prefix
        self._title_label = title_label
        self._context_subtitle = subtitle
        self._sync_context_chrome()

    def _sync_context_chrome(self) -> None:
        self.set_title(make_title(self._title_prefix, self._title_label))
        self.set_subtitle(self._context_subtitle)

    @staticmethod
    def _make_template_tab_builder(
        pdf_path: Path | None,
    ) -> Callable[[], Widget]:
        def _build() -> Widget:
            return TemplatePreview(pdf_path)

        return _build
