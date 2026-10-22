"""AI draft grades for a whole assignment, run in the background for the browser."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event, Lock
from typing import Literal
from urllib.parse import unquote
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from opengrader.core import app_config
from opengrader.core.cloud_preflight import CloudPreflight
from opengrader.core.grading.automated import JOURNAL_ACTOR_LLM
from opengrader.core.grading.draft_grades import draft_grades_for_groups
from opengrader.core.grading.edits import GradeEdit
from opengrader.core.grading.llm_grader import (
    GRADE_SUGGESTION_SCHEMA,
    RubricChoice,
    confirm_ai_analysis,
    format_ai_analysis,
)
from opengrader.core.llm.transport import redact
from opengrader.core.submissions.answer_groups import (
    AnswerGroup,
    GroupableResponse,
    group_responses,
)
from opengrader.core.submissions.processor import transcribe_crop
from opengrader.core.submissions.transcription import DEFAULT_ESCALATION_FLOOR
from opengrader.core.workspace.models import Answer, GradeSaveResult, Question
from opengrader.core.workspace.store import Workspace, WorkspaceConflict
from opengrader.server.auth import viewer

SaveGrade = Callable[[str, int, str, GradeEdit, str | None], GradeSaveResult]

ACTOR_READER = "automated-grading:reader"


class DraftPlan(BaseModel):
    images: int
    calls: int
    provider: str
    confirm: str | None


class QuestionRun(BaseModel):
    question_id: str
    status: Literal["queued", "reading", "drafting", "done", "skipped", "stopped"] = (
        "queued"
    )
    done: int = 0
    total: int = 0
    read: int = 0
    unreadable: int = 0
    drafted: int = 0
    low_confidence: int = 0
    errors: int = 0
    skipped: int = 0


class DraftJob(BaseModel):
    status: Literal["starting", "running", "done", "failed", "cancelled"] = "starting"
    message: str = ""
    questions: list[QuestionRun]


class AIReview(BaseModel):
    expected_revision: str
    expected_context_revision: str


@dataclass(frozen=True)
class _Batch:
    question: Question
    answers: dict[str, Answer]
    groups: list[AnswerGroup]


def _ungraded_by_question(
    workspace: Workspace, assignment_id: str
) -> dict[str, list[Answer]]:
    by_question: dict[str, list[Answer]] = {}
    for submission in workspace.submissions(assignment_id):
        for answer in workspace.answers(assignment_id, submission.id):
            ungraded = by_question.setdefault(answer.question.id, [])
            if answer.points_awarded is None:
                ungraded.append(answer)
    return by_question


def _ungraded(workspace: Workspace, assignment_id: str, question_id: str) -> list[Answer]:
    return [
        workspace.answer(assignment_id, submission.id, question_id)
        for submission in workspace.submissions(assignment_id)
        if question_id in submission.question_scores
        and submission.question_scores[question_id] is None
    ]


def _unread(answers: list[Answer]) -> list[Answer]:
    return [
        answer
        for answer in answers
        if not answer.response_text.strip() and answer.response_image_url
    ]


def _batches(answers: list[Answer]) -> list[_Batch]:
    by_version: dict[str | None, list[Answer]] = {}
    for answer in answers:
        if answer.response_text.strip():
            by_version.setdefault(answer.version, []).append(answer)
    batches = []
    for written in by_version.values():
        question = written[0].question
        groups = [
            group
            for group in group_responses(
                GroupableResponse(
                    str(answer.submission_id),
                    answer.response_text,
                    question.question_type,
                )
                for answer in written
            )
            if not group.is_blank
        ]
        if groups:
            batches.append(
                _Batch(question, {str(a.submission_id): a for a in written}, groups)
            )
    return batches


def _resolve() -> app_config.ResolvedAI:
    try:
        resolved = app_config.resolve()
    except Exception as exc:  # noqa: BLE001 - a broken config is reported, not raised
        raise HTTPException(409, f"AI isn't set up on this server: {exc}") from exc
    problems = resolved.problems()
    if problems:
        raise HTTPException(409, "AI isn't ready on this server. " + " ".join(problems))
    return resolved


def add_ai_draft_routes(
    api: APIRouter,
    workspace: Workspace,
    save: SaveGrade,
    save_review: Callable[[str, int, str, GradeEdit, Request], GradeSaveResult],
) -> Callable[[], None]:
    """Add the routes and return a function that stops running work."""
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ai-drafts")
    lock = Lock()
    jobs: dict[str, DraftJob] = {}
    cancels: dict[str, Event] = {}

    def plan(request: Request, assignment_id: str):
        resolved = _resolve()
        status = viewer(request)
        if resolved.kind == "cloud" and status.enabled and status.role != "instructor":
            raise HTTPException(
                403,
                f"Only instructors can send student work to {resolved.provider.label}.",
            )
        work = {
            question_id: answers
            for question_id, answers in _ungraded_by_question(
                workspace, assignment_id
            ).items()
            if _batches(answers) or _unread(answers)
        }
        runs = [
            QuestionRun(
                question_id=question_id,
                status="skipped"
                if any(not answer.question.rubric for answer in answers)
                else "queued",
            )
            for question_id, answers in work.items()
        ]
        if not runs:
            raise HTTPException(
                409, "Every written answer in this assignment already has a grade."
            )
        if all(run.status == "skipped" for run in runs):
            raise HTTPException(
                409,
                "None of the questions with ungraded answers have a rubric yet. "
                "Add rubrics before drafting grades with AI.",
            )
        queued = [work[run.question_id] for run in runs if run.status == "queued"]
        return resolved, runs, queued

    def read_answers(
        run: QuestionRun,
        cancel: Event,
        resolved: app_config.ResolvedAI,
        reader: object,
        assignment_id: str,
        unread: list[Answer],
    ) -> None:
        run.status, run.done, run.total = "reading", 0, len(unread)
        for answer in unread:
            if cancel.is_set():
                return
            name = unquote(answer.response_image_url.rsplit("/", 1)[-1])
            text, confidence, _source, _provider = transcribe_crop(
                workspace.asset(f"assets/{name}"),
                question_id=answer.question.id,
                vision_reader=reader,
                progress_callback=lambda _line: None,
                math_latex=resolved.transcribe_math,
                escalate_floor=(
                    DEFAULT_ESCALATION_FLOOR if resolved.escalate_only else None
                ),
            )
            if text.strip() and confidence != 0.0:
                try:
                    workspace.save_transcription(
                        assignment_id,
                        answer.submission_id,
                        answer.question.id,
                        text.strip(),
                        expected_revision=answer.revision,
                        actor=ACTOR_READER,
                    )
                    run.read += 1
                except WorkspaceConflict:
                    run.skipped += 1
            else:
                run.unreadable += 1
            run.done += 1

    def draft_answers(
        run: QuestionRun,
        cancel: Event,
        resolved: app_config.ResolvedAI,
        generate: Callable[[str], str],
        assignment_id: str,
        batches: list[_Batch],
    ) -> None:
        run.status, run.done = "drafting", 0
        run.total = sum(len(batch.groups) for batch in batches)
        for batch in batches:
            question = batch.question
            choices = [
                RubricChoice(o.label, o.points_delta, o.kind, o.selection_mode)
                for o in question.rubric
            ]
            for group in batch.groups:
                if cancel.is_set():
                    return
                result = draft_grades_for_groups(
                    [group],
                    prompt=question.prompt,
                    expected_answers=question.expected_answers,
                    rubric_options=choices,
                    possible_points=question.possible_points,
                    generate=generate,
                    min_confidence=resolved.min_confidence,
                )
                run.errors += result.failed_groups
                run.low_confidence += result.low_confidence_student_count
                for grade in result.applied:
                    analysis = format_ai_analysis(
                        grade.suggestion,
                        model=resolved.text_model,
                        provider=resolved.provider.name,
                    )
                    for member_id in grade.member_ids:
                        answer = batch.answers[member_id]
                        edit = GradeEdit(
                            request_id=uuid4(),
                            expected_revision=answer.revision,
                            expected_context_revision=answer.context_revision,
                            rubric_labels=list(grade.suggestion.rubric_labels),
                            analysis=analysis,
                        )
                        try:
                            save(
                                assignment_id,
                                answer.submission_id,
                                question.id,
                                edit,
                                JOURNAL_ACTOR_LLM,
                            )
                            run.drafted += 1
                        except WorkspaceConflict:
                            run.skipped += 1
                run.done += 1

    def run_job(
        job: DraftJob, cancel: Event, resolved: app_config.ResolvedAI, assignment_id: str
    ) -> None:
        try:
            app_config.ensure_ready(resolved, purpose="text")
            generate = app_config.text_generate(
                resolved, response_format=GRADE_SUGGESTION_SCHEMA
            )
            reader = None
            job.status = "running"
            for run in job.questions:
                if run.status == "skipped":
                    continue
                if cancel.is_set():
                    break
                answers = _ungraded(workspace, assignment_id, run.question_id)
                unread = _unread(answers)
                if unread:
                    if reader is None:
                        app_config.ensure_ready(resolved, purpose="vision")
                        reader = app_config.vision_reader(resolved)
                    read_answers(run, cancel, resolved, reader, assignment_id, unread)
                    answers = _ungraded(workspace, assignment_id, run.question_id)
                draft_answers(
                    run, cancel, resolved, generate, assignment_id, _batches(answers)
                )
                if not cancel.is_set():
                    run.status = "done"
            job.status = "cancelled" if cancel.is_set() else "done"
        except Exception as exc:  # noqa: BLE001 - shown to the grader
            job.status = "failed"
            job.message = redact(str(exc)) or "The AI request failed."
        for run in job.questions:
            if run.status in ("queued", "reading", "drafting"):
                run.status = "stopped"

    @api.get("/assignments/{assignment_id}/ai-drafts")
    def draft_job(assignment_id: str) -> DraftJob | None:
        return jobs.get(assignment_id)

    @api.get("/assignments/{assignment_id}/ai-drafts/plan")
    def draft_plan(request: Request, assignment_id: str) -> DraftPlan:
        resolved, _runs, queued = plan(request, assignment_id)
        images = sum(len(_unread(answers)) for answers in queued)
        calls = images + sum(
            len(batch.groups) for answers in queued for batch in _batches(answers)
        )
        confirm = None
        if resolved.kind == "cloud":
            preflight = CloudPreflight(
                provider_label=resolved.provider.label,
                host=resolved.host,
                counts={"answer-image": images, "graded-text": calls},
                students=len({a.submission_id for answers in queued for a in answers}),
            )
            confirm = "\n\n".join(
                part
                for part in (preflight.statement(), app_config.egress_statement(resolved))
                if part
            )
        return DraftPlan(
            images=images, calls=calls, provider=resolved.provider.label, confirm=confirm
        )

    @api.post("/assignments/{assignment_id}/ai-drafts")
    def start_drafts(request: Request, assignment_id: str) -> DraftJob:
        with lock:
            running = next(
                (
                    (key, job)
                    for key, job in jobs.items()
                    if job.status in ("starting", "running")
                ),
                None,
            )
            if running is not None:
                if running[0] == assignment_id:
                    return running[1]
                raise HTTPException(
                    409,
                    "The AI is drafting grades for another assignment. Wait for "
                    "it to finish or cancel it first.",
                )
            resolved, runs, _queued = plan(request, assignment_id)
            job = DraftJob(questions=runs)
            jobs[assignment_id] = job
            cancels[assignment_id] = Event()
            executor.submit(run_job, job, cancels[assignment_id], resolved, assignment_id)
            return job

    @api.post("/assignments/{assignment_id}/ai-drafts/cancel", status_code=204)
    def cancel_drafts(assignment_id: str) -> Response:
        cancel = cancels.get(assignment_id)
        if cancel is not None:
            cancel.set()
        return Response(status_code=204)

    @api.delete("/assignments/{assignment_id}/ai-drafts", status_code=204)
    def dismiss_drafts(assignment_id: str) -> Response:
        with lock:
            job = jobs.get(assignment_id)
            if job is not None and job.status in ("starting", "running"):
                raise HTTPException(409, "Cancel the AI before dismissing it.")
            jobs.pop(assignment_id, None)
        return Response(status_code=204)

    @api.post(
        "/assignments/{assignment_id}/submissions/{submission_id}/answers/{question_id}/ai-review"
    )
    def confirm_draft(
        request: Request,
        assignment_id: str,
        submission_id: int,
        question_id: str,
        review: AIReview,
    ) -> GradeSaveResult:
        current = workspace.answer(assignment_id, submission_id, question_id)
        return save_review(
            assignment_id,
            submission_id,
            question_id,
            GradeEdit(
                request_id=uuid4(),
                expected_revision=review.expected_revision,
                expected_context_revision=review.expected_context_revision,
                rubric_labels=list(current.rubric_labels),
                analysis=confirm_ai_analysis(current.analysis),
                score_mode=current.score_mode,
                points_awarded=(
                    current.points_awarded if current.score_mode == "manual" else None
                ),
            ),
            request,
        )

    def close() -> None:
        for cancel in cancels.values():
            cancel.set()
        executor.shutdown(cancel_futures=True)

    return close
