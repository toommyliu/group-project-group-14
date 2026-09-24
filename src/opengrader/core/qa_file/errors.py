from dataclasses import dataclass, field


@dataclass
class QAErrorLocation:
    line: int | None = None
    version: str = ""
    question_path: list[int] = field(default_factory=list)
    rubric_path: list[int] | None = None


class QAParseError(ValueError):
    def __init__(self, message: str, location: QAErrorLocation | None = None):
        super().__init__(message)
        self.location = location or QAErrorLocation()
