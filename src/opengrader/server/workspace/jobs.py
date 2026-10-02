"""Regenerate feedback after a committed grade without holding its transaction."""

from pathlib import Path
from threading import Event, Thread

import structlog

from opengrader.core.workspace.store import Workspace, record_event
from opengrader.core.workspace.models import FeedbackJob

logger = structlog.stdlib.get_logger(__name__)


class FeedbackJobs:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.stopped = Event()
        self.thread = Thread(target=self._run, name="workspace-feedback", daemon=True)

    def start(self) -> None:
        with self.workspace.connect(write=True) as db:
            db.execute(
                "UPDATE feedback_jobs SET status='pending' WHERE status='running'"
            )
        self.thread.start()

    def close(self) -> None:
        self.stopped.set()
        self.thread.join()

    def _run(self) -> None:
        while not self.stopped.is_set():
            try:
                for job in self.workspace.jobs():
                    if self.stopped.is_set():
                        return
                    if job.status == "pending":
                        self.process(job)
            except Exception:
                logger.exception("feedback.check_failed")
            self.stopped.wait(0.5)

    def process(self, job: FeedbackJob) -> None:
        workspace = self.workspace
        key = (job.assignment_id, job.submission_id, job.generation)
        with workspace.connect(write=True) as db:
            changed = db.execute(
                "UPDATE feedback_jobs SET status='running' WHERE assignment_id=? AND submission_id=? AND generation=? AND status='pending'",
                key,
            ).rowcount
        if not changed:
            return
        artifact = None
        error = ""
        try:
            artifact = (
                self._render(job).relative_to(workspace.root / ".opengrader").as_posix()
            )
        except Exception as exc:
            logger.exception("feedback.failed")
            error = str(exc)
        with workspace.connect(write=True) as db:
            # An older render cannot mark a newer grade's feedback ready.
            db.execute(
                "UPDATE feedback_jobs SET status=?, error=?, artifact=? WHERE assignment_id=? AND submission_id=? AND generation=?",
                ("failed" if error else "ready", error, artifact, *key),
            )
            record_event(
                db,
                job.assignment_id,
                kind="feedback",
                submission_id=job.submission_id,
                revision=str(job.generation),
            )

    def _render(self, job: FeedbackJob) -> Path:
        from opengrader.core.pdf.annotations import (
            sync_feedback_summary_page,
            sync_question_comment_annotation,
            sync_total_score_annotation,
        )
        from opengrader.core.pdf.feedback import build_feedback_summary

        document = self.workspace.document(job.assignment_id, job.submission_id)
        if not document.source_pdf:
            raise ValueError("This submission has no source PDF. Its grade is saved.")
        source = self.workspace.asset(document.source_pdf)
        output = (
            self.workspace.root
            / ".opengrader"
            / "feedback"
            / job.assignment_id
            / str(job.submission_id)
            / f"{job.generation}.pdf"
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        sync_total_score_annotation(
            source_pdf_path=source,
            annotated_pdf_path=output,
            total_points=sum(q.points_awarded or 0 for q in document.questions),
            possible_points=sum(q.possible_points or 0 for q in document.questions),
        )
        for question in document.questions:
            if question.source_segments:
                sync_question_comment_annotation(
                    source_pdf_path=source,
                    annotated_pdf_path=output,
                    question_id=question.question_id,
                    comment=question.analysis,
                    points_awarded=question.points_awarded,
                    possible_points=question.possible_points,
                    source_segments=question.source_segments,
                )
        sync_feedback_summary_page(
            source_pdf_path=source,
            annotated_pdf_path=output,
            summary=build_feedback_summary(document),
            force=True,
        )
        return output
