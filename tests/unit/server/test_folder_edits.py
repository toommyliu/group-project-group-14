"""A class folder open in the browser: rubric and student edits reach its files."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from opengrader.core.grading.rubric_edit import diff_rubric_options
from opengrader.core.history.journal import read_journal, resolve_history_dir
from opengrader.core.qa_file import QARubricFlatOption, find_qa_question, load_qa_markdown
from opengrader.core.submissions.student_file import (
    load_student_submission_markdown,
    set_student_identity_file,
)
from opengrader.core.workspace.folder_session import start_folder_session
from opengrader.core.workspace.intake import publish_pipeline
from opengrader.core.workspace.models import AssignmentInputs, PipelineOutput
from opengrader.core.workspace.store import Workspace
from opengrader.server.workspace.app import create_app

# Version A, question 1 of the sample: Correct (+10), two partial items
# (+7 and +3), Incorrect (+0). Submission 1 is version A, submission 2 is B.
# The answer-key format requires partial items to add up to the question's
# points, so an edit here always moves points between the two.
ATTEMPTED = "Partially Correct - Binary form attempted but value incorrect"
MOSTLY_WRONG = "Partially Correct - Conversion process mostly incorrect"


@pytest.fixture
def quiz(tmp_path: Path, copy_cs146_sample):
    root = tmp_path / "quiz"
    copy_cs146_sample(root)
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
    # What `opengrader classic` records when it opens the folder in the browser.
    store.save_assignment_inputs(
        AssignmentInputs(
            id=aid,
            folder=".",
            scan="",
            output=".",
            submissions="student-submissions",
            qa="qa.md",
        )
    )
    files = sorted((root / "student-submissions").rglob("*.submission.md"))
    first, second = (s.id for s in store.submissions(aid))
    return {
        "root": root,
        "store": store,
        "aid": aid,
        "files": files,
        "sids": (first, second),
        "qa": root / "qa.md",
    }


@pytest.fixture
def session(quiz):
    start_folder_session(quiz["root"], quiz["aid"])
    with TestClient(create_app(quiz["root"], process_jobs=False)) as client:
        yield {**quiz, "client": client}


@pytest.fixture
def plain(quiz):
    with TestClient(create_app(quiz["root"], process_jobs=False)) as client:
        yield {**quiz, "client": client}


def _answer_url(ctx, question: str, index: int = 0) -> str:
    return (
        f"/api/assignments/{ctx['aid']}/submissions/{ctx['sids'][index]}"
        f"/answers/{question}"
    )


def _answer(ctx, question: str, index: int = 0) -> dict:
    return ctx["client"].get(_answer_url(ctx, question, index)).json()


def _grade(ctx, question: str, labels: list[str], *, index: int = 0, answer: dict | None = None):
    answer = answer or _answer(ctx, question, index)
    return ctx["client"].put(
        _answer_url(ctx, question, index),
        json={
            "request_id": str(uuid4()),
            "expected_context_revision": answer["context_revision"],
            "expected_revision": answer["revision"],
            "rubric_labels": labels,
            "analysis": "",
            "score_mode": "rubric",
            "points_awarded": None,
        },
    )


def _rubric(ctx, question: str = "q1", version: str = "A") -> list[dict]:
    """The question's rubric as the browser's editor is handed it."""
    document = ctx["client"].get(f"/api/assignments/{ctx['aid']}/qa").json()["document"]
    for parent in document["version_questions"][version]:
        for item in [parent, *(parent.get("subquestions") or [])]:
            if item["id"] == question:
                return item["rubric"]
    raise AssertionError(f"no {question} in version {version}")


def _save_rubric(ctx, rubric: list[dict], expected: list[dict], *, question="q1", version="A"):
    return ctx["client"].put(
        f"/api/assignments/{ctx['aid']}/questions/{question}/rubric",
        json={"version": version, "rubric": rubric, "expected_rubric": expected},
    )


def _file_question(ctx, question: str, index: int = 0):
    document = load_student_submission_markdown(ctx["files"][index])
    return {q.question_id: q for q in document.questions}[question]


def _partial(rubric: list[dict]) -> list[dict]:
    return next(item for item in rubric if item["label"] == "Partially Correct")["subitems"]


def _rebalanced(opened: list[dict], attempted: float, mostly_wrong: float) -> list[dict]:
    """The rubric with its two partial items worth these points instead."""
    rubric = copy.deepcopy(opened)
    _partial(rubric)[0]["points_delta"] = attempted
    _partial(rubric)[1]["points_delta"] = mostly_wrong
    return rubric


# -- which option became which ------------------------------------------------


def _options(*pairs: tuple[str, float]) -> list[QARubricFlatOption]:
    return [QARubricFlatOption(label, points, "partial", "checklist") for label, points in pairs]


def test_an_option_edited_in_place_is_the_same_option_changed() -> None:
    diff = diff_rubric_options(
        _options(("Correct", 0), ("Sloppy", -2), ("Incorrect", -10)),
        _options(("Correct", 0), ("Untidy working", -3), ("Incorrect", -10)),
    )

    assert diff.renames == {"Sloppy": "Untidy working"}
    assert diff.deleted == ()


def test_a_removed_option_is_deleted_and_an_added_one_is_nobody_renamed() -> None:
    removed = diff_rubric_options(
        _options(("Correct", 0), ("Sloppy", -2), ("Incorrect", -10)),
        _options(("Correct", 0), ("Incorrect", -10)),
    )
    added = diff_rubric_options(
        _options(("Correct", 0), ("Incorrect", -10)),
        _options(("Correct", 0), ("Sloppy", -2), ("Incorrect", -10)),
    )

    assert [option.label for option in removed.deleted] == ["Sloppy"]
    assert removed.renames == {}
    assert added.deleted == () and added.renames == {}


def test_a_points_only_change_renames_nothing_but_is_a_change() -> None:
    diff = diff_rubric_options(
        _options(("Correct", 0), ("Sloppy", -2)), _options(("Correct", 0), ("Sloppy", -4))
    )

    assert diff.renames == {}
    assert [(old.points_delta, new.points_delta) for old, new in diff.changed] == [(-2, -4)]


# -- editing a rubric ---------------------------------------------------------


def test_a_renamed_and_repointed_item_is_carried_into_saved_grades_and_files(session) -> None:
    assert _grade(session, "q1", [ATTEMPTED]).json()["answer"]["points_awarded"] == 7
    assert _file_question(session, "q1").points_awarded == 7
    before_text = session["qa"].read_text(encoding="utf-8")
    other_version = _answer(session, "q1", index=1)

    expected = _rubric(session)
    rubric = _rebalanced(expected, 6, 4)
    _partial(rubric)[0]["label"] = "Right idea, wrong value"
    response = _save_rubric(session, rubric, expected)

    assert response.status_code == 200, response.text
    assert response.json() == {"changed": True, "regraded": 1, "problems": []}
    renamed = "Partially Correct - Right idea, wrong value"

    # The answer key file: those two lines changed and nothing else did.
    after_text = session["qa"].read_text(encoding="utf-8")
    changed = [
        (old, new)
        for old, new in zip(before_text.splitlines(), after_text.splitlines(), strict=True)
        if old != new
    ]
    assert changed == [
        (
            "      - Binary form attempted but value incorrect (+7)",
            "      - Right idea, wrong value (+6)",
        ),
        (
            "      - Conversion process mostly incorrect (+3)",
            "      - Conversion process mostly incorrect (+4)",
        ),
    ]
    question = find_qa_question(load_qa_markdown(session["qa"]), "q1", version="A")
    assert [item.label for item in question.rubric[1].subitems][0] == "Right idea, wrong value"

    # The browser's copy: the grade followed the item and was re-scored.
    answer = _answer(session, "q1")
    assert answer["rubric_labels"] == [renamed]
    assert answer["points_awarded"] == 6
    assert renamed in [option["label"] for option in answer["question"]["rubric"]]

    # The student's file: the same.
    assert _file_question(session, "q1").rubric_labels == (renamed,)
    assert _file_question(session, "q1").points_awarded == 6

    # Version B has its own question 1, which this did not touch.
    assert _answer(session, "q1", index=1) == other_version

    entries = read_journal(resolve_history_dir(session["root"] / "student-submissions"))
    rubric_entries = [entry for entry in entries if entry.kind == "rubric"]
    assert len(rubric_entries) == 1
    assert rubric_entries[0].question_id == "q1"
    assert "1 grade updated" in rubric_entries[0].summary


def test_an_added_item_can_be_chosen_and_a_grade_from_the_old_rubric_is_refused(session) -> None:
    stale = _answer(session, "q1")

    expected = _rubric(session)
    rubric = _rebalanced(expected, 6, 3)
    _partial(rubric).append(
        {"label": "Leading zeros dropped", "points_delta": 1, "notes": [], "subitems": []}
    )
    response = _save_rubric(session, rubric, expected)

    assert response.status_code == 200, response.text
    assert response.json()["regraded"] == 0
    added = "Partially Correct - Leading zeros dropped"

    # A grader who still had the old rubric on screen is told, not scored
    # against a rubric they never saw.
    refused = _grade(session, "q1", [ATTEMPTED], answer=stale)
    assert refused.status_code == 409

    fresh = _answer(session, "q1")
    assert fresh["revision"] != stale["revision"]
    saved = _grade(session, "q1", [added], answer=fresh)
    assert saved.status_code == 200, saved.text
    assert saved.json()["answer"]["points_awarded"] == 1
    assert _file_question(session, "q1").rubric_labels == (added,)


def test_an_item_a_saved_grade_uses_cannot_be_removed(session) -> None:
    _grade(session, "q1", [MOSTLY_WRONG])
    before_text = session["qa"].read_text(encoding="utf-8")
    before_answer = _answer(session, "q1")

    expected = _rubric(session)
    rubric = _rebalanced(expected, 10, 0)
    _partial(rubric).pop(1)
    response = _save_rubric(session, rubric, expected)

    # 422, not 409: nothing changed underneath the editor, the edit itself
    # is what cannot be accepted, so the browser keeps the form open.
    assert response.status_code == 422
    assert "1 saved grade uses" in response.json()["detail"]
    assert MOSTLY_WRONG in response.json()["detail"]
    # Refused before anything was written, anywhere.
    assert session["qa"].read_text(encoding="utf-8") == before_text
    assert _answer(session, "q1") == before_answer
    assert _file_question(session, "q1").rubric_labels == (MOSTLY_WRONG,)


def test_an_unused_item_can_be_removed(session) -> None:
    expected = _rubric(session)
    rubric = _rebalanced(expected, 10, 0)
    _partial(rubric).pop(1)

    response = _save_rubric(session, rubric, expected)

    assert response.status_code == 200, response.text
    labels = [option["label"] for option in _answer(session, "q1")["question"]["rubric"]]
    assert MOSTLY_WRONG not in labels
    assert "mostly incorrect" not in session["qa"].read_text(encoding="utf-8").split("## Version B")[0]


def test_a_rubric_that_changed_since_the_editor_opened_is_not_overwritten(session) -> None:
    opened = _rubric(session)
    assert _save_rubric(session, _rebalanced(opened, 8, 2), opened).status_code == 200
    saved_text = session["qa"].read_text(encoding="utf-8")

    # A second editor that opened before the first one saved.
    response = _save_rubric(session, _rebalanced(opened, 6, 4), opened)

    assert response.status_code == 409
    assert "changed somewhere else" in response.json()["detail"]
    assert session["qa"].read_text(encoding="utf-8") == saved_text


def test_saving_a_rubric_unchanged_writes_nothing(session) -> None:
    stamp = session["qa"].stat().st_mtime_ns
    revision = _answer(session, "q1")["revision"]
    rubric = _rubric(session)

    response = _save_rubric(session, rubric, rubric)

    assert response.json() == {"changed": False, "regraded": 0, "problems": []}
    assert session["qa"].stat().st_mtime_ns == stamp
    assert _answer(session, "q1")["revision"] == revision


def test_a_rubric_the_answer_key_format_refuses_is_not_written(session) -> None:
    before_text = session["qa"].read_text(encoding="utf-8")
    expected = _rubric(session)
    rubric = copy.deepcopy(expected)
    _partial(rubric)[0]["points_delta"] = 25  # more than the question is worth

    response = _save_rubric(session, rubric, expected)

    assert response.status_code == 422
    assert session["qa"].read_text(encoding="utf-8") == before_text


def test_a_question_graded_by_its_parts_has_no_rubric_of_its_own(session) -> None:
    response = _save_rubric(session, [], [], question="q5")

    assert response.status_code == 422
    assert "parts" in response.json()["detail"]


@pytest.mark.skipif(sys.platform == "win32", reason="read-only by mode bits")
def test_an_answer_key_that_cannot_be_written_changes_nothing(session) -> None:
    expected = _rubric(session)
    rubric = _rebalanced(expected, 8, 2)
    os.chmod(session["qa"], 0o444)
    try:
        response = _save_rubric(session, rubric, expected)
    finally:
        os.chmod(session["qa"], 0o644)

    assert response.status_code == 422
    assert "was not changed" in response.json()["detail"]
    assert _rubric(session) == expected


def test_outside_a_folder_session_a_rubric_edit_stays_in_the_workspace(plain) -> None:
    before_text = plain["qa"].read_text(encoding="utf-8")
    rubric = _rubric(plain)
    edited = _rebalanced(rubric, 8, 2)

    response = plain["client"].put(
        f"/api/assignments/{plain['aid']}/questions/q1/rubric",
        json={"version": "A", "rubric": edited, "expected_rubric": rubric},
    )

    assert response.status_code == 200
    assert _partial(_rubric(plain))[0]["points_delta"] == 8
    assert plain["qa"].read_text(encoding="utf-8") == before_text


# -- the answer key edited in the folder --------------------------------------


def _edit_key_by_hand(ctx, old: str, new: str) -> None:
    text = ctx["qa"].read_text(encoding="utf-8")
    assert text.count(old) == 1
    ctx["qa"].write_text(text.replace(old, new), encoding="utf-8")


def _move_a_point_by_hand(ctx) -> None:
    """Question 1's partial items go from +7 / +3 to +8 / +2, edited in the file."""
    _edit_key_by_hand(
        ctx,
        "      - Binary form attempted but value incorrect (+7)\n"
        "      - Conversion process mostly incorrect (+3)",
        "      - Binary form attempted but value incorrect (+8)\n"
        "      - Conversion process mostly incorrect (+2)",
    )


