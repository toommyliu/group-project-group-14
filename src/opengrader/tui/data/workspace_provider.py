"""Present service records to the TUI and preserve unacknowledged edits."""

from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from pydantic import TypeAdapter

from opengrader.core.grading.edits import GradeEdit, grade_points
from opengrader.core.submissions.page_context import build_page_context
from opengrader.core.submissions.student_file import StudentSubmissionDocument
from opengrader.core.workspace.models import (
    Answer,
    Assignment,
    GradeSaveResult,
    Submission,
    WorkspaceInfo,
)
from opengrader.server.workspace.client import ServiceError, WorkspaceClient
from opengrader.server.workspace.drafts import Draft, DraftJournal
from opengrader.tui.data.models import QuestionSeed, RubricOptionSeed, StudentSummary
from opengrader.tui.statistics import QuestionStatistics, statistics_from_scores


class WorkspaceProvider:
    def __init__(self, client: WorkspaceClient, assignment: Assignment):
        self.client = client
        self.assignment = assignment
        self.assignment_label = assignment.title
        self.course_label = Path(client.connection.root).name
        self.base = f"/assignments/{assignment.id}"
        self.cache = Path(client.connection.root) / ".opengrader/client-cache"
        self.journal = DraftJournal(self.cache.parent / "drafts", assignment.id)
        self.cursor = WorkspaceInfo.model_validate_json(
            client.request("GET", "/api/workspace")
        ).event_cursor
        self.loaded: dict[str, Answer] = {}
        self.conflict: tuple[str, Answer] | None = None
        self._submissions: list[Submission] | None = None

    def close(self) -> None:
        self.journal.close()

    def list_students(self, course_id: int | None) -> list[StudentSummary]:
        if course_id is None:
            return []
        return [
            StudentSummary(
                id=item.id,
                full_name=item.student_name or f"Student {item.id}",
                student_id=item.student_id,
            )
            for item in self._submission_summaries()
        ]

    def invalidate_summaries(self) -> None:
        self._submissions = None

    def _submission_summaries(self) -> list[Submission]:
        if self._submissions is None:
            self._submissions = TypeAdapter(list[Submission]).validate_json(
                self.client.request("GET", self.base + "/submissions")
            )
        return self._submissions

    def saved_scores(self) -> dict[str, dict[str, float | None]]:
        return {
            f"s{item.id}": item.question_scores for item in self._submission_summaries()
        }

    def question_statistics(
        self, possible_points: dict[str, float]
    ) -> dict[str, QuestionStatistics]:
        submissions = self._submission_summaries()
        return {
            question: statistics_from_scores(
                question,
                [
                    score
                    for item in submissions
                    if (score := item.question_scores.get(question)) is not None
                ],
                possible_points=points,
                total_submissions=len(submissions),
            )
            for question, points in possible_points.items()
        }

    def _asset(self, url: str | None) -> str:
        if not url:
            return ""
        name = Path(url).name
        destination = self.cache / name
        if not destination.exists():
            self.cache.mkdir(exist_ok=True)
            content = self.client.request(
                "GET", "/api/assets/" + quote(name), timeout=60
            )
            temporary = destination.with_suffix(f".{uuid4()}.tmp")
            temporary.write_bytes(content)
            temporary.replace(destination)
        return str(destination)

    def build_question_seeds(
        self,
        *,
        assignment_id: int | None = None,
        assignment_title: str | None = None,
        student_item_id: str | None = None,
    ) -> list[QuestionSeed]:
        if student_item_id:
            student = int(student_item_id.removeprefix("s"))
        else:
            students = self.list_students(1)
            if not students:
                return []
            student = students[0].id
        url = f"{self.base}/submissions/{student}"
        answers = TypeAdapter(list[Answer]).validate_json(
            self.client.request("GET", url + "/answers")
        )
        document = TypeAdapter(StudentSubmissionDocument).validate_json(
            self.client.request("GET", url + "/document")
        )
        source_pdf = self._asset(document.source_pdf)
        responses = {q.question_id: q for q in document.questions}
        seeds = []
        for answer in answers:
            key = f"{student}/{answer.question.id}"
            self.loaded[key] = answer
            draft = self.journal.value.answers.get(key)
            points = (
                grade_points(answer.question, draft.edit)
                if draft
                else answer.points_awarded
            )
            response = responses[answer.question.id]
            seeds.append(
                QuestionSeed(
                    question_id=answer.question.id,
                    image_path=self._asset(answer.response_image_url),
                    rubric_options=[
                        RubricOptionSeed(**option.model_dump())
                        for option in answer.question.rubric
                    ],
                    points=answer.question.possible_points,
                    prompt=answer.question.prompt,
                    expected_answers=answer.question.expected_answers,
                    response_text=answer.response_text,
                    points_awarded=points,
                    selected_rubric_labels=tuple(draft.edit.rubric_labels)
                    if draft
                    else answer.rubric_labels,
                    analysis=draft.edit.analysis if draft else answer.analysis,
                    warnings=[
                        "Unsaved work recovered. Save again to retry; conflicting changes require review."
                    ]
                    if draft
                    else [],
                    question_type=answer.question.question_type,
                    source_pdf_path=source_pdf,
                    source_pdf_name=Path(document.source_pdf).name,
                    response_confidence=response.response_confidence,
                    response_source=response.response_source,
                    page_context=build_page_context(
                        response.source_segments, document.extra_pages
                    )
                    if source_pdf
                    else (),
                )
            )
        return seeds

    def _draft(self, student_item_id: str, question_id: str) -> tuple[str, Draft]:
        key = f"{int(student_item_id.removeprefix('s'))}/{question_id}"
        draft = self.journal.value.answers.get(key)
        if draft is None:
            base = self.loaded[key]
            draft = Draft(
                base=base,
                edit=GradeEdit(
                    request_id=uuid4(),
                    expected_revision=base.revision,
                    expected_context_revision=base.context_revision,
                    rubric_labels=list(base.rubric_labels),
                    analysis=base.analysis,
                    score_mode=base.score_mode,
                    points_awarded=base.points_awarded,
                ),
            )
            self.journal.value.answers[key] = draft
        return key, draft

    def save_question_grade(
        self,
        *,
        student_item_id: str,
        question_id: str,
        possible_points: float,
        points_awarded: float | None,
        rubric_labels: tuple[str, ...],
        analysis: str | None = None,
    ) -> None:
        key, draft = self._draft(student_item_id, question_id)
        draft.edit = draft.edit.model_copy(
            update={
                "request_id": uuid4(),
                "rubric_labels": list(rubric_labels),
                "points_awarded": points_awarded,
                "score_mode": "rubric"
                if rubric_labels or points_awarded is None
                else "manual",
                "analysis": draft.edit.analysis if analysis is None else analysis,
            }
        )
        self.journal.write()
        self._flush(key)

    def stage_question_analysis(
        self, *, student_item_id: str, question_id: str, analysis: str
    ) -> None:
        _, draft = self._draft(student_item_id, question_id)
        draft.edit = draft.edit.model_copy(
            update={"analysis": analysis, "request_id": uuid4()}
        )
        self.journal.write()

    def commit_question_analysis(
        self,
        *,
        student_item_id: str,
        question_id: str,
        analysis: str,
        previous_analysis: str,
    ) -> None:
        self.save_question_analysis(
            student_item_id=student_item_id, question_id=question_id, analysis=analysis
        )

    def save_question_analysis(
        self, *, student_item_id: str, question_id: str, analysis: str
    ) -> None:
        self.stage_question_analysis(
            student_item_id=student_item_id, question_id=question_id, analysis=analysis
        )
        key, _ = self._draft(student_item_id, question_id)
        self._flush(key)

    def _flush(self, key: str) -> None:
        draft = self.journal.value.answers[key]
        student, question = key.split("/", 1)
        url = f"{self.base}/submissions/{student}/answers/{quote(question, safe='')}"
        while True:
            pending = draft.pending or draft.edit
            draft.pending = pending
            self.journal.write()
            try:
                result = GradeSaveResult.model_validate_json(
                    self.client.request("PUT", url, pending)
                )
            except ServiceError as exc:
                if exc.current is not None:
                    self.conflict = key, exc.current
                raise
            self.loaded[key] = result.answer
            self.invalidate_summaries()
            fields = {"rubric_labels", "score_mode", "points_awarded", "analysis"}
            if draft.edit.model_dump(include=fields) == pending.model_dump(
                include=fields
            ):
                del self.journal.value.answers[key]
                self.journal.write()
                return
            draft.base = result.answer
            draft.pending = None
            draft.edit = draft.edit.model_copy(
                update={
                    "expected_revision": result.answer.revision,
                    "expected_context_revision": result.answer.context_revision,
                    "request_id": uuid4(),
                }
            )

    def resolve_conflict(self, *, keep_draft: bool) -> None:
        if self.conflict is None:
            return
        key, current = self.conflict
        if keep_draft:
            draft = self.journal.value.answers[key]
            draft.base = current
            draft.pending = None
            draft.edit = draft.edit.model_copy(
                update={
                    "request_id": uuid4(),
                    "expected_revision": current.revision,
                    "expected_context_revision": current.context_revision,
                }
            )
            self.journal.write()
            self._flush(key)
        else:
            del self.journal.value.answers[key]
            self.journal.write()
            self.loaded[key] = current
            self.invalidate_summaries()
        self.conflict = None
