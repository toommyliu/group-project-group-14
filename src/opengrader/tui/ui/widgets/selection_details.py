from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rich.markup import escape
from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widget import Widget
from textual.widgets import Static

from .nav_item import NavItem
from .template_preview import TemplatePreview


def make_title(prefix: str, label: str | None = None) -> str:
    title = f"[0] {escape(prefix)}"
    if label:
        title += f" · {escape(label)}"
    return title


@dataclass
class DetailTab:
    label: str
    build: Callable[[], Widget]
    supports_paging: bool = False


class SelectionDetails(Vertical):
    can_focus = True

    DEFAULT_CSS = """
    SelectionDetails {
        width: 1fr;
        height: 1fr;
        layout: vertical;
        background: $background;
        border: round $accent;
        border-title-color: white;
        border-title-align: left;
        border-subtitle-color: $text-muted;
        border-subtitle-align: right;
    }

    SelectionDetails:focus {
        border-title-color: $accent;
    }

    #selection-details-tab-content {
        width: 1fr;
        height: 1fr;
    }

    #selection-details-tab-content > * {
        width: 1fr;
        height: 1fr;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._tabs: list[DetailTab] = []
        self._tab_widgets: dict[int, Widget] = {}
        self._active_tab_index: int = 0
        self._title_prefix: str = "Selection"
        self._title_label: str | None = None
        self._context_subtitle: str = ""

    def compose(self) -> ComposeResult:
        yield Container(id="selection-details-tab-content")

    def on_mount(self) -> None:
        self._sync_chrome()
        self._render_active_tab()

    def set_tabs(self, tabs: list[DetailTab]) -> None:
        self._tabs = list(tabs)
        self._tab_widgets.clear()
        self._active_tab_index = 0
        self._render_active_tab()

    def next_tab(self) -> None:
        if len(self._tabs) <= 1:
            return
        self._active_tab_index = (self._active_tab_index + 1) % len(self._tabs)
        self._render_active_tab()

    def prev_tab(self) -> None:
        if len(self._tabs) <= 1:
            return
        self._active_tab_index = (self._active_tab_index - 1) % len(self._tabs)
        self._render_active_tab()

    def next_page(self) -> None:
        if not self._active_tab_supports_paging():
            return
        widget = self._active_widget()
        if isinstance(widget, TemplatePreview):
            widget.next_page()

    def prev_page(self) -> None:
        if not self._active_tab_supports_paging():
            return
        widget = self._active_widget()
        if isinstance(widget, TemplatePreview):
            widget.prev_page()

    @property
    def tab_count(self) -> int:
        return len(self._tabs)

    @property
    def active_tab_index(self) -> int:
        return self._active_tab_index

    def show_course(
        self,
        course: NavItem,
        *,
        assignment_count: int,
        student_count: int,
    ) -> None:
        self._set_context("Course", course.label)

        def _build_details() -> Widget:
            return Static(
                f"Term/Section: {course.meta or 'N/A'}\n"
                f"Assignments: {assignment_count}\n"
                f"Students: {student_count}\n\n"
                "Pick an assignment to grade, or browse students and submissions."
            )

        self.set_tabs([DetailTab(label="Details", build=_build_details)])

    def show_assignment(
        self,
        assignment: NavItem,
        *,
        template_hint: str,
        template_tabs: list[tuple[str, Path | None]] | None = None,
    ) -> None:
        self._set_context("Assignment", assignment.label)

        def _build_details() -> Widget:
            return Static(
                f"Type: {assignment.meta or 'N/A'}\n"
                f"Template: {template_hint}\n\n"
                "Press [g] to start grading the current assignment."
            )

        tabs: list[DetailTab] = [DetailTab(label="Details", build=_build_details)]

        if template_tabs:
            for tab_label, pdf_path in template_tabs:
                tabs.append(
                    DetailTab(
                        label=tab_label,
                        build=self._make_template_tab_builder(pdf_path),
                        supports_paging=True,
                    )
                )

        self.set_tabs(tabs)

    def show_student(self, student: NavItem, *, submission_count: int) -> None:
        self._set_context("Student", student.label, subtitle="[Enter] Show submissions")

        def _build_details() -> Widget:
            return Static(
                f"Status: {student.meta or 'N/A'}\n"
                f"Submissions available: {submission_count}\n\n"
                "Select a submission assignment to start grading this student."
            )

        self.set_tabs([DetailTab(label="Details", build=_build_details)])

    def show_student_submission(self, student: NavItem, assignment: NavItem) -> None:
        self._set_context("Submission", student.label, subtitle="[g] Grade")

        def _build_details() -> Widget:
            return Static(
                f"Assignment: {assignment.label}\n"
                f"Type: {assignment.meta or 'N/A'}\n\n"
                "Press [g] to open grading for this student submission."
            )

        self.set_tabs([DetailTab(label="Details", build=_build_details)])

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
        self._sync_chrome()

    def _render_active_tab(self) -> None:
        if not self.is_mounted:
            return

        content = self.query_one("#selection-details-tab-content", Container)
        for child in list(content.children):
            child.remove()

        widget = self._active_widget()
        if widget is not None:
            content.mount(widget)

        self._sync_chrome()

    def _sync_chrome(self) -> None:
        self.border_title = self._build_border_title()
        subtitle_parts: list[str] = []
        if self._context_subtitle:
            subtitle_parts.append(self._context_subtitle)
        if self._active_tab_supports_paging():
            subtitle_parts.append(", / . Page")
        self.border_subtitle = " · ".join(subtitle_parts)

    def _build_border_title(self) -> str:
        title = make_title(self._title_prefix, self._title_label)
        if not self._tabs:
            return title
        tab_markup: list[str] = []
        for index, tab in enumerate(self._tabs):
            if index > 0:
                tab_markup.append("[$text-muted] - [/]")
            tab_label = escape(tab.label)
            if index == self._active_tab_index:
                tab_markup.append(f"[bold $accent]{tab_label}[/]")
            else:
                tab_markup.append(f"[$text-muted]{tab_label}[/]")

        return title + "  " + "".join(tab_markup)

    def _active_tab(self) -> DetailTab | None:
        if not self._tabs:
            return None
        return self._tabs[self._active_tab_index]

    def _active_widget(self) -> Widget | None:
        active_tab = self._active_tab()
        if active_tab is None:
            return None

        widget = self._tab_widgets.get(self._active_tab_index)
        if widget is None:
            widget = active_tab.build()
            self._tab_widgets[self._active_tab_index] = widget
        return widget

    def _active_tab_supports_paging(self) -> bool:
        active_tab = self._active_tab()
        if active_tab is None:
            return False
        return active_tab.supports_paging

    @staticmethod
    def _make_template_tab_builder(
        pdf_path: Path | None,
    ) -> Callable[[], Widget]:
        def _build() -> Widget:
            return TemplatePreview(pdf_path)

        return _build