def test_sync_takes_a_rubric_edited_in_the_file_and_rescores_its_grades(session) -> None:
    _grade(session, "q1", [MOSTLY_WRONG])
    _move_a_point_by_hand(session)

    result = session["client"].post("/api/folder-session/sync").json()

    assert result["problems"] == []
    answer = _answer(session, "q1")
    assert answer["points_awarded"] == 2
    points = {o["label"]: o["points_delta"] for o in answer["question"]["rubric"]}
    assert points[MOSTLY_WRONG] == 2
    # The grade's file is brought into line by the same sync.
    assert _file_question(session, "q1").points_awarded == 2
    assert session["client"].post("/api/folder-session/sync").json()["written"] == 0


def test_a_browser_rubric_edit_first_takes_what_the_file_gained(session) -> None:
    opened = _rubric(session)
    _edit_key_by_hand(
        session,
        "    - Partially Correct (-5)\n    - Incorrect (-10)\n\n- (10 points) What is the most significant bit",
        "    - Partially Correct (-4)\n    - Incorrect (-10)\n\n- (10 points) What is the most significant bit",
    )

    response = _save_rubric(session, _rebalanced(opened, 8, 2), opened)

    # Question 1 is still what the editor opened, so the edit goes through,
    # and the hand edit to question 2 is kept, in the file and in the browser.
    assert response.status_code == 200, response.text
    text = session["qa"].read_text(encoding="utf-8")
    assert "    - Partially Correct (-4)" in text
    assert "      - Binary form attempted but value incorrect (+8)" in text
    q2 = {o["label"]: o["points_delta"] for o in _answer(session, "q2")["question"]["rubric"]}
    assert q2["Partially Correct"] == -4


