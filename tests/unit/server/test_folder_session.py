"""A class folder opened from classic: browser grades are written into its files."""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from opengrader.core.history.journal import read_journal, resolve_history_dir
from opengrader.core.submissions.student_file import (
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)
from opengrader.core.workspace.folder_session import (
    BROWSER_ACTOR,
    FOLDER_SESSION,
    load_folder_session,
    start_folder_session,
)
from opengrader.core.workspace.intake import publish_pipeline
from opengrader.core.workspace.models import AssignmentInputs, PipelineOutput
from opengrader.core.workspace.store import Workspace
from opengrader.core.workspace import writeback
from opengrader.core.workspace.writeback import plan_write_back
from opengrader.server.workspace.app import create_app

REPO = Path(__file__).resolve().parents[3]


def _import(root: Path) -> tuple[Workspace, str]:
    store = Workspace.create(root)
    published = publish_pipeline(
        store,
        store.assignment_id_for_folder(root),
        PipelineOutput(
            qa_markdown=(root / "qa.md").read_text(encoding="utf-8"),
            submissions_dir=str((root / "student-submissions").resolve()),
        ),
    )
    store.save_assignment_inputs(
        AssignmentInputs(
            id=published.assignment.id,
            folder=".",
            scan="",
            output=".",
            submissions="student-submissions",
        )
    )
    return store, published.assignment.id


@pytest.fixture
def quiz(tmp_path: Path):
    root = tmp_path / "quiz"
    shutil.copytree(REPO / "samples" / "cs146-quiz1", root)
    store, aid = _import(root)
    files = sorted((root / "student-submissions").rglob("*.submission.md"))
    first, second = (s.id for s in store.submissions(aid))
    return {"root": root, "store": store, "aid": aid, "files": files, "sids": (first, second)}


@pytest.fixture
def session(quiz):
    start_folder_session(quiz["root"], quiz["aid"])
    with TestClient(create_app(quiz["root"], process_jobs=False)) as client:
        yield {**quiz, "client": client}


@pytest.fixture
def plain(quiz):
    with TestClient(create_app(quiz["root"], process_jobs=False)) as client:
        yield {**quiz, "client": client}


def _url(ctx, question: str, index: int = 0) -> str:
    return (
        f"/api/assignments/{ctx['aid']}/submissions/{ctx['sids'][index]}"
        f"/answers/{question}"
    )


def _save(ctx, question: str, *, index: int = 0, **changes):
    client = ctx["client"]
    answer = client.get(_url(ctx, question, index)).json()
    body = {
        "request_id": str(uuid4()),
        "expected_context_revision": answer["context_revision"],
        "expected_revision": answer["revision"],
        "rubric_labels": [],
        "analysis": "",
        "score_mode": "manual",
        "points_awarded": None,
        **changes,
    }
    return client.put(_url(ctx, question, index), json=body)


def _file_cell(ctx, question: str, index: int = 0):
    document = load_student_submission_markdown(ctx["files"][index])
    return {q.question_id: q for q in document.questions}[question]


def _classic_grade(ctx, question: str, points: float, index: int = 0) -> None:
    update_student_submission_file(
        ctx["files"][index],
        StudentSubmissionGradeUpdate(
            question_id=question, points_awarded=points, rubric_labels=()
        ),
    )


# -- marker ------------------------------------------------------------------


def test_the_marker_round_trips_and_a_damaged_one_is_no_session(tmp_path: Path) -> None:
    assert load_folder_session(tmp_path) is None

    started = start_folder_session(tmp_path, "a1")
    again = start_folder_session(tmp_path, "a1")

    assert load_folder_session(tmp_path) == started == again
    assert not list((tmp_path / ".opengrader").glob("*.tmp"))
    for damaged in ("{not json", "[]", '{"version": 2, "assignment_id": "a1"}', '{"version": 1}'):
        (tmp_path / FOLDER_SESSION).write_text(damaged, encoding="utf-8")
        assert load_folder_session(tmp_path) is None


