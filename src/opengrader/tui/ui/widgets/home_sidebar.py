from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import ListView

from .assignment_list import AssignmentList
from .content_list import ContentList
from .course_list import CourseList
from .student_list import StudentList


class HomeSidebar(Vertical):
    DEFAULT_CSS = """
    HomeSidebar {
        width: 38;
        height: 1fr;
        background: $background;
    }

    HomeSidebar > ListView {
        width: 1fr;
    }

    #home-course-list {
        height: 1fr;
    }

    #home-assignment-list,
    #home-student-list {
        height: 1fr;
    }
    """

    def compose(self) -> ComposeResult:
        yield CourseList(id="home-course-list")
        yield AssignmentList(id="home-assignment-list")
        yield StudentList(id="home-student-list")

    def on_mount(self) -> None:
        self.assignment_list().display = False
        self.student_list().display = False
        self.refresh_index_subtitles()

    def course_list(self) -> ContentList:
        return self.query_one("#home-course-list", ContentList)

    def assignment_list(self) -> ContentList:
        return self.query_one("#home-assignment-list", ContentList)

    def student_list(self) -> ContentList:
        return self.query_one("#home-student-list", ContentList)

    def content_visible(self) -> bool:
        return bool(self.assignment_list().display)

    def show_content_sections(self) -> None:
        self.assignment_list().display = True
        self.student_list().display = True
        self.refresh_index_subtitles()

    def focus_courses(self) -> None:
        lv = self.course_list()
        if len(lv.children) > 0:
            lv.index = 0
        lv.focus()
        self.refresh_index_subtitles()

    def focus_assignments(self) -> None:
        lv = self.assignment_list()
        if not lv.display:
            return
        lv.focus()
        if lv.highlighted_child is None and len(lv.children) > 0:
            lv.index = 0
        self.refresh_index_subtitles()

    def focus_students(self) -> None:
        lv = self.student_list()
        if not lv.display:
            return
        lv.focus()
        if lv.highlighted_child is None and len(lv.children) > 0:
            lv.index = 0
        self.refresh_index_subtitles()

    def focused_pane(self) -> str | None:
        if self.course_list().has_focus:
            return "courses"
        if self.assignment_list().has_focus:
            return "assignments"
        if self.student_list().has_focus:
            return "students"
        return None

    def refresh_index_subtitles(self) -> None:
        self.call_after_refresh(self._refresh_index_subtitles_now)

    def _refresh_index_subtitles_now(self) -> None:
        for lv in (self.course_list(), self.assignment_list(), self.student_list()):
            lv.refresh_index_subtitle()

    def on_list_view_highlighted(self, _event: ListView.Highlighted) -> None:
        self.refresh_index_subtitles()

    def on_list_view_selected(self, _event: ListView.Selected) -> None:
        self.refresh_index_subtitles()
