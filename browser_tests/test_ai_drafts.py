"""Draft grades with a local AI from the grade page, against a fake Ollama server."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
from pathlib import Path
from threading import Event, Thread
from time import sleep

from PIL import Image
import httpx
import pytest
from playwright.sync_api import expect

from opengrader.core import app_config
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.workspace.intake import publish_submission
from opengrader.core.workspace.store import Workspace

MODEL = "fake-grader"
READER = "fake-reader"
HANDWRITING = "Because the loop runs n times."
SUGGESTION = {
    "rubric_labels": ["Partially Correct"],
    "confidence": 0.9,
    "rationale": "Mostly right.",
}


@pytest.fixture
def rubric_reply():
    return {
        "partial_credit": [
            {
                "description": "Missing base case",
                "deduction": 4,
                "comment": "Add a base case.",
            },
            {
                "description": "Missing recursive call",
                "deduction": 6,
                "comment": "Explain the recursive call.",
            },
        ],
        "rationale": "The answers omit different parts of the explanation.",
    }


@pytest.fixture
def provider_requests():
    return [], Event()


@pytest.fixture
def fake_ollama(tmp_path, monkeypatch, rubric_reply, provider_requests):
    prompts: list[str] = []
    images: list[str] = []
    gate = Event()
    gate.set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply({"models": [{"name": MODEL}, {"name": READER}]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            provider_requests[0].append(body)
            provider_requests[1].set()
            gate.wait(10)
            if body.get("images"):
                images.append(body["model"])
                reply = {"text": HANDWRITING}
            else:
                prompts.append(body["prompt"])
                if "partial_credit" in body.get("format", {}).get("properties", {}):
                    reply = rubric_reply
                else:
                    labels = re.findall(
                        r'^  - "(.+?)" \(', body["prompt"], re.MULTILINE
                    )
                    label = (
                        "Partially Correct"
                        if "Partially Correct" in labels
                        else labels[0]
                    )
                    reply = {**SUGGESTION, "rubric_labels": [label]}
            self.reply({"response": json.dumps(reply), "done": True})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "home/.config"))
    app_config.save_settings(
        app_config.AISettings(
            provider="ollama",
            host=f"http://127.0.0.1:{server.server_port}",
            text_model=MODEL,
            vision_model=READER,
        )
    )
    yield prompts, images, gate
    gate.set()
    server.shutdown()


def status(page, text):
    return page.get_by_role("status").filter(
        has_text=re.compile(rf"^{re.escape(text)}$")
    )


@pytest.fixture(params=[False, True], ids=["workspace", "folder"])
def rubric_workspace(request, browser_app, tmp_path):
    from opengrader.core.workspace.folder_session import start_folder_session
    from opengrader.core.workspace.models import AssignmentInputs

    workspace = Workspace(tmp_path / "course")
    if request.param:
        (workspace.root / "qa.md").write_text(
            workspace.assignment_source(browser_app.assignment_id)
        )
        workspace.save_assignment_inputs(
            AssignmentInputs(
                id=browser_app.assignment_id,
                folder=".",
                scan="",
                output=".",
                submissions=".",
                qa="qa.md",
            )
        )
        start_folder_session(workspace.root, browser_app.assignment_id)
    return workspace


def test_rubric_draft_is_reviewed_edited_and_saved(
    page, browser_app, fake_ollama, rubric_workspace, output_path
):
    prompts, _images, _gate = fake_ollama
    original = rubric_workspace.assignment_source(browser_app.assignment_id)
    page.goto(browser_app.grade_url)
    edit = page.get_by_role("button", name=re.compile(r"^Edit (answers and )?rubric$"))
    edit.click()
    sheet = page.get_by_role("dialog", name=re.compile(r"Edit (answers and )?rubric"))
    draft = sheet.get_by_role("button", name="Draft with AI", exact=True)
    draft.focus()
    page.keyboard.press("Enter")
    first = sheet.get_by_role("textbox", name="Criterion 1", exact=True)
    expect(first).to_have_value("Missing base case")
    expect(
        sheet.get_by_text("The answers omit different parts of the explanation.")
    ).to_be_visible()
    assert rubric_workspace.assignment_source(browser_app.assignment_id) == original
    assert page.request.get(browser_app.answer_url).json()["points_awarded"] is None
    assert len(prompts) == 1

    first.fill("No stopping condition")
    page.set_viewport_size({"width": 320, "height": 800})
    sheet.get_by_role("button", name="Save", exact=True).scroll_into_view_if_needed()
    page.screenshot(
        path=str(Path(output_path) / "rubric-preview-narrow.png"), full_page=True
    )
    sheet.get_by_role("button", name="Save", exact=True).click()
    expect(sheet).to_have_count(0)
    page.reload()
    expect(
        page.get_by_role("button", name=re.compile("No stopping condition"))
    ).to_be_visible()
    saved = rubric_workspace.assignment_source(browser_app.assignment_id)
    assert "No stopping condition (-4)" in saved
    assert "Missing recursive call (-6)" in saved
    assert "Add a base case." in saved
    if (rubric_workspace.root / "qa.md").exists():
        assert (rubric_workspace.root / "qa.md").read_text() == saved
    (Path(output_path) / "accepted-qa.md").write_text(saved)
    (Path(output_path) / "rubric-prompts.json").write_text(
        json.dumps(prompts, indent=2)
    )


def test_single_distinct_mistake_can_be_reviewed_saved_and_used(
    page, browser_app, fake_ollama, rubric_reply, rubric_workspace, output_path
):
    # The live local model returned endpoints and duplicated its only usable item.
    rubric_reply.update(
        partial_credit=[
            {"description": "Correct answer", "deduction": 0},
            {"description": "Incorrect answer", "deduction": 10},
            {
                "description": "Answer close but incorrect",
                "deduction": 5,
                "comment": "Close but wrong, 5 points deducted.",
            },
            {"description": "Answer close but incorrect", "deduction": 5},
        ]
    )
    original = rubric_workspace.assignment_source(browser_app.assignment_id)
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name=re.compile(r"^Edit (answers and )?rubric$")).click()
    sheet = page.get_by_role("dialog", name=re.compile(r"Edit (answers and )?rubric"))
    sheet.get_by_role("button", name="Draft with AI", exact=True).click()
    expect(
        sheet.get_by_role("textbox", name="Feedback for Partially Correct", exact=True)
    ).to_have_value("Close but wrong, 5 points deducted.")
    assert rubric_workspace.assignment_source(browser_app.assignment_id) == original
    sheet.get_by_role("button", name="Save", exact=True).click()
    expect(sheet).to_have_count(0)
    page.get_by_role("button", name=re.compile(r"^Partially Correct")).click()
    expect(page.get_by_role("spinbutton", name="Score", exact=True)).to_have_value("5")
    expect(status(page, "Saved")).to_be_visible()
    page.reload()
    expect(page.get_by_role("spinbutton", name="Score", exact=True)).to_have_value("5")
    artifact = Path(output_path)
    page.screenshot(path=str(artifact / "single-mistake-rubric.png"), full_page=True)
    (artifact / "single-mistake-qa.md").write_text(
        rubric_workspace.assignment_source(browser_app.assignment_id)
    )


@pytest.mark.parametrize(
    ("rejected", "detail"),
    [
        ({}, "list of partial-credit criteria"),
        (
            {"partial_credit": [{"description": "Incorrect answer", "deduction": 10}]},
            '“Incorrect answer” deducts 10 points',
        ),
    ],
    ids=["missing-criteria", "full-deduction"],
)
def test_invalid_rubric_draft_preserves_editor_and_saved_key(
    page, browser_app, fake_ollama, rubric_reply, rubric_workspace, output_path,
    rejected, detail,
):
    rubric_reply.clear()
    rubric_reply.update(rejected)
    original = rubric_workspace.assignment_source(browser_app.assignment_id)
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name=re.compile(r"^Edit (answers and )?rubric$")).click()
    sheet = page.get_by_role("dialog", name=re.compile(r"Edit (answers and )?rubric"))
    feedback = sheet.get_by_role("textbox", name="Feedback for Correct", exact=True)
    feedback.fill("Keep this feedback")
    sheet.get_by_role("button", name="Draft with AI", exact=True).click()
    expect(sheet.get_by_role("alert")).to_contain_text(detail)
    expect(feedback).to_have_value("Keep this feedback")
    assert rubric_workspace.assignment_source(browser_app.assignment_id) == original
    page.screenshot(path=str(Path(output_path) / "invalid-rubric.png"), full_page=True)


def test_rubric_draft_refuses_to_remove_an_item_used_by_a_grade(
    page, browser_app, fake_ollama, tmp_path, output_path
):
    workspace = Workspace(tmp_path / "course")
    original = workspace.assignment_source(browser_app.assignment_id)
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name=re.compile(r"^Partially Correct")).click()
    expect(page.get_by_role("spinbutton", name="Score", exact=True)).to_have_value("6")
    page.get_by_role("button", name=re.compile(r"^Edit (answers and )?rubric$")).click()
    sheet = page.get_by_role("dialog", name=re.compile(r"Edit (answers and )?rubric"))
    sheet.get_by_role("button", name="Draft with AI", exact=True).click()
    expect(sheet.get_by_role("textbox", name="Criterion 1", exact=True)).to_have_value(
        "Missing base case"
    )
    sheet.get_by_role("button", name="Save", exact=True).click()
    expect(sheet.get_by_role("alert")).to_contain_text("Partially Correct")
    assert workspace.assignment_source(browser_app.assignment_id) == original
    assert page.request.get(browser_app.answer_url).json()["points_awarded"] == 6
    page.screenshot(path=str(Path(output_path) / "rubric-in-use.png"), full_page=True)


def test_rubric_draft_keeps_its_conflict_baseline(
    page, browser_app, fake_ollama, tmp_path, output_path
):
    workspace = Workspace(tmp_path / "course")
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name=re.compile(r"^Edit (answers and )?rubric$")).click()
    sheet = page.get_by_role("dialog", name=re.compile(r"Edit (answers and )?rubric"))
    sheet.get_by_role("button", name="Draft with AI", exact=True).click()
    expect(sheet.get_by_role("textbox", name="Criterion 1", exact=True)).to_have_value(
        "Missing base case"
    )
    api = f"{browser_app.course_url}/api/assignments/{browser_app.assignment_id}"
    opened = page.request.get(api + "/qa").json()["document"]["questions"][0]["rubric"]
    replacement = json.loads(json.dumps(opened))
    replacement[0]["default_comment"] = "Another grader's feedback"
    response = page.request.put(
        api + "/questions/q1/rubric",
        data={
            "rubric": replacement,
            "expected_rubric": opened,
        },
    )
    assert response.ok, response.text()
    current = workspace.assignment_source(browser_app.assignment_id)
    sheet.get_by_role("button", name="Close", exact=True).first.click()
    page.get_by_role("button", name=re.compile(r"^Edit (answers and )?rubric$")).click()
    sheet.get_by_role("button", name="Save", exact=True).click()
    expect(sheet.get_by_role("alert")).to_contain_text("changed somewhere else")
    expect(sheet.get_by_role("textbox", name="Criterion 1", exact=True)).to_have_value(
        "Missing base case"
    )
    assert workspace.assignment_source(browser_app.assignment_id) == current
    sheet.get_by_role("button", name="Load the current question").click()
    expect(
        sheet.get_by_role("textbox", name="Feedback for Correct", exact=True)
    ).to_have_value("Another grader's feedback")
    page.screenshot(path=str(Path(output_path) / "rubric-reloaded.png"), full_page=True)


def test_rubric_draft_reads_scans_once_and_closing_does_not_save(
    page, browser_app, fake_ollama, tmp_path, output_path
):
    prompts, images, _gate = fake_ollama
    workspace = Workspace(tmp_path / "course")
    Image.new("RGB", (400, 120), "white").save(workspace.root / "rubric-answer.png")
    markdown = workspace.root / "3.submission.md"
    markdown.write_text("""---
