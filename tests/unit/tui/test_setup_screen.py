import asyncio

from textual.containers import VerticalScroll
from textual.widgets import Button, Checkbox, Collapsible, Input, Static

import opengrader.tui.ui.screens.setup_screen as setup_screen
from opengrader.core import app_config
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui import button_roles
from opengrader.tui.ui.screens.setup_screen import SetupScreen

from .fakes import DEFAULT_IMAGE_PATH

PROJECT_SAMPLES = __import__('pathlib').Path(__file__).resolve().parents[3] / "samples"


def test_setup_scroll_reaches_the_very_last_section() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()

            container = app.query_one("#setup-container", VerticalScroll)
            container.scroll_end(animate=False)
            await pilot.pause()

            sections = list(app.query(Collapsible))
            last = sections[-1]
            assert last.region.height > 0
            assert last.region.y + last.region.height <= app.size.height

    asyncio.run(run())



def test_every_tools_page_button_is_coloured_by_what_it_does() -> None:
    """Green starts work, blue opens a screen, purple is the browser app.

    Every button on the Tools page has a role, except the two that should not
    stand out: Stop server stays red, and Preview stays grey beside Run
    Pipeline. A new button added without a role fails here, so its colour is a
    decision, not an accident.
    """
    roles = (button_roles.LAUNCH, button_roles.OPEN, button_roles.BROWSER)
    unroled = {"browser-stop", "pipe-preview"}

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            buttons = {b.id: b for b in screen.query(Button) if b.id}

            missing = sorted(
                bid
                for bid, button in buttons.items()
                if bid not in unroled
                and not any(button.has_class(role) for role in roles)
            )
            assert missing == []
            assert buttons["browser-stop"].variant == "error"

            assert buttons["launch-button"].has_class(button_roles.LAUNCH)
            assert buttons["pipe-run"].has_class(button_roles.LAUNCH)
            assert buttons["run-ai-settings"].has_class(button_roles.OPEN)
            assert buttons["browse-tui-sub-path"].has_class(button_roles.OPEN)
            assert buttons["browser-open-button"].has_class(button_roles.BROWSER)

            # The three roles look different (an outline button has no fill
            # until hovered, so it is the text and outline colour).
            colours = {
                role: buttons[bid].styles.color
                for role, bid in (
                    (button_roles.LAUNCH, "launch-button"),
                    (button_roles.OPEN, "run-ai-settings"),
                    (button_roles.BROWSER, "browser-open-button"),
                )
            }
            assert len(set(colours.values())) == 3

    asyncio.run(run())



def test_every_tools_page_button_is_an_outline_button() -> None:
    """One look everywhere: full height, outlined, filled only while hovered."""

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            buttons = list(screen.query(Button))
            assert buttons
            assert all(b.has_class(button_roles.OUTLINE) for b in buttons)
            assert not any(b.compact for b in buttons)

            launch = screen.query_one("#launch-button", Button)
            assert launch.styles.background.a == 0
            assert launch.styles.border_top[0] == "round"
            await pilot.hover("#launch-button")
            await pilot.pause()
            # Filled, with the half-block border that keeps the fill the
            # outline's size.
            assert launch.styles.background.a == 1
            assert launch.styles.border_top[0] == "inner"
            launch.add_class("-active")
            await pilot.pause()
            assert "reverse" in str(launch.styles.text_style)

    asyncio.run(run())


def test_the_screens_the_tools_page_opens_have_outline_buttons() -> None:
    """Browse, Open AI setup, Manage providers … open screens that match it.

    The grading screen and the reports opened while grading are left out on
    purpose (button_roles docstring); a new Tools-page dialog belongs here.
    """
    import importlib

    from opengrader.tui.ui.button_roles import OutlineButtons

    screens = {
        "ai_settings": ["AISettingsScreen"],
        "answer_fill": ["AnswerFillScreen"],
        "backup": ["BackupScreen"],
        "browser_build": ["BrowserBuildScreen"],
        "cloud_consent": [
            "CloudConsentScreen",
            "CredentialConsentScreen",
            "CloudPreflightConfirmScreen",
        ],
        "course_builder": ["CourseBuilderScreen", "_AssignmentRow"],
        "course_grades": ["CourseGradesScreen"],
        "doctor": ["DoctorScreen"],
        "format_guide": ["FormatGuideScreen"],
        "grade_without_ai": ["GradeWithoutAIScreen"],
        "handoff": ["GraderPacketScreen", "MergeScreen"],
        "llm_eval": ["LlmEvalScreen"],
        "page_order": ["PageOrderScreen"],
        "path_picker": ["PathPickerScreen"],
        "prepare_folder": ["PrepareFolderScreen"],
        "provider": ["ProviderScreen"],
        "provider_setup": ["ProviderSetupScreen"],
        "region_map": ["RegionMapScreen"],
    }
    missing = [
        f"{module}.{name}"
        for module, names in screens.items()
        for name in names
        if not issubclass(
            getattr(
                importlib.import_module(f"opengrader.tui.ui.screens.{module}_screen"),
                name,
            ),
            OutlineButtons,
        )
    ]
    assert missing == []
    assert issubclass(SetupScreen, OutlineButtons)


def test_a_dialogs_main_action_is_green_and_what_opens_a_screen_is_blue() -> None:
    from opengrader.tui.ui.screens.provider_screen import ProviderScreen

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.push_screen(ProviderScreen())
            await pilot.pause()
            screen = app.screen
            assert all(
                b.has_class(button_roles.OUTLINE) and not b.compact
                for b in screen.query(Button)
            )
            assert screen.query_one("#prov-setup", Button).has_class(
                button_roles.OPEN
            )
            assert not screen.query_one("#prov-setup", Button).has_class(
                button_roles.LAUNCH
            )
            assert screen.query_one("#prov-remove", Button).variant == "error"
            close = screen.query_one("#prov-close", Button)
            assert not any(
                close.has_class(role)
                for role in (button_roles.LAUNCH, button_roles.OPEN)
            )

    asyncio.run(run())

def test_template_qr_checkbox_reveals_roster_and_builds_argv(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_command",
        lambda _command, argv, **kwargs: captured.__setitem__("argv", list(argv)),
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)

            app.query_one("#tmpl-qa-input", Input).value = "/tmp/qa.md"
            app.query_one("#tmpl-output", Input).value = "/tmp/out.pdf"

            assert app.query_one("#tmpl-roster-area").has_class("hidden")

            app.query_one("#tmpl-qr", Checkbox).value = True
            await pilot.pause()
            assert not app.query_one("#tmpl-roster-area").has_class("hidden")
            screen._run_template()
            assert "--qr" in captured["argv"]
            assert "--roster" not in captured["argv"]

            app.query_one("#tmpl-roster", Input).value = "/tmp/roster.tsv"
            screen._run_template()
            assert captured["argv"].count("--qr") == 0
            assert "--roster" in captured["argv"]
            assert "/tmp/roster.tsv" in captured["argv"]

            app.query_one("#tmpl-qr", Checkbox).value = False
            await pilot.pause()
            assert app.query_one("#tmpl-roster-area").has_class("hidden")
            screen._run_template()
            assert "--roster" not in captured["argv"]
            assert "--qr" not in captured["argv"]

    asyncio.run(run())


def _pipe_request(tmp_path, setup=lambda _app: None, *, notes=None):  # noqa: ANN001, ANN202
    """The request the pipeline card builds after `setup` has filled it in."""

    async def run():  # noqa: ANN202
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            if notes is not None:
                app.notify = lambda message, **kwargs: notes.append(str(message))
            app.query_one("#pipe-scan", Input).value = str(tmp_path / "scan.pdf")
            app.query_one("#pipe-out", Input).value = str(tmp_path / "class")
            setup(app)
            await pilot.pause()
            return app.query_one(SetupScreen)._build_pipeline_request()

    return asyncio.run(run())


def _set_jobs(app, value: str) -> None:  # noqa: ANN001
    app.query_one("#pipe-parallel", Checkbox).value = True
    app.query_one("#pipe-jobs", Input).value = value


def test_the_pipeline_picks_the_job_count_itself_by_default(tmp_path) -> None:  # noqa: ANN001
    request = _pipe_request(tmp_path)
    assert request is not None
    assert request.jobs is None
    assert request.fresh is False


def test_a_job_count_reaches_the_pipeline_request(tmp_path) -> None:  # noqa: ANN001
    request = _pipe_request(tmp_path, lambda app: _set_jobs(app, "4"))
    assert request is not None
    assert request.jobs == 4


def test_a_ticked_but_blank_job_count_stays_automatic(tmp_path) -> None:  # noqa: ANN001
    request = _pipe_request(
        tmp_path,
        lambda app: setattr(app.query_one("#pipe-parallel", Checkbox), "value", True),
    )
    assert request is not None and request.jobs is None


def test_unticking_the_box_ignores_a_number_left_behind(tmp_path) -> None:  # noqa: ANN001
    def setup(app) -> None:  # noqa: ANN001
        _set_jobs(app, "4")
        app.query_one("#pipe-parallel", Checkbox).value = False

    request = _pipe_request(tmp_path, setup)
    assert request is not None and request.jobs is None


