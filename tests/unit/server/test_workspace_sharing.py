"""A workspace shared with other accounts on the same computer (graders over
SSH): they join the owner's service, are told apart, and may only grade —
a change to someone else's grade becomes a request the owner decides."""

from __future__ import annotations

import json
import os
import stat
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opengrader.core.folder_access import current_account
from opengrader.core.grading.edits import GradeEdit
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.workspace.intake import publish_submission
from opengrader.core.workspace.store import Workspace
from opengrader.server import app as server_app
from opengrader.server.app import CONNECTION_NOTE, create_app

pytestmark = pytest.mark.skipif(os.name == "nt", reason="accounts and groups are POSIX")

QA = """---
title: Shared quiz
---
- (10 points) Explain.
  - **Answer**
    - A clear explanation.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-4)
    - Incorrect (-10)
- (10 points) Explain again.
  - **Answer**
    - Another.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-4)
    - Incorrect (-10)
"""
SUBMISSION = """---
student_id: '123'
---
- q1:
  - Response text: one
  - Possible points: 10
  - Points:
  - Analysis:
- q2:
  - Response text: two
  - Possible points: 10
  - Points:
"""


@pytest.fixture
def shared(tmp_path, monkeypatch):
    import grp

    from opengrader.core.workspace import sharing

    # Test folders sit in a private temporary folder graders could not reach;
    # that check has its own test below. The group checks still run.
    real = sharing.sharing_problems
    monkeypatch.setattr(
        sharing, "sharing_problems",
        lambda root, group: [p for p in real(root, group) if "cannot reach" not in p],
    )

    root = tmp_path / "course"
    submissions = root / "submissions"
    submissions.mkdir(parents=True)
    path = submissions / "one.submission.md"
    path.write_text(SUBMISSION)
    workspace = Workspace.create(root)
    assignment = workspace.create_assignment(QA)
    publish_submission(
        workspace, assignment.id, load_student_submission_markdown(path),
        asset_root=submissions, source_key="one",
    )
    app = create_app(tmp_path / "server", process_jobs=False)
    app.state.workspaces.base_url = "http://localhost:9"
    item = app.state.registry.register(root)
    group = grp.getgrgid(os.getgid()).gr_name
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:
        yield {
            "client": client, "app": app, "item": item, "workspace": workspace,
            "aid": assignment.id, "root": root, "group": group,
        }


def _api(shared, path: str) -> str:
    return shared["item"].url_path + "/api" + path


def _share(shared, **body):
    response = shared["client"].put(_api(shared, "/sharing"), json={"group": shared["group"], **body})
    assert response.status_code == 200, response.text
    return response.json()


def _as(shared, login: str) -> dict[str, str]:
    note = json.loads((shared["root"] / CONNECTION_NOTE).read_text())
    return {"Authorization": f"Bearer {note['token']}", "X-Opengrader-Account": login}


def _edit(shared, question: str, label: str) -> dict:
    current = shared["client"].get(_api(shared, f"/assignments/{shared['aid']}/submissions/1/answers/{question}")).json()
    return {
        "request_id": str(uuid.uuid4()),
        "expected_revision": current["revision"],
        "expected_context_revision": current["context_revision"],
        "rubric_labels": [label],
        "analysis": "",
    }


def _save(shared, question: str, label: str, headers: dict[str, str] | None = None):
    return shared["client"].put(
        _api(shared, f"/assignments/{shared['aid']}/submissions/1/answers/{question}"),
        json=_edit(shared, question, label), headers=headers or {},
    )


def test_sharing_leaves_a_note_only_the_group_can_read(shared):
    info = _share(shared)

    note = shared["root"] / CONNECTION_NOTE
    assert info["group"] == shared["group"] and info["owner"] == current_account()
    mode = stat.S_IMODE(note.stat().st_mode)
    assert mode == 0o640
    assert note.stat().st_gid == os.getgid()
    body = json.loads(note.read_text())
    assert body["url"] == "http://localhost:9" + shared["item"].url_path
    assert body["owner"] == current_account() and body["token"]

    _share(shared, off=True)
    assert not note.exists()


