from textual.widgets import Static

from .nav_item import NavItem


class SelectionDetails(Static):
    DEFAULT_CSS = """
    SelectionDetails {
        width: 1fr;
        height: 1fr;
        border: round $accent;
        border-title-color: #ffffff;
        border-title-align: left;
        border-subtitle-color: $text-muted;
        border-subtitle-align: right;
        padding: 1 2;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.show_welcome()

    def show_welcome(self) -> None:
        self.border_title = "Selection Details"
        self.border_subtitle = "Navigate with ↑/↓ and ←/→"
        self.update(
            "Select a course to get started.\n\n"
            "This panel shows details and available actions for the currently "
            "highlighted course, assignment, or student."
        )

    def show_course(
        self,
        course: NavItem,
        *,
        assignment_count: int,
        student_count: int,
    ) -> None:
        self.border_title = f"Course · {course.label}"
        self.border_subtitle = "[n] New"
        self.update(
            f"Term/Section: {course.meta or 'N/A'}\n"
            f"Assignments: {assignment_count}\n"
            f"Students: {student_count}\n\n"
            "Pick an assignment to grade, or browse students and submissions."
        )

    def show_assignment(self, assignment: NavItem, *, template_hint: str) -> None:
        self.border_title = f"Assignment · {assignment.label}"
        self.border_subtitle = "[g] Grade · [d] Details · [r] Rubric · [t] Template"
        self.update(
            f"Type: {assignment.meta or 'N/A'}\n"
            f"Template: {template_hint}\n\n"
            "Press [g] to start grading the current assignment."
        )

    def show_student(self, student: NavItem, *, submission_count: int) -> None:
        self.border_title = f"Student · {student.label}"
        self.border_subtitle = "[Enter] Show submissions"
        self.update(
            f"Status: {student.meta or 'N/A'}\n"
            f"Submissions available: {submission_count}\n\n"
            "Select a submission assignment to start grading this student."
        )

    def show_student_submission(self, student: NavItem, assignment: NavItem) -> None:
        self.border_title = f"Submission · {student.label}"
        self.border_subtitle = "[g] Grade"
        self.update(
            f"Assignment: {assignment.label}\n"
            f"Type: {assignment.meta or 'N/A'}\n\n"
            "Press [g] to open grading for this student submission."
        )
