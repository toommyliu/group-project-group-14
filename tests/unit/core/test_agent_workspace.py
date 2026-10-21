"""The AI assistant on a workspace assignment: a copy made from the database,
merged back through the service as a grader account of its own."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from opengrader.core.agent import AgentError, load_tasks, record_grade, record_transcription, suggest
from opengrader.core.agent.workspace import (
    KeyFileChoiceNeeded,
    accept_workspace_suggestion,
    agent_folder,
    apply_workspace_plan,
    delete_workspace_packet,
    list_workspace_packets,
    plan_workspace_apply,
    prepare_workspace_key_folder,
    prepare_workspace_packet,
    read_assignment,
    require_workspace,
    resolve_assignment,
    use_workspace_key,
    workspace_key_folders,
)
from opengrader.core.grading.edits import GradeEdit
from opengrader.core.grading.llm_grader import parse_ai_analysis
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.workspace.intake import publish_submission
from opengrader.core.workspace.models import AssignmentInputs
from opengrader.core.workspace.store import Workspace
from opengrader.server.app import create_app

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

- (5 points) What is 2 + 2?
  - **Correct**
    - 4
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-2)
    - Incorrect (-5)
"""

SUBMISSION = """---
student_name: "Ada Lovelace"
student_id: "123456789"
assignment_title: "Quiz 1"
source_pdf: "../../split/Ada_Lovelace.pdf"
name_image: "identifiers/name.png"
scan_warnings: ["Ada_Lovelace.pdf: page 2 was upside down"]
---


- q1:
  - Question type: Free Response
  - Prompt: (10 points) Explain what a loop does.
  - Response image: ![q1 response](responses/q1.png)
  - Response text: I am Ada and a loop runs code again, Lovelace 123456789
  - Possible points: 10
  - Points:
  - Rubric:
  - Analysis:

- q2:
  - Question type: Free Response
  - Prompt: (5 points) What is 2 + 2?
  - Response text: 4
  - Possible points: 5
  - Points: 5
  - Rubric: Correct
  - Analysis: Well done Ada
"""


def _png(path: Path, text: str) -> None:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (600, 120), "white")
    ImageDraw.Draw(image).text((20, 40), text, fill="black")
    image.save(path)


@pytest.fixture
def ws(tmp_path: Path):
    root = tmp_path / "course"
    student = root / "quiz1" / "submissions" / "sub-0001"
    (student / "responses").mkdir(parents=True)
    (student / "identifiers").mkdir()
    (student / "sub-0001.submission.md").write_text(SUBMISSION, encoding="utf-8")
    _png(student / "responses" / "q1.png", "a loop repeats")
    _png(student / "identifiers" / "name.png", "Ada Lovelace")
    (root / "quiz1" / "qa.md").write_text(QA, encoding="utf-8")
    (root / "quiz1" / "split").mkdir()
    (root / "quiz1" / "split" / "Ada_Lovelace.pdf").write_bytes(b"%PDF-scan")
    workspace = Workspace.create(root)
    assignment = workspace.create_assignment(QA)
    workspace.save_assignment_inputs(
        AssignmentInputs(id=assignment.id, folder="quiz1", scan="", output="", submissions="submissions", qa="qa.md")
    )
    publish_submission(
        workspace, assignment.id, load_student_submission_markdown(student / "sub-0001.submission.md"),
        asset_root=student, source_key="sub-0001",
    )
    app = create_app(tmp_path / "server", process_jobs=False)
    app.state.workspaces.base_url = "http://localhost:9"
    item = app.state.registry.register(root)
    with TestClient(app, base_url="http://localhost", client=("127.0.0.1", 1234)) as client:

        def send(method: str, path: str, body=None, *, timeout: float = 10) -> bytes:  # noqa: ANN001
            response = client.request(
                method, item.url_path + path,
                content=body.model_dump_json() if body is not None else None,
                headers={"Content-Type": "application/json"},
            )
            if response.status_code >= 400:
                raise RuntimeError(str(response.json().get("detail")))
            return response.content

        yield {"root": root, "aid": assignment.id, "workspace": workspace, "request": send, "out": tmp_path / "out"}


