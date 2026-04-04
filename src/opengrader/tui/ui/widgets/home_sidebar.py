from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import ListView

from .assignment_list import AssignmentList
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

    def course_list(self) -> ListView:
        return self.query_one("#home-course-list", ListView)

    def assignment_list(self) -> ListView:
        return self.query_one("#home-assignment-list", ListView)

    def student_list(self) -> ListView:
        return self.query_one("#home-student-list", ListView)

    def content_visible(self) -> bool:
        return bool(self.assignment_list().display)

    def show_content_sections(self) -> None:
        self.assignment_list().display = True
        self.student_list().display = True

    def focus_courses(self) -> None:
        lv = self.course_list()
        if len(lv.children) > 0:
            lv.index = 0
        lv.focus()

    def focus_assignments(self) -> None:
        lv = self.assignment_list()
        if not lv.display:
            return
        lv.focus()
        if lv.highlighted_child is None and len(lv.children) > 0:
            lv.index = 0

    def focus_students(self) -> None:
        lv = self.student_list()
        if not lv.display:
            return
        lv.focus()
        if lv.highlighted_child is None and len(lv.children) > 0:
            lv.index = 0

    def focused_pane(self) -> str | None:
        if self.course_list().has_focus:
            return "courses"
        if self.assignment_list().has_focus:
            return "assignments"
        if self.student_list().has_focus:
            return "students"
        return None