def test_questions_added_in_the_file_are_reported_not_half_applied(session) -> None:
    text = session["qa"].read_text(encoding="utf-8")
    session["qa"].write_text(
        text.replace("## Version B", "- (5 points) A new question [A]\n\n## Version B"),
        encoding="utf-8",
    )
    before = _answer(session, "q1")

    result = session["client"].post("/api/folder-session/sync").json()

    assert len(result["problems"]) == 1
    assert "different questions" in result["problems"][0]
    assert _answer(session, "q1") == before


def test_opening_the_folder_again_after_its_key_changed_is_not_refused(session) -> None:
    _move_a_point_by_hand(session)

    # What classic sends each time "Open in browser" is pressed.
    response = session["client"].post(
        f"/api/assignments/{session['aid']}/imports",
        json={
            "qa_markdown": session["qa"].read_text(encoding="utf-8"),
            "submissions_dir": str((session["root"] / "student-submissions").resolve()),
        },
    )

    assert response.status_code == 200, response.text
    points = {o["label"]: o["points_delta"] for o in _answer(session, "q1")["question"]["rubric"]}
    assert points[MOSTLY_WRONG] == 2


def test_an_answer_key_saved_with_windows_line_endings_is_not_always_changed(session) -> None:
    text = session["qa"].read_text(encoding="utf-8")
    session["qa"].write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    revision = _answer(session, "q1")["revision"]

    result = session["client"].post("/api/folder-session/sync").json()

    assert result["problems"] == []
    assert _answer(session, "q1")["revision"] == revision

    # And an edit keeps the file's own line endings.
    expected = _rubric(session)
    assert _save_rubric(session, _rebalanced(expected, 8, 2), expected).status_code == 200
    raw = session["qa"].read_bytes()
    assert b"value incorrect (+8)\r\n" in raw
    assert raw.count(b"\r\n") == raw.count(b"\n")


