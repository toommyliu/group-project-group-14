import json
from pathlib import Path
from unittest.mock import patch

import pytest

from opengrader.core.workspace.store import Workspace
from opengrader.server.workspace.client import exclusive_lock
from opengrader.cli import run_command
from opengrader.tui.main import classic_command, tui_command


@pytest.fixture
def service(monkeypatch, tmp_path):
    from opengrader.server.workspace.client import ServiceConnection, WorkspaceClient

    # The repository may itself be a workspace; isolate auto-discovery.
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
        run_command(tui_command, args)
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
        run_command(tui_command, ["--workspace", service.root])

    providers = [call.kwargs["data_provider"] for call in app_cls.call_args_list]
    assert [provider.assignment.id for provider in providers] == [
        latest.id,
        workspace.assignments()[-1].id,
    ]
    home_cls.return_value.run.assert_called_once_with()
    with exclusive_lock(providers[-1].journal.path.with_suffix(".lock")):
        pass


def test_empty_workspace_opens_its_assignment_list_and_can_quit(service) -> None:
    workspace = Workspace(Path(service.root))
    for assignment in workspace.assignments():
        workspace.delete_assignment(assignment.id)
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.return_value = None
        run_command(tui_command, ["--workspace", service.root])
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
        patch("opengrader.cli.run.command.callback") as run_callback,
    ):
        home_cls.return_value.run.return_value = PROCESS_WORKSPACE

        def publish(**_options):
            workspace.create_assignment(
                "---\ntitle: First exam\n---\n- (2 points) First question\n"
            )

        run_callback.side_effect = publish
        run_command(tui_command, ["--workspace", service.root])

    run_callback.assert_called_once()
    options = run_callback.call_args.kwargs
    assert options["folder"] == service.root
    assert options["workspace"] == Path(service.root)
    home_cls.return_value.run.assert_called_once_with()
    provider = app_cls.call_args.kwargs["data_provider"]
    assert provider.assignment.title == "First exam"


def test_check_graphics_reports_and_exits_without_launching(monkeypatch, capsys) -> None:
    monkeypatch.setenv("OPENGRADER_GRAPHICS", "sixel")

    with patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls:
        run_command(tui_command, ["--check-graphics"])

    app_cls.assert_not_called()
    assert "sixel" in capsys.readouterr().out


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
        run_command(tui_command, ["--graphics", "iterm2", "--workspace", service.root, "--assignment", assignment.id])
    finally:
        terminal_graphics.set_active_graphics(None)

    assert seen == ["iterm2"]


def test_unknown_graphics_protocol_is_rejected() -> None:
    with pytest.raises(SystemExit):
        run_command(tui_command, ["--graphics", "jpeg"])


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


def test_a_bare_tui_outside_a_workspace_explains_itself_and_creates_nothing(
    no_service, capsys
) -> None:
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        pytest.raises(SystemExit) as exc,
    ):
        run_command(tui_command, [])

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "no workspace" in err and "opengrader classic" in err
    app_cls.assert_not_called()
    assert not (no_service / ".opengrader").exists()  # no workspace created


def test_a_bare_tui_in_a_workspace_opens_assignment_chooser(service, monkeypatch) -> None:
    monkeypatch.chdir(service.root)
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.return_value = Workspace(Path(service.root)).assignments()[0].id
        run_command(tui_command, [])

    home_cls.assert_called_once()
    app_cls.assert_called_once()


def test_a_class_folder_named_outside_a_workspace_is_imported_and_opened(
    service, tmp_path_factory
) -> None:
    oldest, _latest = Workspace(Path(service.root)).assignments()
    calls = []
    # Outside the fixture's workspace (which is its own tmp_path).
    sample = tmp_path_factory.mktemp("outside") / "quiz"
    (sample / "student-submissions").mkdir(parents=True)
    (sample / "qa.md").write_text(
        "---\ntitle: CS146: Quiz 1\n---\n- (2 points) First question\n"
    )

    def _import(qa, subs):
        calls.append((qa, subs))
        return Path(service.root), oldest.id

    (sample / "student-submissions" / "s1.submission.md").write_text(
        "---\nstudent_name: A\nstudent_id: 1\n---\n"
    )
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
        patch("opengrader.tui.main._import_as_workspace", side_effect=_import),
    ):
        app_cls.return_value.run.return_value = None
        run_command(tui_command, [str(sample)])

    [(qa, _submissions)] = calls  # imported once, with the folder's own key
    assert qa == sample / "qa.md"
    assert app_cls.call_args.kwargs["data_provider"].assignment.id == oldest.id
    home_cls.assert_not_called()  # straight to grading, no list in between


def test_any_other_folder_named_becomes_the_workspace_root(
    service, tmp_path_factory, monkeypatch
) -> None:
    from opengrader.server.workspace import client as client_module

    folder = tmp_path_factory.mktemp("outside") / "new-course"
    folder.mkdir()
    started = []
    real = client_module.ensure_service

    def _ensure(root):
        started.append(Path(root))
        return real(root)

    monkeypatch.setattr(client_module, "ensure_service", _ensure)
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True),
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
        patch("opengrader.tui.main._import_as_workspace") as import_mock,
    ):
        home_cls.return_value.run.return_value = None
        run_command(tui_command, [str(folder)])

    assert started == [folder.resolve()]
    import_mock.assert_not_called()
    home_cls.assert_called_once()


