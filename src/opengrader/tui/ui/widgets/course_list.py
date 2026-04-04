from textual.widgets import ListView


class CourseList(ListView):
    DEFAULT_CSS = """
    CourseList {
        width: 32;
        border: round $accent;
        border-title-color: #ffffff;
        border-title-align: left;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self.border_title = "[0] - Courses"