def _prepare(ws, **kwargs):  # noqa: ANN001, ANN003, ANN202
    return prepare_workspace_packet(ws["root"], ws["aid"], ws["out"], run_as="og-test", check_crops=False, **kwargs)


def _all_text(root: Path) -> str:
    return "\n".join(p.read_bytes().decode("utf-8", "replace") for p in root.rglob("*") if p.is_file())


def _answer(ws, question: str):  # noqa: ANN001, ANN202
    return ws["workspace"].answer(ws["aid"], 1, question)


def _teacher_grades(ws, question: str, label: str) -> None:  # noqa: ANN001
    import uuid

    current = _answer(ws, question)
    ws["workspace"].save_grade(
        ws["aid"], 1, question,
        GradeEdit(
            request_id=uuid.uuid4(), expected_revision=current.revision,
            expected_context_revision=current.context_revision, rubric_labels=[label], analysis="",
        ),
        actor="prof",
    )


def test_a_copy_of_a_workspace_assignment_carries_no_identity_and_only_open_work(ws) -> None:  # noqa: ANN001
    packet = _prepare(ws)

    text = _all_text(packet.path)
    assert "Ada" not in text and "Lovelace" not in text and "123456789" not in text
    assert "upside down" not in text
    assert not list(packet.path.rglob("*.pdf")) and not list(packet.path.rglob("name.png"))
    assert [task.question_id for task in load_tasks(packet.path)] == ["q1"]
    assert list(packet.path.rglob("q1.png")), "the answer image goes in"
    # The key stays in the workspace; the scratch copy with names does not.
    assert packet.key_path.parent == agent_folder(ws["root"].resolve(), ws["aid"]) / ".opengrader" / "handoff"
    assert not [p for p in agent_folder(ws["root"], ws["aid"]).iterdir() if p.name.startswith(".copying-")]
    assert list_workspace_packets(ws["root"], ws["aid"]) == [packet.path]
    # The assistant is fenced off the whole workspace, not only one folder.
    settings = json.loads((packet.path / ".claude" / "settings.json").read_text())
    assert str(ws["root"].resolve()) in settings["sandbox"]["filesystem"]["denyRead"]


def test_the_assistants_grades_come_home_as_ai_grades_through_the_service(ws) -> None:  # noqa: ANN001
    packet = _prepare(ws)
    [task] = load_tasks(packet.path)
    record_grade(packet.path, task.id, ["Correct"], confidence=0.9, rationale="Right.", model="claude")
    record_transcription(packet.path, task.id, "a loop repeats code")

    plan = plan_workspace_apply(ws["root"], ws["aid"], packet.path)
    assert len(plan.grades) == 1 and plan.requests == 0 and plan.readings == 1
    assert "Ada Lovelace · q1: Correct" in "\n".join(plan.lines())

    outcome = apply_workspace_plan(plan, ws["request"])

    assert outcome.count("saved") == 1
    q1, q2 = _answer(ws, "q1"), _answer(ws, "q2")
    assert q1.points_awarded == 10 and parse_ai_analysis(q1.analysis).provider == "agent"
    assert "Ada" in q1.response_text, "the scrub never comes home"
    assert (q2.points_awarded, q2.analysis) == (5, "Well done Ada")
    [entry] = ws["workspace"].history(ws["aid"])
    assert entry["actor"] == plan.actor and plan.actor.startswith("agent:")
    # Merging the same copy again changes nothing.
    assert not plan_workspace_apply(ws["root"], ws["aid"], packet.path).grades


def test_an_answer_graded_meanwhile_becomes_a_change_request_not_a_grade(ws) -> None:  # noqa: ANN001
    from opengrader.core.workspace.sharing import change_requests

    packet = _prepare(ws)
    [task] = load_tasks(packet.path)
    record_grade(packet.path, task.id, ["Correct"], model="claude")
    _teacher_grades(ws, "q1", "Partially Correct")

    plan = plan_workspace_apply(ws["root"], ws["aid"], packet.path)
    assert plan.requests == 1
    outcome = apply_workspace_plan(plan, ws["request"])

    assert outcome.count("requested") == 1 and outcome.count("saved") == 0
    assert _answer(ws, "q1").rubric_labels == ("Partially Correct",)
    [waiting] = change_requests(ws["workspace"], ws["aid"])
    assert waiting.requested_by == plan.actor and waiting.graded_by == "prof"
    assert waiting.rubric_labels == ["Correct"]


