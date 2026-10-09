"""Classic's opt-in *Open in browser* button and the status line under it."""

import asyncio
import webbrowser
from pathlib import Path

import pytest
from textual.widgets import Checkbox, Input, Static

import opengrader.server.workspace.client as service_client
import opengrader.tui.main as launcher
from opengrader.core import browser_app, homebrew
from opengrader.core.browser_app import BrowserPreferences, WebBuildError
from opengrader.core.recents import load_recents
from opengrader.server.workspace.client import ServiceConnection
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens.browser_build_screen import BrowserBuildScreen
from opengrader.tui.ui.widgets.browser_launcher import BrowserLauncher

from .fakes import DEFAULT_IMAGE_PATH


def _connection(root: Path) -> ServiceConnection:
    return ServiceConnection(
        root=str(root), url="http://localhost:5555", pid=1, workspace_id="w"
    )


@pytest.fixture(autouse=True)
def _no_homebrew(monkeypatch):  # noqa: ANN001, ANN202
    """No Homebrew unless a test says so — whatever this machine has."""
    monkeypatch.setattr(homebrew, "find_homebrew", lambda: None)


@pytest.fixture
def service(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    """A fake import + service: records what was opened, shut down and browsed."""
    root = tmp_path / "course"
    root.mkdir()
    calls: dict[str, list] = {
        "imports": [],
        "browsed": [],
        "shutdowns": [],
        "requests": [],
    }
    state = {"running": False}

    def _import(qa, submissions):  # noqa: ANN001, ANN202
        calls["imports"].append((qa, submissions))
        state["running"] = True
        return root, "a1"

    def _request(self, method, path, *args, **kwargs):  # noqa: ANN001, ANN202
        calls["requests"].append((method, path))
        if path == "/api/folder-session/sync":
            return b'{"imported": 0, "updated": 1, "written": 0, "failed": 0}'
        calls["shutdowns"].append((method, path))
        state["running"] = False
        return b""

    monkeypatch.setattr(launcher, "_import_as_workspace", _import)
    monkeypatch.setattr(service_client, "ensure_service", lambda r: _connection(r))
    monkeypatch.setattr(
        service_client,
        "running_service",
        lambda r: _connection(r) if state["running"] else None,
    )
    monkeypatch.setattr(service_client.WorkspaceClient, "request", _request)
    monkeypatch.setattr(webbrowser, "open", lambda url: calls["browsed"].append(url) or True)
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    # The button only shows (and so only presses) once the user has opted in.
    browser_app.save_browser_preferences(BrowserPreferences(enabled=True))
    calls["root"] = root
    calls["state"] = state
    return calls


def _fill_card(app: TuiApp) -> None:
    app.query_one("#tui-qa-path", Input).value = "/tmp/course/qa.md"
    app.query_one("#tui-sub-path", Input).value = "/tmp/course/submissions"


def test_the_button_is_hidden_until_opted_in_and_the_choice_is_kept() -> None:
    async def run() -> tuple[bool, bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            before = app.query_one("#browser-open-button").has_class("hidden")
            app.query_one("#browser-enabled", Checkbox).value = True
            await pilot.pause()
            after = app.query_one("#browser-open-button").has_class("hidden")
        again = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with again.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            next_launch = again.query_one("#browser-open-button").has_class("hidden")
        return before, after, next_launch

    assert asyncio.run(run()) == (True, False, False)
    assert browser_app.load_browser_preferences().enabled


def test_open_in_browser_imports_the_folder_and_shows_open_and_stop(
    service, monkeypatch  # noqa: ANN001
) -> None:
    monkeypatch.setattr(browser_app, "web_dist_state", lambda source=None: "ready")

    async def run() -> tuple[str, bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _fill_card(app)
            app.query_one("#browser-open-button").press()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            status = str(app.query_one("#browser-status-text", Static).render())
            shown = not app.query_one(BrowserLauncher).has_class("hidden")

            app.query_one("#browser-stop").press()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            hidden = app.query_one(BrowserLauncher).has_class("hidden")
        return status, shown, hidden

    status, shown, hidden_after_stop = asyncio.run(run())

    assert service["imports"] == [("/tmp/course/qa.md", "/tmp/course/submissions")]
    assert service["browsed"] == ["http://localhost:5555/assignments/a1"]
    assert shown and "http://localhost:5555" in status
    assert service["shutdowns"] == [("POST", "/api/shutdown")]
    assert hidden_after_stop
    root = str(service["root"].resolve())
    assert browser_app.load_browser_preferences().last_workspace == str(service["root"])
    recents = load_recents()
    assert [(i.kind, i.path, i.assignment) for i in recents] == [
        ("workspace", root, "a1"),
        # Classic's Start grading card lists it too, with the pair to reopen.
        ("folder", str(Path("/tmp/course").resolve()), ""),
    ]
    assert recents[1].qa_file == str(Path("/tmp/course/qa.md").resolve())
    assert recents[1].submissions == str(Path("/tmp/course/submissions").resolve())


def test_stop_says_it_is_waiting_until_the_service_exits(
    service, monkeypatch  # noqa: ANN001
) -> None:
    monkeypatch.setattr(browser_app, "web_dist_state", lambda source=None: "ready")
    lock = service_client.exclusive_lock(service["root"] / ".opengrader/service.lock")

    async def run() -> tuple[str, bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _fill_card(app)
            app.query_one("#browser-open-button").press()
            await pilot.pause()
            await app.workers.wait_for_complete()
            with lock:
                app.query_one("#browser-stop").press()
                await pilot.pause(1.5)
                waiting = str(app.query_one("#browser-status-text", Static).render())
                hidden_while_held = app.query_one(BrowserLauncher).has_class("hidden")
            await app.workers.wait_for_complete()
            await pilot.pause()
            return waiting, hidden_while_held, app.query_one(BrowserLauncher).has_class("hidden")

    assert asyncio.run(run()) == ("Waiting for running jobs to finish…", False, True)


def test_a_service_left_running_earlier_can_still_be_stopped(service) -> None:  # noqa: ANN001
    browser_app.save_browser_preferences(
        BrowserPreferences(last_workspace=str(service["root"]))
    )
    service["state"]["running"] = True

    async def run() -> bool:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            return not app.query_one(BrowserLauncher).has_class("hidden")

    assert asyncio.run(run())


def test_a_copy_without_the_browser_app_says_so_and_starts_nothing(
    service, monkeypatch  # noqa: ANN001
) -> None:
    monkeypatch.setattr(browser_app, "web_dist_state", lambda source=None: "missing")
    monkeypatch.setattr(browser_app, "web_buildable", lambda source=None: False)
    notices: list[tuple[str, str]] = []

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kw: notices.append((str(message), kw.get("severity", ""))),
            )
            _fill_card(app)
            app.query_one("#browser-open-button").press()
            await pilot.pause()
            await app.workers.wait_for_complete()

    asyncio.run(run())

    assert service["imports"] == []
    assert notices and notices[-1][1] == "error"
    assert "without the browser app" in notices[-1][0]


def test_an_unbuilt_app_is_built_only_after_the_user_agrees(
    service, monkeypatch  # noqa: ANN001
) -> None:
    built = {"ready": False, "builds": 0}
    monkeypatch.setattr(
        browser_app,
        "web_dist_state",
        lambda source=None: "ready" if built["ready"] else "missing",
    )
    monkeypatch.setattr(browser_app, "web_buildable", lambda source=None: True)
    monkeypatch.setattr(browser_app, "pnpm_command", lambda: ["pnpm"])

    def _build(source=None, *, on_line=None, log=None):  # noqa: ANN001, ANN202
        built["builds"] += 1
        built["ready"] = True

    monkeypatch.setattr(browser_app, "build_web", _build)

    async def run() -> tuple[bool, int]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _fill_card(app)
            app.query_one("#browser-open-button").press()
            await pilot.pause()
            asked = isinstance(app.screen, BrowserBuildScreen)
            builds_before_answer = built["builds"]
            app.screen.query_one("#bb-build").press()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            return asked, builds_before_answer

    asked, builds_before_answer = asyncio.run(run())

    assert asked and builds_before_answer == 0
    assert built["builds"] == 1
    assert service["browsed"] == ["http://localhost:5555/assignments/a1"]


def test_declining_the_build_installs_nothing(service, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(browser_app, "web_dist_state", lambda source=None: "missing")
    monkeypatch.setattr(browser_app, "web_buildable", lambda source=None: True)
    monkeypatch.setattr(browser_app, "pnpm_command", lambda: None)
    monkeypatch.setattr(
        browser_app, "build_web", lambda *a, **k: pytest.fail("built without asking")
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _fill_card(app)
            app.query_one("#browser-open-button").press()
            await pilot.pause()
            # No pnpm and no Homebrew: the dialog only explains, with Close.
            assert not app.screen.query("#bb-build")
            assert not app.screen.query("#bb-install")
            app.screen.query_one("#bb-cancel").press()
            await pilot.pause()
            await app.workers.wait_for_complete()

    asyncio.run(run())
    assert service["imports"] == []


def _missing_tools_with_homebrew(monkeypatch) -> dict:  # noqa: ANN001
    """An unbuilt app, no pnpm, and a Homebrew that can install it."""
    done = {"installs": [], "builds": 0, "order": []}
    monkeypatch.setattr(
        browser_app,
        "web_dist_state",
        lambda source=None: "ready" if done["builds"] else "missing",
    )
    monkeypatch.setattr(browser_app, "web_buildable", lambda source=None: True)
    monkeypatch.setattr(browser_app, "pnpm_command", lambda: None)
    monkeypatch.setattr(homebrew, "find_homebrew", lambda: "/opt/homebrew/bin/brew")

    def _install(brew, *, on_line=None, on_start=None):  # noqa: ANN001, ANN202
        done["installs"].append(brew)
        done["order"].append("install")
        on_line("==> Pouring node")

    def _build(source=None, *, on_line=None, log=None):  # noqa: ANN001, ANN202
        done["builds"] += 1
        done["order"].append("build")

    monkeypatch.setattr(browser_app, "install_tools", _install)
    monkeypatch.setattr(browser_app, "build_web", _build)
    return done


def test_missing_tools_are_installed_then_built_only_after_the_user_agrees(
    service, monkeypatch  # noqa: ANN001
) -> None:
    done = _missing_tools_with_homebrew(monkeypatch)

    async def run() -> tuple[bool, list[str], str]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _fill_card(app)
            app.query_one("#browser-open-button").press()
            await pilot.pause()
            asked = isinstance(app.screen, BrowserBuildScreen)
            before_answer = list(done["order"])
            # Installing software is never what Enter does by accident.
            focused = app.screen.focused.id
            app.screen.query_one("#bb-install").press()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            return asked, before_answer, focused

    asked, before_answer, focused = asyncio.run(run())

    assert asked and before_answer == []
    assert focused == "bb-cancel"
    assert done["installs"] == ["/opt/homebrew/bin/brew"]
    assert done["order"] == ["install", "build"]
    assert service["browsed"] == ["http://localhost:5555/assignments/a1"]


def test_declining_the_install_changes_nothing(service, monkeypatch) -> None:  # noqa: ANN001
    done = _missing_tools_with_homebrew(monkeypatch)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _fill_card(app)
            app.query_one("#browser-open-button").press()
            await pilot.pause()
            app.screen.query_one("#bb-cancel").press()
            await pilot.pause()
            await app.workers.wait_for_complete()

    asyncio.run(run())

    assert done["order"] == []
    assert service["imports"] == []


def test_a_failed_install_says_why_and_builds_nothing(
    service, monkeypatch  # noqa: ANN001
) -> None:
    done = _missing_tools_with_homebrew(monkeypatch)

    def _install(brew, *, on_line=None, on_start=None):  # noqa: ANN001, ANN202
        raise WebBuildError("Node.js and pnpm were not installed.", "Error: no bottle")

    monkeypatch.setattr(browser_app, "install_tools", _install)
    notices: list[tuple[str, str]] = []

    async def run() -> tuple[bool, bool, str]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kw: notices.append((str(message), kw.get("severity", ""))),
            )
            _fill_card(app)
            app.query_one("#browser-open-button").press()
            await pilot.pause()
            app.screen.query_one("#bb-install").press()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            launcher = app.query_one(BrowserLauncher)
            status = str(launcher.query_one("#browser-status-text").render())
            return launcher.busy, launcher.has_class("hidden"), status

    still_busy, hidden, status = asyncio.run(run())

    assert not still_busy
    assert done["builds"] == 0 and service["imports"] == []
    assert notices and notices[-1][1] == "error"
    assert "were not installed" in notices[-1][0] and "no bottle" in notices[-1][0]
    # The notification goes away; the status line keeps what it said.
    assert not hidden
    assert "were not installed" in status and "no bottle" in status


def test_a_stale_build_opens_anyway_without_rebuilding(
    service, monkeypatch  # noqa: ANN001
) -> None:
    monkeypatch.setattr(browser_app, "web_dist_state", lambda source=None: "stale")
    monkeypatch.setattr(browser_app, "web_buildable", lambda source=None: True)
    monkeypatch.setattr(browser_app, "pnpm_command", lambda: ["pnpm"])
    monkeypatch.setattr(
        browser_app, "build_web", lambda *a, **k: pytest.fail("rebuilt when told to open anyway")
    )

    async def run() -> bool:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _fill_card(app)
            app.query_one("#browser-open-button").press()
            await pilot.pause()
            asked = isinstance(app.screen, BrowserBuildScreen)
            app.screen.query_one("#bb-open").press()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            return asked

    asked = asyncio.run(run())

    assert asked
    assert service["imports"] == [("/tmp/course/qa.md", "/tmp/course/submissions")]
    assert service["browsed"] == ["http://localhost:5555/assignments/a1"]


def test_a_stale_build_can_be_rebuilt_before_opening(service, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(browser_app, "web_dist_state", lambda source=None: "stale")
    monkeypatch.setattr(browser_app, "web_buildable", lambda source=None: True)
    monkeypatch.setattr(browser_app, "pnpm_command", lambda: ["pnpm"])
    builds = {"count": 0}

    def _build(source=None, *, on_line=None, log=None):  # noqa: ANN001, ANN202
        builds["count"] += 1

    monkeypatch.setattr(browser_app, "build_web", _build)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _fill_card(app)
            app.query_one("#browser-open-button").press()
            await pilot.pause()
            app.screen.query_one("#bb-build").press()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())

    assert builds["count"] == 1
    assert service["imports"] == [("/tmp/course/qa.md", "/tmp/course/submissions")]
    assert service["browsed"] == ["http://localhost:5555/assignments/a1"]


def test_a_stale_build_with_no_way_to_rebuild_opens_and_warns(
    service, monkeypatch  # noqa: ANN001
) -> None:
    monkeypatch.setattr(browser_app, "web_dist_state", lambda source=None: "stale")
    monkeypatch.setattr(browser_app, "web_buildable", lambda source=None: False)
    notices: list[tuple[str, str]] = []

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kw: notices.append((str(message), kw.get("severity", ""))),
            )
            _fill_card(app)
            app.query_one("#browser-open-button").press()
            await pilot.pause()
            await app.workers.wait_for_complete()

    asyncio.run(run())

    assert service["imports"] == [("/tmp/course/qa.md", "/tmp/course/submissions")]
    assert notices and notices[0][1] == "warning"


def _fill_card_in(app: TuiApp, root: Path) -> None:
    app.query_one("#tui-qa-path", Input).value = str(root / "qa.md")
    app.query_one("#tui-sub-path", Input).value = str(root / "submissions")


def _open_from_card(service, monkeypatch) -> tuple[str, list[tuple[str, str]]]:  # noqa: ANN001
    monkeypatch.setattr(browser_app, "web_dist_state", lambda source=None: "ready")
    notices: list[tuple[str, str]] = []

    async def run() -> str:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kw: notices.append((str(message), kw.get("severity", ""))),
            )
            _fill_card_in(app, service["root"])
            app.query_one("#browser-open-button").press()
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            return str(app.query_one("#browser-status-text", Static).render())

    return asyncio.run(run()), notices


def test_a_folder_without_a_workspace_opens_as_a_folder_session(service, monkeypatch) -> None:  # noqa: ANN001
    from opengrader.core.workspace.folder_session import load_folder_session

    status, notices = _open_from_card(service, monkeypatch)

    session = load_folder_session(service["root"])
    assert session is not None and session.assignment_id == "a1"
    # Synced after the service is up and before the page opens.
    assert service["requests"] == [("POST", "/api/folder-session/sync")]
    assert service["browsed"] == ["http://localhost:5555/assignments/a1"]
    assert "Folder session" in status
    assert not [n for n in notices if "shared workspace" in n[0]]


def test_an_existing_workspace_opens_as_a_workspace_and_says_so(service, monkeypatch) -> None:  # noqa: ANN001
    from opengrader.core.workspace.folder_session import load_folder_session
    from opengrader.core.workspace.store import DATABASE

    (service["root"] / DATABASE).parent.mkdir(parents=True)
    (service["root"] / DATABASE).touch()

    status, notices = _open_from_card(service, monkeypatch)

    assert load_folder_session(service["root"]) is None
    assert service["requests"] == []
    assert "Workspace running" in status
    assert [n for n in notices if "shared workspace" in n[0] and n[1] == "warning"]


def test_an_earlier_folder_session_stays_one(service, monkeypatch) -> None:  # noqa: ANN001
    from opengrader.core.workspace.folder_session import (
        load_folder_session,
        start_folder_session,
    )
    from opengrader.core.workspace.store import DATABASE

    (service["root"] / DATABASE).parent.mkdir(parents=True)
    (service["root"] / DATABASE).touch()
    start_folder_session(service["root"], "a1")

    status, _notices = _open_from_card(service, monkeypatch)

    assert load_folder_session(service["root"]) is not None
    assert service["requests"] == [("POST", "/api/folder-session/sync")]
    assert "Folder session" in status


def test_a_failed_sync_warns_but_still_opens(service, monkeypatch) -> None:  # noqa: ANN001
    from opengrader.server.workspace.client import ServiceError

    def _request(self, method, path, *args, **kwargs):  # noqa: ANN001, ANN202
        raise ServiceError(409, "The class folder could not be found; it may have moved.")

    monkeypatch.setattr(service_client.WorkspaceClient, "request", _request)

    _status, notices = _open_from_card(service, monkeypatch)

    assert service["browsed"] == ["http://localhost:5555/assignments/a1"]
    assert [n for n in notices if "may have moved" in n[0] and n[1] == "warning"]


def test_classic_opening_a_shared_workspace_folder_warns_once(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    import shutil

    from opengrader.core.workspace.intake import publish_pipeline
    from opengrader.core.workspace.models import AssignmentInputs, PipelineOutput
    from opengrader.core.workspace.store import Workspace

    root = tmp_path / "quiz"
    shutil.copytree(Path(__file__).resolve().parents[3] / "samples" / "cs146-quiz1", root, ignore=shutil.ignore_patterns(".opengrader"))
    store = Workspace.create(root)
    published = publish_pipeline(
        store,
        store.assignment_id_for_folder(root),
        PipelineOutput(
            qa_markdown=(root / "qa.md").read_text(encoding="utf-8"),
            submissions_dir=str(root / "student-submissions"),
        ),
    )
    store.save_assignment_inputs(
        AssignmentInputs(
            id=published.assignment.id, folder=".", scan="", output=".",
            submissions="student-submissions",
        )
    )
    notices: list[str] = []

    async def run() -> bool:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(app, "notify", lambda message, **kw: notices.append(str(message)))
            return app._open_folder(
                root / "qa.md", root / "student-submissions", known_active=None
            )

    assert asyncio.run(run())
    assert len([n for n in notices if "written back" in n]) == 1


def test_a_folder_opened_in_the_browser_is_on_the_start_grading_card_next_time(
    service, monkeypatch  # noqa: ANN001
) -> None:
    from opengrader.tui.ui.widgets.recent_list import RecentList

    _open_from_card(service, monkeypatch)

    async def run() -> list[tuple[str, str]]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.pause()
            assert not app.query_one("#launch-recents").has_class("hidden")
            items = app.query_one("#launch-recent-list", RecentList).items
            return [(item.qa_file, item.submissions) for item in items]

    assert asyncio.run(run()) == [
        (
            str((service["root"] / "qa.md").resolve()),
            str((service["root"] / "submissions").resolve()),
        )
    ]