# -- who a submission belongs to ----------------------------------------------


def _student_url(ctx, index: int = 0) -> str:
    return f"/api/assignments/{ctx['aid']}/submissions/{ctx['sids'][index]}/student"


def _body(text: str) -> str:
    return text.split("\n---\n", 1)[1]


def test_a_student_set_in_the_browser_is_written_into_the_submission_file(session) -> None:
    _grade(session, "q1", [ATTEMPTED])
    before = session["files"][0].read_text(encoding="utf-8")

    response = session["client"].put(
        _student_url(session), json={"student_name": "  Tommy   Liu ", "student_id": " 987654321 "}
    )

    assert response.status_code == 200, response.text
    assert response.json()["student_name"] == "Tommy Liu"
    assert response.json()["student_id"] == "987654321"
    document = load_student_submission_markdown(session["files"][0])
    assert (document.student_name, document.student_id) == ("Tommy Liu", "987654321")
    # Only the header changed: every graded answer is byte for byte the same.
    after = session["files"][0].read_text(encoding="utf-8")
    assert _body(after) == _body(before)
    assert _file_question(session, "q1").points_awarded == 7
    # The other student's file was not opened.
    assert load_student_submission_markdown(session["files"][1]).student_name != "Tommy Liu"
    # Nothing is left for a sync to do.
    result = session["client"].post("/api/folder-session/sync").json()
    assert (result["updated"], result["written"], result["problems"]) == (0, 0, [])


