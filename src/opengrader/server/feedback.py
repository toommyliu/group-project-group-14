"""Bound feedback rendering across all open workspaces."""

import logging
from threading import Event, Lock, Thread

from opengrader.server.workspace.jobs import FeedbackJobs

logger = logging.getLogger(__name__)


class FeedbackScheduler:
    def __init__(self):
        self.jobs: list[FeedbackJobs] = []
        self.lock = Lock()
        self.processing = Lock()
        self.stopped = Event()
        self.thread = Thread(target=self._run, name="server-feedback", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def add(self, jobs: FeedbackJobs) -> None:
        with jobs.workspace.connect(write=True) as db:
            db.execute(
                "UPDATE feedback_jobs SET status='pending' WHERE status='running'"
            )
        with self.lock:
            self.jobs.append(jobs)

    def remove(self, jobs: FeedbackJobs) -> None:
        with self.lock:
            if jobs in self.jobs:
                self.jobs.remove(jobs)
        with self.processing:
            pass

    def close(self) -> None:
        self.stopped.set()
        self.thread.join()

    def _run(self) -> None:
        while not self.stopped.is_set():
            with self.lock:
                workspaces = list(self.jobs)
            for jobs in workspaces:
                if self.stopped.is_set():
                    return
                with self.processing:
                    with self.lock:
                        if jobs not in self.jobs:
                            continue
                    try:
                        # One job per workspace per pass keeps a large class from
                        # starving another workspace's feedback.
                        pending = next(
                            (
                                job
                                for job in jobs.workspace.jobs()
                                if job.status == "pending"
                            ),
                            None,
                        )
                        if pending:
                            jobs.process(pending)
                    except Exception:
                        logger.exception(
                            "Could not process feedback in %s", jobs.workspace.root
                        )
            self.stopped.wait(0.5)
