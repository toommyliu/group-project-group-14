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
    # Grading is anonymous by default: screens show student IDs and only
    # reveal names while teacher mode is toggled on.
    teacher_mode: bool = False
    # Set from the assignment's grading policy (`core/policy.py`). When true the
    # folder is locked to anonymous grading: the teacher-mode toggle is disabled
    # so a grader can't reveal names (and, transitively, the audit view stays
    # hidden). Solo/unlocked folders leave this False.
    anonymity_locked: bool = False
    # Multi-grader work claims (`core/claims.py`), loaded at startup and after
    # any change made in the claims modal. Empty means nobody divided the work,
    # which is the pre-claims behaviour: everything is everyone's.
    active_claims: tuple[WorkClaim, ...] = ()
    # Who this app instance is grading as — the same identity the audit journal
    # records, so a claim and a journal entry name the same person.
    grader_actor: str = ""
    # Whether navigation skips work another grader has claimed. **On by
    # default**, since steering each grader to their own slice is the entire
    # point; the claims modal can turn it off so a TA who finishes early (or a
    # professor) can see the whole cohort. Inert when there are no claims.
    claim_filter_enabled: bool = True
    # "Grade the answer, not the student". Off by default, and a *mode* rather
    # than a setting because it changes what the navigation keys mean: with it
    # on, moving forward goes to the next distinct **answer** for this question
    # instead of the next student, so a class of 200 with 27 different answers
    # is 27 decisions rather than 200. Nothing is written differently — the
    # batch write is still the explicit `g` key — so turning the mode off mid-
    # question simply puts the grader back on the student they are looking at.
    group_mode: bool = False
    # "Confirm the AI's grades". Off by default, and — like `group_mode` — a
    # *mode*, because it changes what the movement keys mean: with it on,
    # `n`/`p` step between answers carrying an unconfirmed `[AI · …]` grade
    # instead of between ungraded students. An AI-graded answer *is* graded, so
    # the ordinary ungraded jumps step straight over exactly the answers a
    # human most needs to look at, and `grade-check` blocks a release on them
    # with no fast way to clear the list. Mutually exclusive with `group_mode`:
    # both redefine the same keys, and a grader should never have to work out
    # which one won.
    ai_review_mode: bool = False
    student_status: dict[tuple[str, str, str], str] = field(default_factory=dict)
    qa_student_status: dict[tuple[str, str, str, str], str] = field(
        default_factory=dict
    )
    # Session-only history of saved grading changes, shown in the QA picker's
    # grading log pane.
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