student_id: '0003'
student_name: Cara Scan
---
- q1:
  - Response image: rubric-answer.png
  - Response text:
  - Possible points: 10
  - Points:
""")
    publish_submission(
        workspace,
        browser_app.assignment_id,
        load_student_submission_markdown(markdown),
        asset_root=workspace.root,
        source_key="3",
    )
    original = workspace.assignment_source(browser_app.assignment_id)
    page.goto(browser_app.grade_url)
    for _ in range(2):
        page.get_by_role(
            "button", name=re.compile(r"^Edit (answers and )?rubric$")
        ).click()
        sheet = page.get_by_role(
            "dialog", name=re.compile(r"Edit (answers and )?rubric")
        )
        sheet.get_by_role("button", name="Draft with AI", exact=True).click()
        expect(
            sheet.get_by_role("textbox", name="Criterion 1", exact=True)
        ).to_have_value("Missing base case")
        sheet.get_by_role("button", name="Discard draft", exact=True).click()
        expect(sheet).to_have_count(0)
    assert images == [READER]
    assert len(prompts) == 2 and all(HANDWRITING in prompt for prompt in prompts)
    assert workspace.assignment_source(browser_app.assignment_id) == original
    assert (
        workspace.answer(browser_app.assignment_id, 3, "q1").response_text
        == HANDWRITING
    )
    artifact = Path(output_path) / "cached-reading.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({"images": images, "prompts": prompts}, indent=2))


def test_rubric_draft_needs_written_answers(
    page, browser_app, fake_ollama, tmp_path, output_path
):
    prompts, images, _gate = fake_ollama
    workspace = Workspace(tmp_path / "course")
    for submission in workspace.submissions(browser_app.assignment_id):
        answer = workspace.answer(browser_app.assignment_id, submission.id, "q1")
        workspace.save_transcription(
            browser_app.assignment_id,
            submission.id,
            "q1",
            "",
            expected_revision=answer.revision,
            actor="fixture",
        )
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name=re.compile(r"^Edit (answers and )?rubric$")).click()
    sheet = page.get_by_role("dialog", name=re.compile(r"Edit (answers and )?rubric"))
    sheet.get_by_role("button", name="Draft with AI", exact=True).click()
    expect(sheet.get_by_role("alert")).to_contain_text("written answers")
    assert prompts == [] and images == []
    page.screenshot(
        path=str(Path(output_path) / "no-written-answers.png"), full_page=True
    )


def test_rubric_draft_continues_when_closed_and_keeps_unsaved_edits(
    page, browser_app, fake_ollama, provider_requests, output_path
):
    _prompts, _images, gate = fake_ollama
    gate.clear()
    page.goto(browser_app.grade_url)
    edit = page.get_by_role("button", name=re.compile(r"^Edit (answers and )?rubric$"))
    edit.click()
    sheet = page.get_by_role("dialog", name=re.compile(r"Edit (answers and )?rubric"))
    sheet.get_by_role("button", name="Draft with AI", exact=True).click()
    expect(
        sheet.get_by_role("button", name="Cancel AI", exact=True)
    ).to_be_enabled()
    assert provider_requests[1].wait(5)
    sheet.get_by_role("button", name="Close", exact=True).first.click()
    expect(sheet).to_have_count(0)
    expect(status(page, "Drafting rubric with AI…")).to_be_visible()
    edit.click()
    expect(
        sheet.get_by_role("button", name="Cancel AI", exact=True)
    ).to_be_enabled()
    gate.set()
    first = sheet.get_by_role("textbox", name="Criterion 1", exact=True)
    expect(first).to_have_value("Missing base case")
    first.fill("Keep this unsaved edit")
    sheet.get_by_role("button", name="Close", exact=True).first.click()
    expect(status(page, "AI rubric draft ready · Review and save")).to_be_visible()
    edit.click()
    expect(first).to_have_value("Keep this unsaved edit")
    assert len(provider_requests[0]) == 1
    page.screenshot(
        path=str(Path(output_path) / "reopened-rubric.png"), full_page=True,
        animations="disabled",
    )
    sheet.get_by_role("button", name="Discard draft", exact=True).click()
    edit.click()
    expect(first).to_have_count(0)


def test_explicitly_cancelling_rubric_draft_stops_reading_remaining_scans(
    page, browser_app, fake_ollama, provider_requests, tmp_path, output_path
):
    _prompts, images, gate = fake_ollama
    requests, started = provider_requests
    workspace = Workspace(tmp_path / "course")
    Image.new("RGB", (400, 120), "white").save(workspace.root / "cancel-answer.png")
    for number in (3, 4):
        path = workspace.root / f"cancel-{number}.submission.md"
        path.write_text(f"""---
