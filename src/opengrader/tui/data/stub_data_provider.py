from __future__ import annotations

from pathlib import Path

from opengrader.core.roster import read_roster
from opengrader.tui.data.models import QuestionSeed, StudentSummary
from opengrader.tui.data.stub_question_content import StubQuestionContent


class StubDataProvider:
    def __init__(self, default_image_path: str) -> None:
        self._question_content = StubQuestionContent(default_image_path)

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
            )
            for index, row in enumerate(rows)
        ]

    def build_question_seeds(
        self,
        *,
        assignment_id: int | None = None,
        assignment_title: str | None = None,
    ) -> list[QuestionSeed]:
        _ = assignment_id
        return self._question_content.build_question_seeds(
            assignment_title=assignment_title
        )
