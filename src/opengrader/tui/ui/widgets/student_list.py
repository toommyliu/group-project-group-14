from .content_list import ContentList


class StudentList(ContentList):
    def __init__(
        self,
        *,
        id: str | None = None,
    ) -> None:
        super().__init__("[3] Students", id=id)