student_id: '{number}'
---
- q1:
  - Response image: cancel-answer.png
  - Response text:
  - Possible points: 10
  - Points:
""")
        publish_submission(
            workspace,
            browser_app.assignment_id,
            load_student_submission_markdown(path),
            asset_root=workspace.root,
            source_key=str(number),
        )
    gate.clear()
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name=re.compile(r"^Edit (answers and )?rubric$")).click()
    sheet = page.get_by_role("dialog", name=re.compile(r"Edit (answers and )?rubric"))
    sheet.get_by_role("button", name="Draft with AI", exact=True).click()
    assert started.wait(5)
    sheet.get_by_role("button", name="Cancel AI", exact=True).click()
    sheet.get_by_role("button", name="Close", exact=True).first.click()
    expect(sheet).to_have_count(0)
    gate.set()
    page.goto(browser_app.grade_url.replace("submission=1", "submission=3"))
    expect(page.get_by_text(HANDWRITING, exact=True)).to_be_visible()
    sleep(0.5)
    assert len(requests) == 1 and images == [READER]
    assert workspace.answer(browser_app.assignment_id, 4, "q1").response_text == ""
    artifact = Path(output_path) / "cancelled-reading.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps(
            {"provider_requests": len(requests), "unread_answer_preserved": True}
        )
    )


def test_rubric_draft_uses_only_the_selected_version(
    page, browser_app, fake_ollama, tmp_path, output_path
):
    prompts, _images, _gate = fake_ollama
    workspace = Workspace(tmp_path / "course")
    assignment = workspace.create_assignment("""---
