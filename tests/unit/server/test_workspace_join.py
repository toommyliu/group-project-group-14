"""Joining two pieces of one paper in a workspace: the class folder's files
are joined and read again, the service republishes the joined file, and the
grades still valid are saved onto it."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from opengrader.cli import submission_extractor
from opengrader.core.grading.edits import GradeEdit
from opengrader.core.pages.split import SIDECAR_SUFFIX
from opengrader.core.submissions.manifest import ExtractionRecord, append_record
from opengrader.core.submissions.merge_split import record_extract_command
from opengrader.core.workspace.intake import publish_pipeline
from opengrader.core.workspace.models import AssignmentInputs, PipelineOutput
from opengrader.core.workspace.store import Workspace
from opengrader.server.transport import ServiceError
from opengrader.server.workspace.app import create_app
from opengrader.server.workspace.merge import merge_in_workspace, neighbours_in_workspace

RUBRIC = """  - **Rubric**
    - Correct (+0)
    - Partially Correct (-4)
    - Incorrect (-10)
"""
QA = "---\ntitle: Split quiz\n---\n" + "".join(
    f"- (10 points) Question {n}.\n  - **Answer**\n    - {n}\n{RUBRIC}" for n in (1, 2, 3)
)
BLOCK = """- {qid}:
  - Question type: Free Response
  - Prompt: (10 points) Question {n}.
{crop}  - Response text: {text}
  - Possible points: 10
  - Points:
  - Rubric:
  - Analysis:
"""


def _submission(folder: Path, stem: str, pages: dict[str, int | None]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    blocks = []
    for n, (qid, page) in enumerate(pages.items(), start=1):
        crop = ""
        if page is not None:
            (folder / "responses").mkdir(exist_ok=True)
            Image.new("RGB", (40, 20), "white").save(folder / "responses" / f"{qid}.png")
            crop = (
                f"  - Response image: ![{qid} response](responses/{qid}.png)\n"
                f"  - Source segments: [{{\"page\":{page},\"bbox\":[0.1,0.1,0.8,0.2]}}]\n"
            )
        blocks.append(BLOCK.format(qid=qid, n=n, crop=crop, text="" if page is None else f"{n}"))
    (folder / f"{stem}.submission.md").write_text(
        f"---\nstudent_name: \"{stem}\"\nstudent_id: \"{stem[-1]}\"\nsource_pdf: \"../../split/{stem}.pdf\"\n---\n\n" + "\n".join(blocks)
    )


def _pdf(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for _ in range(count):
        doc.new_page()
    doc.save(str(path))
    doc.close()
    path.with_suffix(SIDECAR_SUFFIX).write_text(json.dumps({
        "pages": [{"group": f"g{n}", "page_number": n} for n in range(1, count + 1)],
    }))


class _Client:
    """WorkspaceClient's request(), answered by the service app in-process."""

    def __init__(self, http: TestClient, root: Path) -> None:
        self.http = http
        self.connection = type("Connection", (), {"root": str(root)})()

    def request(self, method, path, body=None, *, timeout: float = 10) -> bytes:  # noqa: ANN001
        response = self.http.request(
            method, path, content=body.model_dump_json() if body else None,
            headers={"Content-Type": "application/json"},
        )
        if response.status_code >= 400:
            raise ServiceError(response.status_code, response.text)
        return response.content


@pytest.fixture
def cut(tmp_path, monkeypatch):
    root = tmp_path / "quiz"
    split, subs = root / "split", root / "submissions"
    root.mkdir()
    (root / "qa.md").write_text(QA)
    _pdf(split / "submission-0001.pdf", 5)
    _pdf(split / "submission-0002.pdf", 1)
    _pdf(split / "submission-0003.pdf", 6)
    for stem, pages in (
        ("submission-0001", {"q1": 1, "q2": 5, "q3": None}),
        ("submission-0002", {"q1": None, "q2": None, "q3": 1}),
        ("submission-0003", {"q1": 1, "q2": 5, "q3": 6}),
    ):
        _submission(subs / stem, stem, pages)
        append_record(subs, ExtractionRecord(
            key=stem, source=str(split / f"{stem}.pdf"), source_sha256="x", settings_id="s",
            outputs=(f"{stem}/{stem}.submission.md",),
        ))
    record_extract_command(subs, split, ["--input", str(split), "--output", str(subs), "--resume"])
    store = Workspace.create(root)
    published = publish_pipeline(
        store, store.assignment_id_for_folder(root),
        PipelineOutput(qa_markdown=QA, submissions_dir=str(subs.resolve())),
    )
    aid = published.assignment.id
    store.save_assignment_inputs(
        AssignmentInputs(id=aid, folder=".", scan="", output=".", submissions="submissions")
    )

    def reread(argv, **_kwargs):
        if "--adopt-only" in argv:
            return
        _submission(subs / "submission-0001", "submission-0001", {"q1": 1, "q2": 5, "q3": 6})

    monkeypatch.setattr(submission_extractor, "run_cli", reread)
    ids = {s.student_name: s.id for s in store.submissions(aid)}
    return root, store, aid, ids


def _grade(store: Workspace, aid: str, sid: int, qid: str, label: str) -> None:
    answer = store.answer(aid, sid, qid)
    store.save_grade(aid, sid, qid, GradeEdit(
        request_id=uuid.uuid4(), expected_context_revision=answer.context_revision,
        expected_revision=answer.revision, rubric_labels=[label], analysis="",
    ))


def test_the_neighbours_come_from_the_scan_order(cut):
    root, _store, aid, ids = cut

    before, here, after = neighbours_in_workspace(root, aid, ids["submission-0002"])

    assert before[0] == ids["submission-0001"] and after[0] == ids["submission-0003"]
    assert here.stem == "submission-0002"


def test_joining_replaces_both_and_carries_the_valid_grades(cut):
    root, store, aid, ids = cut
    first, second = ids["submission-0001"], ids["submission-0002"]
    _grade(store, aid, first, "q1", "Correct")
    _grade(store, aid, first, "q3", "Incorrect")  # its page was in the other piece
    _grade(store, aid, second, "q3", "Partially Correct")

    with TestClient(create_app(root, process_jobs=False)) as http:
        new_id, result = merge_in_workspace(_Client(http, root), aid, first, second)

    assert result.grades == 2
    remaining = {s.id for s in store.submissions(aid)}
    assert first not in remaining and second not in remaining
    assert new_id in remaining and len(remaining) == 2
    assert store.answer(aid, new_id, "q1").rubric_labels == ("Correct",)
    assert store.answer(aid, new_id, "q3").rubric_labels == ("Partially Correct",)
    assert store.answer(aid, new_id, "q3").points_awarded == 6
    assert store.answer(aid, new_id, "q2").points_awarded is None
    assert not (root / "submissions" / "submission-0002").exists()


def test_the_replace_route_is_refused_in_a_folder_session(cut):
    from opengrader.server.workspace.app import folder_session_refusal

    _root, _store, aid, _ids = cut
    assert folder_session_refusal("POST", f"/api/assignments/{aid}/submissions/replace")