def test_a_damaged_marker_does_not_break_the_service(quiz) -> None:
    (quiz["root"] / FOLDER_SESSION).parent.mkdir(exist_ok=True)
    (quiz["root"] / FOLDER_SESSION).write_text("{oops", encoding="utf-8")
    with TestClient(create_app(quiz["root"], process_jobs=False)) as client:
        assert client.get("/api/workspace").json()["folder_session"] is None
        response = _save({**quiz, "client": client}, "q1", points_awarded=4)
    assert response.status_code == 200 and response.json()["folder"] is None


# -- write-through -----------------------------------------------------------


def test_a_browser_save_is_written_into_the_submission_file(session) -> None:
    labels = [o["label"] for o in session["client"].get(_url(session, "q1")).json()["question"]["rubric"]]
    response = _save(
        session, "q1", score_mode="rubric", rubric_labels=[labels[0]], analysis="Nice work"
    )

    assert response.status_code == 200
    folder = response.json()["folder"]
    assert folder["status"] == "written"
    assert folder["path"] == str(session["files"][0])
    cell = _file_cell(session, "q1")
    assert cell.points_awarded == response.json()["answer"]["points_awarded"] == 10
    assert cell.rubric_labels == (labels[0],)
    assert cell.analysis == "Nice work"
    # History holds the file's old value, so grade-restore can undo it.
    entries = read_journal(resolve_history_dir(session["root"] / "student-submissions"))
    grade = [e for e in entries if e.actor == BROWSER_ACTOR and e.kind == "grade"]
    assert grade and grade[0].old == {"points": None, "rubric": []}
    assert grade[0].new == {"points": 10.0, "rubric": [labels[0]]}
    assert grade[0].summary == "saved from the browser"
    # The base moved with it: a write-back has nothing left to offer.
    assert plan_write_back(session["root"], session["aid"]).nothing_to_do


def test_saving_the_same_value_again_leaves_the_file_alone(session) -> None:
    _save(session, "q2", points_awarded=3)
    before = session["files"][0].stat().st_mtime_ns

    response = _save(session, "q2", points_awarded=3)

    assert response.json()["folder"]["status"] == "unchanged"
    assert session["files"][0].stat().st_mtime_ns == before


def test_outside_a_folder_session_nothing_is_written(plain) -> None:
    before = plain["files"][0].read_bytes()

    response = _save(plain, "q1", points_awarded=4)

    assert response.status_code == 200
    assert response.json()["folder"] is None
    assert plain["files"][0].read_bytes() == before


def test_the_browser_wins_over_a_file_edit_and_history_keeps_the_file_value(session) -> None:
    _classic_grade(session, "q3", 2)

    response = _save(session, "q3", points_awarded=8, analysis="browser")

    assert response.json()["folder"]["status"] == "written"
    assert _file_cell(session, "q3").points_awarded == 8
    entries = read_journal(resolve_history_dir(session["root"] / "student-submissions"))
    [grade] = [e for e in entries if e.actor == BROWSER_ACTOR and e.kind == "grade"]
    assert grade.old["points"] == 2 and grade.new["points"] == 8


def test_a_file_edit_to_another_aspect_is_not_overwritten(session) -> None:
    """Only what the browser changed is written: a comment saved in the
    browser does not put back a grade classic changed meanwhile."""
    _classic_grade(session, "q3", 2)

    _save(session, "q3", points_awarded=None, analysis="just a comment")

    cell = _file_cell(session, "q3")
    assert (cell.points_awarded, cell.analysis) == (2, "just a comment")


def test_a_failed_file_write_keeps_the_saved_grade(session) -> None:
    session["files"][0].chmod(0o444)
    try:
        response = _save(session, "q1", points_awarded=6)
    finally:
        session["files"][0].chmod(0o644)

    assert response.status_code == 200
    body = response.json()
    assert body["answer"]["points_awarded"] == 6
    assert body["folder"]["status"] == "failed" and "read-only" in body["folder"]["message"]
    assert _file_cell(session, "q1").points_awarded is None
    # The next sync writes what the save could not.
    result = session["client"].post("/api/folder-session/sync").json()
    assert result["written"] == 1 and result["failed"] == 0
    assert _file_cell(session, "q1").points_awarded == 6


