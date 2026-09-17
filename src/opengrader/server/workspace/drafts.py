"""Recoverable terminal edits, kept separately from committed workspace state."""

from contextlib import ExitStack
import os
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from opengrader.core.grading.edits import GradeEdit
from opengrader.core.workspace.models import Answer
from opengrader.server.workspace.client import exclusive_lock


class Draft(BaseModel):
    base: Answer
    edit: GradeEdit
    pending: GradeEdit | None = None


class DraftFile(BaseModel):
    assignment_id: str
    answers: dict[str, Draft] = Field(default_factory=dict)


class DraftJournal:
    """Recover an inactive terminal session without taking another client's drafts."""

    def __init__(self, directory: Path, assignment_id: str):
        directory.mkdir(parents=True, exist_ok=True)
        self.locks = ExitStack()
        self.value = DraftFile(assignment_id=assignment_id)
        self.path = directory / f"{uuid4()}.json"
        for path in sorted(
            directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            lock = ExitStack()
            try:
                lock.enter_context(exclusive_lock(path.with_suffix(".lock")))
                value = DraftFile.model_validate_json(path.read_bytes())
                if value.assignment_id == assignment_id and value.answers:
                    self.locks.enter_context(lock.pop_all())
                    self.path, self.value = path, value
                    break
            except BlockingIOError:
                pass
            finally:
                lock.close()
        else:
            self.locks.enter_context(exclusive_lock(self.path.with_suffix(".lock")))

    def write(self) -> None:
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(self.value.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)
        if os.name == "posix":
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)

    def close(self) -> None:
        self.locks.close()
