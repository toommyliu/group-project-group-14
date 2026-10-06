"""Say who wrote each submission: typed by a grader or matched against the roster."""

from typing import Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from opengrader.core.submissions.region_crop import RosterStudent, identify
from opengrader.core.workspace import roster
from opengrader.core.workspace.models import Submission
from opengrader.core.workspace.store import Workspace
from opengrader.server.scan_jobs import ScanJobs


class StudentUpdate(BaseModel):
    student_name: str = Field(max_length=200)
    student_id: str = Field(max_length=100)


class RosterMatches(BaseModel):
    matched: int
    ambiguous: int
    unmatched: int


def add_student_routes(
    api: APIRouter, workspace: Workspace, jobs: ScanJobs, require_assignment: Callable[[str], None]
) -> None:
    @api.put("/assignments/{assignment_id}/submissions/{submission_id}/student")
    def set_student(assignment_id: str, submission_id: int, request: StudentUpdate) -> Submission:
        require_assignment(assignment_id)
        return workspace.set_student(
            assignment_id,
            submission_id,
            " ".join(request.student_name.split()),
            request.student_id.strip(),
        )

    @api.get(
        "/assignments/{assignment_id}/submissions/{submission_id}/pdf",
        response_class=FileResponse,
        responses={200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}},
    )
    def submission_pdf(assignment_id: str, submission_id: int) -> FileResponse:
        require_assignment(assignment_id)
        document = workspace.document(assignment_id, submission_id)
        if not document.source_pdf:
            raise HTTPException(404, "This submission has no scanned PDF.")
        name = " ".join(document.student_name.split()) or f"Submission {submission_id}"
        return FileResponse(
            workspace.asset(document.source_pdf),
            media_type="application/pdf",
            filename=f"{name}.pdf",
            content_disposition_type="inline",
        )

    @api.post("/assignments/{assignment_id}/submissions/match")
    def match_roster(assignment_id: str) -> RosterMatches:
        require_assignment(assignment_id)
        students = tuple(RosterStudent(student.sid, student.name) for student in roster.students(workspace))
        if not students:
            raise HTTPException(409, "Add students to the roster on the People page first.")
        counts = {"matched": 0, "ambiguous": 0, "unmatched": 0}
        for submission in workspace.submissions(assignment_id):
            if submission.student_name:
                continue
            document = workspace.document(assignment_id, submission.id)
            crops = [
                workspace.asset(path) if path else None
                for path in (document.name_image_path, document.id_image_path)
            ]
            if not any(crops):
                counts["unmatched"] += 1
                continue
            found = jobs.processing.submit(identify, *crops, students).result()
            kind = "matched" if found.student_name else "ambiguous" if found.candidates else "unmatched"
            if kind != "unmatched" and not workspace.suggest_student(
                assignment_id,
                submission.id,
                found.student_name,
                found.student_id,
                found.match_percent,
                list(found.candidates),
            ):
                continue
            counts[kind] += 1
        return RosterMatches(**counts)
