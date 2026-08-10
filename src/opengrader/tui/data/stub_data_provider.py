from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from opengrader.core.claims import WorkClaim, WorkSlice
from opengrader.core.comments import CommentLibrary
from opengrader.core.roster import read_roster
from opengrader.tui.data.models import QuestionSeed, StudentSummary
from opengrader.tui.data.stub_question_content import StubQuestionContent


class StubDataProvider:
    def __init__(self, default_image_path: str) -> None:
        self._question_content = StubQuestionContent(default_image_path)
        # Demo mode has no submissions folder to persist to, so the reusable
        # comment library lives in memory for the session.
        self._comment_library = CommentLibrary()
        self._claims: list[WorkClaim] = []

    # Demo data has no assignment folder to store claims in, so they live for
    # the session only — enough for the modal to work against stub data.
    def grader_actor(self) -> str:
        return "demo"

    def read_work_claims(self) -> list[WorkClaim]:
        return [claim for claim in self._claims if claim.is_active]

    def cohort_ids(self) -> tuple[list[str], list[str]]:
        return [], []

    def claim_work_slice(
        self, work: WorkSlice, *, actor: str | None = None, note: str = ""
    ) -> WorkClaim:
        claim = WorkClaim(
            claim_id=f"cl-stub{len(self._claims) + 1}",
            actor=actor or self.grader_actor(),
            work=work,
            note=note,
        )
        self._claims.append(claim)
        return claim

    def release_work_claim(self, claim_id: str) -> None:
        self._close_claim(claim_id, "released")

    def complete_work_claim(self, claim_id: str) -> None:
        self._close_claim(claim_id, "completed")

    def _close_claim(self, claim_id: str, status: str) -> None:
        self._claims = [
            replace(claim, status=status) if claim.claim_id == claim_id else claim
            for claim in self._claims
        ]

    def read_comment_library(self) -> CommentLibrary:
        return self._comment_library

    def save_comment_library(self, library: CommentLibrary) -> None:
        self._comment_library = library

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []
        roster_path = Path(__file__).resolve().parents[4] / "data" / "roster.csv"
        try:
            rows = read_roster(str(roster_path))
        except OSError:
            rows = []
        if not rows:
            return []
        return [
            StudentSummary(
                id=index + 1,
                full_name=(row.get("name") or f"Student {index + 1}").strip(),
                student_id=(
                    row.get("student_id")
                    or row.get("sid")
                    or row.get("id")
                    or ""
                ).strip(),
            )
            for index, row in enumerate(rows)
        ]

    def build_question_seeds(
        self,
        *,
        assignment_id: int | None = None,
        assignment_title: str | None = None,
        student_item_id: str | None = None,
    ) -> list[QuestionSeed]:
        _ = (assignment_id, student_item_id)
        return self._question_content.build_question_seeds(
            assignment_title=assignment_title
        )