def test_import_records_the_source_submissions_folder(
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


def test_qa_file_and_submissions_grade_a_folder_directly(no_service, sample) -> None:
    with patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls:
        app_cls.return_value.run.return_value = None
        run_command(tui_command, ["--qa-file", str(sample / "qa.md"), "--submissions", str(sample)])

    app_cls.assert_called_once_with(
        qa_file_path=sample / "qa.md", submissions_path=sample
    )


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
        run_command(tui_command, argv)

    assert exc.value.code == 2


def test_an_explicit_workspace_whose_service_fails_is_an_error(service, monkeypatch) -> None:
    def _broken(root):
        raise RuntimeError("The workspace service could not start.")

    monkeypatch.setattr("opengrader.server.workspace.client.ensure_service", _broken)
    with patch("opengrader.tui.main.TuiApp", autospec=True), pytest.raises(SystemExit) as exc:
        run_command(tui_command, ["--workspace", service.root])

    assert exc.value.code == 2


def test_classic_opens_tools_and_never_a_workspace(no_service) -> None:
    with patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls:
        app_cls.return_value.run.return_value = None
        run_command(classic_command, [])

    app_cls.assert_called_once_with()  # Tools, with the Start grading card
    assert not (no_service / ".opengrader").exists()


def test_classic_inside_a_workspace_still_opens_tools_not_the_workspace(
    service, monkeypatch
) -> None:
    monkeypatch.chdir(service.root)
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
    ):
        run_command(classic_command, [])

    app_cls.assert_called_once_with()
    home_cls.assert_not_called()


def test_classic_grades_a_named_folder_in_place(no_service, sample) -> None:
    with patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls:
        run_command(
            classic_command,
            ["--qa-file", str(sample / "qa.md"), "--submissions", str(sample)]
        )

    app_cls.assert_called_once_with(
        qa_file_path=sample / "qa.md", submissions_path=sample
    )


@pytest.mark.parametrize("argv", [["--qa-file", "qa.md"], ["--workspace", "."]])
def test_classic_rejects_half_a_folder_and_workspace_flags(no_service, argv) -> None:
    with patch("opengrader.tui.main.TuiApp", autospec=True), pytest.raises(SystemExit) as exc:
        run_command(classic_command, argv)

    assert exc.value.code == 2


def test_a_bare_tui_outside_a_workspace_offers_the_recent_ones(
    service, tmp_path_factory, monkeypatch
) -> None:
    from opengrader.core.recents import record_recent

    record_recent("workspace", service.root)
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    monkeypatch.chdir(elsewhere)
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.recent_workspaces.RecentWorkspaces", autospec=True) as recent_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
    ):
        recent_cls.return_value.run.return_value = Path(service.root)
        home_cls.return_value.run.return_value = None
        run_command(tui_command, [])

    [item] = recent_cls.call_args.args[0]
    assert Path(item.path) == Path(service.root).resolve()
    assert recent_cls.call_args.kwargs["here"] == elsewhere.resolve()
    home_cls.assert_called_once()  # two assignments: the list, as usual
    app_cls.assert_not_called()
    assert not (elsewhere / ".opengrader").exists()


def test_quitting_the_recent_list_quits_quietly(
    service, tmp_path_factory, monkeypatch
) -> None:
    from opengrader.core.recents import record_recent

    record_recent("workspace", service.root)
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.recent_workspaces.RecentWorkspaces", autospec=True) as recent_cls,
    ):
        recent_cls.return_value.run.return_value = None
        run_command(tui_command, [])

    app_cls.assert_not_called()


def test_opening_an_assignment_records_it_and_the_list_starts_there_next_time(
    service,
) -> None:
    from opengrader.core.recents import recent_workspace

    older, newer = Workspace(Path(service.root)).assignments()
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.return_value = newer.id
        app_cls.return_value.run.return_value = None
        run_command(tui_command, ["--workspace", service.root])
        assert home_cls.call_args.kwargs["highlight"] is None  # nothing on record yet

        item = recent_workspace(service.root)
        assert (item.assignment, item.assignment_title) == (newer.id, "Newer exam")

        run_command(tui_command, ["--workspace", service.root])
        assert home_cls.call_args.kwargs["highlight"] == newer.id


def test_f2_comes_back_to_the_list_on_the_assignment_just_graded(service) -> None:
    older, _newer = Workspace(Path(service.root)).assignments()
    with (
        patch("opengrader.tui.main.TuiApp", autospec=True) as app_cls,
        patch("opengrader.tui.workspace_home.WorkspaceHome", autospec=True) as home_cls,
    ):
        home_cls.return_value.run.side_effect = [older.id, None]
        app_cls.return_value.run.return_value = "switch_assignment"
        run_command(tui_command, ["--workspace", service.root])

    assert [c.kwargs["highlight"] for c in home_cls.call_args_list] == [None, older.id]
