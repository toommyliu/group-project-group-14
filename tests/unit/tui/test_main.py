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

    # The repository may itself be a workspace; isolate Home's auto-discovery.
    monkeypatch.chdir(tmp_path.parent)
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
        assert method == "GET" and path == "/api/assignments"
        return json.dumps(
            [item.model_dump() for item in workspace.assignments()]
        ).encode()

    monkeypatch.setattr(WorkspaceClient, "request", request)
    return connection


@pytest.mark.parametrize("explicit", [False, True])
def test_run_app_chooses_when_multiple_assignments_exist(service, explicit) -> None:
    assignments = Workspace(Path(service.root)).assignments()
    expected = assignments[0]
    args = ["--workspace", service.root]
    if explicit:
        args += ["--assignment", expected.id]
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.return_value = expected.id
        run_app(args)
    assert home_cls.return_value.run.call_count == (0 if explicit else 1)
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
        run_app(["--workspace", service.root])

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
        run_app(["--workspace", service.root])
    home_cls.return_value.run.assert_called_once_with()
    app_cls.assert_not_called()


def test_empty_workspace_processes_its_folder_then_opens_assignment(service) -> None:
    from opengrader.tui.workspace_home import PROCESS_WORKSPACE

    workspace = Workspace(Path(service.root))
    for assignment in workspace.assignments():
        workspace.delete_assignment(assignment.id)

    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
        patch("opengrader.cli.run.run_cli") as run_cli,
    ):
        home_cls.return_value.run.return_value = PROCESS_WORKSPACE

        def publish(_argv):
            workspace.create_assignment(
                "---\ntitle: First exam\n---\n- (2 points) First question\n"
            )

        run_cli.side_effect = publish
        run_app(["--workspace", service.root])

    run_cli.assert_called_once_with(
        [service.root, "--workspace", service.root]
    )
    home_cls.return_value.run.assert_called_once_with()
    provider = app_cls.call_args.kwargs["data_provider"]
    assert provider.assignment.title == "First exam"


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
        assignment = Workspace(Path(service.root)).assignments()[0]
        run_app(["--graphics", "iterm2", "--workspace", service.root, "--assignment", assignment.id])
    finally:
        terminal_graphics.set_active_graphics(None)

    assert seen == ["iterm2"]


def test_unknown_graphics_protocol_is_rejected() -> None:
    with pytest.raises(SystemExit):
        run_app(["--graphics", "jpeg"])


@pytest.fixture
def no_service(monkeypatch, tmp_path):
    """An ordinary folder, and a service that must never be started."""

    def _refuse(root):
        raise AssertionError(f"the standalone path started a service for {root}")

    monkeypatch.setattr("opengrader.server.workspace.client.ensure_service", _refuse)
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def sample(tmp_path):
    target = tmp_path / "quiz"
    target.mkdir()
    (target / "qa.md").write_text(
        "---\ntitle: CS146: Quiz 1\n---\n- (2 points) First question\n"
    )
    (target / "student-submissions").mkdir()
    return target


def test_a_bare_tui_opens_home_and_nothing_else(no_service) -> None:
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.home.Home", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.return_value = None
        run_app([])

    home_cls.assert_called_once_with(start_path="", message="")
    app_cls.assert_not_called()
    assert not (no_service / ".opengrader").exists()  # no workspace created


def test_a_bare_tui_in_a_workspace_opens_assignment_chooser(service, monkeypatch) -> None:
    monkeypatch.chdir(service.root)
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.return_value = Workspace(Path(service.root)).assignments()[0].id
        run_app([])

    home_cls.assert_called_once()
    app_cls.assert_called_once()


def test_a_folder_chosen_on_home_is_graded_remembered_and_f2_returns(
    no_service, sample
) -> None:
    from opengrader.core.recents import load_recents
    from opengrader.tui.home import HomeChoice

    qa, subs = sample / "qa.md", sample / "student-submissions"
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.home.Home", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.side_effect = [
            HomeChoice("folder", folder=sample, qa_file=qa, submissions=subs),
            None,
        ]
        app_cls.return_value.run.return_value = "switch_assignment"
        run_app([])

    app_cls.assert_called_once_with(qa_file_path=qa, submissions_path=subs)
    assert home_cls.call_count == 2  # F2 came back to Home
    assert home_cls.call_args.kwargs["start_path"] == str(sample)
    [recent] = load_recents()
    assert (recent.kind, recent.title) == ("folder", "CS146: Quiz 1")


def test_quitting_grading_quits_the_app(no_service, sample) -> None:
    from opengrader.tui.home import HomeChoice

    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.home.Home", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.return_value = HomeChoice(
            "folder", folder=sample, qa_file=sample / "qa.md",
            submissions=sample / "student-submissions",
        )
        app_cls.return_value.run.return_value = None
        run_app([])

    assert home_cls.call_count == 1
    app_cls.assert_called_once_with(
        qa_file_path=sample / "qa.md",
        submissions_path=sample / "student-submissions",
    )


def test_a_broken_folder_comes_back_to_home_with_the_reason(no_service, sample) -> None:
    from opengrader.tui.home import HomeChoice

    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.home.Home", autospec=True) as home_cls,
    ):
        app_cls.side_effect = ValueError("bad answer key")
        home_cls.return_value.run.side_effect = [
            HomeChoice("folder", folder=sample, qa_file=sample / "qa.md",
                       submissions=sample / "student-submissions"),
            None,
        ]
        run_app([])

    assert "bad answer key" in home_cls.call_args.kwargs["message"]


def test_scans_open_tools_with_the_pipeline_filled_in(no_service) -> None:
    from opengrader.tui.home import HomeChoice

    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.home.Home", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.return_value = HomeChoice(
            "tools", pipeline_folder=no_service
        )
        app_cls.return_value.run.return_value = None
        run_app([])

    app_cls.assert_called_once_with(pipeline_folder=no_service)