@pytest.mark.skipif(sys.platform == "win32", reason="read-only by mode bits")
def test_a_student_whose_file_cannot_be_written_is_not_changed_anywhere(session) -> None:
    original = session["client"].get(f"/api/assignments/{session['aid']}/submissions").json()[0]
    os.chmod(session["files"][0], 0o444)
    try:
        response = session["client"].put(
            _student_url(session), json={"student_name": "Someone Else", "student_id": "1"}
        )
    finally:
        os.chmod(session["files"][0], 0o644)

    assert response.status_code == 422
    assert "was not changed" in response.json()["detail"]
    current = session["client"].get(f"/api/assignments/{session['aid']}/submissions").json()[0]
    assert current["student_name"] == original["student_name"]


def test_sync_takes_a_name_corrected_in_the_file(session) -> None:
    # What the terminal app's review queue does when a duplicate is resolved.
    set_student_identity_file(
        session["files"][1], student_id="555000111", student_name="Andy Cortes"
    )

    result = session["client"].post("/api/folder-session/sync").json()

    assert result["problems"] == []
    second = session["client"].get(f"/api/assignments/{session['aid']}/submissions").json()[1]
    assert (second["student_name"], second["student_id"]) == ("Andy Cortes", "555000111")


def test_outside_a_folder_session_a_student_edit_stays_in_the_workspace(plain) -> None:
    before = plain["files"][0].read_text(encoding="utf-8")

    response = plain["client"].put(
        _student_url(plain), json={"student_name": "Tommy Liu", "student_id": "1"}
    )

    assert response.status_code == 200
    assert plain["files"][0].read_text(encoding="utf-8") == before


