"""Real-server browser fixtures, deliberately outside the default unit suite."""

from dataclasses import dataclass
from pathlib import Path
import socket
from threading import Thread
from time import monotonic, sleep

import pytest
import pymupdf
import uvicorn

from opengrader.core.submissions import load_student_submission_markdown
from opengrader.core.workspace.intake import publish_submission
from opengrader.core.workspace.models import CourseDetails
from opengrader.core.workspace.store import Workspace
from opengrader.server.app import create_app
from opengrader.server.settings import ServerSettings


QA = """---
title: Browser quiz
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
def answer_key():
    return QA


@pytest.fixture
def scan_pdf():
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Student explanation")
        return document.tobytes()


@dataclass(frozen=True)
class BrowserApp:
    course_url: str
    assignment_id: str

    @property
    def assignment_url(self):
        return f"{self.course_url}/assignments/{self.assignment_id}"

    @property
    def grade_url(self):
        return f"{self.assignment_url}/grade?submission=1&question=q1"

    @property
    def answer_url(self):
        return f"{self.course_url}/api/assignments/{self.assignment_id}/submissions/1/answers/q1"


@pytest.fixture
def browser_app(tmp_path, monkeypatch):
    """Every test owns a tiny course, a server home, and an ephemeral port."""
    dist = Path(__file__).resolve().parents[1] / "src/opengrader/web/dist"
    if not (dist / "index.html").is_file():
        pytest.fail("Build the website first: npm --prefix src/opengrader/web run build")

    home = tmp_path / "server"
    monkeypatch.setenv("OPENGRADER_SERVER_HOME", str(home))
    workspace = Workspace.create(tmp_path / "course")
    assignment = workspace.create_assignment(QA)
    for number, name in enumerate(("Alice Example", "Bob Example"), start=1):
        submission = workspace.root / f"{number}.submission.md"
        submission.write_text(
            f"""---
student_id: '{number:04d}'
student_name: {name}
---
- q1:
  - Response text: Explanation from {name}
  - Possible points: 10
  - Points:
  - Analysis:
- q2:
  - Response text: Yes
  - Possible points: 2
  - Points:
""",
            encoding="utf-8",
        )
        publish_submission(
            workspace,
            assignment.id,
            load_student_submission_markdown(submission),
            asset_root=workspace.root,
            source_key=str(number),
        )

    app = create_app(
        home,
        web_dist=dist,
        process_jobs=False,
        settings=ServerSettings(_env_file=None, auth_enabled=False),
    )
    registered = app.state.registry.register(
        workspace.root, CourseDetails(number="BROWSER101")
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
                timeout_graceful_shutdown=2,
            )
        )
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 15
            while not server.started:
                if not thread.is_alive() or monotonic() >= deadline:
                    pytest.fail("The isolated browser test server did not start")
                sleep(0.01)
            yield BrowserApp(
                f"http://127.0.0.1:{port}/courses/{registered.id}", assignment.id
            )
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            if thread.is_alive():
                server.force_exit = True
                thread.join(timeout=5)
                pytest.fail("The browser test server did not shut down")


@pytest.fixture
def page(browser_app, context):
    # Close pages (including their event streams) before stopping the server.
    page = context.new_page()
    page.set_default_timeout(10_000)
    yield page
    # Let the plugin capture failure screenshots before closing every tab.
    context.close()
