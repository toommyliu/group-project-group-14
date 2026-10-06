import logging
import shutil

from opengrader.core.workspace.recut import apply_recut, fail_recut, next_recut, recut_inputs
from opengrader.core.workspace.store import Workspace
from opengrader.server.scan_jobs import ScanCancelled, ScanJobs

logger = logging.getLogger(__name__)


def start_recut(workspace: Workspace, jobs: ScanJobs, assignment_id: str) -> None:
    jobs.start(assignment_id, lambda: _recut(workspace, jobs, assignment_id))


def _recut(workspace: Workspace, jobs: ScanJobs, assignment_id: str) -> None:
    staging = jobs.store.assignments_dir / assignment_id / "recut"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        while True:
            with jobs.lock:
                jobs.check_running(assignment_id)
                recut = next_recut(workspace, assignment_id)
            if recut is None:
                return
            try:
                inputs = recut_inputs(workspace, assignment_id, recut)
                directory = jobs.crop(workspace.asset(recut.source_pdf), inputs, staging)
                with jobs.lock:
                    jobs.check_running(assignment_id)
                    apply_recut(
                        workspace, assignment_id, recut, directory, rematched=bool(inputs.roster)
                    )
                shutil.rmtree(directory, ignore_errors=True)
            except ScanCancelled:
                raise
            except (Exception, SystemExit) as exc:
                logger.exception("Could not crop submission %s again", recut.submission_id)
                fail_recut(
                    workspace,
                    assignment_id,
                    recut,
                    str(exc)
                    if isinstance(exc, ValueError) and str(exc)
                    else "Could not crop this submission. Check its scan, then try again.",
                )
    except ScanCancelled:
        return