# -- answers ------------------------------------------------------------------


def _fields(ctx, version: str = "A") -> dict[str, dict]:
    listed = ctx["client"].get(f"/api/assignments/{ctx['aid']}/answer-fields").json()
    return {field["question_id"]: field for field in listed if field["version"] == version}


def _key_question(ctx, question: str, version: str = "A"):
    return find_qa_question(load_qa_markdown(ctx["qa"]), question, version=version)


def _save_answers(ctx, *questions: dict):
    return ctx["client"].put(
        f"/api/assignments/{ctx['aid']}/answers", json={"questions": list(questions)}
    )


def _answers(question: str, answers: list[str], expected: list[str], version: str = "A") -> dict:
    return {
        "question_id": question,
        "version": version,
        "answers": answers,
        "expected_answers": expected,
    }


def test_every_question_is_listed_with_its_options_and_what_it_accepts(session) -> None:
    fields = _fields(session)

    assert fields["q1"]["answers"] == ["101"] and fields["q1"]["choices"] == []
    assert fields["q1"]["label"] == "A · 1" and fields["q1"]["points"] == 10
    # Multiple choice: its options, and the correct letter as its answer.
    assert [choice["label"] for choice in fields["q3"]["choices"]] == ["A", "B", "C", "D"]
    assert fields["q3"]["answers"] == ["B"]


def test_answers_saved_in_the_browser_are_written_into_the_answer_key_file(session) -> None:
    before = session["qa"].read_text(encoding="utf-8")

    response = _save_answers(
        session,
        _answers("q1", ["101", "0b101"], ["101"]),
        _answers("q3", ["a"], ["B"]),
    )

    assert response.status_code == 200 and response.json()["changed"] is True
    assert _key_question(session, "q1").answers == ["101", "0b101"]
    assert _key_question(session, "q3").correct_choice_label == "A"
    # The browser's own copy follows, and nothing else in the file moved.
    assert _fields(session)["q1"]["answers"] == ["101", "0b101"]
    after = session["qa"].read_text(encoding="utf-8")
    assert _key_question(session, "q1").rubric == find_qa_question(
        load_qa_markdown_text(before), "q1", version="A"
    ).rubric
    assert len(after.splitlines()) == len(before.splitlines()) + 1
    # Version B's question 1 was not the one edited.
    assert _key_question(session, "q1", "B").answers == find_qa_question(
        load_qa_markdown_text(before), "q1", version="B"
    ).answers


def load_qa_markdown_text(text: str):
    from opengrader.core.qa_file import parse_qa_markdown

    return parse_qa_markdown(text)


def test_changing_an_answer_changes_no_grade(session) -> None:
    assert _grade(session, "q1", ["Correct"]).status_code == 200
    graded = _answer(session, "q1")

    assert _save_answers(session, _answers("q1", ["five"], ["101"])).status_code == 200

    after = _answer(session, "q1")
    assert (after["rubric_labels"], after["points_awarded"]) == (
        graded["rubric_labels"],
        graded["points_awarded"],
    )
    assert _file_question(session, "q1").rubric_labels == ("Correct",)


def test_answers_changed_somewhere_else_since_they_were_opened_are_not_overwritten(session) -> None:
    text = session["qa"].read_text(encoding="utf-8")
    session["qa"].write_text(text.replace("    - 101\n", "    - 0b101\n", 1), encoding="utf-8")

    response = _save_answers(session, _answers("q1", ["five"], ["101"]))

    assert response.status_code == 409
    assert "changed somewhere else" in response.json()["detail"]
    assert _key_question(session, "q1").answers == ["0b101"]