title: Versioned rubric quiz
---
## Version A
- (10 points) Explain recursion.
  - **Answer**
    - A function calls itself.
## Version B
- (10 points) Explain iteration.
  - **Answer**
    - A loop repeats.
""")
    ids = []
    for number, version, text in (
        (1, "A", "A_ONLY_RESPONSE"),
        (2, "B", "B_ONLY_RESPONSE"),
    ):
        path = workspace.root / f"version-{version}.submission.md"
        path.write_text(f"""---
student_id: '{number}'
assignment_version: {version}
---
- q1:
  - Response text: {text}
  - Possible points: 10
  - Points:
""")
        ids.append(
            publish_submission(
                workspace,
                assignment.id,
                load_student_submission_markdown(path),
                asset_root=workspace.root,
                source_key=version,
            )
        )
    page.goto(
        f"{browser_app.course_url}/assignments/{assignment.id}/grade?submission={ids[0]}&question=q1"
    )
    page.get_by_role("button", name=re.compile(r"^Edit (answers and )?rubric$")).click()
    sheet = page.get_by_role("dialog", name=re.compile(r"Edit (answers and )?rubric"))
    sheet.get_by_role("button", name="Draft with AI", exact=True).click()
    expect(sheet.get_by_role("textbox", name="Criterion 1", exact=True)).to_have_value(
        "Missing base case"
    )
    assert (
        len(prompts) == 1
        and "A_ONLY_RESPONSE" in prompts[0]
        and "B_ONLY_RESPONSE" not in prompts[0]
    )
    sheet.get_by_role("button", name="Save", exact=True).click()
    expect(sheet).to_have_count(0)
    source = workspace.assignment_source(assignment.id)
    assert "Missing base case" in source.split("## Version B")[0]
    assert "Missing base case" not in source.split("## Version B")[1]
    artifact = Path(output_path) / "versioned-qa.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(source)


def test_cloud_rubric_draft_requires_instructor_and_confirmation(
    page, browser_app, fake_ollama, monkeypatch, output_path
):
    from opengrader.server.auth import AuthStatus
    from opengrader.server.workspace import ai_drafts

    prompts, _images, _gate = fake_ollama
    monkeypatch.setattr(app_config.ResolvedAI, "kind", property(lambda _self: "cloud"))
    settings = app_config.load_settings()
    settings = app_config.consent.grant_cloud_consent(
        settings, app_config.resolve(settings)
    )
    app_config.save_settings(settings)
    monkeypatch.setattr(
        ai_drafts, "viewer", lambda _request: AuthStatus(enabled=True, role="grader")
    )
    api = f"{browser_app.course_url}/api/assignments/{browser_app.assignment_id}/questions/q1/rubric/draft"
    refused = page.request.get(api + "/plan")
    assert refused.status == 403 and prompts == []
    monkeypatch.setattr(
        ai_drafts,
        "viewer",
        lambda _request: AuthStatus(enabled=True, role="instructor"),
    )
    refused = page.request.post(
        api, data={"expected_answers": ["A clear explanation."]}
    )
    assert refused.status == 409 and prompts == []
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name=re.compile(r"^Edit (answers and )?rubric$")).click()
    sheet = page.get_by_role("dialog", name=re.compile(r"Edit (answers and )?rubric"))
    sheet.get_by_role("button", name="Draft with AI", exact=True).click()
    confirm = page.get_by_role("alertdialog", name="Send answers to a cloud AI?")
    expect(confirm).to_be_visible()
    confirm.get_by_role("button", name="Don't send", exact=True).click()
    assert prompts == []
    sheet.get_by_role("button", name="Draft with AI", exact=True).click()
    confirm.get_by_role("button", name="Send and draft", exact=True).click()
    expect(sheet.get_by_role("textbox", name="Criterion 1", exact=True)).to_have_value(
        "Missing base case"
    )
    assert len(prompts) == 1
    page.screenshot(
        path=str(Path(output_path) / "cloud-rubric-preview.png"), full_page=True
    )


def test_draft_confirm_and_reload(page, browser_app, fake_ollama, output_path):
    prompts, _images, _gate = fake_ollama
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name="Draft grades with AI").click()
    expect(status(page, "AI drafted 4 grades")).to_be_visible()
    expect(status(page, "AI drafted 2 grades")).to_be_visible()

    rubric = page.get_by_role("region", name="Rubric", exact=True)
    badge = rubric.get_by_text("AI draft · 90% confident")
    expect(badge).to_be_visible()
    selected = rubric.get_by_role(
        "button", name=re.compile(r"^Partially Correct")
    ).locator("..")
    expect(selected).to_have_css("border-top-style", "dashed")
    expect(page.get_by_role("spinbutton", name="Score", exact=True)).to_have_value("6")
    page.screenshot(path=str(Path(output_path) / "drafted.png"), full_page=True)

    confirm = rubric.get_by_role("button", name="Confirm AI grade")
    page.set_viewport_size({"width": 320, "height": 800})
    button_box = confirm.bounding_box()
    score_box = page.get_by_role("spinbutton", name="Score", exact=True).bounding_box()
    assert (
        button_box["x"] + button_box["width"] <= score_box["x"]
        or button_box["y"] >= score_box["y"] + score_box["height"]
    )
    page.screenshot(path=str(Path(output_path) / "drafted-narrow.png"), full_page=True)
    page.set_viewport_size({"width": 1280, "height": 720})
    confirm.focus()
    page.keyboard.press("Enter")
    expect(badge).to_have_count(0)
    expect(selected).to_have_css("border-top-style", "solid")
    page.reload()
    expect(page.get_by_role("spinbutton", name="Score", exact=True)).to_have_value("6")
    expect(badge).to_have_count(0)

    page.goto(browser_app.grade_url.replace("submission=1", "submission=2"))
    expect(badge).to_be_visible()

    answer = page.request.get(browser_app.answer_url).json()
    assert answer["points_awarded"] == 6
    assert answer["rubric_labels"] == ["Partially Correct"]
    assert answer["analysis"].startswith(
        "[AI · 90% · fake-grader (ollama) · confirmed]"
    )
    assert len(prompts) == 3
    assert sum("Explain your reasoning." in prompt for prompt in prompts) == 2
    assert sum("A second question." in prompt for prompt in prompts) == 1
    (Path(output_path) / "prompts.json").write_text(json.dumps(prompts, indent=2))

    page.get_by_role("button", name="Dismiss AI results").click()
    expect(page.get_by_text("AI drafted 4 grades")).to_have_count(0)
    page.reload()
    expect(page.get_by_role("button", name="Draft grades with AI")).to_be_visible()
    expect(page.get_by_text("AI drafted")).to_have_count(0)


def test_progress_stays_visible_on_other_questions(
    page, browser_app, fake_ollama, output_path
):
    _prompts, _images, gate = fake_ollama
    gate.clear()
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name="Draft grades with AI").click()
    expect(status(page, "AI is drafting grades: 1 of 2")).to_be_visible()

    page.get_by_role("button", name="Next question").click()
    expect(page).to_have_url(re.compile(r"question=q2"))
    expect(status(page, "Waiting for the AI to finish Question q1.")).to_be_visible()
    expect(status(page, "AI is on Question q1 (1 of 2)")).to_be_visible()
    page.screenshot(
        path=str(Path(output_path) / "running-elsewhere.png"), full_page=True
    )
    page.get_by_role("combobox", name="Question").click()
    expect(page.get_by_role("option", name=re.compile("Question q1"))).to_contain_text(
        "AI working"
    )
    expect(page.get_by_role("option", name=re.compile("Question q2"))).to_contain_text(
        "AI queued"
    )
    page.keyboard.press("Escape")

    gate.set()
    expect(status(page, "AI drafted 2 grades")).to_be_visible()
    expect(status(page, "AI drafted 4 grades")).to_be_visible()
    page.get_by_role("button", name="Previous question").click()
    expect(status(page, "AI drafted 2 grades")).to_be_visible()


def test_scanned_answers_are_read_then_drafted(
    page, browser_app, fake_ollama, tmp_path, output_path
):
    prompts, images, _gate = fake_ollama
    workspace = Workspace(tmp_path / "course")
    Image.new("RGB", (400, 120), "white").save(workspace.root / "crop.png")
    for number, name in ((3, "Cara Scan"), (4, "Dev Scan")):
        markdown = workspace.root / f"{number}.submission.md"
        markdown.write_text(
            f"""---
