from .content_list import ContentList

class CourseList(ContentList):
    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(title="[0] Courses", id=id)
        self.border_title = "[0] Courses"