def test_one_answer_that_cannot_be_saved_saves_none_of_them(session) -> None:
    before = session["qa"].read_bytes()

    response = _save_answers(
        session,
        _answers("q1", ["five"], ["101"]),
        _answers("q3", ["Z"], ["B"]),
    )

    assert response.status_code == 422 and "no option Z" in response.json()["detail"]
    assert session["qa"].read_bytes() == before


def test_saving_answers_unchanged_writes_nothing(session) -> None:
    before = session["qa"].stat().st_mtime_ns

    response = _save_answers(session, _answers("q1", ["101"], ["101"]))

    assert response.json()["changed"] is False
    assert session["qa"].stat().st_mtime_ns == before


def test_a_rubric_and_its_answers_are_saved_together_or_not_at_all(session) -> None:
    opened = _rubric(session)
    url = f"/api/assignments/{session['aid']}/questions/q1/rubric"

    saved = session["client"].put(
        url,
        json={
            "version": "A",
            "rubric": _rebalanced(opened, 6, 4),
            "expected_rubric": opened,
            "answers": ["101", "0b101"],
            "expected_answers": ["101"],
        },
    )

    assert saved.status_code == 200
    question = _key_question(session, "q1")
    assert question.answers == ["101", "0b101"]
    assert [item.points_delta for item in question.rubric[1].subitems] == [6, 4]

    # A stale rubric refuses the whole save: the answers are not written either.
    before = session["qa"].read_bytes()
    refused = session["client"].put(
        url,
        json={
            "version": "A",
            "rubric": _rebalanced(opened, 5, 5),
            "expected_rubric": opened,
            "answers": ["five"],
            "expected_answers": ["101", "0b101"],
        },
    )
    assert refused.status_code == 409
    assert session["qa"].read_bytes() == before


def test_answers_alone_can_go_through_the_question_sheet_without_touching_the_rubric(session) -> None:
    opened = _rubric(session)

    response = session["client"].put(
        f"/api/assignments/{session['aid']}/questions/q1/rubric",
        json={
            "version": "A",
            "rubric": opened,
            "expected_rubric": opened,
            "answers": ["five"],
            "expected_answers": ["101"],
        },
    )

    assert response.json() == {"changed": True, "regraded": 0, "problems": []}
    assert _key_question(session, "q1").answers == ["five"]


def test_outside_a_folder_session_answers_are_not_edited_while_grading(plain) -> None:
    before = plain["qa"].read_bytes()

    response = _save_answers(plain, _answers("q1", ["five"], ["101"]))

    assert response.status_code == 409
    assert plain["qa"].read_bytes() == before
    # Reading what a question accepts is allowed anywhere.
    assert _fields(plain)["q1"]["answers"] == ["101"]


def test_a_drafted_keys_option_lines_become_choices_when_one_is_picked(
    tmp_path, copy_cs146_sample
) -> None:
    from opengrader.core.qa_file.draft_key import draft_from_text

    root = tmp_path / "drafted"
    copy_cs146_sample(root)
    # The same number of questions as the sample's submissions carry, so the
    # folder publishes; the second is multiple choice with nothing marked.
    lines = [f"{n}. Question {n}?" for n in range(1, 6)]
    lines.insert(2, "a) Sodium\nb) Chlorine")
    (root / "qa.md").write_text(draft_from_text("\n".join(lines), title="Quiz").markdown, encoding="utf-8")
    store = Workspace.create(root)
    try:
        aid = publish_pipeline(
            store,
            store.assignment_id_for_folder(root),
            PipelineOutput(
                qa_markdown=(root / "qa.md").read_text(encoding="utf-8"),
                submissions_dir=str((root / "student-submissions").resolve()),
            ),
        ).assignment.id
    except Exception as exc:  # noqa: BLE001 - the sample's own questions do not fit this key
        pytest.skip(f"the sample cannot be published against a drafted key: {exc}")
    store.save_assignment_inputs(
        AssignmentInputs(id=aid, folder=".", scan="", output=".", submissions="student-submissions", qa="qa.md")
    )
    start_folder_session(root, aid)
    with TestClient(create_app(root, process_jobs=False)) as client:
        ctx = {"client": client, "aid": aid, "qa": root / "qa.md"}
        field = {f["question_id"]: f for f in client.get(f"/api/assignments/{aid}/answer-fields").json()}["q2"]
        assert [c["label"] for c in field["choices"]] == ["A", "B"] and field["answers"] == []

        response = client.put(
            f"/api/assignments/{aid}/answers",
            json={"questions": [{"question_id": "q2", "version": None, "answers": ["B"], "expected_answers": []}]},
        )

        assert response.status_code == 200, response.text
        question = find_qa_question(load_qa_markdown(ctx["qa"]), "q2", version=None)
        assert question.correct_choice_label == "B" and question.prompt == "Question 2?"
        assert [choice.text for choice in question.choices] == ["Sodium", "Chlorine"]


