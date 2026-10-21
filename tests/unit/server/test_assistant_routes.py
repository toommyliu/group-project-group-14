"""The browser's AI assistant page: a workspace assignment's copies, made,
merged back and cleaned up through the service, for its owner only."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opengrader.core.agent import load_tasks, record_grade, suggest
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.workspace.intake import publish_submission
from opengrader.core.workspace.models import AssignmentInputs
from opengrader.core.workspace.store import Workspace
from opengrader.server.workspace.app import create_app

QA = """---
title: Quiz 1
---

- (10 points) Explain what a loop does.
  - **Correct**
    - repeats code
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""

SUBMISSION = """---
student_name: "Ada Lovelace"
student_id: "123456789"
assignment_title: "Quiz 1"
---


- q1:
  - Question type: Free Response
  - Prompt: (10 points) Explain what a loop does.
  - Response text: it runs code again
  - Possible points: 10
  - Points:
  - Rubric:
  - Analysis:
"""


@pytest.fixture
def ws(tmp_path: Path):
    root = tmp_path / "course"
    student = root / "quiz1" / "submissions" / "sub-0001"
    student.mkdir(parents=True)
    (student / "sub-0001.submission.md").write_text(SUBMISSION, encoding="utf-8")
    (root / "quiz1" / "qa.md").write_text(QA, encoding="utf-8")
    store = Workspace.create(root)
    assignment = store.create_assignment(QA)
    store.save_assignment_inputs(
        AssignmentInputs(id=assignment.id, folder="quiz1", scan="", output="", submissions="submissions", qa="qa.md")
    )
    publish_submission(
        store, assignment.id, load_student_submission_markdown(student / "sub-0001.submission.md"),
        asset_root=student, source_key="sub-0001",
    )
    with TestClient(create_app(root, process_jobs=False)) as http:
        yield {"http": http, "store": store, "aid": assignment.id, "root": root,
               "base": f"/api/assignments/{assignment.id}/assistant"}


def test_a_copy_is_made_merged_and_deleted_from_the_browser(ws) -> None:  # noqa: ANN001
    http, base = ws["http"], ws["base"]
    overview = http.get(base).json()
    assert overview["can_grade"] and overview["copies"] == []
    assert overview["key_file"].endswith("qa.md") and "AGENTS.md" in overview["prompts"]["grading"]

    made = http.post(base + "/copies", json={"purpose": "grading"})
    assert made.status_code == 201, made.text
    copy = made.json()
    assert copy["ungraded"] == 1 and "mcp" in copy["mcp_setup"].lower()
    packet = Path(copy["path"])
    [task] = load_tasks(packet)
    record_grade(packet, task.id, ["Correct"], model="claude")
    suggest(packet, question="q1", kind="answer", message="Also right", correct=["loops"])

    preview = http.get(f"{base}/copies/{copy['name']}/merge").json()
    assert preview["grades"] == 1 and ws["store"].answer(ws["aid"], 1, "q1").points_awarded is None
    merged = http.post(f"{base}/copies/{copy['name']}/merge").json()
    assert merged["grades"] == 1 and ws["store"].answer(ws["aid"], 1, "q1").points_awarded == 10

    [fix] = http.get(f"{base}/copies/{copy['name']}/suggestions").json()
    unasked = http.post(f"{base}/copies/{copy['name']}/suggestions/{fix['id']}/accept", json={})
    assert unasked.status_code == 422 and "--key-file" in unasked.json()["detail"]
    accepted = http.post(f"{base}/copies/{copy['name']}/suggestions/{fix['id']}/accept", json={"key_file": "update"})
    assert accepted.status_code == 200, accepted.text
    assert "loops" in ws["store"].assignment_source(ws["aid"])
    assert "loops" in (ws["root"] / "quiz1" / "qa.md").read_text()

    assert http.get(f"{base}/copies/not-a-copy/merge").status_code == 404
    assert http.delete(f"{base}/copies/{copy['name']}").status_code == 204
    assert not packet.exists() and http.get(base).json()["copies"] == []


def test_the_key_folder_is_made_and_its_key_stored_from_the_browser(ws) -> None:  # noqa: ANN001
    http, base = ws["http"], ws["base"]
    folder = http.post(base + "/key-folders").json()
    (Path(folder["path"]) / "qa.md").write_text(QA.replace("    - repeats code\n", "    - repeats code\n    - loops\n"))

    stored = http.post(base + "/key-folders/use", json={"name": folder["name"], "key_file": "leave"})

    assert stored.status_code == 200, stored.text
    assert "loops" in ws["store"].assignment_source(ws["aid"])
    assert (ws["root"] / "quiz1" / "qa.md").read_text() == QA
    assert http.post(base + "/key-folders/use", json={"name": "elsewhere"}).status_code == 404


def test_a_folder_session_is_sent_to_classic_mode(ws, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    import opengrader.server.assistant as routes

    monkeypatch.setattr(routes, "load_folder_session", lambda root: object())

    response = ws["http"].get(ws["base"])

    assert response.status_code == 409 and "classic mode" in response.json()["detail"]
