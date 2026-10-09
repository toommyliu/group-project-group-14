"""A class folder's roster file, on the browser's People and Submissions pages."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from opengrader.core.pipeline_settings import FolderPipelineSettings, save_folder_settings
from opengrader.core.submissions.region_crop import Identity
from opengrader.core.submissions.student_file import load_student_submission_markdown
from opengrader.core.workspace import roster as roster_store
from opengrader.core.workspace.folder_session import start_folder_session
from opengrader.core.workspace.intake import publish_pipeline
from opengrader.core.workspace.models import AssignmentInputs, PipelineOutput, StudentDetails
from opengrader.core.workspace.store import Workspace
from opengrader.server.workspace.app import create_app

REPO = Path(__file__).resolve().parents[3]

# The sample's two submissions are "tommy liv" (ID 123456789) and a second
# student; the rosters below put the first of them on the roster.
TSV = "123456789\tTommy Liu\n555000111\tAndy Cortes\n"
CSV = (
    "First Name,Last Name,SID,Email,Section\n"
    "Tommy,Liu,123456789,tommy@example.edu,A\n"
    "Andy,Cortes,555000111,,B\n"
)


def _folder(tmp_path: Path, roster_name: str | None, roster_text: str = "") -> dict:
    root = tmp_path / "quiz"
    shutil.copytree(
        REPO / "samples" / "cs146-quiz1", root, ignore=shutil.ignore_patterns(".opengrader")
    )
    if roster_name:
        (root / roster_name).write_text(roster_text, encoding="utf-8")
    store = Workspace.create(root)
    published = publish_pipeline(
        store,
        store.assignment_id_for_folder(root),
        PipelineOutput(
            qa_markdown=(root / "qa.md").read_text(encoding="utf-8"),
            submissions_dir=str((root / "student-submissions").resolve()),
        ),
    )
    aid = published.assignment.id
    store.save_assignment_inputs(
        AssignmentInputs(
            id=aid, folder=".", scan="", output=".", submissions="student-submissions", qa="qa.md"
        )
    )
    start_folder_session(root, aid)
    files = sorted((root / "student-submissions").rglob("*.submission.md"))
    return {
        "root": root,
        "store": store,
        "aid": aid,
        "files": files,
        "sids": tuple(s.id for s in store.submissions(aid)),
        "roster": root / roster_name if roster_name else None,
    }


@pytest.fixture(params=["roster.tsv", "roster.csv"])
def session(request, tmp_path: Path):
    """A folder session whose class folder has a roster, in each shape."""
    ctx = _folder(tmp_path, request.param, TSV if request.param.endswith(".tsv") else CSV)
    with TestClient(create_app(ctx["root"], process_jobs=False)) as client:
        ctx["client"] = client
        assert client.post("/api/folder-session/sync").json()["problems"] == []
        yield ctx


def _students(ctx) -> dict[str, dict]:
    return {s["sid"]: s for s in ctx["client"].get("/api/roster").json()}


def _submissions(ctx) -> list[dict]:
    return ctx["client"].get(f"/api/assignments/{ctx['aid']}/submissions").json()


def _file_identity(ctx, index: int = 0) -> tuple[str, str]:
    document = load_student_submission_markdown(ctx["files"][index])
    return document.student_name, document.student_id


def _info(ctx) -> dict | None:
    return ctx["client"].get("/api/workspace").json()["folder_session"]["roster"]


# -- the roster reaches the browser -------------------------------------------


def test_the_folders_roster_is_the_browsers_roster(session) -> None:
    students = _students(session)

    assert {sid: (s["first_name"], s["last_name"]) for sid, s in students.items()} == {
        "123456789": ("Tommy", "Liu"),
        "555000111": ("Andy", "Cortes"),
    }
    info = _info(session)
    csv = session["roster"].suffix == ".csv"
    assert info == {
        "files": [session["roster"].name],
        "format": "csv" if csv else "tsv",
        "editable": True,
        "has_email": csv,
        "students": 2,
        "skipped": 0,
    }
    assert students["123456789"]["email"] == ("tommy@example.edu" if csv else None)


def test_a_folder_without_a_roster_has_no_people_page(tmp_path: Path) -> None:
    ctx = _folder(tmp_path, None)
    with TestClient(create_app(ctx["root"], process_jobs=False)) as client:
        result = client.post("/api/folder-session/sync").json()
        info = client.get("/api/workspace").json()["folder_session"]["roster"]
        students = client.get("/api/roster").json()
        refused = client.post(
            "/api/roster/students", json={"sid": "1", "first_name": "A", "last_name": "B"}
        )

    assert (result["roster_changed"], info, students) == (False, None, [])
    assert refused.status_code == 409
    assert "no roster file" in refused.json()["detail"]


def test_a_sync_takes_what_the_file_gained_lost_and_changed(session) -> None:
    text = session["roster"].read_text(encoding="utf-8")
    if session["roster"].suffix == ".csv":
        text = text.replace("Andy,Cortes,555000111,,B\n", "Grace,Hopper,777,,C\n")
    else:
        text = text.replace("555000111\tAndy Cortes\n", "777\tGrace Hopper\n")
    session["roster"].write_text(text, encoding="utf-8")
    kept = _students(session)["123456789"]

    result = session["client"].post("/api/folder-session/sync").json()

    students = _students(session)
    assert result["roster_changed"] is True and result["problems"] == []
    assert set(students) == {"123456789", "777"}
    # A student the file still has is the same student, untouched.
    assert students["123456789"] == kept
    # And a second sync has nothing left to do.
    assert session["client"].post("/api/folder-session/sync").json()["roster_changed"] is False


def test_a_roster_that_cannot_be_read_is_reported_and_changes_nothing(session) -> None:
    before = _students(session)
    session["roster"].write_bytes(b"\xff\xfe\x00not utf-8")

    result = session["client"].post("/api/folder-session/sync").json()

    assert len(result["problems"]) == 1
    assert session["roster"].name in result["problems"][0]
    assert _students(session) == before


def test_a_roster_file_that_goes_away_empties_the_people_page(session) -> None:
    session["roster"].unlink()

    result = session["client"].post("/api/folder-session/sync").json()

    assert result["roster_changed"] is True
    assert _students(session) == {} and _info(session) is None


def test_the_roster_the_pipeline_was_pointed_at_wins_over_the_one_in_the_folder(
    tmp_path: Path,
) -> None:
    ctx = _folder(tmp_path, "roster.tsv", TSV)
    elsewhere = tmp_path / "registrar" / "enrolled.tsv"
    elsewhere.parent.mkdir()
    elsewhere.write_text("42\tAlan Turing\n", encoding="utf-8")
    save_folder_settings(
        ctx["root"], FolderPipelineSettings(use_roster=True, roster=str(elsewhere))
    )

    with TestClient(create_app(ctx["root"], process_jobs=False)) as client:
        client.post("/api/folder-session/sync")
        students = client.get("/api/roster").json()
        info = client.get("/api/workspace").json()["folder_session"]["roster"]

    assert [s["sid"] for s in students] == ["42"]
    assert info["files"] == ["enrolled.tsv"]


def test_one_roster_per_section_is_shown_but_not_edited(tmp_path: Path) -> None:
    ctx = _folder(tmp_path, None)
    (ctx["root"] / "sec1.tsv").write_text("1\tAda Lovelace\n", encoding="utf-8")
    (ctx["root"] / "sec2.tsv").write_text("2\tGrace Hopper\n", encoding="utf-8")
    save_folder_settings(
        ctx["root"],
        FolderPipelineSettings(
            use_section_rosters=True, section_rosters=("sec1.tsv=One", "sec2.tsv=Two")
        ),
    )

    with TestClient(create_app(ctx["root"], process_jobs=False)) as client:
        client.post("/api/folder-session/sync")
        students = client.get("/api/roster").json()
        info = client.get("/api/workspace").json()["folder_session"]["roster"]
        refused = client.post(
            "/api/roster/students", json={"sid": "3", "first_name": "Alan", "last_name": "Turing"}
        )

    assert sorted(s["sid"] for s in students) == ["1", "2"]
    assert (info["editable"], info["files"]) == (False, ["sec1.tsv", "sec2.tsv"])
    assert refused.status_code == 409 and "one roster per section" in refused.json()["detail"]
    assert (ctx["root"] / "sec1.tsv").read_text(encoding="utf-8") == "1\tAda Lovelace\n"


# -- editing from the People page ---------------------------------------------


def test_a_student_added_in_the_browser_is_appended_to_the_file(session) -> None:
    before = session["roster"].read_text(encoding="utf-8")

    response = session["client"].post(
        "/api/roster/students",
        json={"sid": "888", "first_name": "Grace", "last_name": "Hopper"},
    )

    assert response.status_code == 201, response.text
    after = session["roster"].read_text(encoding="utf-8")
    line = "Grace,Hopper,888,,\n" if session["roster"].suffix == ".csv" else "888\tGrace Hopper\n"
    assert after == before + line
    assert _students(session)["888"]["id"] == response.json()["id"]
    assert _info(session)["students"] == 3


def test_a_duplicate_id_is_refused_before_the_file_is_touched(session) -> None:
    before = session["roster"].read_text(encoding="utf-8")

    response = session["client"].post(
        "/api/roster/students",
        json={"sid": "123456789", "first_name": "Someone", "last_name": "Else"},
    )

    assert response.status_code == 409
    assert session["roster"].read_text(encoding="utf-8") == before


def test_an_edited_name_changes_one_line_and_the_students_matched_submission(
    session,
) -> None:
    # Submission 1 carries ID 123456789, so it is this student's.
    other_before = session["files"][1].read_text(encoding="utf-8")
    student = _students(session)["123456789"]
    before = session["roster"].read_text(encoding="utf-8").splitlines()

    response = session["client"].put(
        f"/api/roster/students/{student['id']}",
        json={
            "sid": "123456789",
            "first_name": "Thomas",
            "last_name": "Liu",
            "email": student["email"],
            "expected_revision": student["revision"],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["first_name"], body["renamed"], body["problems"]) == ("Thomas", 1, [])
    after = session["roster"].read_text(encoding="utf-8").splitlines()
    assert [a == b for a, b in zip(before, after, strict=True)].count(False) == 1
    # The submission that was this student's follows, in the browser and in
    # its file; the other student's file is not opened.
    assert _submissions(session)[0]["student_name"] == "Thomas Liu"
    assert _file_identity(session) == ("Thomas Liu", "123456789")
    assert session["files"][1].read_text(encoding="utf-8") == other_before
    # Nothing is left over for a sync.
    result = session["client"].post("/api/folder-session/sync").json()
    assert (result["updated"], result["roster_changed"], result["problems"]) == (0, False, [])


def test_a_corrected_id_is_followed_too(session) -> None:
    student = _students(session)["123456789"]

    response = session["client"].put(
        f"/api/roster/students/{student['id']}",
        json={
            "sid": "123450000",
            "first_name": "Tommy",
            "last_name": "Liu",
            "email": student["email"],
            "expected_revision": student["revision"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["renamed"] == 1
    assert _file_identity(session) == ("Tommy Liu", "123450000")
    assert _submissions(session)[0]["student_id"] == "123450000"
    assert "123450000" in _students(session) and "123456789" not in _students(session)


@pytest.mark.skipif(sys.platform == "win32", reason="read-only by mode bits")
def test_a_roster_file_that_cannot_be_written_changes_nothing(session) -> None:
    student = _students(session)["123456789"]
    os.chmod(session["roster"], 0o444)
    try:
        response = session["client"].put(
            f"/api/roster/students/{student['id']}",
            json={
                "sid": "123456789",
                "first_name": "Thomas",
                "last_name": "Liu",
                "email": student["email"],
                "expected_revision": student["revision"],
            },
        )
    finally:
        os.chmod(session["roster"], 0o644)

    assert response.status_code == 422
    assert "was not changed" in response.json()["detail"]
    assert _students(session)["123456789"]["first_name"] == "Tommy"
    assert _file_identity(session)[0] == "tommy liv"


def test_an_edit_made_from_a_stale_page_is_refused(session) -> None:
    student = _students(session)["555000111"]
    text = session["roster"].read_text(encoding="utf-8")
    session["roster"].write_text(text.replace("Andy", "Andrew"), encoding="utf-8")
    saved = session["roster"].read_text(encoding="utf-8")

    # The page still shows "Andy"; the file moved on underneath it.
    response = session["client"].put(
        f"/api/roster/students/{student['id']}",
        json={
            "sid": "555000111",
            "first_name": "Andy",
            "last_name": "Cortez",
            "email": None,
            "expected_revision": student["revision"],
        },
    )

    assert response.status_code == 409
    assert session["roster"].read_text(encoding="utf-8") == saved
    assert _students(session)["555000111"]["first_name"] == "Andrew"


def test_a_removed_student_leaves_the_file_and_their_submission_keeps_its_name(
    session,
) -> None:
    student = _students(session)["123456789"]
    identity = _file_identity(session)

    response = session["client"].delete(f"/api/roster/students/{student['id']}")

    assert response.status_code == 204
    assert "123456789" not in session["roster"].read_text(encoding="utf-8")
    assert "123456789" not in _students(session)
    # A name on a graded paper is not erased because the roster shrank.
    assert _file_identity(session) == identity


def test_an_email_is_saved_only_where_the_file_has_a_column_for_it(session) -> None:
    response = session["client"].post(
        "/api/roster/students",
        json={"sid": "888", "first_name": "Grace", "last_name": "Hopper", "email": "g@example.edu"},
    )

    if session["roster"].suffix == ".csv":
        assert response.status_code == 201
        assert "Grace,Hopper,888,g@example.edu," in session["roster"].read_text(encoding="utf-8")
    else:
        assert response.status_code == 422
        assert "no Email column" in response.json()["detail"]
        assert "888" not in session["roster"].read_text(encoding="utf-8")


def test_a_roster_is_not_replaced_from_an_upload_in_a_folder_session(session) -> None:
    for path in ("/api/roster/import/preview", "/api/roster/import?expected=x"):
        response = session["client"].post(
            path, content=b"First Name,Last Name,SID\n", headers={"Content-Type": "text/csv"}
        )

        assert response.status_code == 409
        assert "file in the class folder" in response.json()["detail"]


# -- matching submissions to the roster ----------------------------------------


def _blank_the_first_student(ctx) -> None:
    url = f"/api/assignments/{ctx['aid']}/submissions/{ctx['sids'][0]}/student"
    assert ctx["client"].put(url, json={"student_name": "", "student_id": ""}).status_code == 200


def _fake_reader(ctx, monkeypatch: pytest.MonkeyPatch, identity: Identity) -> None:
    """Stand in for reading the handwriting: every name crop reads as `identity`."""
    jobs = ctx["client"].app.state.scan_jobs
    pool = SimpleNamespace(
        submit=lambda function, *args: SimpleNamespace(result=lambda: identity)
    )
    monkeypatch.setattr(type(jobs), "processing", property(lambda self: pool))


def test_matching_fills_a_blank_name_in_the_browser_and_in_the_file(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _blank_the_first_student(session)
    assert _file_identity(session) == ("", "")
    second_before = session["files"][1].read_text(encoding="utf-8")
    _fake_reader(
        session,
        monkeypatch,
        Identity(student_name="Tommy Liu", student_id="123456789", match_percent=92.0),
    )

    response = session["client"].post(f"/api/assignments/{session['aid']}/submissions/match")

    assert response.status_code == 200, response.text
    assert response.json() == {"matched": 1, "ambiguous": 0, "unmatched": 0}
    first = _submissions(session)[0]
    assert (first["student_name"], first["student_id"]) == ("Tommy Liu", "123456789")
    assert first["match_percent"] == 92.0
    assert _file_identity(session) == ("Tommy Liu", "123456789")
    # A submission that already had a name is never re-matched.
    assert session["files"][1].read_text(encoding="utf-8") == second_before
    result = session["client"].post("/api/folder-session/sync").json()
    assert (result["updated"], result["problems"]) == (0, [])


@pytest.mark.skipif(sys.platform == "win32", reason="read-only by mode bits")
def test_a_match_whose_file_cannot_be_written_is_left_unnamed(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _blank_the_first_student(session)
    _fake_reader(
        session,
        monkeypatch,
        Identity(student_name="Tommy Liu", student_id="123456789", match_percent=92.0),
    )
    os.chmod(session["files"][0], 0o444)
    try:
        response = session["client"].post(
            f"/api/assignments/{session['aid']}/submissions/match"
        )
    finally:
        os.chmod(session["files"][0], 0o644)

    assert response.json() == {"matched": 0, "ambiguous": 0, "unmatched": 0}
    assert _submissions(session)[0]["student_name"] == ""
    assert _file_identity(session) == ("", "")


# -- a workspace (no class folder) ---------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path):
    ctx = _folder(tmp_path, None)
    (ctx["root"] / ".opengrader" / "folder-session.json").unlink()
    roster_store.add_student(
        ctx["store"], StudentDetails(sid="123456789", first_name="Tommy", last_name="Liu")
    )
    with TestClient(create_app(ctx["root"], process_jobs=False)) as client:
        ctx["client"] = client
        yield ctx


def test_in_a_workspace_a_roster_edit_also_follows_into_matched_submissions(
    workspace,
) -> None:
    student = _students(workspace)["123456789"]
    file_before = workspace["files"][0].read_text(encoding="utf-8")

    response = workspace["client"].put(
        f"/api/roster/students/{student['id']}",
        json={
            "sid": "123450000",
            "first_name": "Thomas",
            "last_name": "Liu",
            "email": None,
            "expected_revision": student["revision"],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["renamed"] == 1
    first, second = _submissions(workspace)
    assert (first["student_name"], first["student_id"]) == ("Thomas Liu", "123450000")
    assert second["student_id"] != "123450000"
    # A workspace's files are written only by write-back, as before.
    assert workspace["files"][0].read_text(encoding="utf-8") == file_before


def test_in_a_workspace_an_edit_that_changes_neither_name_nor_id_renames_nothing(
    workspace,
) -> None:
    student = _students(workspace)["123456789"]

    response = workspace["client"].put(
        f"/api/roster/students/{student['id']}",
        json={
            "sid": "123456789",
            "first_name": "Tommy",
            "last_name": "Liu",
            "email": "tommy@example.edu",
            "expected_revision": student["revision"],
        },
    )

    assert response.json()["renamed"] == 0
    assert _submissions(workspace)[0]["student_name"] == "tommy liv"
    assert json.loads(response.text)["email"] == "tommy@example.edu"