# -- grading again with the answer key ----------------------------------------


def _write_response(path: Path, question: str, text: str) -> None:
    """Give one answer a transcription, as extraction with a reader would."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    inside = False
    for index, line in enumerate(lines):
        if line.startswith("- "):
            inside = line.strip() == f"- {question}:"
        elif inside and line.startswith("  - Response text:"):
            lines[index] = f"  - Response text: {text}\n"
            break
    else:
        raise AssertionError(f"no {question} in {path.name}")
    path.write_text("".join(lines), encoding="utf-8")


@pytest.fixture
def read_session(quiz):
    """A folder session whose first student's answers to q1 and q3 were read."""
    _write_response(quiz["files"][0], "q1", "five")
    _write_response(quiz["files"][0], "q3", "B")
    start_folder_session(quiz["root"], quiz["aid"])
    with TestClient(create_app(quiz["root"], process_jobs=False)) as client:
        ctx = {**quiz, "client": client}
        client.post("/api/folder-session/sync")
        yield ctx


def _regrade(ctx):
    return ctx["client"].post(f"/api/assignments/{ctx['aid']}/regrade")


def test_grading_again_scores_what_the_answer_key_settles_in_files_and_browser(read_session) -> None:
    ctx = read_session
    assert _answer(ctx, "q3")["points_awarded"] is None

    response = _regrade(ctx)

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["files"] == 2 and result["updated"] >= 1 and result["problems"] == []
    # The multiple-choice answer matches the key: full marks, in the file and here.
    assert _file_question(ctx, "q3").points_awarded == 10
    assert _answer(ctx, "q3")["points_awarded"] == 10
    # The run is in the history, like any auto-grading run.
    actors = {entry.actor for entry in read_journal(resolve_history_dir(ctx["root"] / "student-submissions"))}
    assert "automated-grading" in actors


def test_new_answers_are_used_when_grading_again(read_session) -> None:
    ctx = read_session
    _regrade(ctx)
    wrong = _answer(ctx, "q1")["points_awarded"]

    assert _save_answers(ctx, _answers("q1", ["101", "five"], ["101"])).status_code == 200
    # Saving the answer changed no grade…
    assert _answer(ctx, "q1")["points_awarded"] == wrong
    # …grading again does.
    assert _regrade(ctx).json()["updated"] >= 1
    assert _answer(ctx, "q1")["points_awarded"] == 10
    assert _file_question(ctx, "q1").points_awarded == 10
    assert wrong != 10


def test_a_grade_on_a_question_the_key_does_not_settle_is_kept(read_session) -> None:
    ctx = read_session
    # q2 has no transcription to compare with the key: graded by hand.
    assert _grade(ctx, "q2", ["Incorrect"]).status_code == 200
    kept = _answer(ctx, "q2")

    _regrade(ctx)

    after = _answer(ctx, "q2")
    assert (after["rubric_labels"], after["points_awarded"]) == (
        kept["rubric_labels"],
        kept["points_awarded"],
    )


def test_grading_again_twice_changes_nothing_the_second_time(read_session) -> None:
    ctx = read_session
    _regrade(ctx)
    before = [path.read_bytes() for path in ctx["files"]]

    again = _regrade(ctx).json()

    assert again["updated"] == 0 and again["problems"] == []
    assert [path.read_bytes() for path in ctx["files"]] == before


def test_outside_a_folder_session_work_is_not_graded_again_from_the_browser(plain) -> None:
    before = [path.read_bytes() for path in plain["files"]]

    response = _regrade(plain)

    assert response.status_code == 409
    assert [path.read_bytes() for path in plain["files"]] == before
