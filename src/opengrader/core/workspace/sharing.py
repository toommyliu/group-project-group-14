"""A workspace shared by several accounts on one computer.

On a machine several people reach over SSH, each with an account of their
own, the workspace belongs to the account that runs its service — the
professor. Nobody else writes its files: other accounts join that service
(`server/workspace/client.join_workspace`) and every change goes through it,
so the professor's account is the only writer and file permissions never get
in the way.

Roles: the owner is always an instructor; anyone else is a grader unless the
owner makes them an instructor (`opengrader share --role`). A grader grades:
they may give a grade where there is none and change their own, but a grade
someone else gave is changed only by asking — the change is kept as a
request an instructor approves or dismisses (`Q` in the app). A grader the
owner trusts may be allowed to change others' grades directly.

Who is asking is decided by the service (`server/peer.py`), not claimed by
the app, wherever the operating system can tell.
"""

from __future__ import annotations

import json
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from opengrader.core.grading.edits import GradeEdit
from opengrader.core.workspace.models import AccountRole, ChangeRequest, StaffRole, Viewer
from opengrader.core.workspace.store import Workspace, WorkspaceNotFound


def sharing_group(workspace: Workspace) -> str | None:
    with workspace.connect() as db:
        row = db.execute("SELECT group_name FROM sharing WHERE id = 1").fetchone()
    return row[0] if row else None


def set_sharing_group(workspace: Workspace, group: str | None) -> None:
    with workspace.connect(write=True) as db:
        if group:
            db.execute(
                "INSERT INTO sharing (id, group_name) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET group_name=excluded.group_name",
                (group,),
            )
        else:
            db.execute("DELETE FROM sharing")


def accounts(workspace: Workspace) -> list[AccountRole]:
    with workspace.connect() as db:
        return [
            AccountRole(login=login, role=role, may_change_others=bool(may))
            for login, role, may in db.execute(
                "SELECT login, role, may_change_others FROM accounts ORDER BY login"
            )
        ]


def set_account(workspace: Workspace, account: AccountRole) -> None:
    with workspace.connect(write=True) as db:
        db.execute(
            "INSERT INTO accounts (login, role, may_change_others) VALUES (?, ?, ?) "
            "ON CONFLICT(login) DO UPDATE SET role=excluded.role, "
            "may_change_others=excluded.may_change_others",
            (account.login, account.role, int(account.may_change_others)),
        )


def viewer(workspace: Workspace, login: str, owner: str) -> Viewer:
    if login == owner:
        return Viewer(login=login, role="instructor", owner=owner, may_change_others=True)
    known = next((a for a in accounts(workspace) if a.login == login), None)
    role: StaffRole = known.role if known else "grader"
    return Viewer(
        login=login,
        role=role,
        owner=owner,
        may_change_others=role == "instructor" or bool(known and known.may_change_others),
    )


def last_grader(workspace: Workspace, assignment_id: str, submission_id: int, question_id: str) -> str | None:
    """Who saved this answer last, from the history; None when nobody has
    since it was published (a grade read from the files, an auto-grade)."""
    with workspace.connect() as db:
        row = db.execute(
            "SELECT actor FROM history WHERE assignment_id=? AND submission_id=? "
            "AND question_id=? ORDER BY id DESC LIMIT 1",
            (assignment_id, submission_id, question_id),
        ).fetchone()
    return row[0] if row else None


def change_needs_approval(
    workspace: Workspace, assignment_id: str, submission_id: int, question_id: str, who: Viewer
) -> str | None:
    """The person whose grade this save would replace, when `who` may not
    replace it; None when the save may go ahead."""
    if who.may_change_others:
        return None
    answer = workspace.answer(assignment_id, submission_id, question_id)
    graded = answer.points_awarded is not None or bool(answer.rubric_labels) or bool(answer.analysis.strip())
    if not graded:
        return None
    by = last_grader(workspace, assignment_id, submission_id, question_id)
    if by == who.login:
        return None
    # A grade nobody has saved here came with the files: read from them, or
    # given by the auto-grader. It is the instructor's until one changes it.
    return by or who.owner


def request_change(
    workspace: Workspace,
    assignment_id: str,
    submission_id: int,
    question_id: str,
    edit: GradeEdit,
    *,
    requested_by: str,
    graded_by: str,
) -> int:
    """Keep a grader's change as a request; a newer one from the same grader
    for the same answer replaces theirs instead of piling up."""
    body = edit.model_dump_json(include={"rubric_labels", "points_awarded", "score_mode", "analysis"})
    now = datetime.now(timezone.utc).isoformat()
    with workspace.connect(write=True) as db:
        row = db.execute(
            "SELECT id FROM change_requests WHERE assignment_id=? AND submission_id=? "
            "AND question_id=? AND requested_by=? AND status='pending'",
            (assignment_id, submission_id, question_id, requested_by),
        ).fetchone()
        if row is not None:
            db.execute(
                "UPDATE change_requests SET edit=?, created=?, graded_by=? WHERE id=?",
                (body, now, graded_by, row[0]),
            )
            request_id = row[0]
        else:
            request_id = db.execute(
                "INSERT INTO change_requests (assignment_id, submission_id, question_id, "
                "requested_by, graded_by, edit, created, status) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending') "
                "RETURNING id",
                (assignment_id, submission_id, question_id, requested_by, graded_by, body, now),
            ).fetchone()[0]
    return request_id