def test_a_copy_from_another_assignment_is_refused(ws, tmp_path: Path) -> None:  # noqa: ANN001
    packet = _prepare(ws)
    other = ws["workspace"].create_assignment(QA.replace("Quiz 1", "Quiz 2"))

    with pytest.raises(AgentError, match="not made from this assignment"):
        plan_workspace_apply(ws["root"], other.id, packet.path)
    with pytest.raises(AgentError, match="not an assistant copy"):
        delete_workspace_packet(packet.path, ws["root"], other.id)
    delete_workspace_packet(packet.path, ws["root"], ws["aid"])
    assert not packet.path.exists()


def test_a_fix_goes_into_the_workspace_and_the_file_only_if_the_teacher_says(ws) -> None:  # noqa: ANN001
    packet = _prepare(ws)
    qa_file = ws["root"] / "quiz1" / "qa.md"
    first = suggest(packet.path, question="q1", kind="answer", message="Also right", correct=["runs code again"])
    second = suggest(packet.path, question="q1", kind="answer", message="And this", correct=["iterates"])

    with pytest.raises(KeyFileChoiceNeeded) as asked:
        accept_workspace_suggestion(packet.path, first.id, root=ws["root"], assignment_id=ws["aid"], request=ws["request"])
    assert asked.value.path == qa_file.resolve()

    done = accept_workspace_suggestion(
        packet.path, first.id, root=ws["root"], assignment_id=ws["aid"], request=ws["request"], key_file="leave"
    )
    assert "runs code again" in done
    assert "runs code again" in ws["workspace"].assignment_source(ws["aid"])
    assert qa_file.read_text() == QA, "the file was left alone, as asked"
    assert "runs code again" in (packet.path / "qa.md").read_text(), "the copy grades on with the fix"

    done = accept_workspace_suggestion(
        packet.path, second.id, root=ws["root"], assignment_id=ws["aid"], request=ws["request"], key_file="update"
    )
    # The file differs from the workspace's key now, so only the one question changes.
    assert "only that question" in done
    assert "iterates" in qa_file.read_text() and "runs code again" not in qa_file.read_text()


def test_a_rubric_fix_is_refused_over_a_grade_that_uses_a_dropped_option(ws) -> None:  # noqa: ANN001
    packet = _prepare(ws)
    _teacher_grades(ws, "q1", "Partially Correct")
    made = suggest(
        packet.path, question="q1", kind="rubric", message="Two mistakes",
        partial=[("Off by one", -2), ("No base case", -8)],
    )

    with pytest.raises(AgentError, match="Partially Correct"):
        accept_workspace_suggestion(
            packet.path, made.id, root=ws["root"], assignment_id=ws["aid"], request=ws["request"], key_file="leave"
        )
    assert ws["workspace"].assignment_source(ws["aid"]) == QA


def test_the_assistant_fills_the_key_in_its_own_folder_and_it_is_stored(ws) -> None:  # noqa: ANN001
    _teacher_grades(ws, "q2", "Incorrect")  # graded with an option the new key keeps
    folder = prepare_workspace_key_folder(ws["root"], ws["aid"], run_as="og-test")
    assert folder.path.parent == ws["root"].resolve().parent
    assert workspace_key_folders(ws["root"], ws["aid"]) == [folder.path]
    assert "Ada" not in _all_text(folder.path)
    (folder.path / "qa.md").write_text(QA.replace("    - 4\n", "    - 4\n    - four\n"))

    with pytest.raises(KeyFileChoiceNeeded):
        use_workspace_key(folder.path, root=ws["root"], assignment_id=ws["aid"], request=ws["request"])
    result, note = use_workspace_key(
        folder.path, root=ws["root"], assignment_id=ws["aid"], request=ws["request"], key_file="update"
    )

    assert result.ok and "updated too" in note
    assert "four" in ws["workspace"].assignment_source(ws["aid"])
    assert "four" in (ws["root"] / "quiz1" / "qa.md").read_text()
    assert _answer(ws, "q2").rubric_labels == ("Incorrect",)

    # Changed here since: the folder's key goes over it only on purpose.
    (folder.path / "qa.md").write_text(QA)
    ws_key = ws["workspace"].assignment_source(ws["aid"])
    from opengrader.core.workspace.answer_key import replace_answer_key

    replace_answer_key(ws["workspace"], ws["aid"], ws_key.replace("repeats code", "repeats code\n    - loops"))
    with pytest.raises(AgentError, match="on purpose"):
        use_workspace_key(folder.path, root=ws["root"], assignment_id=ws["aid"], request=ws["request"], key_file="leave")


