import pytest
from fastapi.testclient import TestClient

from opengrader.server.auth import AuthSettings
from opengrader.server.workspace.app import create_app


def test_preview_locates_rubric_errors_in_the_correct_version(tmp_path):
    source = """## Version A
- (10 points) Valid question
  - **Answer**
    - Yes

## Version B
- (0 points) Broken question
  - **Rubric**
    - Correct (10)
    - Partially Correct (0)
    - Incorrect (0)
"""
    app = create_app(tmp_path, auth_settings=AuthSettings(), process_jobs=False)
    with TestClient(app) as client:
        response = client.post("/api/drafts/preview", json={"qa_markdown": source})
    assert response.status_code == 422
    assert response.json() == {
        "detail": "question 'q1' rubric item 'Correct' must stay within -0 to +0 points",
        "location": {
            "line": 9,
            "version": "B",
            "question_path": [0],
            "rubric_path": [0],
        },
    }


def test_visual_edit_locates_nested_rubric_item_after_source_lines_shift(tmp_path):
    source = """- (10 points) Parent
  - (10 points) Child
    - **Rubric**
      - Bonus
        - Extra (2)
      - Correct (0)
      - Partially Correct
        - Missing step (-4)
        - Another step (-6)
      - Incorrect (-10)
"""
    app = create_app(tmp_path, auth_settings=AuthSettings(), process_jobs=False)
    with TestClient(app) as client:
        preview = client.post(
            "/api/drafts/preview", json={"qa_markdown": source}
        ).json()
        child = preview["document"]["questions"][0]["subquestions"][0]
        child["rubric"][2]["subitems"][1]["points_delta"] = -20
        response = client.post(
            "/api/drafts/edit",
            json={
                "qa_markdown": source,
                "metadata": {"title": "New title", "instructions": "New instructions"},
                "edits": [{
                    "source_line": 2,
                    "prompt": child["prompt"],
                    "points": child["points"],
                    "answers": child["answers"],
                    "rubric": child["rubric"],
                }],
            },
        )
    assert response.status_code == 200
    result = response.json()
    assert result["validation_error"] == (
        "subquestion 'q1a' rubric item 'Another step' must stay within -10 to +10 points"
    )
    assert result["validation_location"] == {
        "line": 16,
        "version": "",
        "question_path": [0, 0],
        "rubric_path": [2, 1],
    }
    assert result["qa_markdown"].splitlines()[15].strip() == "- Another step (-20)"


def test_question_errors_keep_a_navigation_target(tmp_path):
    app = create_app(tmp_path, auth_settings=AuthSettings(), process_jobs=False)
    with TestClient(app) as client:
        response = client.post(
            "/api/drafts/preview",
            json={
                "qa_markdown": (
                    "- (10 points) Question\n  - **Choices**\n    - A. Only choice\n"
                ),
            },
        )
    assert response.status_code == 422
    assert response.json()["location"] == {
        "line": 1,
        "version": "",
        "question_path": [0],
        "rubric_path": None,
    }


def test_preview_explains_ignored_settings_sections_and_continuation_lines(tmp_path):
    source = """---
title: Quiz
unsupported: value
---

- (2 points) Name the process.
  Keep the explanation brief.
  - **Answer**
    - Scheduling
  - **Expected**
    - This is not an accepted answer.
"""
    app = create_app(tmp_path, auth_settings=AuthSettings(), process_jobs=False)
    with TestClient(app) as client:
        response = client.post("/api/drafts/preview", json={"qa_markdown": source})
    assert response.status_code == 200
    preview = response.json()
    assert preview["notes"] == [
        {
            "line": 3,
            "kind": "ignored",
            "message": "Ignored: 'unsupported' is not a supported setting.",
        },
        {
            "line": 7,
            "kind": "continuation",
            "message": "Joined to line 6. Start it with '- ' to make a new item.",
        },
        {
            "line": 10,
            "kind": "ignored",
            "message": "Ignored: 'Expected' sections are not used for grading.",
        },
    ]
    question = preview["document"]["questions"][0]
    assert question["prompt"] == "Name the process.\nKeep the explanation brief."
    assert question["answers"] == ["Scheduling"]


@pytest.mark.parametrize("value", ["1.5", "3", "invalid", "nan"])
def test_invalid_start_page_points_to_the_frontmatter_setting(tmp_path, value):
    source = (
        f"---\ntitle: Quiz\nquestions_start_page: {value}\n---\n"
        "- (2 points) Name the process.\n  - **Answer**\n    - Scheduling\n"
    )
    app = create_app(tmp_path, auth_settings=AuthSettings(), process_jobs=False)
    with TestClient(app) as client:
        response = client.post("/api/drafts/preview", json={"qa_markdown": source})
    assert response.status_code == 422
    assert response.json() == {
        "detail": f"questions_start_page must be 1 or 2, got {value!r}",
        "location": {
            "line": 3,
            "version": "",
            "question_path": [],
            "rubric_path": None,
        },
    }
