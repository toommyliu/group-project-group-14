from __future__ import annotations

from dataclasses import dataclass, field

from opengrader.core.claims import WorkClaim
from opengrader.tui.state.grading_log import GradingLog
from opengrader.tui.state.grading_session import StudentGradingRun
from opengrader.tui.state.route import PAGE_QA_PICKER
from opengrader.tui.state.selection import GradingSelection
from opengrader.tui.ui.widgets.nav_item import NavItem


@dataclass
class TuiWorkflowState:
    current_page: str = PAGE_QA_PICKER
    selection: GradingSelection = field(default_factory=GradingSelection)
    grading_run: StudentGradingRun = field(
        default_factory=lambda: StudentGradingRun(
            student_id=None,
            student_name="",
            assignment_name="",
            max_points=100.0,
        )
    )
    selection_labels: dict[str, str] = field(
        default_factory=lambda: {
            "course": "",
            "assignment": "",
            "question": "",
            "student": "",
        }
    )
    question_items: list[NavItem] = field(default_factory=list)
    student_items: list[NavItem] = field(default_factory=list)
    active_student_index: int = -1
    active_question_index: int = -1
    teacher_mode: bool = False
    anonymity_locked: bool = False
    # Something is open for grading (a folder or a workspace assignment), so
    # the start page can offer a way back to it.
    session_open: bool = False
    active_claims: tuple[WorkClaim, ...] = ()
    grader_actor: str = ""
    claim_filter_enabled: bool = True
    group_mode: bool = False
    ai_review_mode: bool = False
    student_status: dict[tuple[str, str, str], str] = field(default_factory=dict)
    qa_student_status: dict[tuple[str, str, str, str], str] = field(
        default_factory=dict
    )
    grading_log: GradingLog = field(default_factory=GradingLog)

    def breadcrumb_labels(self) -> list[str]:
        labels = [
            self.selection_labels.get("course") or "Course",
            self.selection_labels.get("assignment") or "Assignment",
        ]
        question = self.selection_labels.get("question")
        if question:
            labels.append(question)
        labels.append(self.selection_labels.get("student") or "Student")
        return labels