def test_a_removed_file_is_reported_not_fatal(session) -> None:
    session["files"][0].unlink()

    response = _save(session, "q1", points_awarded=6)

    assert response.status_code == 200
    assert response.json()["folder"]["status"] == "failed"
    assert session["client"].get(_url(session, "q1")).json()["points_awarded"] == 6


def test_a_question_the_file_lacks_is_reported(session) -> None:
    response = _save(session, "q5a", points_awarded=1)

    assert response.json()["folder"]["status"] == "failed"
    assert "q5a" in response.json()["folder"]["message"]


def test_concurrent_saves_to_one_file_all_reach_it(session) -> None:
    questions = ["q1", "q2", "q3", "q4"]
    barrier = Barrier(len(questions))

    def save(index: int):
        answer = session["client"].get(_url(session, questions[index])).json()
        barrier.wait()
        return session["client"].put(
            _url(session, questions[index]),
            json={
                "request_id": str(uuid4()),
                "expected_context_revision": answer["context_revision"],
                "expected_revision": answer["revision"],
                "rubric_labels": [],
                "analysis": f"note {index}",
                "score_mode": "manual",
                "points_awarded": index + 1,
            },
        )

    with ThreadPoolExecutor(len(questions)) as pool:
        responses = list(pool.map(save, range(len(questions))))

    assert [r.json()["folder"]["status"] for r in responses] == ["written"] * 4
    for index, question in enumerate(questions):
        cell = _file_cell(session, question)
        assert (cell.points_awarded, cell.analysis) == (index + 1, f"note {index}")


def test_a_file_changed_before_the_replace_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "one.submission.md"
    path.write_text("old", encoding="utf-8")
    seen = writeback.file_fingerprint(path)
    path.write_text("classic saved this", encoding="utf-8")

    with pytest.raises(writeback.FileChanged):
        writeback._replace_atomically(path, "browser", seen)

    assert path.read_text(encoding="utf-8") == "classic saved this"
    assert [p.name for p in tmp_path.iterdir()] == ["one.submission.md"]


# -- sync --------------------------------------------------------------------


def test_sync_after_a_failed_write_lets_the_browser_win_over_a_file_edit(session) -> None:
    session["files"][0].chmod(0o444)
    try:
        _save(session, "q1", points_awarded=6)
    finally:
        session["files"][0].chmod(0o644)
    _classic_grade(session, "q1", 2)

    result = session["client"].post("/api/folder-session/sync").json()

    assert (result["written"], result["updated"], result["failed"]) == (1, 0, 0)
    assert _file_cell(session, "q1").points_awarded == 6
    entries = read_journal(resolve_history_dir(session["root"] / "student-submissions"))
    [grade] = [e for e in entries if e.actor == BROWSER_ACTOR and e.kind == "grade"]
    assert (grade.old["points"], grade.new["points"]) == (2, 6)



def test_sync_pulls_a_classic_edit_through_the_save_path(session) -> None:
    store = session["store"]
    cursor = store.info().event_cursor
    _classic_grade(session, "q2", 7, index=1)

    result = session["client"].post("/api/folder-session/sync").json()

    assert result == {"imported": 0, "updated": 1, "written": 0, "failed": 0, "problems": []}
    answer = session["client"].get(_url(session, "q2", 1)).json()
    assert answer["points_awarded"] == 7 and answer["revision"] == "1"
    events = store.events(cursor)
    assert [(e.kind, e.question_id) for e in events] == [("grade", "q2")]
    assert plan_write_back(session["root"], session["aid"]).nothing_to_do


