from textual.widgets import Label


class Breadcrumb(Label):
    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("Browse > Select a submission", id=id)

    def set_path(self, labels: list[str]) -> None:
        if not labels:
            self.update("Browse > Select a submission")
            return
        self.update(" > ".join(labels))