def test_a_grader_grades_open_answers_and_their_own_but_only_asks_to_change_others(shared):
    _share(shared)
    bob = _as(shared, "bob")
    assert shared["client"].get(_api(shared, "/me"), headers=bob).json()["role"] == "grader"

    # The owner graded q1; bob's change is kept as a request, not made.
    assert _save(shared, "q1", "Correct").status_code == 200
    refused = _save(shared, "q1", "Incorrect", bob)
    assert refused.status_code == 403
    assert "sent to" in refused.json()["detail"]
    workspace, aid = shared["workspace"], shared["aid"]
    assert workspace.answer(aid, 1, "q1").rubric_labels == ("Correct",)

    # Nobody graded q2: bob grades it, and may change his own grade.
    assert _save(shared, "q2", "Partially Correct", bob).status_code == 200
    assert _save(shared, "q2", "Correct", bob).status_code == 200
    assert workspace.history(aid)[-1]["actor"] == "bob"

    # A second try on q1 replaces his request instead of adding one.
    _save(shared, "q1", "Partially Correct", bob)
    requests = shared["client"].get(_api(shared, f"/assignments/{aid}/change-requests")).json()
    assert [(r["requested_by"], r["rubric_labels"], r["graded_by"]) for r in requests] == [
        ("bob", ["Partially Correct"], current_account())
    ]

    # Graders grade; everything else is an instructor's.
    rubric = shared["client"].put(
        _api(shared, f"/assignments/{aid}/questions/q1/rubric"), json={}, headers=bob
    )
    assert rubric.status_code == 403 and "Only an instructor" in rubric.json()["detail"]
    listing = shared["client"].get(_api(shared, f"/assignments/{aid}/change-requests"), headers=bob)
    assert listing.status_code == 403


def test_the_owner_approves_or_dismisses_and_approval_is_recorded_under_them(shared):
    _share(shared)
    bob, aid = _as(shared, "bob"), shared["aid"]
    _save(shared, "q1", "Correct")
    _save(shared, "q1", "Incorrect", bob)
    [request] = shared["client"].get(_api(shared, f"/assignments/{aid}/change-requests")).json()

    decided = shared["client"].post(_api(shared, f"/assignments/{aid}/change-requests/{request['id']}/approve"))

    assert decided.status_code == 200 and decided.json()["status"] == "approved"
    assert shared["workspace"].answer(aid, 1, "q1").rubric_labels == ("Incorrect",)
    assert shared["workspace"].history(aid)[-1]["actor"] == current_account()
    assert shared["client"].get(_api(shared, f"/assignments/{aid}/change-requests")).json() == []


def test_a_trusted_grader_and_a_joined_instructor_change_grades_directly(shared):
    _share(shared, accounts=[
        {"login": "carol", "role": "instructor"},
        {"login": "dan", "role": "grader", "may_change_others": True},
    ])
    _save(shared, "q1", "Correct")

    assert _save(shared, "q1", "Incorrect", _as(shared, "dan")).status_code == 200
    assert _save(shared, "q1", "Partially Correct", _as(shared, "carol")).status_code == 200
    assert shared["client"].get(_api(shared, "/me"), headers=_as(shared, "carol")).json()["role"] == "instructor"


def test_only_the_owner_changes_who_shares(shared):
    _share(shared, accounts=[{"login": "carol", "role": "instructor"}])

    refused = shared["client"].put(_api(shared, "/sharing"), json={"off": True}, headers=_as(shared, "carol"))

    assert refused.status_code == 403
    assert (shared["root"] / CONNECTION_NOTE).exists()


def test_the_owner_cannot_be_claimed_without_the_system_confirming_it(shared):
    _share(shared)

    response = shared["client"].get(_api(shared, "/me"), headers=_as(shared, current_account()))

    assert response.status_code == 401


def test_another_account_is_told_apart_by_the_system_not_by_what_it_says(shared, monkeypatch):
    _share(shared)
    headers = _as(shared, "bob")
    monkeypatch.setattr(server_app, "peer_uid", lambda *_a, **_k: os.getuid() + 1)
    monkeypatch.setattr(server_app, "login_of", lambda _uid: "mallory")
    client = shared["client"]

    # Claiming to be someone else is refused…
    assert client.get(_api(shared, "/me"), headers=headers).status_code == 403
    # …and without the note's token another account cannot act as the owner.
    stranger = client.get(_api(shared, "/me"))
    assert stranger.status_code == 403 and "belongs to" in stranger.json()["detail"]
    assert client.get("/api/workspaces").status_code == 403
    # With the token it is itself: mallory, a grader.
    honest = {**headers, "X-Opengrader-Account": "mallory"}
    assert client.get(_api(shared, "/me"), headers=honest).json()["login"] == "mallory"


def test_a_group_the_owner_is_not_in_is_refused_before_anything_is_shared(shared):
    import grp

    others = [g.gr_name for g in grp.getgrall() if g.gr_gid != os.getgid() and g.gr_gid not in os.getgroups()]
    if not others:
        pytest.skip("every group on this machine includes this account")
    response = shared["client"].put(_api(shared, "/sharing"), json={"group": others[0]})

    assert response.status_code == 422 and "is not in the group" in response.json()["detail"]
    assert not (shared["root"] / CONNECTION_NOTE).exists()


def test_a_class_folder_graders_cannot_reach_is_named_with_the_fix(tmp_path):
    import grp

    from opengrader.core.workspace.sharing import sharing_problems

    root = tmp_path / "course"
    (root / ".opengrader").mkdir(parents=True)
    root.chmod(0o700)
    try:
        problems = sharing_problems(root, grp.getgrgid(os.getgid()).gr_name)
    finally:
        root.chmod(0o755)

    assert len(problems) == 1 and "cannot reach" in problems[0]