def change_requests(
    workspace: Workspace, assignment_id: str, *, status: str | None = "pending"
) -> list[ChangeRequest]:
    with workspace.connect() as db:
        rows = db.execute(
            "SELECT * FROM change_requests WHERE assignment_id=? "
            + ("AND status=? " if status else "")
            + "ORDER BY id",
            (assignment_id, status) if status else (assignment_id,),
        ).fetchall()
    found: list[ChangeRequest] = []
    for row in rows:
        edit = json.loads(row["edit"])
        try:
            current = workspace.answer(assignment_id, row["submission_id"], row["question_id"])
        except WorkspaceNotFound:
            continue
        found.append(
            ChangeRequest(
                id=row["id"],
                assignment_id=assignment_id,
                submission_id=row["submission_id"],
                question_id=row["question_id"],
                requested_by=row["requested_by"],
                graded_by=row["graded_by"],
                created=row["created"],
                status=row["status"],
                rubric_labels=list(edit.get("rubric_labels", [])),
                points_awarded=edit.get("points_awarded"),
                score_mode=edit.get("score_mode", "rubric"),
                analysis=edit.get("analysis", ""),
                current_rubric_labels=list(current.rubric_labels),
                current_points=current.points_awarded,
                decided_by=row["decided_by"],
            )
        )
    return found


def decide_change(
    workspace: Workspace, assignment_id: str, request_id: int, *, approve: bool, by: str
) -> ChangeRequest:
    """Approve (the grade is saved, recorded under the instructor who
    approved it) or dismiss a pending request."""
    pending = next(
        (r for r in change_requests(workspace, assignment_id) if r.id == request_id), None
    )
    if pending is None:
        raise WorkspaceNotFound("That request is no longer waiting.")
    if approve:
        current = workspace.answer(assignment_id, pending.submission_id, pending.question_id)
        workspace.save_grade(
            assignment_id,
            pending.submission_id,
            pending.question_id,
            GradeEdit(
                request_id=uuid4(),
                expected_revision=current.revision,
                expected_context_revision=current.context_revision,
                rubric_labels=pending.rubric_labels,
                points_awarded=pending.points_awarded,
                score_mode=pending.score_mode,
                analysis=pending.analysis,
            ),
            actor=by,
        )
    with workspace.connect(write=True) as db:
        db.execute(
            "UPDATE change_requests SET status=?, decided_by=?, decided=? WHERE id=? AND status='pending'",
            ("approved" if approve else "dismissed", by, datetime.now(timezone.utc).isoformat(), request_id),
        )
    return next(r for r in change_requests(workspace, assignment_id, status=None) if r.id == request_id)


def sharing_problems(root: Path, group: str) -> list[str]:
    """Why the group's members could not join yet, as things to fix; empty
    when they can. Checked as the owner, before anything is shared."""
    import os
    import stat

    try:
        import grp
    except ImportError:
        return ["Sharing a workspace with other accounts needs Linux or macOS."]
    try:
        gid = grp.getgrnam(group).gr_gid
    except KeyError:
        return [f"There is no group named {group} on this computer (sudo groupadd {group})."]
    from opengrader.core.folder_access import current_account

    me = current_account()
    if gid != os.getgid() and gid not in os.getgroups():
        return [
            f"{me} is not in the group {group}, so it cannot hand the group the "
            f"note that lets them join: sudo usermod -aG {group} {me}, then log "
            "in again and restart Opengrader (opengrader stop)."
        ]
    problems: list[str] = []
    # To read the note, members must be able to pass through every folder
    # down to .opengrader. Nothing else in the folder needs to be open to them.
    for folder in (root, *[p for p in root.parents if p != Path(p.anchor)], root / ".opengrader"):
        try:
            info = folder.stat()
        except OSError:
            continue
        reachable = info.st_mode & stat.S_IXOTH or (info.st_gid == gid and info.st_mode & stat.S_IXGRP)
        if not reachable:
            if folder in (root, root / ".opengrader"):
                fix = f"chgrp {group} '{folder}' && chmod g+x '{folder}'"
            else:
                # A folder above the class folder, often a home folder: better
                # to move the class folder than to open someone's home.
                fix = (
                    "keep shared class folders where every grader can reach them "
                    f"(for example /srv/<course>), or chmod o+x '{folder}'"
                )
            problems.append(f"Members of {group} cannot reach {folder}: {fix}.")
            break
    return problems
