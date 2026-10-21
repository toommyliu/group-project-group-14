"""What a class folder has, for deciding which assistant jobs make sense now.

The classic Tools page asks for one class folder and shows only the jobs
that fit it: writing the answer key while there is none (or it has blank
answers), drafting a standard key when a blank assignment is there to read
it from, grading and drafting rubrics once there are submissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from opengrader.core.class_folder import scan_class_folder
from opengrader.core.export.gradebook import discover_submission_paths

__all__ = ["ClassState", "read_class_state"]


@dataclass(frozen=True)
class ClassState:
    folder: Path
    qa_file: Path | None = None
    key_problem: str = ""  # the answer key is there but does not load
    unanswered: int = 0
    rubric_gaps: int = 0
    blank_template: Path | None = None
    submissions: Path | None = None
    submission_count: int = 0
    # Set when the folder is (inside) a workspace: nothing is offered there.
    workspace: Path | None = None

    @property
    def needs_key(self) -> bool:
        if self.workspace is not None:
            return False
        return self.qa_file is None or bool(self.key_problem) or self.unanswered > 0

    @property
    def can_draft_standard_key(self) -> bool:
        return self.workspace is None and self.qa_file is None and self.blank_template is not None

    @property
    def can_grade(self) -> bool:
        return (
            self.workspace is None
            and self.submission_count > 0
            and self.qa_file is not None
            and not self.key_problem
        )

    @property
    def can_draft_rubrics(self) -> bool:
        return self.can_grade and self.rubric_gaps > 0

    def describe(self) -> str:
        if self.workspace is not None:
            return (
                f"a workspace ({self.workspace.name}), whose grades live in its database. "
                "Open it (opengrader tui) and press A on its assignment list for the AI assistant"
            )
        parts: list[str] = []
        if self.qa_file is None:
            parts.append("no answer key yet")
        elif self.key_problem:
            parts.append(f"{self.qa_file.name} does not load: {self.key_problem}")
        else:
            key = f"answer key {self.qa_file.name}"
            if self.unanswered:
                key += f" ({self.unanswered} question(s) without an answer)"
            parts.append(key)
        if self.blank_template is not None and self.qa_file is None:
            parts.append(f"blank assignment {self.blank_template.name}")
        parts.append(f"{self.submission_count} submission(s)" if self.submission_count else "no submissions yet")
        return " · ".join(parts)


def read_class_state(folder: str | Path) -> ClassState:
    from opengrader.core.qa_file.answers import unanswered_count
    from opengrader.core.qa_file.markdown import parse_qa_markdown

    from .packet import rubric_gaps

    from .common import workspace_root

    root = workspace_root(folder)
    if root is not None:
        return ClassState(folder=Path(folder).expanduser().resolve(), workspace=root)
    found = scan_class_folder(folder)
    unanswered = gaps = 0
    problem = ""
    if found.qa_file is not None:
        text = found.qa_file.read_text(encoding="utf-8")
        try:
            document = parse_qa_markdown(text)
        except ValueError as exc:
            problem = str(exc)
        else:
            unanswered = unanswered_count(text)
            gaps = len(rubric_gaps(document))
    count = len(discover_submission_paths(found.submissions)) if found.submissions else 0
    return ClassState(
        folder=found.folder,
        qa_file=found.qa_file,
        key_problem=problem,
        unanswered=unanswered,
        rubric_gaps=gaps,
        blank_template=found.blank_template,
        submissions=found.submissions,
        submission_count=count,
    )
