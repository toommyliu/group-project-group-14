"""Publish extraction results and their assets into a workspace."""

from dataclasses import replace
import hashlib
from pathlib import Path
import os
from tempfile import TemporaryDirectory
from urllib.parse import quote

from PIL import Image

from opengrader.core.grading.questions import (
    flatten_question_contexts,
    questions_for_submission,
    rubric_options,
)
from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)
from opengrader.core.workspace.store import (
    DOCUMENT,
    Workspace,
    WorkspaceConflict,
    record_event,
)
from opengrader.core.workspace.models import (
    Answer,
    PipelineOutput,
    PipelinePublication,
    Question,
    RubricOption,
)


def publish_pipeline(
    workspace: Workspace, assignment_id: str, output: PipelineOutput
) -> PipelinePublication:
    """Accept completed CLI work on the service host, preserving previous grading on retry."""
    directory = Path(output.submissions_dir)
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError(
            "The pipeline output must be an existing absolute directory on this host."
        )
    directory = directory.resolve()
    paths = sorted(directory.rglob("*.submission.md"))
    if not paths:
        raise ValueError(
            "No extracted submissions were found. Run extraction before publishing."
        )
    assignment = workspace.create_assignment(
        output.qa_markdown, assignment_id=assignment_id
    )
    for path, source_key in _new_files(workspace, assignment.id, directory, paths):
        try:
            _publish_file(workspace, assignment.id, path, source_key)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Could not publish {path.name}: {exc}") from exc
    return PipelinePublication(assignment=assignment, submission_count=len(paths))


def publish_new_files(
    workspace: Workspace, assignment_id: str, directory: Path
) -> tuple[int, list[str]]:
    """Publish submission files added to a class folder since its import.

    Unlike `publish_pipeline`, one unreadable file (perhaps still being
    written) does not stop the others; it is reported and tried next time.
    """
    directory = directory.resolve()
    paths = sorted(directory.rglob("*.submission.md"))
    published = 0
    problems: list[str] = []
    for path, source_key in _new_files(workspace, assignment_id, directory, paths):
        try:
            _publish_file(workspace, assignment_id, path, source_key)
        except (OSError, ValueError) as exc:
            problems.append(f"Could not import {path.name}: {exc}")
            continue
        published += 1
    return published, problems


def _new_files(
    workspace: Workspace, assignment_id: str, directory: Path, paths: list[Path]
) -> list[tuple[Path, str]]:
    with workspace.connect() as db:
        existing = {
            row[0]
            for row in db.execute(
                "SELECT source_key FROM submissions WHERE assignment_id=?",
                (assignment_id,),
            )
        }
    keyed = [
        (path, f"pipeline:{path.relative_to(directory).as_posix()}") for path in paths
    ]
    return [(path, key) for path, key in keyed if key not in existing]


def _publish_file(
    workspace: Workspace, assignment_id: str, path: Path, source_key: str
) -> None:
    document = load_student_submission_markdown(path)
    if not document.questions:
        raise ValueError("The extraction contains no answers. Run extraction again.")
    publish_submission(
        workspace,
        assignment_id,
        document,
        asset_root=path.parent,
        source_key=source_key,
    )