def test_a_nonsense_job_count_is_refused(tmp_path) -> None:  # noqa: ANN001
    notes: list[str] = []
    assert _pipe_request(tmp_path, lambda app: _set_jobs(app, "-2"), notes=notes) is None
    assert notes


def test_start_over_reaches_the_pipeline_request(tmp_path) -> None:  # noqa: ANN001
    request = _pipe_request(
        tmp_path,
        lambda app: setattr(app.query_one("#pipe-fresh", Checkbox), "value", True),
    )
    assert request is not None and request.fresh is True


def test_a_mixed_stack_sends_one_roster_per_line(tmp_path) -> None:  # noqa: ANN001
    from textual.widgets import TextArea

    def setup(app) -> None:  # noqa: ANN001
        app.query_one("#pipe-many-rosters", Checkbox).value = True
        app.query_one("#pipe-roster-list", TextArea).text = (
            "/tmp/mw.tsv=MW\n\n  /tmp/tth.tsv  \n"
        )

    request = _pipe_request(tmp_path, setup)
    assert request is not None
    assert request.section_rosters == ("/tmp/mw.tsv=MW", "/tmp/tth.tsv")


def test_rosters_left_in_the_list_are_ignored_once_the_box_is_unticked(tmp_path) -> None:  # noqa: ANN001
    from textual.widgets import TextArea

    def setup(app) -> None:  # noqa: ANN001
        app.query_one("#pipe-roster-list", TextArea).text = "/tmp/mw.tsv=MW\n"

    request = _pipe_request(tmp_path, setup)
    assert request is not None
    assert request.section_rosters == ()


def test_a_mixed_stack_with_no_rosters_is_refused(tmp_path) -> None:  # noqa: ANN001
    notes: list[str] = []
    request = _pipe_request(
        tmp_path,
        lambda app: setattr(app.query_one("#pipe-many-rosters", Checkbox), "value", True),
        notes=notes,
    )
    assert request is None
    assert notes


def test_browse_appends_roster_lines(monkeypatch) -> None:
    from pathlib import Path

    from textual.widgets import TextArea

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-many-rosters", Checkbox).value = True
            await pilot.pause()

            captured: dict[str, object] = {}
            monkeypatch.setattr(
                app,
                "push_screen",
                lambda _screen, callback=None: captured.__setitem__("cb", callback),
            )

            screen._append_roster_path()
            captured["cb"](Path("/tmp/mw.tsv"))
            await pilot.pause()
            screen._append_roster_path()
            captured["cb"](Path("/tmp/tth.tsv"))
            await pilot.pause()

            text = app.query_one("#pipe-roster-list", TextArea).text
            assert text == "/tmp/mw.tsv\n/tmp/tth.tsv\n"

    asyncio.run(run())


def test_bubble_sheet_builds_argv_and_never_adds_a_redundant_qr(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_command",
        lambda _command, argv, **kwargs: captured.__setitem__("argv", list(argv)),
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#tmpl-qa-input", Input).value = "/tmp/qa.md"
            app.query_one("#tmpl-output", Input).value = "/tmp/out.pdf"

            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()
            screen._run_template()
            assert "--bubble-sheet" in captured["argv"]
            assert "--qr" not in captured["argv"]

            app.query_one("#tmpl-roster", Input).value = "/tmp/roster.tsv"
            screen._run_template()
            assert "--bubble-sheet" in captured["argv"]
            assert "--roster" in captured["argv"]
            assert "--qr" not in captured["argv"]

    asyncio.run(run())


def test_unticking_bubble_sheet_drops_a_roster_left_behind(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_command",
        lambda _command, argv, **kwargs: captured.__setitem__("argv", list(argv)),
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#tmpl-qa-input", Input).value = "/tmp/qa.md"
            app.query_one("#tmpl-output", Input).value = "/tmp/out.pdf"

            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()
            app.query_one("#tmpl-roster", Input).value = "/tmp/roster.tsv"
            app.query_one("#tmpl-bubble-sheet", Checkbox).value = False
            await pilot.pause()

            screen._run_template()
            assert "--bubble-sheet" not in captured["argv"]
            assert "--roster" not in captured["argv"]

    asyncio.run(run())


def test_questions_left_off_a_bubble_sheet_are_surfaced_to_the_user(monkeypatch) -> None:
    def fake_cli(_command, argv, **kwargs) -> None:  # noqa: ANN001
        print("Note: 1 question(s) are not multiple choice and were left off "
              "the sheet: q2b")
        print("Wrote bubble sheet (3 questions, 1 page(s)): /tmp/out.pdf")

    monkeypatch.setattr(setup_screen, "run_command", fake_cli)
    notices: list[tuple[str, str]] = []

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kwargs: notices.append(
                    (str(message), kwargs.get("severity", "information"))
                ),
            )
            app.query_one("#tmpl-qa-input", Input).value = "/tmp/qa.md"
            app.query_one("#tmpl-output", Input).value = "/tmp/out.pdf"
            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()
            screen._run_template()

    asyncio.run(run())

    assert len(notices) == 1
    message, severity = notices[0]
    assert "q2b" in message
    assert "Bubble sheet written" in message
    assert severity == "warning"


def test_copy_count_and_shuffle_reach_the_cli(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_command",
        lambda _command, argv, **kwargs: captured.__setitem__("argv", list(argv)),
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#tmpl-qa-input", Input).value = "/tmp/qa.md"
            app.query_one("#tmpl-output", Input).value = "/tmp/out.pdf"
            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()

            app.query_one("#tmpl-copies", Input).value = "120"
            app.query_one("#tmpl-shuffle", Checkbox).value = True
            screen._run_template()

            argv = captured["argv"]
            assert "--copies" in argv and "120" in argv
            assert "--shuffle" in argv
            assert "--roster" not in argv

    asyncio.run(run())


def test_a_roster_and_a_copy_count_together_are_refused(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_screen, "run_command", lambda _command, argv, **kwargs: calls.append(list(argv))
    )
    notices: list[tuple[str, str]] = []

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kwargs: notices.append(
                    (str(message), kwargs.get("severity", "information"))
                ),
            )
            app.query_one("#tmpl-qa-input", Input).value = "/tmp/qa.md"
            app.query_one("#tmpl-output", Input).value = "/tmp/out.pdf"
            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()
            app.query_one("#tmpl-shuffle", Checkbox).value = True
            await pilot.pause()
            app.query_one("#tmpl-roster", Input).value = "/tmp/roster.tsv"
            app.query_one("#tmpl-copies", Input).value = "50"
            screen._run_template()

    asyncio.run(run())

    assert calls == []
    assert notices and notices[0][1] == "error"
    assert "not both" in notices[0][0]


def test_a_nonsense_copy_count_is_refused(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_screen, "run_command", lambda _command, argv, **kwargs: calls.append(list(argv))
    )
    notices: list[tuple[str, str]] = []

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kwargs: notices.append(
                    (str(message), kwargs.get("severity", "information"))
                ),
            )
            app.query_one("#tmpl-qa-input", Input).value = "/tmp/qa.md"
            app.query_one("#tmpl-output", Input).value = "/tmp/out.pdf"
            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()
            app.query_one("#tmpl-shuffle", Checkbox).value = True
            await pilot.pause()
            app.query_one("#tmpl-copies", Input).value = "a few"
            screen._run_template()

    asyncio.run(run())
    assert calls == []
    assert notices and notices[0][1] == "error"


