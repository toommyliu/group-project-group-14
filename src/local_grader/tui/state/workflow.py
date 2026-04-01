from __future__ import annotations

from dataclasses import dataclass, field

from local_grader.tui.state.grading_session import StudentGradingRun
from local_grader.tui.state.route import PAGE_BROWSE
from local_grader.tui.state.selection import GradingSelection
from local_grader.tui.widgets.miller_column import MillerItem


@dataclass
class TuiWorkflowState:
    current_page: str = PAGE_BROWSE
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
            "student": "",
        }
    )
    student_items: list[MillerItem] = field(default_factory=list)
    active_student_index: int = -1
    student_status: dict[tuple[str, str, str], str] = field(default_factory=dict)

    def breadcrumb_labels(self) -> list[str]:
        return [
            self.selection_labels.get("course") or "Course",
            self.selection_labels.get("assignment") or "Assignment",
            self.selection_labels.get("student") or "Student",
        ]
