"""Grade persistence, cross-surface conflicts, and recovery after PDF failure."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from uuid import uuid4
from opengrader.core.workspace.store import Workspace
from opengrader.core.workspace.intake import publish_submission
from opengrader.server.workspace.jobs import FeedbackJobs
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.server.workspace.app import create_app

QA = """---
title: Grading test
---
- (10 points) Explain your reasoning.
  - **Answer**
    - A clear explanation.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-4)
    - Incorrect (-10)
- (2 points) A second question.
  - **Answer**
    - Yes
"""


@pytest.fixture
def workspace(tmp_path):
    root = tmp_path / "assignments" / "exam"
    submissions = root / "submissions"
    submissions.mkdir(parents=True)
    (root / "qa.md").write_text(QA)
    path = submissions / "one.submission.md"
    path.write_text("""---
student_id: '123'
---
- q1:
  - Response text: Student explanation
  - Possible points: 10
  - Points:
  - Analysis:
- q2:
  - Response text: Yes
  - Possible points: 2
  - Points:
""")
    workspace = Workspace.create(tmp_path)
    assignment = workspace.create_assignment(QA)
    publish_submission(
        workspace,
        assignment.id,
        load_student_submission_markdown(path),
        asset_root=path.parent,
        source_key="one",
    )
    with TestClient(create_app(tmp_path, process_jobs=False)) as client:
        url = f"/api/assignments/{assignment.id}/submissions/1/answers/q1"
        yield client, url, path, tmp_path


def edit(answer, **changes):
    return {
        "request_id": str(uuid4()),
        "expected_context_revision": answer["context_revision"],
        "expected_revision": answer["revision"],
        "rubric_labels": ["Correct"],
        "analysis": "Well explained.",
        **changes,
    }


def test_save_scores_on_server_and_records_feedback_with_history(workspace):
    client, url, path, home = workspace
    answer = client.get(url).json()
    result = client.put(url, json=edit(answer, points_awarded=999))
    assert result.status_code == 200, result.text
    saved = result.json()["answer"]
    assert saved["points_awarded"] == 10
    assert saved["revision"] != answer["revision"]
    workspace = Workspace(home)
    assignment_id = workspace.assignments()[0].id
    document = workspace.document(assignment_id, 1)
    assert document.questions[0].analysis == "Well explained."
    assert document.questions[0].rubric_labels == ("Correct",)
    assert document.questions[1].points_awarded is None
    history = workspace.history(assignment_id)
    assert len(history) == 1
    assert client.get(f"/api/assignments/{assignment_id}/submissions").json()[0][
        "question_scores"
    ] == {"q1": 10, "q2": None}
    with TestClient(create_app(home)) as restarted:
        assert restarted.get(url).json()["points_awarded"] == 10


def test_racing_saves_accept_only_one(workspace):
    client, url, path, _ = workspace
    answer = client.get(url).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda label: client.put(url, json=edit(answer, rubric_labels=[label])),
                ["Correct", "Incorrect"],
            )
        )
    assert sorted(result.status_code for result in results) == [200, 409]
    fresh = client.get(url).json()
    assert fresh["revision"] == "1"


@pytest.mark.parametrize(
    "changes",
    [
        {"rubric_labels": ["not an item"]},
        {"rubric_labels": ["Correct", "Correct"]},
        {"rubric_labels": ["Correct", "Incorrect"]},
        {"score_mode": "manual", "points_awarded": -1, "rubric_labels": []},
        {"score_mode": "manual", "points_awarded": 8},
    ],
)
def test_invalid_edits_leave_submission_untouched(workspace, changes):
    client, url, path, _ = workspace
    before = path.read_bytes()
    assert (
        client.put(url, json=edit(client.get(url).json(), **changes)).status_code == 422
    )
    assert path.read_bytes() == before


def test_zero_clear_manual_and_restore_are_distinct(workspace):
    client, url, _, _ = workspace
    for changes, expected in [
        ({"rubric_labels": ["Incorrect"]}, 0),
        ({"rubric_labels": []}, None),
        ({"rubric_labels": [], "score_mode": "manual", "points_awarded": 12.5}, 12.5),
        ({"rubric_labels": ["Partially Correct"]}, 6),
        ({"rubric_labels": [], "score_mode": "manual", "points_awarded": None}, None),
    ]:
        result = client.put(url, json=edit(client.get(url).json(), **changes))
        assert result.status_code == 200, result.text
        assert result.json()["answer"]["points_awarded"] == expected
        assert result.json()["answer"]["score_mode"] == changes.get(
            "score_mode", "rubric"
        )


def test_pdf_failure_preserves_saved_grade_and_history(workspace, monkeypatch):
    client, url, _, home = workspace

    def fail(*args, **kwargs):
        raise ValueError("Broken PDF")

    monkeypatch.setattr(FeedbackJobs, "_render", fail)
    result = client.put(url, json=edit(client.get(url).json()))
    assert result.status_code == 200
    store = Workspace(home)
    FeedbackJobs(store).process(store.jobs()[0])
    assert store.jobs()[0].status == "failed"
    assert client.get(url).json()["points_awarded"] == 10
    assert len(store.history(store.assignments()[0].id)) == 1
