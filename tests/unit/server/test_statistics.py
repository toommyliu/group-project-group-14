from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)
from opengrader.core.workspace.intake import publish_submission
from opengrader.core.workspace.store import Workspace
from opengrader.server.workspace.app import create_app


def publish(workspace, assignment, scores, version=None):
    return publish_submission(
        workspace,
        assignment,
        StudentSubmissionDocument(
            student_name="Student",
            student_id="",
            course_code=None,
            assignment_title="Exam",
            assignment_version=version,
            source_pdf="",
            questions=[
                StudentSubmissionQuestion(
                    question_id=question,
                    prompt="",
                    possible_points=None,
                    response_image_path=None,
                    response_text="Answer",
                    points_awarded=score,
                )
                for question, score in scores.items()
            ],
        ),
        asset_root=workspace.root,
        source_key=str(uuid4()),
    )


def test_statistics_distinguish_complete_partial_zero_and_missing_grades(tmp_path):
    workspace = Workspace.create(tmp_path)
    assignment = workspace.create_assignment(
        "- (10 points) First\n- (10 points) Second\n"
    ).id
    publish(workspace, assignment, {"q1": 10, "q2": 10})
    publish(workspace, assignment, {"q1": 4, "q2": 6})
    publish(workspace, assignment, {"q1": 0, "q2": 0})
    publish(workspace, assignment, {"q1": 8})
    publish(workspace, assignment, {})
    with TestClient(create_app(tmp_path, process_jobs=False)) as client:
        response = client.get(f"/assignments/{assignment}/statistics")
        assert response.status_code == 200, response.text
        report, = response.json()
        total = report["scores"]
        assert total["possible_points"] == 20
        assert total["total_submissions"] == 5
        assert total["graded_count"] == 3
        assert report["partial_count"] == 1
        assert report["ungraded_count"] == 1
        assert total["average"] == 10
        assert total["median"] == 10
        assert total["minimum"] == 0
        assert total["maximum"] == 20
        assert total["std_dev"] == pytest.approx(8.1649658093)
        assert total["average_percent"] == 50
        assert [bucket["count"] for bucket in total["buckets"]] == [1, 0, 1, 0, 0, 1]
        first, second = report["questions"]
        assert first["scores"]["average"] == 5.5
        assert first["scores"]["graded_count"] == 4
        assert second["scores"]["average"] == pytest.approx(16 / 3)
        assert second["scores"]["ungraded_count"] == 2

        answer = client.get(f"/assignments/{assignment}/submissions/4/answers/q2").json()
        saved = client.put(
            f"/assignments/{assignment}/submissions/4/answers/q2",
            json={
                "request_id": str(uuid4()),
                "expected_revision": answer["revision"],
                "expected_context_revision": answer["context_revision"],
                "rubric_labels": ["Correct"],
                "analysis": "",
                "score_mode": "rubric",
            },
        )
        assert saved.status_code == 200, saved.text
        updated, = client.get(f"/assignments/{assignment}/statistics").json()
        assert updated["scores"]["graded_count"] == 4
        assert updated["scores"]["average"] == 12
        assert updated["partial_count"] == 0


def test_empty_statistics_and_unknown_assignment(tmp_path):
    with TestClient(create_app(tmp_path, process_jobs=False)) as client:
        assignment = client.post(
            "/assignments", json={"qa_markdown": "- (5 points) Question\n"}
        ).json()["id"]
        response = client.get(f"/assignments/{assignment}/statistics")
        assert response.status_code == 200
        report, = response.json()
        assert report["scores"]["total_submissions"] == 0
        assert report["scores"]["possible_points"] == 5
        assert report["scores"]["average"] is None
        assert report["questions"][0]["prompt"] == "Question"
        assert report["questions"][0]["submission_id"] is None
        assert client.get("/assignments/missing/statistics").status_code == 404


def test_zero_point_and_bonus_scores_are_not_dropped(tmp_path):
    workspace = Workspace.create(tmp_path)
    assignment = workspace.create_assignment("- (0 points) Bonus\n").id
    publish(workspace, assignment, {"q1": 2})
    publish(workspace, assignment, {"q1": 0})
    with TestClient(create_app(tmp_path, process_jobs=False)) as client:
        report, = client.get(f"/assignments/{assignment}/statistics").json()
        assert report["scores"]["average"] == 1
        assert report["scores"]["average_percent"] is None
        assert report["scores"]["buckets"] == [
            {"label": "0.0", "count": 1}, {"label": "2.0", "count": 1}
        ]

        other = workspace.create_assignment("- (10 points) Question\n").id
        publish(workspace, other, {"q1": 12})
        bonus, = client.get(f"/assignments/{other}/statistics").json()
        assert bonus["scores"]["average_percent"] == 120
        assert bonus["scores"]["std_dev"] == 0
        assert bonus["scores"]["buckets"][-1] == {"label": "100%+", "count": 1}


def test_versions_and_multipart_questions_use_their_own_points_and_cohorts(tmp_path):
    workspace = Workspace.create(tmp_path)
    assignment = workspace.create_assignment("""## Version A

- (10 points) Group
  - (4 points) First part
  - (6 points) Second part

## Version B

- (20 points) Different question

## Version C

- (5 points) No submissions yet
""").id
    publish(workspace, assignment, {"q1a": 4, "q1b": 3}, "A")
    second = publish(workspace, assignment, {"q1": 20}, "B")
    with TestClient(create_app(tmp_path, process_jobs=False)) as client:
        response = client.get(f"/assignments/{assignment}/statistics")
        assert response.status_code == 200, response.text
        a, b, c = response.json()
        assert [a["version"], b["version"], c["version"]] == ["A", "B", "C"]
        assert a["scores"]["possible_points"] == 10
        assert a["scores"]["average_percent"] == 70
        assert a["scores"]["total_submissions"] == 1
        assert [q["scores"]["question_id"] for q in a["questions"]] == ["q1a", "q1b"]
        assert a["questions"][0]["parent_id"] == "q1"
        assert a["questions"][0]["parent_prompt"] == "Group"
        assert b["scores"]["possible_points"] == 20
        assert b["scores"]["average_percent"] == 100
        assert b["questions"][0]["submission_id"] == second
        assert c["scores"]["total_submissions"] == 0
        assert c["questions"][0]["scores"]["possible_points"] == 5
