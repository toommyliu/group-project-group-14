"""Join two submissions the splitter cut apart, in a workspace.

The class folder's files are joined and read again exactly as for a folder
(`core/submissions/merge_split.py`). What differs is where the grades live:
in the workspace, written only by its service. So the grades are read
through the service first, the service then drops both submissions and
publishes the joined file (a new submission, with a new ID — the old
submissions' history stays under their IDs), and the grades still valid are
saved onto it through the same route a grader's own save takes, so each is
checked against the rubric and recorded in the history.

Read-only lookups (which file a submission came from) read the workspace
database directly, as write-back does; nothing here writes it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from pydantic import TypeAdapter

from opengrader.core.grading.edits import GradeEdit
from opengrader.core.submissions.merge_split import (
    AnswerSeen,
    Grade,
    MergeUnavailable,
    Piece,
    finish_merge,
    grades_to_carry,
    join_and_reread,
    Carried,
    JoinResult,
    pending_carried,
    readings_to_carry,
    recorded_piece,
    save_carried,
)
from opengrader.core.workspace.models import (
    Answer,
    GradeSaveResult,
    ReplacedSubmissions,
    ReplaceSubmissions,
)
from opengrader.server.workspace.client import WorkspaceClient

_PIPELINE = "pipeline:"


def workspace_pieces(root: Path, assignment_id: str) -> tuple[Path, dict[int, Piece]]:
    """The class folder's submissions folder, and the piece behind each of
    the assignment's submissions that was read from a split PDF."""
    from opengrader.core.workspace.store import Workspace
    from opengrader.core.workspace.writeback import locate_submissions_dir

    workspace = Workspace(root)
    folder = locate_submissions_dir(workspace, assignment_id)
    if folder is None:
        raise MergeUnavailable(
            "This assignment's class folder could not be found (it may have "
            "moved), so its scans cannot be joined from here."
        )
    with workspace.connect() as db:
        rows = db.execute(
            "SELECT id, source_key FROM submissions WHERE assignment_id=?",
            (assignment_id,),
        ).fetchall()
    found: dict[int, Piece] = {}
    for submission_id, key in rows:
        if str(key).startswith(_PIPELINE):
            piece = recorded_piece(folder, str(key).removeprefix(_PIPELINE))
            if piece is not None:
                found[submission_id] = piece
    return folder, found


def neighbours_in_workspace(
    root: Path, assignment_id: str, submission_id: int
) -> tuple[tuple[int, Piece] | None, Piece, tuple[int, Piece] | None]:
    """(before, this, after): the submissions scanned either side of this one."""
    _folder, found = workspace_pieces(root, assignment_id)
    here = found.get(submission_id)
    if here is None:
        raise MergeUnavailable(
            "This submission was not read from a split scan in a class folder, "
            "so there is nothing to join it with."
        )
    same_scan = sorted(
        ((sid, piece) for sid, piece in found.items() if piece.split_pdf.parent == here.split_pdf.parent),
        key=lambda item: item[1].split_pdf.name,
    )
    index = [sid for sid, _ in same_scan].index(submission_id)
    before = same_scan[index - 1] if index > 0 else None
    after = same_scan[index + 1] if index + 1 < len(same_scan) else None
    return before, here, after


def _answers(client: WorkspaceClient, assignment_id: str, submission_id: int) -> list[AnswerSeen]:
    answers = TypeAdapter(list[Answer]).validate_json(
        client.request("GET", f"/api/assignments/{assignment_id}/submissions/{submission_id}/answers")
    )
    return [
        AnswerSeen(
            question_id=answer.question.id,
            had_answer=bool(answer.response_image_url),
            grade=Grade(
                answer.question.id,
                answer.points_awarded,
                tuple(answer.rubric_labels),
                answer.analysis,
            )
            if answer.points_awarded is not None or answer.rubric_labels or answer.analysis.strip()
            else None,
        )
        for answer in answers
    ]


def merge_in_workspace(
    client: WorkspaceClient,
    assignment_id: str,
    first_id: int,
    second_id: int,
    *,
    status: Callable[[str], None] | None = None,
    prompt_cloud_send: bool = True,
    send_to_cloud_confirmed: bool = False,
) -> tuple[int, JoinResult]:
    """Join submission `second_id` onto `first_id` (scanned just before it).
    Returns the joined submission's new ID and what was carried over.

    Grades come from the service; corrected readings from the class
    folder's files, which are written back before the joined file is
    published, so it arrives with them."""
    folder, found = workspace_pieces(Path(client.connection.root), assignment_id)
    first, second = found.get(first_id), found.get(second_id)
    if first is None or second is None:
        raise MergeUnavailable("Both submissions must come from the same split scan.")
    carried = pending_carried(folder, first.stem)
    if carried is None:
        carried = Carried(
            grades_to_carry(
                _answers(client, assignment_id, first_id),
                _answers(client, assignment_id, second_id),
            ),
            readings_to_carry(first.submission, second.submission),
        )
        save_carried(folder, first.stem, carried)
    path, readings = join_and_reread(
        folder,
        first,
        second,
        readings=carried.readings,
        status=status,
        prompt_cloud_send=prompt_cloud_send,
        send_to_cloud_confirmed=send_to_cloud_confirmed,
    )
    keys = [_PIPELINE + path.relative_to(folder).as_posix() for path in (first.submission, second.submission)]
    replaced = ReplacedSubmissions.model_validate_json(
        client.request(
            "POST",
            f"/api/assignments/{assignment_id}/submissions/replace",
            ReplaceSubmissions(source_keys=keys),
            timeout=120,
        )
    )
    new_id = replaced.submission_ids.get(keys[0])
    if new_id is None:
        problems = "; ".join(replaced.problems) or "the joined file could not be published"
        raise MergeUnavailable(f"The joined submission was read but not published: {problems}.")
    grades = 0
    for grade in carried.grades.values():
        url = f"/api/assignments/{assignment_id}/submissions/{new_id}/answers/{quote(grade.question_id, safe='')}"
        try:
            current = Answer.model_validate_json(client.request("GET", url))
        except Exception:  # noqa: BLE001 - a question the joined reading no longer has
            continue
        manual = not grade.rubric_labels and grade.points_awarded is not None
        GradeSaveResult.model_validate_json(
            client.request(
                "PUT",
                url,
                GradeEdit(
                    request_id=uuid4(),
                    expected_revision=current.revision,
                    expected_context_revision=current.context_revision,
                    rubric_labels=list(grade.rubric_labels),
                    analysis=grade.analysis,
                    score_mode="manual" if manual else "rubric",
                    points_awarded=grade.points_awarded if manual else None,
                ),
            )
        )
        grades += 1
    finish_merge(folder, first.stem)
    return new_id, JoinResult(path, grades, readings)
