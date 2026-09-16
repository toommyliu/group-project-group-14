"""Grade persistence, cross-surface conflicts, and recovery after PDF failure."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from opengrader.core.grading.files import FileGradingSession
from opengrader.core.history import read_journal, resolve_history_dir
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.server.app import create_app

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
    path.write_text('''---
student_id: '123'
source_pdf: missing.pdf
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
''')
    with TestClient(create_app(tmp_path)) as client:
        url = "/assignments/exam/submissions/1/answers/q1"
        yield client, url, path, tmp_path


def edit(answer, **changes):
    return {
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
    document = load_student_submission_markdown(path)
    assert document.questions[0].analysis == "Well explained."
    assert document.questions[0].rubric_labels == ("Correct",)
    assert document.questions[1].points_awarded is None
    history = read_journal(resolve_history_dir(path.parent))
    assert [entry.kind for entry in history] == ["grade", "comment"]
    assert history[1].old == {"analysis": ""}
    assert history[1].new == {"analysis": "Well explained."}
    assert client.get("/assignments/exam/submissions").json()[0]["question_scores"] == {"q1": 10, "q2": None}
    with TestClient(create_app(home)) as restarted:
        assert restarted.get(url).json()["points_awarded"] == 10


def test_racing_saves_accept_only_one_and_external_edits_are_preserved(workspace):
    client, url, path, _ = workspace
    answer = client.get(url).json()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda label: client.put(url, json=edit(answer, rubric_labels=[label])), ["Correct", "Incorrect"]))
    assert sorted(result.status_code for result in results) == [200, 409]
    fresh = client.get(url).json()
    path.write_text(path.read_text().replace("Student explanation", "Corrected transcription"))
    assert client.put(url, json=edit(fresh)).status_code == 409
    assert client.get(url).json()["response_text"] == "Corrected transcription"
    assert client.get("/assignments/exam/submissions").json()[0]["question_scores"]["q1"] == fresh["points_awarded"]


@pytest.mark.parametrize("changes", [
    {"rubric_labels": ["not an item"]},
    {"rubric_labels": ["Correct", "Correct"]},
    {"rubric_labels": ["Correct", "Incorrect"]},
    {"score_mode": "manual", "points_awarded": -1, "rubric_labels": []},
    {"score_mode": "manual", "points_awarded": 8},
])
def test_invalid_edits_leave_submission_untouched(workspace, changes):
    client, url, path, _ = workspace
    before = path.read_bytes()
    assert client.put(url, json=edit(client.get(url).json(), **changes)).status_code == 422
    assert path.read_bytes() == before


def test_zero_clear_manual_and_restore_are_distinct(workspace):
    client, url, _, _ = workspace
    for changes, expected in [
        ({"rubric_labels": ["Incorrect"]}, 0),
        ({"rubric_labels": []}, None),
        ({"rubric_labels": [], "score_mode": "manual", "points_awarded": 12.5}, 12.5),
        ({"rubric_labels": ["Partially Correct"]}, 6),
    ]:
        result = client.put(url, json=edit(client.get(url).json(), **changes))
        assert result.status_code == 200, result.text
        assert result.json()["answer"]["points_awarded"] == expected


def test_pdf_failure_returns_saved_grade_warning_and_preserves_history(workspace, monkeypatch):
    client, url, path, _ = workspace
    def fail(*args, **kwargs):
        raise ValueError("Broken PDF")
    monkeypatch.setattr(FileGradingSession, "sync_pdf_annotations", fail)
    result = client.put(url, json=edit(client.get(url).json()))
    assert result.status_code == 200, result.text
    assert result.json()["warning"]
    assert result.json()["answer"]["points_awarded"] == 10
    assert len(read_journal(resolve_history_dir(path.parent))) == 2