def test_a_workspace_chosen_on_home_opens_and_its_home_key_returns(service) -> None:
    from opengrader.core.recents import load_recents
    from opengrader.tui.home import HomeChoice
    from opengrader.tui.workspace_home import GO_HOME

    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.home.Home", autospec=True) as home_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as ws_home,
    ):
        home_cls.return_value.run.side_effect = [
            HomeChoice("workspace", folder=Path(service.root)),
            None,
        ]
        app_cls.return_value.run.return_value = "switch_assignment"  # F2
        ws_home.return_value.run.side_effect = [
            Workspace(Path(service.root)).assignments()[0].id,
            GO_HOME,
        ]
        run_app([])

    assert app_cls.call_args.kwargs["data_provider"].client.connection == service
    assert home_cls.call_count == 2
    assert load_recents()[0].kind == "workspace"


def test_home_import_records_the_source_submissions_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from opengrader.core.workspace.intake import publish_pipeline
    from opengrader.core.workspace.writeback import locate_submissions_dir
    from opengrader.server.workspace.client import ServiceConnection, WorkspaceClient
    from opengrader.tui.main import _import_as_workspace

    root = tmp_path / "quiz"
    shutil.copytree(Path("samples/cs146-quiz1"), root)
    workspace = Workspace.create(root)
    connection = ServiceConnection(
        root=str(root), url="http://localhost", pid=0,
        workspace_id=workspace.info().id,
    )
    monkeypatch.setattr(
        "opengrader.server.workspace.client.ensure_service", lambda root: connection
    )

    def request(self, method, path, body, **kwargs):
        assert method == "POST"
        assignment_id = path.removeprefix("/api/assignments/").removesuffix("/imports")
        assert path == f"/api/assignments/{assignment_id}/imports"
        return publish_pipeline(workspace, assignment_id, body).model_dump_json().encode()

    monkeypatch.setattr(WorkspaceClient, "request", request)
    imported_root, assignment_id = _import_as_workspace(
        root / "qa.md", root / "student-submissions"
    )

    assert imported_root == root
    assert locate_submissions_dir(workspace, assignment_id) == root / "student-submissions"


def test_a_workspace_that_will_not_start_returns_to_home_with_the_reason(
    service, monkeypatch
) -> None:
    from opengrader.tui.home import HomeChoice

    def _broken(root):
        raise RuntimeError("The workspace service could not start.")

    monkeypatch.setattr("opengrader.server.workspace.client.ensure_service", _broken)
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True),
        patch("opengrader.tui.home.Home", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.side_effect = [
            HomeChoice("workspace", folder=Path(service.root)),
            None,
        ]
        run_app([])

    assert "could not start" in home_cls.call_args.kwargs["message"]


def test_qa_file_and_submissions_grade_a_folder_directly(no_service, sample) -> None:
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.home.Home", autospec=True) as home_cls,
    ):
        app_cls.return_value.run.return_value = None
        run_app(["--qa-file", str(sample / "qa.md"), "--submissions", str(sample)])

    app_cls.assert_called_once_with(
        qa_file_path=sample / "qa.md", submissions_path=sample
    )
    home_cls.assert_not_called()


@pytest.mark.parametrize(
    "argv",
    [
        ["--qa-file", "qa.md"],
        ["--submissions", "subs"],
        ["--qa-file", "qa.md", "--submissions", "subs", "--workspace", "."],
    ],
)
def test_folder_flags_are_validated(no_service, argv) -> None:
    with patch("opengrader.tui.main.TuiApp", autospec=True), pytest.raises(SystemExit) as exc:
        run_app(argv)

    assert exc.value.code == 2


def test_an_explicit_workspace_whose_service_fails_is_an_error(service, monkeypatch) -> None:
    def _broken(root):
        raise RuntimeError("The workspace service could not start.")

    monkeypatch.setattr("opengrader.server.workspace.client.ensure_service", _broken)
    with patch("opengrader.tui.main.TuiApp", autospec=True), pytest.raises(SystemExit) as exc:
        run_app(["--workspace", service.root])

    assert exc.value.code == 2


def test_tools_from_the_workspace_chooser_then_f2_returns_to_the_chooser(service) -> None:
    from opengrader.tui.workspace_home import OPEN_TOOLS

    workspace = Workspace(Path(service.root))
    for assignment in workspace.assignments():
        workspace.delete_assignment(assignment.id)

    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.side_effect = [OPEN_TOOLS, None]
        app_cls.return_value.run.return_value = "switch_assignment"
        run_app(["--workspace", service.root])

    app_cls.assert_called_once_with()  # Tools, over no workspace
    assert home_cls.return_value.run.call_count == 2


def test_open_as_workspace_imports_then_opens_that_assignment(service, sample) -> None:
    from opengrader.tui.home import HomeChoice

    oldest, _latest = Workspace(Path(service.root)).assignments()
    calls = []

    def _import(qa, subs):
        calls.append((qa, subs))
        return Path(service.root), oldest.id

    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.home.Home", autospec=True) as home_cls,
        patch("opengrader.tui.main._import_as_workspace", side_effect=_import),
    ):
        home_cls.return_value.run.side_effect = [
            HomeChoice(
                "new_workspace", folder=sample, qa_file=sample / "qa.md",
                submissions=sample / "student-submissions",
            ),
            None,
        ]
        app_cls.return_value.run.return_value = None
        run_app([])

    assert calls == [(sample / "qa.md", sample / "student-submissions")]
    assert app_cls.call_args.kwargs["data_provider"].assignment.id == oldest.id
