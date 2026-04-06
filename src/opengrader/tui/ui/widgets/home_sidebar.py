from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import ListView

from .assignment_pane import AssignmentPane
from .tabbed_list_pane import IndexedListView, TabbedListPane
from .course_pane import CoursePane
from .tabbed_pane import TabbedPane
from .student_pane import StudentPane


class HomeSidebar(Vertical):
    DEFAULT_CSS = """
    HomeSidebar {
        width: 38;
        height: 1fr;
        background: $background;
    }

    HomeSidebar > ListPane {
        width: 1fr;
    }

    #home-course-pane,
    #home-assignment-pane,
    #home-student-pane {
        height: 1fr;
    }
    """

    def __init__(self, **kwargs: object) -> None:
        self._content_shown = False
        super().__init__(**kwargs)  # pyright: ignore[reportArgumentType]

    def compose(self) -> ComposeResult:
        yield CoursePane(id="home-course-list", pane_id="home-course-pane")
        yield AssignmentPane(id="home-assignment-list", pane_id="home-assignment-pane")
        yield StudentPane(id="home-student-list", pane_id="home-student-pane")

    def on_mount(self) -> None:
        self.set_pane_size_units(courses=1, assignments=1, students=1)
        self.refresh_index_subtitles()

    def course_pane(self) -> CoursePane:
        return self.query_one("#home-course-pane", CoursePane)

    def assignment_pane(self) -> AssignmentPane:
        return self.query_one("#home-assignment-pane", AssignmentPane)

    def student_pane(self) -> StudentPane:
        return self.query_one("#home-student-pane", StudentPane)

    def course_list(self) -> IndexedListView:
        return self.course_pane().primary_list()

    def assignment_list(self) -> IndexedListView:
        return self.assignment_pane().primary_list()

    def student_list(self) -> IndexedListView:
        return self.student_pane().primary_list()

    def content_visible(self) -> bool:
        return self._content_shown

    def show_content_sections(self) -> None:
        self._content_shown = True
        self.assignment_pane().display = True
        self.student_pane().display = True
        self.refresh_index_subtitles()

    def focus_courses(self, item_id: str | None = None) -> None:
        self._focus_list(
            self.course_list(),
            set_first_row=False,
            item_id=item_id,
        )

    def focus_assignments(self) -> None:
        lv = self.assignment_list()
        if not lv.display:
            return
        self._focus_list(lv, set_first_row=False)

    def focus_students(self) -> None:
        lv = self.student_list()
        if not lv.display:
            return
        self._focus_list(lv, set_first_row=False)

    def focused_pane(self) -> str | None:
        if self.course_pane().has_focus_within:
            return "courses"
        if self.assignment_pane().has_focus_within:
            return "assignments"
        if self.student_pane().has_focus_within:
            return "students"
        return None

    def focused_tabbed_pane(self) -> TabbedPane | None:
        for pane in (
            self.course_pane(),
            self.assignment_pane(),
            self.student_pane(),
        ):
            if pane.has_focus_within:
                return pane
        return None

    def set_pane_size_units(
        self,
        *,
        courses: int,
        assignments: int,
        students: int,
    ) -> None:
        self.course_pane().set_size_units(courses)
        self.assignment_pane().set_size_units(assignments)
        self.student_pane().set_size_units(students)

    def refresh_index_subtitles(self) -> None:
        self.call_after_refresh(self._refresh_index_subtitles_now)

    def sync_pane_focus_classes(self) -> None:
        for pane in (self.course_pane(), self.assignment_pane(), self.student_pane()):
            pane.sync_focus_class()

    def _refresh_index_subtitles_now(self) -> None:
        for pane in (self.course_pane(), self.assignment_pane(), self.student_pane()):
            pane.refresh_index_subtitle()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if isinstance(event.list_view, IndexedListView):
            event.list_view.remember_highlighted_index()
            event.list_view.normalize_highlight_classes()
        self.refresh_index_subtitles()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.list_view, IndexedListView):
            event.list_view.remember_highlighted_index()
            event.list_view.normalize_highlight_classes()
        self.refresh_index_subtitles()

    def _focus_list(
        self,
        lv: IndexedListView,
        *,
        set_first_row: bool,
        item_id: str | None = None,
    ) -> None:
        if item_id is not None:
            for index, child in enumerate(lv.children):
                nav = getattr(child, "nav", None)
                if nav is not None and getattr(nav, "item_id", None) == item_id:
                    lv.index = index
                    lv.remember_highlighted_index()
                    lv.normalize_highlight_classes()
                    lv.focus()
                    self.refresh_index_subtitles()
                    return

        if set_first_row and len(lv.children) > 0:
            lv.index = 0
        else:
            lv.ensure_focus_index(fallback_first=True)

        lv.remember_highlighted_index()
        lv.normalize_highlight_classes()
        lv.focus()
        self.refresh_index_subtitles()
