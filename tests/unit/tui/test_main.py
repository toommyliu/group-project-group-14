import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from opengrader.core.workspace.store import Workspace
from opengrader.server.workspace.client import exclusive_lock
from opengrader.tui.main import _enable_textual_feature, run_app, run_app_dev


def test_enable_textual_feature_merges_and_deduplicates(monkeypatch) -> None:
    monkeypatch.setenv("TEXTUAL", "debug,devtools")

    _enable_textual_feature("devtools")

    assert os.environ["TEXTUAL"] == "debug,devtools"


@pytest.fixture
def service(monkeypatch, tmp_path):
    from opengrader.server.workspace.client import ServiceConnection, WorkspaceClient

    workspace = Workspace.create(tmp_path)
    workspace.create_assignment(
        "---\ntitle: Older exam\n---\n- (2 points) First question\n"
    )
    workspace.create_assignment(
        "---\ntitle: Newer exam\n---\n- (2 points) Second question\n"
    )
    connection = ServiceConnection(
        root=str(tmp_path),
        url="http://localhost",
        pid=0,
        workspace_id=workspace.info().id,
    )
    monkeypatch.setattr(
        "opengrader.server.workspace.client.ensure_service", lambda root: connection
    )

    def request(self, method, path, **kwargs):
        if path == "/api/workspace":
            return workspace.info().model_dump_json().encode()
        assert method == "GET" and path == "/assignments"
        return json.dumps(
            [item.model_dump() for item in workspace.assignments()]
        ).encode()

    monkeypatch.setattr(WorkspaceClient, "request", request)
    return connection


@pytest.mark.parametrize("explicit", [False, True])
def test_run_app_opens_latest_assignment_unless_explicit(service, explicit) -> None:
    assignments = Workspace(Path(service.root)).assignments()
    expected = assignments[0] if explicit else assignments[-1]
    args = ["--assignment", expected.id] if explicit else []
    with patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls:
        run_app(args)
    provider = app_cls.call_args.kwargs["data_provider"]
    assert provider.client.connection == service
    assert provider.assignment == expected
    app_cls.return_value.run.assert_called_once_with()
    with exclusive_lock(provider.journal.path.with_suffix(".lock")):
        pass


def test_switch_assignment_releases_session_and_loads_new_selection(service) -> None:
    workspace = Workspace(Path(service.root))
    oldest, latest = workspace.assignments()
    workspace.delete_assignment(oldest.id)

    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
    ):
        app_cls.return_value.run.side_effect = ["switch_assignment", None]

        def choose_assignment():
            provider = app_cls.call_args.kwargs["data_provider"]
            with exclusive_lock(provider.journal.path.with_suffix(".lock")):
                pass
            assert home_cls.call_args.args[0] is provider.client
            # Setup can create an assignment while the chooser is open.
            return workspace.create_assignment(
                "---\ntitle: Another exam\n---\n- (3 points) Third question\n"
            ).id

        home_cls.return_value.run.side_effect = choose_assignment
        run_app([])

    providers = [call.kwargs["data_provider"] for call in app_cls.call_args_list]
    assert [provider.assignment.id for provider in providers] == [
        latest.id,
        workspace.assignments()[-1].id,
    ]
    home_cls.return_value.run.assert_called_once_with()
    with exclusive_lock(providers[-1].journal.path.with_suffix(".lock")):
        pass


def test_empty_workspace_opens_home_and_can_quit(service) -> None:
    workspace = Workspace(Path(service.root))
    for assignment in workspace.assignments():
        workspace.delete_assignment(assignment.id)
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.return_value = None
        run_app([])
    home_cls.return_value.run.assert_called_once_with()
    app_cls.assert_not_called()


def test_run_app_dev_enables_devtools_before_launch(monkeypatch) -> None:
    monkeypatch.delenv("TEXTUAL", raising=False)

    with patch("opengrader.tui.main.run_app") as run_app_mock:
        run_app_dev()

    assert os.environ["TEXTUAL"] == "devtools"
    run_app_mock.assert_called_once()


def test_check_graphics_reports_and_exits_without_launching(monkeypatch) -> None:
    monkeypatch.setenv("OPENGRADER_GRAPHICS", "sixel")

    with patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls:
        with pytest.raises(SystemExit) as excinfo:
            run_app(["--check-graphics"])

    assert excinfo.value.code == 0
    app_cls.assert_not_called()


def test_graphics_override_is_applied_before_the_app_starts(
    monkeypatch, service
) -> None:
    from opengrader.tui import terminal_graphics

    monkeypatch.delenv("OPENGRADER_GRAPHICS", raising=False)
    terminal_graphics.set_active_graphics(None)
    seen: list[str] = []

    class RecordingApp:
        def __init__(self, **kwargs) -> None:
            seen.append(terminal_graphics.active_graphics().protocol)

        def run(self) -> None:
            pass

    monkeypatch.setattr("opengrader.tui.main.TuiApp", RecordingApp)
    try:
        run_app(["--graphics", "iterm2"])
    finally:
        terminal_graphics.set_active_graphics(None)

    assert seen == ["iterm2"]


def test_unknown_graphics_protocol_is_rejected() -> None:
    with pytest.raises(SystemExit):
        run_app(["--graphics", "jpeg"])