def test_sync_without_changes_does_nothing_and_skips_unchanged_files(session, monkeypatch) -> None:
    client = session["client"]
    client.post("/api/folder-session/sync")
    import opengrader.core.workspace.folder_session as folder_session

    planned: list[int] = []
    real = folder_session._sync_submission
    monkeypatch.setattr(
        folder_session,
        "_sync_submission",
        lambda *args: planned.append(args[2]) or real(*args),
    )

    result = client.post("/api/folder-session/sync").json()

    assert result == {"imported": 0, "updated": 0, "written": 0, "failed": 0, "problems": []}
    assert planned == []


def test_sync_imports_a_submission_file_added_since(session) -> None:
    source = session["files"][1].parent
    copy = source.parent / "submission-0003"
    shutil.copytree(source, copy)
    (copy / "submission-0002.submission.md").rename(copy / "submission-0003.submission.md")

    result = session["client"].post("/api/folder-session/sync").json()

    assert result["imported"] == 1 and result["failed"] == 0
    assert len(session["client"].get(f"/api/assignments/{session['aid']}/submissions").json()) == 3


# -- info, convert, refusals -------------------------------------------------


def test_workspace_info_names_the_folder_session(session) -> None:
    info = session["client"].get("/api/workspace").json()["folder_session"]

    assert info == {
        "assignment_id": session["aid"],
        "assignment_title": "CS146: Quiz 1",
        "folder": str((session["root"] / "student-submissions").resolve()),
    }


def test_converting_ends_write_through(session) -> None:
    client = session["client"]
    _classic_grade(session, "q2", 5)

    info = client.post("/api/folder-session/convert").json()

    assert info["folder_session"] is None
    assert not (session["root"] / FOLDER_SESSION).exists()
    # The final sync pulled the classic edit first.
    assert client.get(_url(session, "q2")).json()["points_awarded"] == 5
    before = session["files"][0].read_bytes()
    response = _save(session, "q1", points_awarded=9)
    assert response.json()["folder"] is None
    assert session["files"][0].read_bytes() == before
    assert client.post("/api/folder-session/sync").status_code == 409


def test_folder_session_routes_need_a_folder_session(plain) -> None:
    for path in ("/api/folder-session/sync", "/api/folder-session/convert"):
        response = plain["client"].post(path)
        assert response.status_code == 409
        assert response.json()["detail"] == "This workspace is not a folder session."


REFUSED = [
    ("post", "/api/assignments", {"qa_markdown": "- (1 points) Q\n"}),
    ("put", "/api/assignments/{aid}", {"qa_markdown": "- (1 points) Q\n", "expected_qa_markdown": ""}),
    ("delete", "/api/assignments/{aid}", None),
    ("post", "/api/assignments/{aid}/scans?filename=a.pdf", None),
]


@pytest.mark.parametrize(("method", "path", "body"), REFUSED)
def test_workspace_only_actions_are_refused_in_a_folder_session(session, method, path, body) -> None:
    client = session["client"]
    kwargs = {"json": body} if body is not None else {}

    response = getattr(client, method)(path.format(aid=session["aid"]), **kwargs)

    assert response.status_code == 409
    assert "Create a workspace first" in response.json()["detail"]
    assert client.get(f"/api/assignments/{session['aid']}/scans").status_code == 200


@pytest.mark.parametrize(("method", "path", "body"), REFUSED[:2])
def test_the_same_actions_are_not_refused_in_a_workspace(plain, method, path, body) -> None:
    response = getattr(plain["client"], method)(path.format(aid=plain["aid"]), json=body)

    assert "Create a workspace first" not in str(response.json().get("detail"))


# -- classic's notice --------------------------------------------------------


def test_classic_is_warned_only_about_a_shared_workspace(quiz) -> None:
    from opengrader.core.workspace.folder_session import (
        end_folder_session,
        shared_workspace_for,
    )

    submissions = quiz["root"] / "student-submissions"
    assert shared_workspace_for(submissions) == quiz["root"].resolve()
    start_folder_session(quiz["root"], quiz["aid"])
    assert shared_workspace_for(submissions) is None
    end_folder_session(quiz["root"])
    # A folder the workspace does not hold is none of its business.
    other = quiz["root"] / "split-submissions"
    assert shared_workspace_for(other) is None
