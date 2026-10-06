from dataclasses import dataclass, replace
import json
from pathlib import Path

from PIL import Image

from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.submissions.region_crop import CropInputs, RosterStudent
from opengrader.core.workspace import roster
from opengrader.core.workspace.store import (
    DOCUMENT,
    Workspace,
    asset_url,
    picked_by_hand,
    record_event,
)


@dataclass(frozen=True)
class Recut:
    submission_id: int
    version: str
    questions: tuple[str, ...]
    identity: bool
    source_pdf: str


@dataclass(frozen=True)
class RecutFailure:
    submission_id: int
    student_name: str
    error: str


@dataclass(frozen=True)
class RecutStatus:
    done: int
    remaining: int
    failures: tuple[RecutFailure, ...]


def recut_status(workspace: Workspace, assignment_id: str) -> RecutStatus | None:
    with workspace.connect() as db:
        done, remaining = db.execute(
            """SELECT coalesce(sum(status = 'done'), 0), coalesce(sum(status = 'pending'), 0)
               FROM recuts WHERE assignment_id=?""",
            (assignment_id,),
        ).fetchone()
        failures = db.execute(
            """SELECT recuts.submission_id, coalesce(json_extract(submissions.original, '$.student_name'), ''),
               recuts.error FROM recuts JOIN submissions
               ON submissions.assignment_id=recuts.assignment_id AND submissions.id=recuts.submission_id
               WHERE recuts.assignment_id=? AND recuts.status = 'failed' ORDER BY recuts.submission_id""",
            (assignment_id,),
        ).fetchall()
    if not remaining and not failures:
        return None
    return RecutStatus(done, remaining, tuple(RecutFailure(*row) for row in failures))


def recutting_assignments(workspace: Workspace) -> list[str]:
    with workspace.connect() as db:
        return [
            row[0]
            for row in db.execute("SELECT DISTINCT assignment_id FROM recuts WHERE status = 'pending'")
        ]


def next_recut(workspace: Workspace, assignment_id: str) -> Recut | None:
    with workspace.connect() as db:
        row = db.execute(
            """SELECT recuts.submission_id, coalesce(submissions.version, ''), recuts.questions,
               recuts.identity, json_extract(submissions.original, '$.source_pdf')
               FROM recuts JOIN submissions
               ON submissions.assignment_id=recuts.assignment_id AND submissions.id=recuts.submission_id
               WHERE recuts.assignment_id=? AND recuts.status = 'pending'
               ORDER BY recuts.submission_id LIMIT 1""",
            (assignment_id,),
        ).fetchone()
    if row is None:
        return None
    submission_id, version, questions, identity, source_pdf = row
    return Recut(
        submission_id=submission_id,
        version=version,
        questions=tuple(json.loads(questions)),
        identity=bool(identity),
        source_pdf=source_pdf or "",
    )


def recut_inputs(workspace: Workspace, assignment_id: str, recut: Recut) -> CropInputs:
    if not recut.source_pdf:
        raise ValueError("This submission has no scanned PDF.")
    stored = workspace.outlines(assignment_id).get(recut.version)
    if stored is None:
        raise ValueError("This submission's version has no outline.")
    regions = stored.regions
    wanted = set(recut.questions)
    return CropInputs(
        regions=replace(
            regions,
            questions=tuple(
                question for question in regions.questions if question.question_id in wanted
            ),
            name_field=regions.name_field if recut.identity else None,
            student_id_field=regions.student_id_field if recut.identity else None,
        ),
        title="",
        version=recut.version or None,
        roster=tuple(
            RosterStudent(student.sid, student.name) for student in roster.students(workspace)
        )
        if recut.identity
        else (),
    )


def apply_recut(
    workspace: Workspace,
    assignment_id: str,
    recut: Recut,
    directory: Path,
    *,
    rematched: bool,
) -> None:
    cut = load_student_submission_markdown(directory / f"{directory.name}.submission.md")

    def stored(relative: str | None) -> str | None:
        return workspace.add_asset(directory / relative) if relative else None

    crops = {question.question_id: question for question in cut.questions}
    images = {}
    for question_id in recut.questions:
        crop = crops.get(question_id)
        path = stored(crop.response_image_path) if crop else None
        size = (None, None)
        if path:
            with Image.open(workspace.asset(path)) as image:
                size = image.size
        images[question_id] = (path, crop.source_segments if crop else (), size)
    name_image = stored(cut.name_image_path)
    id_image = stored(cut.id_image_path)
    key = (assignment_id, recut.submission_id)
    with workspace.connect(write=True) as db:
        row = db.execute(
            "SELECT original FROM submissions WHERE assignment_id=? AND id=?", key
        ).fetchone()
        if row is None:
            return
        original = DOCUMENT.validate_json(row[0])
        original = replace(
            original,
            questions=[
                replace(
                    question,
                    response_image_path=images[question.question_id][0],
                    source_segments=images[question.question_id][1],
                )
                if question.question_id in images
                else question
                for question in original.questions
            ],
        )
        if recut.identity:
            original = replace(original, name_image_path=name_image, id_image_path=id_image)
            if (
                rematched
                and (name_image or id_image)
                and not picked_by_hand(original.student_name, original.name_confidence)
            ):
                original = replace(
                    original,
                    student_name=cut.student_name,
                    student_id=cut.student_id,
                    name_confidence=cut.name_confidence,
                    id_candidates=cut.id_candidates,
                )
        db.execute(
            "UPDATE submissions SET original=? WHERE assignment_id=? AND id=?",
            (DOCUMENT.dump_json(original).decode(), *key),
        )
        for question_id, (path, _, (width, height)) in images.items():
            db.execute(
                """UPDATE answers SET value=json_set(value, '$.response_image_url', ?,
                   '$.response_image_width', ?, '$.response_image_height', ?)
                   WHERE assignment_id=? AND submission_id=? AND question_id=?""",
                (asset_url(path), width, height, *key, question_id),
            )
        db.execute(
            """UPDATE feedback_jobs SET generation=generation+1, status='pending', error=''
               WHERE assignment_id=? AND submission_id=?""",
            key,
        )
        db.execute(
            "UPDATE recuts SET status='done' WHERE assignment_id=? AND submission_id=?", key
        )
        record_event(db, assignment_id, kind="submission", submission_id=recut.submission_id)


def fail_recut(workspace: Workspace, assignment_id: str, recut: Recut, error: str) -> None:
    with workspace.connect(write=True) as db:
        db.execute(
            "UPDATE recuts SET status='failed', error=? WHERE assignment_id=? AND submission_id=?",
            (error, assignment_id, recut.submission_id),
        )
        record_event(db, assignment_id, kind="submission", submission_id=recut.submission_id)


def retry_recuts(workspace: Workspace, assignment_id: str) -> None:
    with workspace.connect(write=True) as db:
        db.execute(
            "UPDATE recuts SET status='pending', error=NULL WHERE assignment_id=? AND status='failed'",
            (assignment_id,),
        )
        record_event(db, assignment_id, kind="assignment")
