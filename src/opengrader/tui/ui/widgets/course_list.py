from .content_list import ContentList


class CourseList(ContentList):
    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(title="[1] Courses", id=id)