def test_scrambling_without_a_batch_is_refused(monkeypatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(
        setup_screen, "run_command", lambda _command, argv, **kwargs: calls.append(list(argv))
    )
    notices: list[tuple[str, str]] = []

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            monkeypatch.setattr(
                app,
                "notify",
                lambda message, **kwargs: notices.append(
                    (str(message), kwargs.get("severity", "information"))
                ),
            )
            app.query_one("#tmpl-qa-input", Input).value = "/tmp/qa.md"
            app.query_one("#tmpl-output", Input).value = "/tmp/out.pdf"
            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()
            app.query_one("#tmpl-shuffle", Checkbox).value = True
            screen._run_template()

    asyncio.run(run())
    assert calls == []
    assert notices and "roster or a number of sheets" in notices[0][0]


def test_unticking_bubble_sheet_ignores_a_copy_count_left_behind(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_command",
        lambda _command, argv, **kwargs: captured.__setitem__("argv", list(argv)),
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#tmpl-qa-input", Input).value = "/tmp/qa.md"
            app.query_one("#tmpl-output", Input).value = "/tmp/out.pdf"
            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()
            app.query_one("#tmpl-copies", Input).value = "50"
            app.query_one("#tmpl-shuffle", Checkbox).value = True
            app.query_one("#tmpl-bubble-sheet", Checkbox).value = False
            await pilot.pause()
            screen._run_template()

    asyncio.run(run())
    argv = captured["argv"]
    assert "--copies" not in argv and "--shuffle" not in argv


def test_unticking_scramble_ignores_a_count_left_behind(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_command",
        lambda _command, argv, **kwargs: captured.__setitem__("argv", list(argv)),
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#tmpl-qa-input", Input).value = "/tmp/qa.md"
            app.query_one("#tmpl-output", Input).value = "/tmp/out.pdf"
            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            app.query_one("#tmpl-shuffle", Checkbox).value = True
            await pilot.pause()
            app.query_one("#tmpl-copies", Input).value = "50"
            app.query_one("#tmpl-shuffle", Checkbox).value = False
            await pilot.pause()
            screen._run_template()

    asyncio.run(run())
    argv = captured["argv"]
    assert "--copies" not in argv and "--shuffle" not in argv
    assert "--bubble-sheet" in argv


def test_calibration_opens_prefilled_and_saves_its_answer_to_ai_setup() -> None:
    async def run() -> tuple[str, str, float]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#tui-qa-path", Input).value = "/tmp/sol.md"
            app.query_one("#tui-sub-path", Input).value = "/tmp/subs"
            await pilot.pause()

            screen._open_llm_eval()
            await pilot.pause()
            modal = app.screen
            solutions = modal._solutions_path
            submissions = modal._submissions_path

            modal.dismiss(0.75)
            await pilot.pause()

            return solutions, submissions, app_config.load_settings().min_confidence

    solutions, submissions, saved = asyncio.run(run())
    assert solutions == "/tmp/sol.md"
    assert submissions == "/tmp/subs"
    assert saved == 0.75


def test_calibration_falls_back_to_the_class_being_prepared() -> None:
    async def run() -> tuple[str, str]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#pipe-qa", Input).value = "/tmp/class/qa.md"
            app.query_one("#pipe-out", Input).value = "/tmp/class"
            await pilot.pause()

            app.query_one(SetupScreen)._open_llm_eval()
            await pilot.pause()
            modal = app.screen
            paths = modal._solutions_path, modal._submissions_path
            modal.dismiss(None)
            await pilot.pause()
            return paths

    assert asyncio.run(run()) == ("/tmp/class/qa.md", "/tmp/class/submissions")


def test_calibration_accept_marks_the_confidence_measured_and_wants_ai_grading() -> None:
    async def run() -> app_config.AISettings:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#tui-qa-path", Input).value = "/tmp/sol.md"
            app.query_one("#tui-sub-path", Input).value = "/tmp/subs"
            await pilot.pause()

            screen._open_llm_eval()
            await pilot.pause()
            app.screen.dismiss(0.8)
            await pilot.pause()

            return app_config.load_settings()

    settings = asyncio.run(run())
    assert settings.min_confidence_measured is True
    assert settings.grade_written is True


def test_calibration_refreshes_the_ai_setup_summary() -> None:
    async def run() -> tuple[str, str]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            before = str(app.query_one("#ai-current-summary", Static).render())
            screen._open_llm_eval()
            await pilot.pause()
            app.screen.dismiss(0.42)
            await pilot.pause()
            after = str(app.query_one("#ai-current-summary", Static).render())
            return before, after

    before, after = asyncio.run(run())
    assert "0.42" not in before
    assert "0.42" in after


def test_declining_the_calibration_leaves_ai_setup_alone() -> None:
    async def run() -> float | None:
        app_config.save_settings(app_config.AISettings(min_confidence=0.6))
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            screen._open_llm_eval()
            await pilot.pause()
            app.screen.dismiss(None)
            await pilot.pause()
            return app_config.load_settings().min_confidence

    assert asyncio.run(run()) == 0.6


def test_the_handoff_tool_falls_back_to_the_folder_being_graded(tmp_path) -> None:  # noqa: ANN001
    folder = tmp_path / "quiz"
    folder.mkdir()
    qa = folder / "qa.md"
    qa.write_text("---\ntitle: Quiz\n---\n- (2 points) First question\n")
    subs = folder / "student-submissions"
    subs.mkdir()
    (subs / "student-1.submission.md").write_text(
        "---\nstudent_name: Student 1\nstudent_id: 1\ncourse_code: CMPE\n"
        "assignment_title: Quiz\nsource_pdf: student.pdf\n---\n\n"
        "- q1:\n  - Question type: Free Response\n  - Prompt: First question\n"
    )

    async def run() -> str:
        app = TuiApp(
            default_image_path=DEFAULT_IMAGE_PATH, qa_file_path=qa, submissions_path=subs
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("f3")  # to Tools, with the folder still open
            await pilot.pause()
            app.query_one(SetupScreen)._open_grader_packet()
            await pilot.pause()
            modal = app.screen
            return f"{modal._submissions_path}|{modal._qa_file_path}"

    assert asyncio.run(run()) == f"{subs}|{qa}"


def test_the_handoff_tool_prefers_its_own_folder_field() -> None:
    async def run() -> str:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#handoff-submissions", Input).value = "/tmp/other"
            await pilot.pause()
            screen._open_merge()
            await pilot.pause()
            return app.screen._submissions_path

    assert asyncio.run(run()) == "/tmp/other"


def test_opening_a_handoff_tool_with_no_folder_is_refused_not_blank() -> None:
    async def run() -> int:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            depth_before = len(app.screen_stack)
            app.query_one(SetupScreen)._open_backup()
            await pilot.pause()
            return len(app.screen_stack) - depth_before

    assert asyncio.run(run()) == 0


def _prepared_class(app, tmp_path) -> None:  # noqa: ANN001
    """Point the pipeline card at a class folder that already has an extraction."""
    submission = tmp_path / "class" / "submissions" / "s1" / "s1.submission.md"
    submission.parent.mkdir(parents=True)
    submission.write_text("", encoding="utf-8")
    app.query_one("#pipe-scan", Input).value = str(tmp_path / "scan.pdf")
    app.query_one("#pipe-out", Input).value = str(tmp_path / "class")


def _run_refine_argv(monkeypatch, tmp_path, setup=lambda _app: None, *, prepared=True):  # noqa: ANN001, ANN202
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_extractor_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", list(argv)),
    )
    notes: list[str] = []

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.notify = lambda message, **kwargs: notes.append(str(message))
            if prepared:
                _prepared_class(app, tmp_path)
            else:
                app.query_one("#pipe-scan", Input).value = str(tmp_path / "scan.pdf")
                app.query_one("#pipe-out", Input).value = str(tmp_path / "class")
            setup(app)
            await pilot.pause()
            app.query_one(SetupScreen)._run_refine()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    captured["notes"] = notes
    return captured


def test_the_re_read_reuses_the_pipeline_cards_folders_and_never_resumes(
    monkeypatch, tmp_path
) -> None:
    argv = _run_refine_argv(monkeypatch, tmp_path)["argv"]
    assert argv[argv.index("--input") + 1] == str(tmp_path / "class" / "student-pdfs")
    assert argv[argv.index("--output") + 1] == str(tmp_path / "class" / "submissions")
    assert "--refine" in argv
    # The extractor refuses --refine with --resume.
    assert "--resume" not in argv
    assert "--refine-dpi" not in argv


def test_the_re_read_passes_its_dpi_and_the_region_map(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    def setup(app) -> None:  # noqa: ANN001
        app.query_one("#refine-dpi", Input).value = "450"
        app.query_one("#pipe-use-region-map", Checkbox).value = True
        app.query_one("#pipe-region-map", Input).value = "/tmp/midterm.regions.json"

    argv = _run_refine_argv(monkeypatch, tmp_path, setup)["argv"]
    assert argv[argv.index("--refine-dpi") + 1] == "450"
    assert argv[argv.index("--region-map") + 1] == "/tmp/midterm.regions.json"


def test_the_re_read_refuses_a_nonsense_dpi(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    captured = _run_refine_argv(
        monkeypatch,
        tmp_path,
        lambda app: setattr(app.query_one("#refine-dpi", Input), "value", "sharp"),
    )
    assert "argv" not in captured
    assert captured["notes"]


def test_the_re_read_refuses_a_class_with_nothing_extracted(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    captured = _run_refine_argv(monkeypatch, tmp_path, prepared=False)
    assert "argv" not in captured
    assert "Prepare an assignment" in captured["notes"][-1]


def test_extraction_in_progress_flag_tracks_the_re_read_worker(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    monkeypatch.setattr(setup_screen, "run_extractor_cli", lambda argv, **kwargs: None)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            _prepared_class(app, tmp_path)

            assert getattr(screen, "extraction_in_progress", False) is False
            screen._run_refine()
            assert screen.extraction_in_progress is True

            await app.workers.wait_for_complete()
            await pilot.pause()
            assert screen.extraction_in_progress is False

    asyncio.run(run())


def _stub_photo_import(monkeypatch, report=None, error=None) -> dict[str, object]:  # noqa: ANN001
    captured: dict[str, object] = {}

    def _fake(input_path, output_dir, **kwargs):  # noqa: ANN001, ANN202
        captured["input"] = input_path
        captured["output"] = output_dir
        captured.update(kwargs)
        if error is not None:
            raise error
        return report

    monkeypatch.setattr(setup_screen, "import_photos", _fake)
    return captured


def _photo_report(output_dir: str, *, submissions: int = 2, unreadable: int = 0):
    from pathlib import Path as _Path

    from opengrader.core.submissions.photo_intake import (
        ImageOutcome,
        PhotoIntakeReport,
        StudentSubmission,
    )

    return PhotoIntakeReport(
        grouping_mode="filename_token",
        outcomes=tuple(
            ImageOutcome(source=_Path(f"/photos/bad-{i}.jpg"), method="unreadable", note="x")
            for i in range(unreadable)
        ),
        submissions=tuple(
            StudentSubmission(
                token=f"s{i}",
                output_pdf=_Path(output_dir) / f"s{i}.pdf",
                pages=(_Path(f"/photos/s{i}-p1.jpg"),),
            )
            for i in range(submissions)
        ),
        output_dir=_Path(output_dir),
        dpi=200.0,
    )


def _run_photo_form(monkeypatch, fill, *, report=None, error=None):  # noqa: ANN001
    captured = _stub_photo_import(monkeypatch, report=report, error=error)
    notes: list[str] = []

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(
                app, "notify", lambda message, **kwargs: notes.append(str(message))
            )
            fill(app)
            await pilot.pause()
            app.query_one(SetupScreen)._run_photo_import()
            await app.workers.wait_for_complete()
            await pilot.pause()
            captured["pipe_scan"] = app.query_one("#pipe-scan", Input).value

    asyncio.run(run())
    captured["notes"] = notes
    return captured


def test_photo_import_passes_the_form_through(monkeypatch) -> None:  # noqa: ANN001
    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"
        app.query_one("#photo-pages", Input).value = "3"
        app.query_one("#photo-a4", Checkbox).value = True
        app.query_one("#photo-no-perspective", Checkbox).value = True

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert captured["input"] == "/tmp/photos"
    assert captured["output"] == "/tmp/split"
    assert captured["pages_per_photo_set"] == 3
    assert captured["paper"] == "a4"
    assert captured["perspective"] is False


def test_photo_import_defaults_are_letter_and_full_correction(monkeypatch) -> None:  # noqa: ANN001

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert captured["pages_per_photo_set"] is None
    assert captured["paper"] == "letter"
    assert captured["perspective"] is True


def test_photo_import_refuses_a_non_numeric_page_count(monkeypatch) -> None:  # noqa: ANN001

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"
        app.query_one("#photo-pages", Input).value = "three"

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert "input" not in captured
    assert any("whole number" in note for note in captured["notes"])


def test_photo_import_requires_both_paths(monkeypatch) -> None:  # noqa: ANN001
    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert "input" not in captured
    assert captured["notes"]


def test_photo_import_hands_what_it_wrote_to_the_pipeline_card(monkeypatch) -> None:  # noqa: ANN001

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert captured["pipe_scan"] == "/tmp/split"


def test_photo_import_leaves_a_scan_path_the_user_typed(monkeypatch) -> None:  # noqa: ANN001

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"
        app.query_one("#pipe-scan", Input).value = "/tmp/mine"

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert captured["pipe_scan"] == "/tmp/mine"


def test_photo_import_reports_skipped_photos_not_only_successes(monkeypatch) -> None:  # noqa: ANN001

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"

    captured = _run_photo_form(
        monkeypatch, fill, report=_photo_report("/tmp/split", unreadable=2)
    )

    summary = " ".join(captured["notes"])
    assert "2 submissions" in summary
    assert "2 photos could not be read" in summary


def test_photo_import_shows_the_tools_own_error_verbatim(monkeypatch) -> None:  # noqa: ANN001
    from opengrader.core.submissions.photo_intake import PhotoIntakeError

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"

    captured = _run_photo_form(
        monkeypatch, fill, error=PhotoIntakeError("no images found in /tmp/photos")
    )

    assert any("no images found in /tmp/photos" == note for note in captured["notes"])
    assert captured["pipe_scan"] == ""


def _blank_assignment_pdf(tmp_path) -> str:  # noqa: ANN001
    import fitz
    from PIL import Image

    raster = tmp_path / "page.png"
    Image.new("RGB", (600, 780), "white").save(raster)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(page.rect, filename=str(raster))
    pdf = tmp_path / "midterm.pdf"
    doc.save(str(pdf))
    doc.close()
    return str(pdf)


def test_region_map_editor_needs_the_assignment_pdf() -> None:
    notes: list[str] = []

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.notify = lambda message, **kwargs: notes.append(str(message))
            app.query_one(SetupScreen)._open_region_map()
            await pilot.pause()
            assert len(app.screen_stack) == 1

    asyncio.run(run())
    assert notes


def test_a_saved_region_map_lands_in_the_pipeline_card(tmp_path) -> None:  # noqa: ANN001
    pdf = _blank_assignment_pdf(tmp_path)
    saved = tmp_path / "midterm.regions.json"

    async def run() -> tuple[str, bool, bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#region-pdf", Input).value = pdf
            await pilot.pause()
            app.query_one(SetupScreen)._open_region_map()
            await pilot.pause()

            app.screen.dismiss(saved)
            await pilot.pause()
            return (
                app.query_one("#pipe-region-map", Input).value,
                app.query_one("#pipe-use-region-map", Checkbox).value,
                app.query_one("#pipe-use-template", Checkbox).value,
                app.query_one("#pipe-advanced", Collapsible).collapsed,
            )

    path_value, region_on, template_on, more_collapsed = asyncio.run(run())
    assert path_value == str(saved)
    assert region_on is True
    assert template_on is False
    # The box it just ticked is inside Advanced settings: show it.
    assert more_collapsed is False


def test_cancelling_the_region_map_editor_changes_nothing(tmp_path) -> None:  # noqa: ANN001
    pdf = _blank_assignment_pdf(tmp_path)

    async def run() -> tuple[str, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#region-pdf", Input).value = pdf
            await pilot.pause()
            app.query_one(SetupScreen)._open_region_map()
            await pilot.pause()
            app.screen.dismiss(None)
            await pilot.pause()
            return (
                app.query_one("#pipe-region-map", Input).value,
                app.query_one("#pipe-use-region-map", Checkbox).value,
            )

    assert asyncio.run(run()) == ("", False)


def test_the_region_map_reaches_the_pipeline_request(tmp_path) -> None:  # noqa: ANN001
    from pathlib import Path

    def setup(app) -> None:  # noqa: ANN001
        app.query_one("#pipe-use-region-map", Checkbox).value = True
        app.query_one("#pipe-region-map", Input).value = "/tmp/midterm.regions.json"

    request = _pipe_request(tmp_path, setup)
    assert request is not None
    assert request.region_map == Path("/tmp/midterm.regions.json")
    assert request.blank_template is None


def test_the_two_layout_sources_untick_each_other() -> None:
    async def run() -> tuple[bool, bool, bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#pipe-use-template", Checkbox).value = True
            await pilot.pause()
            app.query_one("#pipe-use-region-map", Checkbox).value = True
            await pilot.pause()
            after_region = (
                app.query_one("#pipe-use-template", Checkbox).value,
                app.query_one("#pipe-use-region-map", Checkbox).value,
            )
            app.query_one("#pipe-use-template", Checkbox).value = True
            await pilot.pause()
            after_template = (
                app.query_one("#pipe-use-template", Checkbox).value,
                app.query_one("#pipe-use-region-map", Checkbox).value,
            )
            return (*after_region, *after_template)

    assert asyncio.run(run()) == (False, True, True, False)


def test_a_ticked_region_map_with_no_path_is_refused(tmp_path) -> None:  # noqa: ANN001
    notes: list[str] = []
    request = _pipe_request(
        tmp_path,
        lambda app: setattr(app.query_one("#pipe-use-region-map", Checkbox), "value", True),
        notes=notes,
    )
    assert request is None
    assert notes


def test_the_policy_form_writes_randomized_order(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.policy import read_policy

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#policy-submissions", Input).value = str(tmp_path)
            app.query_one("#policy-shuffle", Checkbox).value = True
            await pilot.pause()
            app.query_one(SetupScreen)._run_policy()
            await pilot.pause()

    asyncio.run(run())
    assert read_policy(tmp_path).traversal_shuffle is True


def test_applying_a_policy_keeps_the_other_settings(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.policy import GradingPolicy, read_policy, write_policy

    write_policy(
        tmp_path, GradingPolicy(mode="solo", anonymity_locked=False, traversal_shuffle=True)
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#policy-submissions", Input).value = str(tmp_path)
            app.query_one(SetupScreen)._sync_policy_checkbox()
            await pilot.pause()
            app.query_one("#policy-lock-anon", Checkbox).value = True
            await pilot.pause()
            app.query_one(SetupScreen)._run_policy()
            await pilot.pause()

    asyncio.run(run())
    policy = read_policy(tmp_path)
    assert policy.anonymity_locked is True
    assert policy.traversal_shuffle is True


def test_the_policy_form_shows_what_the_folder_is_already_set_to(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.policy import GradingPolicy, write_policy

    write_policy(
        tmp_path, GradingPolicy(mode="multi", anonymity_locked=True, traversal_shuffle=True)
    )

    async def run() -> tuple[bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#policy-submissions", Input).value = str(tmp_path)
            app.query_one(SetupScreen)._sync_policy_checkbox()
            await pilot.pause()
            return (
                app.query_one("#policy-lock-anon", Checkbox).value,
                app.query_one("#policy-shuffle", Checkbox).value,
            )

    assert asyncio.run(run()) == (True, True)


def test_pipeline_run_requires_scan_and_cohort_folder() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            notices: list[tuple[str, str]] = []
            original_notify = app.notify
            app.notify = lambda message, **kwargs: notices.append(  # type: ignore[method-assign]
                (str(message), kwargs.get("severity", "information"))
            )
            try:
                screen._run_pipeline()
            finally:
                app.notify = original_notify  # type: ignore[method-assign]

            assert notices
            message, severity = notices[-1]
            assert severity == "error"
            assert getattr(screen, "extraction_in_progress", False) is False

    asyncio.run(run())


def test_pipeline_request_maps_every_control(tmp_path) -> None:  # noqa: ANN001
    app_config.save_settings(
        app_config.AISettings(
            vision_model="llava",
            text_model="llama3.1",
            host="http://192.168.1.50:9999",
            read_handwriting=True,
            grade_written=True,
            auto_grade=False,
        )
    )

    scan = tmp_path / "scan.pdf"
    out = tmp_path / "class"
    qa = tmp_path / "qa.md"
    roster = tmp_path / "roster.tsv"
    template = tmp_path / "template.pdf"

    async def run():
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)

            app.query_one("#pipe-scan", Input).value = str(scan)
            app.query_one("#pipe-out", Input).value = str(out)
            app.query_one("#pipe-qa", Input).value = str(qa)

            app.query_one("#pipe-use-roster", Checkbox).value = True
            await pilot.pause()
            app.query_one("#pipe-roster", Input).value = str(roster)

            app.query_one("#pipe-use-template", Checkbox).value = True
            await pilot.pause()
            app.query_one("#pipe-template", Input).value = str(template)

            app.query_one("#pipe-pages", Input).value = "6"
            app.query_one("#pipe-force", Checkbox).value = True
            app.query_one("#pipe-draft-rubrics", Checkbox).value = True

            return screen._build_pipeline_request()

    request = asyncio.run(run())
    assert request is not None
    assert request.scan == scan
    assert request.output_dir == out
    assert request.qa_file == qa
    assert request.roster == roster
    assert request.blank_template == template
    assert request.pages_per_submission == 6
    assert request.force is True
    assert request.draft_rubrics is True
    assert request.llm.enabled is True
    assert request.llm.read_handwriting is True
    assert request.llm.grade_written is True
    assert request.llm.vision_model == ""
    assert request.llm.text_model == ""
    assert request.llm.host == "http://192.168.1.50:9999"
    assert request.auto_grade is False


def test_pipeline_request_defaults_when_every_disclosure_is_off(tmp_path) -> None:  # noqa: ANN001

    async def run():
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-scan", Input).value = str(tmp_path / "scan.pdf")
            app.query_one("#pipe-out", Input).value = str(tmp_path / "cohort")
            return screen._build_pipeline_request()

    request = asyncio.run(run())
    assert request is not None
    assert request.qa_file is None
    assert request.roster is None
    assert request.blank_template is None
    assert request.pages_per_submission is None
    assert request.force is False
    assert request.draft_rubrics is False
    resolved = app_config.resolve()
    assert resolved.read_handwriting is False
    assert resolved.grade_written is False
    assert resolved.auto_grade is True
    assert request.llm.read_handwriting == resolved.read_handwriting
    assert request.llm.grade_written == resolved.grade_written
    assert request.llm.enabled is False
    assert request.llm.host == resolved.host
    assert request.auto_grade == resolved.auto_grade


def test_pipeline_request_llm_options_follow_the_two_ai_jobs_independently(
    tmp_path,
) -> None:
    app_config.save_settings(
        app_config.AISettings(read_handwriting=True, grade_written=False)
    )

    async def run():
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-scan", Input).value = str(tmp_path / "scan.pdf")
            app.query_one("#pipe-out", Input).value = str(tmp_path / "cohort")
            return screen._build_pipeline_request()

    request = asyncio.run(run())
    assert request is not None
    assert request.llm.read_handwriting is True
    assert request.llm.grade_written is False
    assert request.llm.enabled is True


_PIPE_QA_TEXT = """---
title: Quiz
---

- (10 points) What is 2 + 2?
  - **Correct**
    - 4
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""


def _pipe_class_folder(tmp_path):  # noqa: ANN001, ANN202
    folder = tmp_path / "class"
    folder.mkdir()
    (folder / "scans.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "qa.md").write_text(_PIPE_QA_TEXT, encoding="utf-8")
    (folder / "roster.tsv").write_text("1\tAda\n", encoding="utf-8")
    (folder / "blank-template.pdf").write_bytes(b"%PDF-1.4\n")
    return folder


def test_scanning_the_class_folder_fills_the_pipeline_fields(tmp_path) -> None:  # noqa: ANN001
    folder = _pipe_class_folder(tmp_path)

    async def run():
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-out", Input).value = str(folder)
            screen._scan_class_folder()
            await pilot.pause()
            return {
                "scan": app.query_one("#pipe-scan", Input).value,
                "qa": app.query_one("#pipe-qa", Input).value,
                "roster": app.query_one("#pipe-roster", Input).value,
                "use_roster": app.query_one("#pipe-use-roster", Checkbox).value,
                "template": app.query_one("#pipe-template", Input).value,
                "use_template": app.query_one("#pipe-use-template", Checkbox).value,
                "roster_row_hidden": app.query_one("#pipe-roster-row").has_class("hidden"),
                "summary_hidden": app.query_one("#pipe-folder-summary").has_class("hidden"),
            }

    seen = asyncio.run(run())
    assert seen["scan"] == str(folder / "scans.pdf")
    assert seen["qa"] == str(folder / "qa.md")
    assert seen["roster"] == str(folder / "roster.tsv")
    assert seen["use_roster"] is True
    assert seen["roster_row_hidden"] is False
    assert seen["template"] == str(folder / "blank-template.pdf")
    assert seen["use_template"] is True
    assert seen["summary_hidden"] is False


def test_a_folder_with_no_answer_key_says_where_to_make_one(tmp_path) -> None:  # noqa: ANN001
    folder = _pipe_class_folder(tmp_path)

    async def run() -> tuple[bool, bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            hint = app.query_one("#pipe-qa-hint")
            before = hint.has_class("hidden")

            app.query_one("#pipe-out", Input).value = str(folder)
            screen._scan_class_folder()
            await pilot.pause()
            with_key = hint.has_class("hidden")

            (folder / "qa.md").unlink()
            screen._scan_class_folder()
            await pilot.pause()
            return before, with_key, hint.has_class("hidden")

    assert asyncio.run(run()) == (True, True, False)


def test_a_rescan_keeps_what_the_user_typed_but_replaces_what_it_filled(
    tmp_path,
) -> None:  # noqa: ANN001
    first = _pipe_class_folder(tmp_path)
    second = tmp_path / "other"
    second.mkdir()
    (second / "scan.pdf").write_bytes(b"%PDF-1.4\n")
    typed_key = tmp_path / "my-key.md"

    async def run():
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-out", Input).value = str(first)
            screen._scan_class_folder()
            app.query_one("#pipe-qa", Input).value = str(typed_key)
            app.query_one("#pipe-out", Input).value = str(second)
            screen._scan_class_folder()
            await pilot.pause()
            return (
                app.query_one("#pipe-scan", Input).value,
                app.query_one("#pipe-qa", Input).value,
                app.query_one("#pipe-roster", Input).value,
                app.query_one("#pipe-use-roster", Checkbox).value,
            )

    scan, qa, roster, use_roster = asyncio.run(run())
    assert scan == str(second / "scan.pdf")
    assert qa == str(typed_key)
    assert roster == ""
    assert use_roster is False


def test_run_with_only_a_class_folder_scans_it_first(tmp_path) -> None:  # noqa: ANN001
    folder = _pipe_class_folder(tmp_path)

    async def run():
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-out", Input).value = str(folder)
            return screen._build_pipeline_request()

    request = asyncio.run(run())
    assert request is not None
    assert request.scan == folder / "scans.pdf"
    assert request.qa_file == folder / "qa.md"
    assert request.roster == folder / "roster.tsv"
    assert request.blank_template == folder / "blank-template.pdf"
    assert request.output_dir == folder


def _capture_launches(screen, posted) -> None:  # noqa: ANN001
    # Only the launch is intercepted; Textual's own messages must still flow.
    original = screen.post_message

    def spy(message):  # noqa: ANN001, ANN202
        if isinstance(message, SetupScreen.LaunchRequested):
            posted.append(message)
            return True
        return original(message)

    screen.post_message = spy  # type: ignore[method-assign]


def _grading_folder(tmp_path, *, with_submissions=True):  # noqa: ANN001, ANN202
    folder = tmp_path / "graded"
    folder.mkdir()
    (folder / "qa.md").write_text(_PIPE_QA_TEXT, encoding="utf-8")
    if with_submissions:
        student = folder / "submissions" / "submission-0001"
        student.mkdir(parents=True)
        (student / "submission-0001.submission.md").write_text("---\n---\n")
    return folder


def test_pipeline_blocked_report_does_not_open_the_grading_workspace(monkeypatch) -> None:
    from opengrader.core.pipeline import PipelineReport, StageOutcome, StageStatus

    def fake_run_pipeline(request, *, progress=None):
        if progress is not None:
            progress.stage_start("scan-check")
        outcome = StageOutcome(
            stage="scan-check",
            status=StageStatus.BLOCKED,
            message="2 high-severity finding(s) in 6 page(s)",
            detail="page 3: [HIGH] upside down",
        )
        if progress is not None:
            progress.stage_finish(outcome)
        return PipelineReport(
            stages=(outcome,),
            submissions_dir=request.submissions_dir,
            qa_file=request.qa_file,
        )

    monkeypatch.setattr(setup_screen, "run_pipeline", fake_run_pipeline)

    events: list[tuple[str, str]] = []
    original = SetupScreen.LaunchRequested.__init__

    def _spy(self, qa_path: str, submissions_path: str) -> None:  # noqa: ANN001
        events.append((qa_path, submissions_path))
        original(self, qa_path, submissions_path)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-scan", Input).value = "/tmp/scan.pdf"
            app.query_one("#pipe-out", Input).value = "/tmp/cohort"
            app.query_one("#pipe-qa", Input).value = "/tmp/qa.md"
            screen._run_pipeline()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert screen.extraction_in_progress is False

    SetupScreen.LaunchRequested.__init__ = _spy  # type: ignore[method-assign]
    try:
        asyncio.run(run())
    finally:
        SetupScreen.LaunchRequested.__init__ = original  # type: ignore[method-assign]

    assert events == []


def test_pipeline_ok_report_with_submissions_opens_the_grading_workspace(
    monkeypatch, tmp_path
) -> None:
    from opengrader.core.pipeline import PipelineReport, StageOutcome, StageStatus

    out_dir = tmp_path / "cohort"
    subs_dir = out_dir / "submissions"
    subs_dir.mkdir(parents=True)
    (subs_dir / "student1.submission.md").write_text("# stub")
    qa = tmp_path / "qa.md"

    def fake_run_pipeline(request, *, progress=None):
        return PipelineReport(
            stages=(StageOutcome(stage="extract", status=StageStatus.RAN, message="ok"),),
            submissions_dir=request.submissions_dir,
            qa_file=request.qa_file,
        )

    monkeypatch.setattr(setup_screen, "run_pipeline", fake_run_pipeline)

    events: list[tuple[str, str]] = []
    original = SetupScreen.LaunchRequested.__init__

    def _spy(self, qa_path: str, submissions_path: str) -> None:  # noqa: ANN001
        events.append((qa_path, submissions_path))
        original(self, qa_path, submissions_path)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-scan", Input).value = str(tmp_path / "scan.pdf")
            app.query_one("#pipe-out", Input).value = str(out_dir)
            app.query_one("#pipe-qa", Input).value = str(qa)
            app.query_one("#pipe-grade-asap", Checkbox).value = False
            screen._run_pipeline()
            await app.workers.wait_for_complete()
            await pilot.pause()

    SetupScreen.LaunchRequested.__init__ = _spy  # type: ignore[method-assign]
    try:
        asyncio.run(run())
    finally:
        SetupScreen.LaunchRequested.__init__ = original  # type: ignore[method-assign]

    assert events == [(str(qa), str(subs_dir))]


def test_pipeline_asap_without_an_answer_key_notifies_instead_of_ignoring_it(
    monkeypatch, tmp_path
) -> None:
    from opengrader.core.pipeline import PipelineReport, StageOutcome, StageStatus

    def fake_run_pipeline(request, *, progress=None):
        return PipelineReport(
            stages=(StageOutcome(stage="extract", status=StageStatus.RAN, message="ok"),),
            submissions_dir=request.submissions_dir,
            qa_file=request.qa_file,
        )

    monkeypatch.setattr(setup_screen, "run_pipeline", fake_run_pipeline)

    async def run() -> list[tuple[str, str]]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            notices: list[tuple[str, str]] = []
            app.notify = lambda message, **kwargs: notices.append(  # type: ignore[method-assign]
                (str(message), kwargs.get("severity", "information"))
            )
            app.query_one("#pipe-scan", Input).value = str(tmp_path / "scan.pdf")
            app.query_one("#pipe-out", Input).value = str(tmp_path / "cohort")
            screen._run_pipeline()
            await app.workers.wait_for_complete()
            await pilot.pause()
            return notices

    notices = asyncio.run(run())
    assert any("answer key" in message.lower() for message, _severity in notices)


def _stub_pipeline_run(monkeypatch) -> None:  # noqa: ANN001
    from opengrader.core.pipeline import PipelineReport, StageOutcome, StageStatus

    def fake_run_pipeline(request, *, progress=None):  # noqa: ANN001, ANN202
        return PipelineReport(
            stages=(StageOutcome(stage="extract", status=StageStatus.RAN, message="ok"),),
            submissions_dir=request.submissions_dir,
            qa_file=request.qa_file,
        )

    monkeypatch.setattr(setup_screen, "run_pipeline", fake_run_pipeline)


def test_the_card_has_no_scan_button_and_tucks_the_file_fields_away() -> None:
    async def run() -> tuple[int, bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            advanced = app.query_one("#pipe-advanced", Collapsible)
            return (
                len(app.query("#pipe-scan-folder")),
                advanced.collapsed,
                # The scan and answer-key fields live inside Advanced settings.
                app.query_one("#pipe-scan") in advanced.query(Input)
                and app.query_one("#pipe-qa") in advanced.query(Input),
            )

    assert asyncio.run(run()) == (0, True, True)


def test_advanced_settings_open_only_when_the_folder_cannot_answer_for_itself(
    tmp_path,
) -> None:
    clear = _pipe_class_folder(tmp_path)
    unclear = tmp_path / "unclear"
    unclear.mkdir()
    (unclear / "qa.md").write_text(_PIPE_QA_TEXT, encoding="utf-8")

    async def run() -> tuple[bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            advanced = app.query_one("#pipe-advanced", Collapsible)

            app.query_one("#pipe-out", Input).value = str(clear)
            screen._load_class_folder()
            await pilot.pause()
            after_clear = advanced.collapsed

            # No scan in this one: the field to choose it has to be shown.
            app.query_one("#pipe-out", Input).value = str(unclear)
            screen._load_class_folder()
            await pilot.pause()
            return after_clear, advanced.collapsed

    assert asyncio.run(run()) == (True, False)


def test_running_remembers_the_settings_but_never_the_one_shot_overrides(
    monkeypatch, tmp_path
) -> None:
    from opengrader.core.pipeline_settings import (
        folder_settings_path,
        load_folder_settings,
        load_machine_settings,
    )

    _stub_pipeline_run(monkeypatch)
    folder = _pipe_class_folder(tmp_path)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-out", Input).value = str(folder)
            screen._load_class_folder()
            await pilot.pause()

            app.query_one("#pipe-pages", Input).value = "6"
            app.query_one("#pipe-use-roster", Checkbox).value = False
            app.query_one("#pipe-reorder", Checkbox).value = True
            app.query_one("#pipe-grade-asap", Checkbox).value = False
            app.query_one("#pipe-parallel", Checkbox).value = True
            app.query_one("#pipe-jobs", Input).value = "3"
            app.query_one("#pipe-force", Checkbox).value = True
            app.query_one("#pipe-fresh", Checkbox).value = True
            await pilot.pause()

            screen._run_pipeline()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())

    saved = load_folder_settings(folder)
    assert saved is not None
    assert saved.pages_per_submission == 6
    assert saved.use_roster is False
    assert saved.use_template is True
    assert saved.reorder_pages is True
    # What the folder scan found is found again next time; it is not an override.
    assert saved.scan == "" and saved.qa_file == ""
    stored = folder_settings_path(folder).read_text(encoding="utf-8")
    assert "force" not in stored and "fresh" not in stored

    machine = load_machine_settings()
    assert machine.grade_as_ready is False
    assert machine.jobs == 3
    assert machine.last_class_folder == str(folder)


def test_preview_saves_nothing(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.pipeline_settings import folder_settings_path

    monkeypatch.setattr(setup_screen, "plan_pipeline", lambda request: ())
    folder = _pipe_class_folder(tmp_path)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#pipe-out", Input).value = str(folder)
            app.query_one(SetupScreen)._run_pipeline_preview()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    assert not folder_settings_path(folder).exists()


def test_choosing_a_folder_brings_back_what_was_saved_for_it(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.pipeline_settings import (
        FolderPipelineSettings,
        save_folder_settings,
    )

    folder = _pipe_class_folder(tmp_path)
    elsewhere = tmp_path / "keys" / "final-key.md"
    save_folder_settings(
        folder,
        FolderPipelineSettings(
            qa_file=str(elsewhere),
            pages_per_submission=6,
            # The folder has a roster, but last time the box was unticked.
            use_roster=False,
            use_template=True,
            blank_template=str(folder / "blank-template.pdf"),
            use_section_rosters=True,
            section_rosters=("/rosters/mw.tsv=MW",),
            reorder_pages=True,
            draft_rubrics=True,
        ),
    )

    async def run():  # noqa: ANN202
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-out", Input).value = str(folder)
            screen._load_class_folder()
            await pilot.pause()
            return screen._build_pipeline_request()

    request = asyncio.run(run())
    assert request is not None
    assert request.scan == folder / "scans.pdf"
    assert request.qa_file == elsewhere
    assert request.pages_per_submission == 6
    assert request.roster is None
    assert request.blank_template == folder / "blank-template.pdf"
    assert request.section_rosters == ("/rosters/mw.tsv=MW",)
    assert request.reorder_pages is True
    assert request.draft_rubrics is True
    # Never remembered, so never restored.
    assert request.force is False and request.fresh is False


def test_saved_settings_are_applied_once_and_never_over_a_later_change(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.pipeline_settings import (
        FolderPipelineSettings,
        save_folder_settings,
    )

    folder = _pipe_class_folder(tmp_path)
    save_folder_settings(folder, FolderPipelineSettings(pages_per_submission=6))

    async def run():  # noqa: ANN202
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-out", Input).value = str(folder)
            screen._load_class_folder()
            await pilot.pause()
            app.query_one("#pipe-pages", Input).value = "8"
            # Building the request loads the folder again; the 8 must survive.
            return screen._build_pipeline_request()

    request = asyncio.run(run())
    assert request is not None and request.pages_per_submission == 8


def test_the_app_opens_on_the_class_folder_and_preferences_used_last(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.pipeline_settings import (
        FolderPipelineSettings,
        MachinePipelineSettings,
        save_folder_settings,
        save_machine_settings,
    )

    folder = _pipe_class_folder(tmp_path)
    save_folder_settings(folder, FolderPipelineSettings(pages_per_submission=6))
    save_machine_settings(
        MachinePipelineSettings(
            grade_as_ready=False, jobs=3, last_class_folder=str(folder)
        )
    )

    async def run() -> tuple[str, str, str, bool, bool, str]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return (
                app.query_one("#pipe-out", Input).value,
                app.query_one("#pipe-scan", Input).value,
                app.query_one("#pipe-pages", Input).value,
                app.query_one("#pipe-grade-asap", Checkbox).value,
                app.query_one("#pipe-parallel", Checkbox).value,
                app.query_one("#pipe-jobs", Input).value,
            )

    assert asyncio.run(run()) == (
        str(folder),
        str(folder / "scans.pdf"),
        "6",
        False,
        True,
        "3",
    )


def test_a_last_class_folder_that_is_gone_is_quietly_forgotten(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.pipeline_settings import (
        MachinePipelineSettings,
        save_machine_settings,
    )

    save_machine_settings(
        MachinePipelineSettings(last_class_folder=str(tmp_path / "deleted"))
    )
    notes: list[str] = []

    async def run() -> str:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        app.notify = lambda message, **kwargs: notes.append(str(message))  # type: ignore[method-assign]
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return app.query_one("#pipe-out", Input).value

    assert asyncio.run(run()) == ""
    # Other start-up notices are fine; none may be about the missing folder.
    assert not any("class folder" in note for note in notes)


def test_pipeline_preview_calls_plan_pipeline_and_writes_nothing(
    monkeypatch, tmp_path
) -> None:
    from opengrader.core.pipeline import StagePlan

    captured: dict[str, object] = {}
    run_pipeline_called = False

    def fake_plan_pipeline(request):
        captured["request"] = request
        return (
            StagePlan(
                stage="scan-check",
                will_run=True,
                reason="no record of this stage having run here before",
            ),
            StagePlan(
                stage="split",
                will_run=True,
                reason="no record of this stage having run here before",
                argv=("submission-splitter", "--input", str(request.scan)),
            ),
        )

    def fake_run_pipeline(request, *, progress=None):
        nonlocal run_pipeline_called
        run_pipeline_called = True
        raise AssertionError("Preview must not run the pipeline")

    monkeypatch.setattr(setup_screen, "plan_pipeline", fake_plan_pipeline)
    monkeypatch.setattr(setup_screen, "run_pipeline", fake_run_pipeline)

    out_dir = tmp_path / "cohort"

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-scan", Input).value = str(tmp_path / "scan.pdf")
            app.query_one("#pipe-out", Input).value = str(out_dir)
            screen._run_pipeline_preview()
            await app.workers.wait_for_complete()
            await pilot.pause()
            output = app.query_one("#pipe-preview-output")
            assert not output.has_class("hidden")
            # Plain words first; the tools' plan is folded away below.
            summary = str(app.query_one("#pipe-preview-summary").render())
            assert "Split it into one PDF per student" in summary
            details = str(app.query_one("#pipe-preview-details").render())
            assert "split" in details and "submission-splitter" in details

    asyncio.run(run())

    assert captured["request"].scan == tmp_path / "scan.pdf"
    assert run_pipeline_called is False
    assert not out_dir.exists()


def test_ai_setup_button_pushes_the_ai_settings_screen() -> None:
    async def run() -> bool:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            button = app.query_one("#run-ai-settings", Button)
            screen.on_button_pressed(Button.Pressed(button))
            await pilot.pause()
            return isinstance(app.screen, setup_screen.AISettingsScreen)

    assert asyncio.run(run()) is True


def test_refresh_ai_displays_updates_every_status_line_and_the_summary() -> None:
    async def run() -> tuple[str, ...]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app_config.save_settings(
                app_config.AISettings(
                    read_handwriting=True, grade_written=True, text_model="llama3.1"
                )
            )
            screen._refresh_ai_displays()
            await pilot.pause()
            ids = (
                "ai-current-summary",
                "refine-ai-status",
                "ak-ai-status",
                "pipe-ai-status",
            )
            return tuple(
                str(app.query_one(f"#{node_id}", Static).render()) for node_id in ids
            )

    summary, refine_status, ak_status, pipe_status = asyncio.run(run())
    assert "llama3.1" in summary
    assert "AI: off" not in refine_status
    assert "AI: off" not in ak_status
    assert "reads hard-to-read handwriting" in pipe_status


def test_the_answer_key_tool_never_emits_a_model_host_or_feature_flag(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_answer_key_cli",
        lambda argv, **kwargs: captured.__setitem__("answer_key", list(argv)),
    )

    app_config.save_settings(
        app_config.AISettings(
            text_model="llama3.1",
            vision_model="llava",
            min_confidence=0.85,
            read_handwriting=True,
            escalate_only=True,
            transcribe_math=True,
            grade_written=True,
        )
    )

    ak_input = tmp_path / "assignment.md"
    ak_input.write_text("# stub")

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#ak-input", Input).value = str(ak_input)
            app.query_one("#ak-output", Input).value = str(tmp_path / "qa.md")
            await pilot.pause()
            screen._run_answer_key()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())

    banned = (
        "--ollama-model",
        "--ollama-host",
        "--llm-model",
        "--llm-min-confidence",
        "--math-backend",
        "--llm",
        "--llm-responses",
        "--llm-escalate",
        "--llm-math",
    )
    for flag in banned:
        assert flag not in captured["answer_key"]


def test_a_run_paused_for_rubric_review_opens_grading_to_check_the_drafts(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.pipeline import (
        STAGE_DRAFT_RUBRICS,
        PipelineReport,
        StageOutcome,
        StageStatus,
    )

    submissions = tmp_path / "cohort" / "submissions" / "submission-0001"
    submissions.mkdir(parents=True)
    (submissions / "submission-0001.submission.md").write_text("# stub\n", encoding="utf-8")
    report = PipelineReport(
        stages=(
            StageOutcome(
                stage=STAGE_DRAFT_RUBRICS,
                status=StageStatus.PAUSED,
                message="drafted rubric items for q2 into qa.md",
            ),
        ),
        submissions_dir=tmp_path / "cohort" / "submissions",
        qa_file=tmp_path / "qa.md",
    )

    async def run():
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            posted: list[object] = []
            original_post = screen.post_message

            def record(message):  # noqa: ANN001, ANN202
                if isinstance(message, SetupScreen.LaunchRequested):
                    posted.append(message)
                    return True
                return original_post(message)

            screen.post_message = record  # type: ignore[method-assign]
            screen._pipe_finish(report, False, str(tmp_path / "qa.md"), None, False)
            await pilot.pause()
            status = str(app.query_one("#pipe-progress-status", Static).render())
            return posted, status

    posted, status = asyncio.run(run())
    assert status.startswith("Paused: drafted rubric items for q2")
    launches = [m for m in posted if isinstance(m, SetupScreen.LaunchRequested)]
    assert len(launches) == 1


def _create_answer_key(monkeypatch, tmp_path, fill=lambda _app: None, *, report=(0, 0)):  # noqa: ANN001, ANN202
    """Press Create answer key with a stand-in for the tool; what it was
    handed and what the user was told."""
    seen: dict[str, object] = {"notices": []}

    def fake(argv, *, default_points_callback=None, **_hooks):  # noqa: ANN001, ANN202
        seen["argv"] = list(argv)
        if default_points_callback is not None:
            default_points_callback(*report)

    monkeypatch.setattr(setup_screen, "run_answer_key_cli", fake)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.notify = lambda message, **_kwargs: seen["notices"].append(str(message))  # type: ignore[method-assign]
            app.query_one("#ak-input", Input).value = str(tmp_path / "questions.txt")
            app.query_one("#ak-output", Input).value = str(tmp_path / "qa.md")
            fill(app)
            app.query_one(SetupScreen)._run_answer_key()
            await app.workers.wait_for_complete()
            await pilot.pause()
            seen["field"] = app.query_one("#ak-default-points", Input).value

    asyncio.run(run())
    return seen


def test_create_answer_key_uses_ten_points_unless_told_otherwise(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    seen = _create_answer_key(monkeypatch, tmp_path)

    argv = seen["argv"]
    assert argv[argv.index("--default-points") + 1] == "10"


def test_create_answer_key_passes_and_remembers_the_points_typed(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.pipeline_settings import load_machine_settings

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#ak-default-points", Input).value = "5"

    seen = _create_answer_key(monkeypatch, tmp_path, fill)

    argv = seen["argv"]
    assert argv[argv.index("--default-points") + 1] == "5"
    assert load_machine_settings().answer_key_points == 5.0

    # The next session starts with it filled in.
    again = _create_answer_key(monkeypatch, tmp_path)
    assert again["field"] == "5"
    assert again["argv"][again["argv"].index("--default-points") + 1] == "5"


def test_create_answer_key_refuses_points_that_are_not_a_number(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#ak-default-points", Input).value = "ten"

    seen = _create_answer_key(monkeypatch, tmp_path, fill)

    assert "argv" not in seen
    assert any("whole number, 1 or more" in notice for notice in seen["notices"])


def test_create_answer_key_says_when_the_standard_points_were_used(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    seen = _create_answer_key(monkeypatch, tmp_path, report=(6, 8))

    (notice,) = [n for n in seen["notices"] if n.startswith("Answer key created.")]
    assert "No points were found for 6 of 8 questions" in notice
    assert "standard 10 (5 for partial credit, 0 for none)" in notice


def test_create_answer_key_says_nothing_extra_when_points_were_all_stated(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    seen = _create_answer_key(monkeypatch, tmp_path, report=(0, 8))

    assert "Answer key created." in seen["notices"]


def _template_pdf(path) -> None:  # noqa: ANN001
    import fitz

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 100), "1. What is water? (5 points)", fontsize=12)
    page.insert_text((72, 200), "2. Name a prime number. (5 points)", fontsize=12)
    document.save(str(path))


def _assistant_buttons(app) -> set[str]:  # noqa: ANN001
    ids = (
        "run-assistant-draft-key",
        "run-assistant-type-answers",
        "run-assistant-list-answers",
        "run-assistant-key",
        "run-assistant-rubrics",
        "run-assistant",
    )
    return {button_id for button_id in ids if app.query_one(f"#{button_id}", Button).display}


def test_the_ai_assistant_card_offers_only_what_the_class_folder_is_ready_for(tmp_path) -> None:  # noqa: ANN001
    import shutil

    from opengrader.tui.ui.screens.assistant_screen import AssistantScreen

    blank = tmp_path / "new-class"
    blank.mkdir()
    _template_pdf(blank / "template.pdf")
    answers = tmp_path / "answers.txt"
    answers.write_text("1: H2O; Water\n2: 2, 3, 5\n")
    graded = tmp_path / "graded-class"
    shutil.copytree(PROJECT_SAMPLES / "cmpe30-quiz2", graded, ignore=shutil.ignore_patterns("._*", ".opengrader"))

    async def run():  # noqa: ANN202
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            nothing = _assistant_buttons(app)
            app.query_one("#assistant-folder", Input).value = str(blank)
            await pilot.pause()
            before_key = _assistant_buttons(app)
            screen._assistant_draft_key()
            await pilot.pause()
            drafted = _assistant_buttons(app)
            screen._apply_answer_list(blank / "qa.md", answers)
            await pilot.pause()
            filled = _assistant_buttons(app)
            app.query_one("#assistant-folder", Input).value = str(graded)
            await pilot.pause()
            with_submissions = _assistant_buttons(app)
            state_text = str(app.query_one("#assistant-state", Static).render())
            screen._open_assistant(purpose="rubrics")
            await pilot.pause()
            opened = app.screen
            return nothing, before_key, drafted, filled, with_submissions, state_text, (type(opened), opened._purpose)

    nothing, before_key, drafted, filled, with_submissions, state_text, opened = asyncio.run(run())

    assert nothing == set()
    assert before_key == {"run-assistant-draft-key", "run-assistant-key"}
    assert drafted == {"run-assistant-type-answers", "run-assistant-list-answers", "run-assistant-key"}
    assert filled == set()  # every answer is in; no submissions yet
    assert "H2O" in (blank / "qa.md").read_text()
    assert with_submissions == {"run-assistant-rubrics", "run-assistant"}
    assert "1 submission(s)" in state_text
    assert opened == (AssistantScreen, "rubrics")


def test_how_to_split_the_scan_is_on_the_card_not_under_settings() -> None:
    async def run():  # noqa: ANN202
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return [
                [ancestor.id for ancestor in app.query_one(selector).ancestors]
                for selector in ("#pipe-split-method", "#pipe-pages")
            ]

    for ancestors in asyncio.run(run()):
        assert "section-pipeline" in ancestors
        assert "pipe-settings" not in ancestors


def test_every_n_pages_comes_back_with_the_folder_and_cuts_the_scan(tmp_path) -> None:  # noqa: ANN001
    from textual.widgets import RadioButton

    from opengrader.core.pipeline_settings import FolderPipelineSettings, save_folder_settings

    folder = _pipe_class_folder(tmp_path)
    save_folder_settings(folder, FolderPipelineSettings(pages_per_submission=6, fixed_split=True))

    async def run():  # noqa: ANN202
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-out", Input).value = str(folder)
            screen._load_class_folder()
            await pilot.pause()
            return (
                app.query_one("#pipe-split-fixed", RadioButton).value,
                app.query_one("#pipe-split-auto", RadioButton).value,
                str(app.query_one("#pipe-split-hint", Static).render()),
                screen._build_pipeline_request(),
            )

    fixed, auto, hint, request = asyncio.run(run())
    assert fixed and not auto
    assert "cut every N pages" in hint
    assert request is not None and request.fixed_split and request.pages_per_submission == 6


def test_every_n_pages_without_a_page_count_is_refused(tmp_path) -> None:  # noqa: ANN001
    from textual.widgets import RadioButton

    folder = _pipe_class_folder(tmp_path)

    async def run():  # noqa: ANN202
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        notices: list[str] = []
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-out", Input).value = str(folder)
            screen._load_class_folder()
            await pilot.pause()
            app.query_one("#pipe-pages", Input).value = ""
            app.query_one("#pipe-split-fixed", RadioButton).value = True
            await pilot.pause()
            app.notify = lambda message, **_kwargs: notices.append(str(message))  # type: ignore[method-assign]
            return screen._build_pipeline_request(), notices

    request, notices = asyncio.run(run())
    assert request is None
    assert any("cut the scan every N pages" in notice for notice in notices)


def test_a_blank_page_count_stands_for_the_templates_length(tmp_path) -> None:  # noqa: ANN001
    import fitz
    from textual.widgets import RadioButton

    folder = _pipe_class_folder(tmp_path)
    doc = fitz.open()
    for _ in range(3):
        doc.new_page()
    doc.save(str(folder / "blank-template.pdf"))
    doc.close()

    async def run():  # noqa: ANN202
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-out", Input).value = str(folder)
            screen._load_class_folder()
            await pilot.pause()
            placeholder = app.query_one("#pipe-pages", Input).placeholder
            app.query_one("#pipe-pages", Input).value = ""
            app.query_one("#pipe-split-fixed", RadioButton).value = True
            await pilot.pause()
            return placeholder, screen._build_pipeline_request()

    placeholder, request = asyncio.run(run())
    assert placeholder == "3 — the template's length"
    assert request is not None and request.fixed_split and request.pages_per_submission is None
