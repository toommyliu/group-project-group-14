from .content_list import ContentList


class AssignmentList(ContentList):
    def __init__(
        self,
        *,
        id: str | None = None,
    ) -> None:
        super().__init__("[1] Assignments", id=id)