def test_the_assignment_state_decides_which_jobs_are_offered(ws) -> None:  # noqa: ANN001
    state = read_assignment(ws["root"], ws["aid"])
    assert state.title == "Quiz 1" and state.submission_count == 1
    # "Partially Correct" alone names no mistake: partial credit can be drafted.
    assert state.can_grade and not state.needs_key and state.can_draft_rubrics and state.rubric_gaps == 2
    assert resolve_assignment(ws["root"]) == ws["aid"]
    assert resolve_assignment(ws["root"], "quiz 1") == ws["aid"]


def test_a_class_folder_open_in_the_browser_stays_a_class_folder(tmp_path: Path) -> None:
    from opengrader.core.agent.common import refuse_workspace

    folder = tmp_path / "class"
    (folder / ".opengrader").mkdir(parents=True)
    (folder / ".opengrader" / "workspace.sqlite3").write_bytes(b"")
    (folder / ".opengrader" / "folder-session.json").write_text("{}")

    refuse_workspace(folder / "submissions")  # its files are the truth: allowed
    with pytest.raises(AgentError, match="open in the browser"):
        require_workspace(folder)


def test_the_commands_work_on_a_workspace(ws, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001
    from click.testing import CliRunner

    import opengrader.cli.agent as cli
    from opengrader.cli.agent import command as agent_command

    # The service in this process, never a real one started for the test.
    monkeypatch.setattr(cli, "_workspace_request", lambda root: ws["request"])
    runner = CliRunner()
    where = ["--workspace", str(ws["root"]), "--assignment", "Quiz 1"]

    made = runner.invoke(agent_command, ["prepare", *where, "--out", str(ws["out"]), "--no-crop-check"])
    assert made.exit_code == 0, made.output
    assert "opengrader agent apply --workspace" in made.output
    [packet] = list_workspace_packets(ws["root"], ws["aid"])
    [task] = load_tasks(packet)
    record_grade(packet, task.id, ["Incorrect"], model="claude")
    suggest(packet, question="q1", kind="answer", message="Also right", correct=["loops"])

    preview = runner.invoke(agent_command, ["apply", *where, "--incoming", str(packet)])
    assert preview.exit_code == 0, preview.output
    assert "Nothing was written" in preview.output and _answer(ws, "q1").points_awarded is None

    applied = runner.invoke(agent_command, ["apply", *where, "--incoming", str(packet), "--yes"])
    assert applied.exit_code == 0, applied.output
    assert "Saved 1 AI grade" in applied.output and "suggested 1 fix" in applied.output
    assert _answer(ws, "q1").points_awarded == 0

    unasked = runner.invoke(agent_command, ["accept", "s1", "--incoming", str(packet), *where])
    assert unasked.exit_code == 2 and "--key-file" in unasked.output
    accepted = runner.invoke(agent_command, ["accept", "s1", "--incoming", str(packet), *where, "--key-file", "leave"])
    assert accepted.exit_code == 0, accepted.output
    assert "loops" in ws["workspace"].assignment_source(ws["aid"])

    gone = runner.invoke(agent_command, ["delete", *where, "--incoming", str(packet), "--yes"])
    assert gone.exit_code == 0 and not packet.exists()
    both = runner.invoke(agent_command, ["prepare", *where, "--submissions", "x"])
    assert both.exit_code == 2 and "not both" in both.output