student_id: '{number:04d}'
student_name: {name}
---
- q1:
  - Response image: crop.png
  - Response text:
  - Possible points: 10
  - Points:
""",
            encoding="utf-8",
        )
        publish_submission(
            workspace,
            browser_app.assignment_id,
            load_student_submission_markdown(markdown),
            asset_root=workspace.root,
            source_key=str(number),
        )

    page.goto(browser_app.grade_url.replace("submission=1", "submission=3"))
    page.get_by_role("button", name="Draft grades with AI").click()
    expect(status(page, "Read 2 answers · AI drafted 4 grades")).to_be_visible()
    expect(page.get_by_text(HANDWRITING)).to_be_visible()
    expect(page.get_by_role("spinbutton", name="Score", exact=True)).to_have_value("6")
    page.screenshot(
        path=str(Path(output_path) / "read-and-drafted.png"), full_page=True
    )

    assert images == [READER, READER]
    assert len(prompts) == 4
    assert sum(HANDWRITING in prompt for prompt in prompts) == 1
    (Path(output_path) / "requests.json").write_text(
        json.dumps({"images": images, "prompts": prompts}, indent=2)
    )


def test_grader_changes_are_preserved_while_ai_is_running(
    page, browser_app, fake_ollama, output_path
):
    _prompts, _images, gate = fake_ollama
    gate.clear()
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name="Draft grades with AI").click()
    expect(status(page, "AI is drafting grades: 1 of 2")).to_be_visible()
    rubric = page.get_by_role("region", name="Rubric", exact=True)
    rubric.get_by_role("button", name="Correct 0", exact=True).click()
    expect(rubric.get_by_role("status")).to_have_text("Saved")
    gate.set()
    expect(status(page, "AI drafted 3 grades")).to_be_visible()
    expect(
        status(page, "AI drafted 1 grade · 1 skipped (changed meanwhile)")
    ).to_be_visible()
    expect(page.get_by_role("spinbutton", name="Score", exact=True)).to_have_value("10")
    answer = page.request.get(browser_app.answer_url).json()
    assert answer["points_awarded"] == 10
    assert answer["rubric_labels"] == ["Correct"]
    assert answer["analysis"] == ""
    page.screenshot(
        path=str(Path(output_path) / "grader-change-preserved.png"), full_page=True
    )


def test_cancel_keeps_completed_grades_and_stops_remaining_questions(
    page, browser_app, fake_ollama, monkeypatch, output_path
):
    prompts, _images, gate = fake_ollama
    request_started = Event()
    wait = gate.wait

    def wait_for_reply(timeout):
        request_started.set()
        return wait(timeout)

    monkeypatch.setattr(gate, "wait", wait_for_reply)
    gate.clear()
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name="Draft grades with AI").click()
    assert request_started.wait(5)
    expect(status(page, "AI is drafting grades: 1 of 2")).to_be_visible()
    try:
        page.get_by_role("button", name="Cancel AI", exact=True).click()
        expect(
            status(page, "Cancelling AI after the current request…")
        ).to_be_visible(timeout=3000)
        expect(page.get_by_role("button", name="Cancelling AI…")).to_be_disabled()
        job_url = (
            f"{browser_app.course_url}/api/assignments/"
            f"{browser_app.assignment_id}/ai-drafts"
        )
        assert page.request.get(job_url).json()["status"] == "cancelling"
        assert page.request.post(job_url).json()["status"] == "cancelling"
        assert page.request.delete(job_url).status == 409
        page.reload()
        expect(
            status(page, "Cancelling AI after the current request…")
        ).to_be_visible(timeout=3000)
        expect(page.get_by_role("button", name="Cancelling AI…")).to_be_disabled()
        page.screenshot(
            path=str(Path(output_path) / "cancelling.png"), full_page=True
        )
    finally:
        gate.set()
    expect(status(page, "Cancelled after drafting 1 grade")).to_be_visible()
    page.reload()
    expect(page.get_by_role("spinbutton", name="Score", exact=True)).to_have_value("6")
    other = page.request.get(
        browser_app.answer_url.replace("/submissions/1/", "/submissions/2/")
    ).json()
    second_question = page.request.get(
        browser_app.answer_url.replace("/q1", "/q2")
    ).json()
    assert other["points_awarded"] is None
    assert second_question["points_awarded"] is None
    assert len(prompts) == 1
    (Path(output_path) / "cancelled-job.json").write_text(
        json.dumps(page.request.get(job_url).json(), indent=2)
    )
    page.screenshot(path=str(Path(output_path) / "cancelled.png"), full_page=True)


def test_deleting_course_waits_for_the_current_ai_request(
    page, browser_app, fake_ollama, monkeypatch, tmp_path, output_path
):
    _prompts, _images, gate = fake_ollama
    request_started = Event()
    wait = gate.wait

    def wait_for_reply(timeout):
        request_started.set()
        return wait(timeout)

    monkeypatch.setattr(gate, "wait", wait_for_reply)
    gate.clear()
    page.goto(browser_app.grade_url)
    page.get_by_role("button", name="Draft grades with AI").click()
    assert request_started.wait(5)

    base, course_id = browser_app.course_url.split("/courses/")
    with ThreadPoolExecutor(max_workers=1) as requests:
        deletion = requests.submit(
            httpx.post,
            f"{base}/api/workspaces/{course_id}/delete",
            json={"number": "BROWSER101"},
        )
        try:
            with pytest.raises(TimeoutError):
                deletion.result(timeout=0.5)
            assert (tmp_path / "course/.opengrader/workspace.sqlite3").exists()
            assert httpx.get(f"{base}/api/server", timeout=2).status_code == 200
        finally:
            gate.set()
        response = deletion.result(timeout=5)
    assert response.status_code == 204, response.text
    assert not (tmp_path / "course/.opengrader").exists()
    assert httpx.get(f"{base}/api/workspaces").json() == []
    artifact = Path(output_path) / "deletion.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps({"waited_for_ai": True, "server_responsive": True, "deleted": True})
    )


@pytest.mark.skipif(os.name == "nt", reason="Shared accounts require POSIX")
def test_ai_review_obeys_shared_grading_permissions(
    page, browser, browser_app, fake_ollama, monkeypatch, tmp_path, output_path
):
    import grp

    from opengrader.core.workspace import sharing
    from opengrader.server import app as server_app
    from opengrader.server.app import CONNECTION_NOTE

    monkeypatch.setattr(server_app, "peer_uid", lambda *_args: None)
    real_problems = sharing.sharing_problems
    monkeypatch.setattr(
        sharing,
        "sharing_problems",
        lambda root, group: [
            p for p in real_problems(root, group) if "cannot reach" not in p
        ],
    )
    api = f"{browser_app.course_url}/api"
    group = grp.getgrgid(os.getgid()).gr_name
    shared = page.request.put(f"{api}/sharing", data={"group": group})
    assert shared.ok
    note = json.loads((tmp_path / "course" / CONNECTION_NOTE).read_text())

    page.goto(browser_app.grade_url)
    page.get_by_role("button", name="Draft grades with AI").click()
    expect(status(page, "AI drafted 4 grades")).to_be_visible()
    with browser.new_context(
        extra_http_headers={
            "Authorization": f"Bearer {note['token']}",
            "X-Opengrader-Account": "browser-grader",
        }
    ) as grader_context:
        grader = grader_context.new_page()
        grader.goto(browser_app.grade_url)
        grader.get_by_role("button", name="Confirm AI grade").click()
        expect(grader.get_by_role("alert")).to_contain_text("sent to")
        answer = page.request.get(browser_app.answer_url).json()
        assert "confirmed" not in answer["analysis"]

        allowed = page.request.put(
            f"{api}/sharing",
            data={
                "group": group,
                "accounts": [
                    {
                        "login": "browser-grader",
                        "role": "grader",
                        "may_change_others": True,
                    }
                ],
            },
        )
        assert allowed.ok
        grader.get_by_role("button", name="Confirm AI grade").click()
        expect(grader.get_by_role("button", name="Confirm AI grade")).to_have_count(0)
        answer = page.request.get(browser_app.answer_url).json()
        assert "confirmed" in answer["analysis"]
        grader.screenshot(
            path=str(Path(output_path) / "shared-review.png"), full_page=True
        )