def publish_submission(
    workspace: Workspace,
    assignment_id: str,
    document: StudentSubmissionDocument,
    *,
    asset_root: Path,
    source_key: str,
) -> int:
    """Publish an extraction once; a retried job must never replace saved grades."""
    source = workspace.assignment_source(assignment_id)
    qa = parse_qa_markdown(source)
    assets = workspace.root / ".opengrader" / "assets"
    assets.mkdir(exist_ok=True)
    copied: dict[Path, str] = {}

    def copy_asset(raw: str | None, base: Path) -> str | None:
        if not raw:
            return None
        path = Path(raw).expanduser()
        path = (base / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_file():
            raise ValueError(
                f"An extraction asset is missing: {path}. Run extraction again."
            )
        if path not in copied:
            with TemporaryDirectory(dir=assets) as staging:
                temporary = Path(staging) / "asset"
                digest = hashlib.sha256()
                with path.open("rb") as source, temporary.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                name = digest.hexdigest() + path.suffix.lower()
                destination = assets / name
                if not destination.exists():
                    temporary.replace(destination)
            copied[path] = f"assets/{name}"
        return copied[path]

    version, questions = questions_for_submission(qa, document)
    original = replace(
        document,
        source_pdf=copy_asset(document.source_pdf, asset_root) or "",
        name_image_path=copy_asset(document.name_image_path, asset_root),
        id_image_path=copy_asset(document.id_image_path, asset_root),
        questions=[
            replace(
                question,
                response_image_path=copy_asset(
                    question.response_image_path, asset_root
                ),
                ink_image_path=copy_asset(question.ink_image_path, asset_root),
            )
            for question in document.questions
        ],
    )
    responses = {question.question_id: question for question in original.questions}
    answers = []
    workspace_id = workspace.info().id
    for context in flatten_question_contexts(questions):
        response = responses.get(context.question_id)
        if response is None:
            # Missing extraction must remain visible as an ungraded question.
            response = StudentSubmissionQuestion(
                question_id=context.question_id,
                prompt=context.prompt,
                possible_points=context.points,
                response_image_path=None,
                response_text="",
                question_type=context.question_type,
            )
            responses[context.question_id] = response
        question = Question(
            id=context.question_id,
            prompt=context.prompt,
            possible_points=context.points,
            question_type=context.question_type,
            expected_answers=context.expected_answers,
            rubric=[
                RubricOption(
                    label=option.label,
                    points_delta=option.points_delta,
                    kind=option.kind,
                    selection_mode=option.selection_mode,
                    default_comment=option.default_comment,
                )
                for option in rubric_options(context)
            ],
        )
        width = height = None
        image_url = None
        if response.response_image_path:
            with Image.open(workspace.asset(response.response_image_path)) as image:
                width, height = image.size
            image_url = f"/api/assets/{quote(Path(response.response_image_path).name)}"
        context_revision = hashlib.sha256(
            (question.model_dump_json() + response.response_text).encode()
        ).hexdigest()
        answers.append(
            Answer(
                workspace_id=workspace_id,
                revision="0",
                context_revision=context_revision,
                submission_id=0,
                version=version,
                question=question,
                response_text=response.response_text,
                response_image_url=image_url,
                response_image_width=width,
                response_image_height=height,
                score_mode="rubric"
                if response.rubric_labels or response.points_awarded is None
                else "manual",
                points_awarded=response.points_awarded,
                rubric_labels=response.rubric_labels,
                analysis=response.analysis,
            )
        )
    original = replace(original, questions=list(responses.values()))
    with workspace.connect(write=True) as db:
        row = db.execute(
            "SELECT id FROM submissions WHERE assignment_id=? AND source_key=?",
            (assignment_id, source_key),
        ).fetchone()
        if row is not None:
            return row[0]
        current = db.execute(
            "SELECT source FROM assignments WHERE id=?", (assignment_id,)
        ).fetchone()
        if current is None or current[0] != source:
            raise WorkspaceConflict(
                "The assignment changed during extraction. Run extraction again."
            )
        # IDs are never reused, including when intake deletes a submission.
        submission_id = db.execute(
            "INSERT INTO submission_sequence DEFAULT VALUES RETURNING id"
        ).fetchone()[0]
        db.execute(
            "INSERT INTO submissions VALUES (?, ?, ?, ?, ?)",
            (
                assignment_id,
                submission_id,
                DOCUMENT.dump_json(original).decode(),
                version,
                source_key,
            ),
        )
        db.executemany(
            "INSERT INTO answers VALUES (?, ?, ?, ?)",
            [
                (
                    assignment_id,
                    submission_id,
                    answer.question.id,
                    answer.model_copy(
                        update={"submission_id": submission_id}
                    ).model_dump_json(),
                )
                for answer in answers
            ],
        )
        record_event(db, assignment_id, kind="submission", submission_id=submission_id)
    return submission_id
