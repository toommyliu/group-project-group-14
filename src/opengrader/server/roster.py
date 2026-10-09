from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from opengrader.core.workspace import folder_roster, roster
from opengrader.core.workspace.folder_session import folder_lock, load_folder_session
from opengrader.core.workspace.models import (
    RosterImport,
    Student,
    StudentChange,
    StudentDetails,
    StudentEdit,
)
from opengrader.core.workspace.store import Workspace
from opengrader.server.auth import Viewer

_CSV_BODY = {
    "requestBody": {
        "required": True,
        "content": {"text/csv": {"schema": {"type": "string", "format": "binary"}}},
    }
}


def instructor(status: Viewer) -> None:
    if status.enabled and status.role != "instructor":
        raise HTTPException(403, "Only instructors can manage the roster.")


async def _csv(request: Request) -> bytes:
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > roster.MAX_ROSTER_BYTES:
            break
    return bytes(data)


def add_roster_routes(api: APIRouter, workspace: Workspace) -> None:
    router = APIRouter(prefix="/roster", dependencies=[Depends(instructor)])

    @router.get("")
    def list_students() -> list[Student]:
        return roster.students(workspace)

    # In a folder session (a class folder open from classic mode) the roster
    # is a file in that folder. Each change below is written into it first
    # (`core/workspace/folder_roster`); the lock keeps a sync from reading
    # the file between that write and the roster catching up.

    @router.post("/students", status_code=201)
    def add_student(details: StudentDetails) -> Student:
        with folder_lock(workspace.root):
            session = load_folder_session(workspace.root)
            if session is None:
                return roster.add_student(workspace, details)
            return folder_roster.add_student(workspace, session.assignment_id, details)

    @router.put("/students/{student_id}")
    def edit_student(student_id: str, edit: StudentEdit) -> StudentChange:
        with folder_lock(workspace.root):
            session = load_folder_session(workspace.root)
            if session is None:
                return roster.update_student(
                    workspace, student_id, edit, edit.expected_revision
                )
            return folder_roster.update_student(
                workspace, session.assignment_id, student_id, edit
            )

    @router.delete("/students/{student_id}", status_code=204)
    def remove_student(student_id: str) -> Response:
        with folder_lock(workspace.root):
            session = load_folder_session(workspace.root)
            if session is None:
                roster.remove_student(workspace, student_id)
            else:
                folder_roster.remove_student(
                    workspace, session.assignment_id, student_id
                )
        return Response(status_code=204)

    @router.post("/import/preview", openapi_extra=_CSV_BODY)
    async def preview_import(request: Request) -> RosterImport:
        data = await _csv(request)
        return await run_in_threadpool(roster.import_roster, workspace, data, dry_run=True)

    @router.post("/import", openapi_extra=_CSV_BODY)
    async def import_roster(request: Request, expected: str) -> RosterImport:
        data = await _csv(request)
        result = await run_in_threadpool(
            roster.import_roster, workspace, data, expected_fingerprint=expected
        )
        if result.problems:
            raise HTTPException(422, "Fix the problems in this file, then import it again.")
        return result

    api.include_router(router)
