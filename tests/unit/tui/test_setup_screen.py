import asyncio

from textual.containers import VerticalScroll
from textual.widgets import Button, Checkbox, Collapsible, Input, Label, RadioButton, Static

import opengrader.tui.ui.screens.setup_screen as setup_screen
from opengrader.core import app_config
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens.setup_screen import SetupScreen

from .fakes import DEFAULT_IMAGE_PATH
from .test_screen_conventions import CHECKBOX_LABEL_LIMIT


def test_the_session_card_is_visible_without_scrolling_and_points_home() -> None:
    """Choosing what to grade moved to Home; this card says how to get there."""

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()

            card = app.query_one("#setup-session")
            assert card.region.height > 0
            assert card.region.y >= 0
            assert card.region.y + card.region.height <= app.size.height
            assert "F2 for Home" in str(card.render())
            assert not app.query("#launch-button")

    asyncio.run(run())


def test_setup_scroll_reaches_last_section() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()

            content = app.query_one("#setup-content")
            children_height = sum(
                child.outer_size.height for child in content.children
            )
            assert content.outer_size.height >= children_height

            section = app.query_one("#section-grading")
            section.scroll_visible(animate=False)
            await pilot.pause()

            assert section.region.height > 0
            assert section.region.y >= 0
            assert section.region.y + section.region.height <= app.size.height

    asyncio.run(run())


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


def test_setup_grading_never_passes_llm_and_probes_the_saved_model(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_grading_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", argv),
    )

    def _probe(resolved=None, *, purpose="text"):  # noqa: ANN001, ANN202
        captured["model"] = (resolved or app_config.resolve()).text_model

    monkeypatch.setattr(app_config, "ensure_ready", _probe)
    app_config.save_settings(
        app_config.AISettings(text_model="llama3.1", grade_written=True)
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)

            app.query_one("#grade-solutions", Input).value = "/tmp/sol.md"
            app.query_one("#grade-input", Input).value = "/tmp/subs"

            screen._run_grading()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())

    argv = captured.get("argv")
    assert argv == ["--solutions", "/tmp/sol.md", "--input", "/tmp/subs"]
    assert "--llm-model" not in argv
    assert captured["model"] == "llama3.1"


def test_template_qr_checkbox_reveals_roster_and_builds_argv(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_qa_file_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", list(argv)),
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


def test_setup_grading_defaults_model_when_no_ai_settings_are_saved(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_grading_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", argv),
    )

    def _probe(resolved=None, *, purpose="text"):  # noqa: ANN001, ANN202
        captured["model"] = (resolved or app_config.resolve()).text_model

    monkeypatch.setattr(app_config, "ensure_ready", _probe)
    app_config.save_settings(app_config.AISettings(grade_written=True))

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#grade-solutions", Input).value = "/tmp/sol.md"
            app.query_one("#grade-input", Input).value = "/tmp/subs"
            screen._run_grading()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())

    assert captured["model"] == app_config.DEFAULT_TEXT_MODEL
    assert captured["argv"] == ["--solutions", "/tmp/sol.md", "--input", "/tmp/subs"]
    assert "--llm-model" not in captured["argv"]


def test_setup_splitter_never_passes_a_model_and_auto_starts_per_saved_setting(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_splitter_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", list(argv)),
    )
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    out = tmp_path / "out"

    async def run(*, auto_start: bool) -> None:
        app_config.save_settings(
            app_config.AISettings(
                text_model="minicpm-v", auto_start=auto_start, read_handwriting=True
            )
        )
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#split-input", Input).value = str(pdf)
            app.query_one("#split-output", Input).value = str(out)
            screen._run_splitter()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run(auto_start=True))
    argv = captured["argv"]
    assert "--auto-ollama" in argv
    assert "--ollama-model" not in argv
    assert "--ollama-host" not in argv

    asyncio.run(run(auto_start=False))
    assert "--auto-ollama" not in captured["argv"]


def test_no_checkbox_label_is_long_enough_to_be_cut_off() -> None:
    limit = CHECKBOX_LABEL_LIMIT

    async def run() -> list[tuple[str, int, str]]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            return [
                (box.id or "<no id>", len(str(box.label)), str(box.label))
                for box in app.query(Checkbox)
                if len(str(box.label)) > limit
            ]

    too_long = asyncio.run(run())
    assert not too_long, (
        "these checkbox labels would be cut off; shorten the label and move the "
        "detail into a card-hint Static below it: "
        + "; ".join(f"{name} ({length})" for name, length, _ in too_long)
    )


def test_setup_splitter_checks_the_scan_by_default(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_splitter_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", list(argv)),
    )
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    async def run(*, checked: bool) -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#split-input", Input).value = str(pdf)
            app.query_one("#split-output", Input).value = str(tmp_path / "out")
            box = app.query_one("#split-scan-check", Checkbox)
            assert box.value is True, "the scan check should default to on"
            box.value = checked
            await pilot.pause()
            screen._run_splitter()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run(checked=True))
    assert "--fix-rotation" in captured["argv"]
    assert "--fix-order" in captured["argv"]
    assert "--reorder" not in captured["argv"]

    asyncio.run(run(checked=False))
    assert "--fix-rotation" not in captured["argv"]
    assert "--fix-order" not in captured["argv"]


def test_setup_grading_progress_shows_file_and_question() -> None:
    from textual.widgets import ProgressBar, Static

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)

            assert screen.query_one("#autograde-progress").has_class("hidden")

            screen.query_one("#autograde-progress").remove_class("hidden")
            screen._grade_file_start("bob.submission.md", 2, 5)
            screen._grade_question("q3")

            bar = screen.query_one("#autograde-progress-bar", ProgressBar)
            assert bar.total == 5
            assert bar.progress == 1
            status = str(screen.query_one("#autograde-progress-status", Static).render())
            assert "2/5" in status
            assert "bob.submission.md" in status
            assert "q3" in status

    asyncio.run(run())


def test_setup_grading_runs_in_background_with_progress(monkeypatch) -> None:
    from opengrader.cli.automated_grading import GradingProgress

    seen: dict[str, object] = {}

    def fake_run(
        argv, *, progress: GradingProgress | None = None, status_callback=None
    ) -> None:
        seen["argv"] = argv
        seen["has_progress"] = isinstance(progress, GradingProgress)
        seen["has_status_callback"] = callable(status_callback)
        progress.file_start("only.submission.md", 1, 1)
        progress.question("only.submission.md", "q1")

    monkeypatch.setattr(setup_screen, "run_grading_cli", fake_run)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#grade-solutions", Input).value = "/tmp/sol.md"
            app.query_one("#grade-input", Input).value = "/tmp/subs"
            screen._run_grading()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert screen.query_one("#autograde-progress").has_class("hidden")

    asyncio.run(run())

    assert seen.get("has_progress") is True
    assert seen.get("has_status_callback") is True
    assert seen.get("argv") == ["--solutions", "/tmp/sol.md", "--input", "/tmp/subs"]


def test_setup_llm_unavailable_triggers_auto_ollama(monkeypatch) -> None:
    def _raise(resolved=None, *, purpose="text") -> None:
        raise app_config.AIUnavailableError("model 'llama3.1' is not available")

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_grading_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", argv),
    )
    monkeypatch.setattr(app_config, "ensure_ready", _raise)
    app_config.save_settings(app_config.AISettings(grade_written=True))

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#grade-solutions", Input).value = "/tmp/sol.md"
            app.query_one("#grade-input", Input).value = "/tmp/subs"
            screen._run_grading()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())

    assert "--auto-ollama" in captured["argv"]
    assert "--llm" not in captured["argv"]


def _run_extractor_argv(monkeypatch, setup) -> list[str] | None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_extractor_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", list(argv)),
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#ext-input", Input).value = "/tmp/scans"
            app.query_one("#ext-output", Input).value = "/tmp/subs"
            setup(app)
            await pilot.pause()
            app.query_one(SetupScreen)._run_extractor()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    return captured.get("argv")  # type: ignore[return-value]


def _set_jobs(app, value: str) -> None:
    app.query_one("#ext-parallel", Checkbox).value = True
    app.query_one("#ext-jobs", Input).value = value


def test_parallel_jobs_field_is_hidden_until_the_box_is_ticked() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#ext-parallel-options").has_class("hidden")
            app.query_one("#ext-parallel", Checkbox).value = True
            await pilot.pause()
            assert not app.query_one("#ext-parallel-options").has_class("hidden")
            app.query_one("#ext-parallel", Checkbox).value = False
            await pilot.pause()
            assert app.query_one("#ext-parallel-options").has_class("hidden")

    asyncio.run(run())


def test_extractor_defaults_to_auto_jobs_and_no_resume(monkeypatch) -> None:
    argv = _run_extractor_argv(monkeypatch, lambda _app: None)
    assert argv is not None
    assert "--jobs" not in argv
    assert "--resume" not in argv


def test_extractor_passes_jobs_and_resume(monkeypatch) -> None:
    def setup(app) -> None:
        _set_jobs(app, "4")
        app.query_one("#ext-resume", Checkbox).value = True

    argv = _run_extractor_argv(monkeypatch, setup)
    assert argv is not None
    assert argv[argv.index("--jobs") + 1] == "4"
    assert "--resume" in argv


def test_a_ticked_but_blank_job_count_stays_automatic(monkeypatch) -> None:
    argv = _run_extractor_argv(
        monkeypatch, lambda app: setattr(app.query_one("#ext-parallel", Checkbox), "value", True)
    )
    assert argv is not None and "--jobs" not in argv


def test_unticking_the_box_ignores_a_number_left_behind(monkeypatch) -> None:
    def setup(app) -> None:
        _set_jobs(app, "4")
        app.query_one("#ext-parallel", Checkbox).value = False

    argv = _run_extractor_argv(monkeypatch, setup)
    assert argv is not None and "--jobs" not in argv


def test_extractor_rejects_a_nonsense_job_count(monkeypatch) -> None:
    argv = _run_extractor_argv(monkeypatch, lambda app: _set_jobs(app, "-2"))
    assert argv is None


def test_roster_argv_one_section_uses_named_id_file() -> None:
    async def run() -> list[str]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#ext-id-path", Input).value = "/tmp/roster.tsv"
            app.query_one("#ext-section-name", Input).value = "SectionA"
            return screen._roster_argv()

    argv = asyncio.run(run())
    assert argv == ["--id-file", "/tmp/roster.tsv=SectionA"]


def test_roster_argv_one_section_defaults_name_to_filename() -> None:
    async def run() -> list[str]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#ext-id-path", Input).value = "/tmp/rosterMW.tsv"
            return screen._roster_argv()

    argv = asyncio.run(run())
    assert argv == ["--id-file", "/tmp/rosterMW.tsv=rosterMW"]


def test_roster_argv_multi_section_emits_one_id_file_per_line() -> None:
    from textual.widgets import RadioButton, TextArea

    async def run() -> list[str]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#ext-mode-many", RadioButton).value = True
            await pilot.pause()
            app.query_one("#ext-roster-list", TextArea).text = (
                "/tmp/mw.tsv=MW\n/tmp/tth.tsv\n"
            )
            return screen._roster_argv()

    argv = asyncio.run(run())
    assert argv == [
        "--id-file", "/tmp/mw.tsv=MW",
        "--id-file", "/tmp/tth.tsv",
    ]


def test_roster_area_keeps_run_extractor_reachable() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()

            app.query_one("#section-extractor", Collapsible).collapsed = False
            app.query_one("#ext-use-id", Checkbox).value = True
            await pilot.pause()
            roster = app.query_one("#ext-roster-area")
            assert not roster.has_class("hidden")

            assert roster.region.height < app.size.height

            button = app.query_one("#run-extractor", Button)
            app.query_one("#setup-container", VerticalScroll).scroll_to_widget(
                button, animate=False
            )
            await pilot.pause()
            assert button.region.height > 0
            assert button.region.y >= 0
            assert button.region.y + button.region.height <= app.size.height

    asyncio.run(run())


def test_parallel_options_keep_the_rest_of_the_card_visible() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()

            app.query_one("#section-extractor", Collapsible).collapsed = False
            app.query_one("#ext-parallel", Checkbox).value = True
            await pilot.pause()
            assert not app.query_one("#ext-parallel-options").has_class("hidden")

            options = app.query_one("#ext-parallel-options")
            content_height = sum(child.region.height for child in options.children)
            assert content_height <= options.region.height <= content_height + 4

            for widget_id in ("#ext-resume", "#ext-lock-anon", "#run-extractor"):
                widget = app.query_one(widget_id)
                assert widget.region.height > 0, widget_id

            button = app.query_one("#run-extractor", Button)
            app.query_one("#setup-container", VerticalScroll).scroll_to_widget(
                button, animate=False
            )
            await pilot.pause()
            assert button.region.y >= 0
            assert button.region.y + button.region.height <= app.size.height

    asyncio.run(run())


def test_browse_appends_roster_lines(monkeypatch) -> None:
    from pathlib import Path

    from textual.widgets import RadioButton, TextArea

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#ext-mode-many", RadioButton).value = True
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

            text = app.query_one("#ext-roster-list", TextArea).text
            assert text == "/tmp/mw.tsv\n/tmp/tth.tsv\n"

    asyncio.run(run())


def test_bubble_sheet_checkbox_reveals_its_note_and_the_roster_row() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            assert app.query_one("#tmpl-bubble-note").has_class("hidden")
            assert app.query_one("#tmpl-roster-area").has_class("hidden")

            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()
            assert not app.query_one("#tmpl-bubble-note").has_class("hidden")
            assert not app.query_one("#tmpl-roster-area").has_class("hidden")

            app.query_one("#tmpl-bubble-sheet", Checkbox).value = False
            await pilot.pause()
            assert app.query_one("#tmpl-bubble-note").has_class("hidden")
            assert app.query_one("#tmpl-roster-area").has_class("hidden")

    asyncio.run(run())


def test_bubble_sheet_builds_argv_and_never_adds_a_redundant_qr(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_qa_file_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", list(argv)),
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
        "run_qa_file_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", list(argv)),
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
    def fake_cli(argv, **kwargs) -> None:  # noqa: ANN001
        print("Note: 1 question(s) are not multiple choice and were left off "
              "the sheet: q2b")
        print("Wrote bubble sheet (3 questions, 1 page(s)): /tmp/out.pdf")

    monkeypatch.setattr(setup_screen, "run_qa_file_cli", fake_cli)
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


def test_a_clean_template_run_reports_plain_success(monkeypatch) -> None:
    monkeypatch.setattr(
        setup_screen, "run_qa_file_cli", lambda argv, **kwargs: print("Wrote template: x")
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
            screen._run_template()

    asyncio.run(run())

    assert notices == [("Template PDF written to /tmp/out.pdf", "information")]


def test_bubble_note_keeps_the_rest_of_the_card_visible() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()

            app.query_one("#section-template", Collapsible).collapsed = False
            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()

            note = app.query_one("#tmpl-bubble-note")
            content_height = sum(child.region.height for child in note.children)
            assert content_height <= note.region.height <= content_height + 4

            app.query_one("#setup-container", VerticalScroll).scroll_end(animate=False)
            await pilot.pause()
            for widget_id in ("#tmpl-roster", "#run-template"):
                assert app.query_one(widget_id).region.height > 0, widget_id
            button = app.query_one("#run-template", Button)
            assert button.region.y + button.region.height <= app.size.height

    asyncio.run(run())


def test_batch_options_appear_only_for_a_bubble_sheet() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#tmpl-batch-area").has_class("hidden")

            app.query_one("#tmpl-qr", Checkbox).value = True
            await pilot.pause()
            assert not app.query_one("#tmpl-roster-area").has_class("hidden")
            assert app.query_one("#tmpl-batch-area").has_class("hidden")

            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()
            assert not app.query_one("#tmpl-batch-area").has_class("hidden")

    asyncio.run(run())


def test_copy_count_and_shuffle_reach_the_cli(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_qa_file_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", list(argv)),
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
        setup_screen, "run_qa_file_cli", lambda argv, **kwargs: calls.append(list(argv))
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
        setup_screen, "run_qa_file_cli", lambda argv, **kwargs: calls.append(list(argv))
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
        setup_screen, "run_qa_file_cli", lambda argv, **kwargs: calls.append(list(argv))
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
        "run_qa_file_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", list(argv)),
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


def test_batch_area_keeps_the_generate_button_reachable() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app.query_one("#section-template", Collapsible).collapsed = False
            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            app.query_one("#tmpl-shuffle", Checkbox).value = True
            await pilot.pause()

            area = app.query_one("#tmpl-copies-area")
            content_height = sum(child.region.height for child in area.children)
            assert content_height <= area.region.height <= content_height + 4

            app.query_one("#setup-container", VerticalScroll).scroll_end(animate=False)
            await pilot.pause()
            button = app.query_one("#run-template", Button)
            assert button.region.height > 0
            assert button.region.y + button.region.height <= app.size.height

    asyncio.run(run())


def test_the_sheet_count_appears_only_under_scrambling() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()
            assert not app.query_one("#tmpl-batch-area").has_class("hidden")
            assert app.query_one("#tmpl-copies-area").has_class("hidden")

            app.query_one("#tmpl-shuffle", Checkbox).value = True
            await pilot.pause()
            assert not app.query_one("#tmpl-copies-area").has_class("hidden")

            app.query_one("#tmpl-shuffle", Checkbox).value = False
            await pilot.pause()
            assert app.query_one("#tmpl-copies-area").has_class("hidden")

    asyncio.run(run())


def test_unticking_scramble_ignores_a_count_left_behind(monkeypatch) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_qa_file_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", list(argv)),
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


def _stub_grading_cli(monkeypatch) -> dict[str, object]:  # noqa: ANN001
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_grading_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", argv),
    )
    monkeypatch.setattr(
        app_config, "ensure_ready", lambda resolved=None, *, purpose="text": None
    )
    return captured


def test_min_confidence_is_omitted_when_none_is_saved(monkeypatch) -> None:  # noqa: ANN001
    captured = _stub_grading_cli(monkeypatch)
    app_config.save_settings(app_config.AISettings(grade_written=True))

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#grade-solutions", Input).value = "/tmp/sol.md"
            app.query_one("#grade-input", Input).value = "/tmp/subs"
            screen._run_grading()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    assert "--llm-min-confidence" not in captured["argv"]


def test_min_confidence_never_becomes_a_cli_flag_even_when_saved(monkeypatch) -> None:  # noqa: ANN001
    captured = _stub_grading_cli(monkeypatch)
    app_config.save_settings(
        app_config.AISettings(min_confidence=0.85, grade_written=True)
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#grade-solutions", Input).value = "/tmp/sol.md"
            app.query_one("#grade-input", Input).value = "/tmp/subs"
            screen._run_grading()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    argv = captured["argv"]
    assert "--llm-min-confidence" not in argv
    assert "--llm" not in argv


def test_calibration_opens_prefilled_and_saves_its_answer_to_ai_setup() -> None:
    async def run() -> tuple[str, str, float]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#grade-solutions", Input).value = "/tmp/sol.md"
            app.query_one("#grade-input", Input).value = "/tmp/subs"
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


def test_calibration_accept_marks_the_confidence_measured_and_wants_ai_grading() -> None:
    async def run() -> app_config.AISettings:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#grade-solutions", Input).value = "/tmp/sol.md"
            app.query_one("#grade-input", Input).value = "/tmp/subs"
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


def test_handoff_and_backup_tools_are_present_and_collapsed() -> None:
    async def run() -> tuple[bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            handoff = app.query_one("#section-handoff", Collapsible)
            backup = app.query_one("#section-backup", Collapsible)
            assert app.query_one("#run-handoff-pack", Button)
            assert app.query_one("#run-handoff-merge", Button)
            assert app.query_one("#run-backup", Button)
            return handoff.collapsed, backup.collapsed

    assert asyncio.run(run()) == (True, True)


def test_the_handoff_tool_falls_back_to_the_folder_being_graded(tmp_path) -> None:  # noqa: ANN001
    import shutil

    shutil.copytree("samples/cs146-quiz1", tmp_path / "quiz")
    qa = (tmp_path / "quiz" / "qa.md").resolve()
    subs = (tmp_path / "quiz" / "student-submissions").resolve()

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


def test_extractor_never_emits_llm_responses_escalate_or_math_flags(monkeypatch) -> None:
    app_config.save_settings(
        app_config.AISettings(
            read_handwriting=True, escalate_only=True, transcribe_math=True
        )
    )
    argv = _run_extractor_argv(monkeypatch, lambda _app: None)
    assert argv is not None
    for flag in ("--llm-responses", "--llm-escalate", "--llm-math"):
        assert flag not in argv
    assert "--math-backend" not in argv


def test_ext_ai_status_names_escalation_and_math(monkeypatch) -> None:  # noqa: ANN001

    async def status() -> str:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return str(app.query_one("#ext-ai-status", Static).render())

    app_config.save_settings(app_config.AISettings())
    off = asyncio.run(status())
    assert "AI: off" in off

    app_config.save_settings(
        app_config.AISettings(read_handwriting=True, escalate_only=True)
    )
    escalate_on = asyncio.run(status())
    assert "AI: off" not in escalate_on
    assert "OCR read badly" in escalate_on
    assert "LaTeX" not in escalate_on

    app_config.save_settings(
        app_config.AISettings(
            read_handwriting=True, escalate_only=False, transcribe_math=True
        )
    )
    math_on = asyncio.run(status())
    assert "every answer" in math_on
    assert "LaTeX" in math_on


def test_extraction_in_progress_flag_tracks_the_extractor_worker(monkeypatch) -> None:
    monkeypatch.setattr(setup_screen, "run_extractor_cli", lambda argv, **kwargs: None)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#ext-input", Input).value = "/tmp/scans"
            app.query_one("#ext-output", Input).value = "/tmp/subs"

            assert getattr(screen, "extraction_in_progress", False) is False
            screen._run_extractor()
            assert screen.extraction_in_progress is True

            await app.workers.wait_for_complete()
            await pilot.pause()
            assert screen.extraction_in_progress is False

    asyncio.run(run())


def test_grade_while_extracting_button_is_visible_only_during_extraction(
    monkeypatch,
) -> None:
    monkeypatch.setattr(setup_screen, "run_extractor_cli", lambda argv, **kwargs: None)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#section-extractor", Collapsible).collapsed = False
            await pilot.pause()

            app.query_one("#grade-while-extracting", Button)
            assert app.query_one("#ext-progress").has_class("hidden")

            app.query_one("#ext-input", Input).value = "/tmp/scans"
            app.query_one("#ext-output", Input).value = "/tmp/subs"
            screen._run_extractor()
            # The no-op extractor can finish and hide progress at the next await.
            assert not app.query_one("#ext-progress").has_class("hidden")

            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.query_one("#ext-progress").has_class("hidden")

    asyncio.run(run())


def test_grade_while_extracting_requires_an_answer_key_path() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            notices: list[tuple[str, str]] = []
            monkeypatch_notify = app.notify
            app.notify = lambda message, **kwargs: notices.append(  # type: ignore[method-assign]
                (str(message), kwargs.get("severity", "information"))
            )
            try:
                app.query_one("#ext-output", Input).value = "/tmp/subs"
                screen._open_grade_while_extracting()
            finally:
                app.notify = monkeypatch_notify  # type: ignore[method-assign]

            assert notices
            message, severity = notices[-1]
            assert "answer key" in message.lower()
            assert severity == "error"

    asyncio.run(run())


def test_grade_while_extracting_posts_the_current_paths() -> None:
    events: list[tuple[str, str]] = []
    original = SetupScreen.GradeWhileExtractingRequested.__init__

    def _spy(self, qa_path: str, submissions_path: str) -> None:  # noqa: ANN001
        events.append((qa_path, submissions_path))
        original(self, qa_path, submissions_path)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#ext-qa", Input).value = "/tmp/qa.md"
            app.query_one("#ext-output", Input).value = "/tmp/subs"
            screen._open_grade_while_extracting()
            await pilot.pause()

    SetupScreen.GradeWhileExtractingRequested.__init__ = _spy  # type: ignore[method-assign]
    try:
        asyncio.run(run())
    finally:
        SetupScreen.GradeWhileExtractingRequested.__init__ = original  # type: ignore[method-assign]

    assert events == [("/tmp/qa.md", "/tmp/subs")]


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
            captured["ext_input"] = app.query_one("#ext-input", Input).value

    asyncio.run(run())
    captured["notes"] = notes
    return captured


def test_photo_import_tool_is_present_and_collapsed() -> None:
    async def run() -> bool:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#run-photo-import", Button)
            return app.query_one("#section-photo-import", Collapsible).collapsed

    assert asyncio.run(run()) is True


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


def test_photo_import_points_extract_at_what_it_wrote(monkeypatch) -> None:  # noqa: ANN001

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert captured["ext_input"] == "/tmp/split"


def test_photo_import_leaves_an_extract_path_the_user_typed(monkeypatch) -> None:  # noqa: ANN001

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"
        app.query_one("#ext-input", Input).value = "/tmp/mine"

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert captured["ext_input"] == "/tmp/mine"


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
    assert captured["ext_input"] == ""


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


def test_region_map_tool_is_present_and_collapsed() -> None:
    async def run() -> bool:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#run-region-map", Button)
            return app.query_one("#section-region-map", Collapsible).collapsed

    assert asyncio.run(run()) is True


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


def test_a_saved_region_map_lands_in_the_extract_step(tmp_path) -> None:  # noqa: ANN001
    pdf = _blank_assignment_pdf(tmp_path)
    saved = tmp_path / "midterm.regions.json"

    async def run() -> tuple[str, bool, bool]:
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
                app.query_one("#ext-region-map-path", Input).value,
                app.query_one("#ext-use-region-map", Checkbox).value,
                app.query_one("#ext-use-template", Checkbox).value,
            )

    path_value, region_on, template_on = asyncio.run(run())
    assert path_value == str(saved)
    assert region_on is True
    assert template_on is False


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
                app.query_one("#ext-region-map-path", Input).value,
                app.query_one("#ext-use-region-map", Checkbox).value,
            )

    assert asyncio.run(run()) == ("", False)


def test_the_region_map_reaches_the_extractor(monkeypatch) -> None:  # noqa: ANN001
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_extractor_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", argv),
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#ext-input", Input).value = "/tmp/split"
            app.query_one("#ext-output", Input).value = "/tmp/out"
            app.query_one("#ext-use-region-map", Checkbox).value = True
            await pilot.pause()
            app.query_one("#ext-region-map-path", Input).value = "/tmp/midterm.regions.json"
            app.query_one(SetupScreen)._run_extractor()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    argv = captured["argv"]
    assert argv[argv.index("--region-map") + 1] == "/tmp/midterm.regions.json"
    assert "--blank-template" not in argv


def test_the_two_layout_sources_untick_each_other() -> None:
    async def run() -> tuple[bool, bool, bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.query_one("#ext-use-template", Checkbox).value = True
            await pilot.pause()
            app.query_one("#ext-use-region-map", Checkbox).value = True
            await pilot.pause()
            after_region = (
                app.query_one("#ext-use-template", Checkbox).value,
                app.query_one("#ext-use-region-map", Checkbox).value,
            )
            app.query_one("#ext-use-template", Checkbox).value = True
            await pilot.pause()
            after_template = (
                app.query_one("#ext-use-template", Checkbox).value,
                app.query_one("#ext-use-region-map", Checkbox).value,
            )
            return (*after_region, *after_template)

    assert asyncio.run(run()) == (False, True, True, False)


def test_a_ticked_region_map_with_no_path_is_refused(monkeypatch) -> None:  # noqa: ANN001
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_extractor_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", argv),
    )
    notes: list[str] = []

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.notify = lambda message, **kwargs: notes.append(str(message))
            app.query_one("#ext-input", Input).value = "/tmp/split"
            app.query_one("#ext-output", Input).value = "/tmp/out"
            app.query_one("#ext-use-region-map", Checkbox).value = True
            await pilot.pause()
            app.query_one(SetupScreen)._run_extractor()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())
    assert "argv" not in captured
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


def test_the_pipeline_has_a_section_of_its_own_above_the_numbered_steps() -> None:
    async def run() -> bool:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            content = app.query_one("#setup-content")
            ids = [child.id for child in content.children]
            assert (
                ids.index("launch-section")
                < ids.index("pipeline-heading")
                < ids.index("pipeline-caption")
                < ids.index("section-pipeline")
                < ids.index("steps-heading")
                < ids.index("steps-caption")
                < ids.index("section-splitter")
            )
            return app.query_one("#section-pipeline", Collapsible).collapsed

    assert asyncio.run(run()) is True


def test_every_section_heading_is_styled_the_same_way() -> None:
    async def run() -> tuple[list[str], list[str]]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            headings = [node.id for node in screen.query(".section-heading")]
            captions = [node.id for node in screen.query(".section-caption")]
            return headings, captions

    headings, captions = asyncio.run(run())
    assert headings == ["pipeline-heading", "steps-heading", "helpers-heading"]
    assert captions == ["pipeline-caption", "steps-caption", "helpers-caption"]


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


def test_pipeline_checkboxes_reveal_their_dependent_rows() -> None:
    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#pipe-roster-row").has_class("hidden")
            assert app.query_one("#pipe-template-row").has_class("hidden")

            app.query_one("#pipe-use-roster", Checkbox).value = True
            await pilot.pause()
            assert not app.query_one("#pipe-roster-row").has_class("hidden")
            assert app.query_one("#pipe-template-row").has_class("hidden")

            app.query_one("#pipe-use-template", Checkbox).value = True
            await pilot.pause()
            assert not app.query_one("#pipe-template-row").has_class("hidden")

            app.query_one("#pipe-use-roster", Checkbox).value = False
            await pilot.pause()
            assert app.query_one("#pipe-roster-row").has_class("hidden")
            assert not app.query_one("#pipe-template-row").has_class("hidden")

    asyncio.run(run())


def test_pipeline_request_maps_every_control(tmp_path) -> None:  # noqa: ANN001
    app_config.save_settings(
        app_config.AISettings(
            vision_model="llava",
            text_model="llama3.1",
            host="http://192.168.1.50:9999",
            auto_start=True,
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
    assert request.llm.enabled is True
    assert request.llm.read_handwriting is True
    assert request.llm.grade_written is True
    assert request.llm.vision_model == ""
    assert request.llm.text_model == ""
    assert request.llm.host == "http://192.168.1.50:9999"
    assert request.llm.auto_ollama is True
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
    resolved = app_config.resolve()
    assert resolved.read_handwriting is False
    assert resolved.grade_written is False
    assert resolved.auto_grade is True
    assert request.llm.read_handwriting == resolved.read_handwriting
    assert request.llm.grade_written == resolved.grade_written
    assert request.llm.enabled is False
    assert request.llm.host == resolved.host
    assert request.llm.auto_ollama == resolved.auto_start
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
            assert "split" in str(output.render())

    asyncio.run(run())

    assert captured["request"].scan == tmp_path / "scan.pdf"
    assert run_pipeline_called is False
    assert not out_dir.exists()


def test_no_user_facing_text_says_cohort() -> None:
    async def run() -> list[str]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            texts: list[str] = []
            texts.extend(str(widget.render()) for widget in app.query(Static))
            texts.extend(str(widget.render()) for widget in app.query(Label))
            texts.extend(str(widget.label) for widget in app.query(Checkbox))
            texts.extend(str(widget.label) for widget in app.query(RadioButton))
            texts.extend(str(widget.label) for widget in app.query(Button))
            texts.extend(str(widget.title) for widget in app.query(Collapsible))
            return texts

    texts = asyncio.run(run())
    offenders = [text for text in texts if "cohort" in text.lower()]
    assert not offenders, offenders


def test_ai_setup_helper_tool_is_present_and_collapsed() -> None:
    async def run() -> bool:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#run-ai-settings", Button)
            return app.query_one("#section-ai", Collapsible).collapsed

    assert asyncio.run(run()) is True


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


def test_ai_summary_names_the_currently_resolved_provider_and_models() -> None:
    app_config.save_settings(
        app_config.AISettings(
            text_model="llama3.1",
            vision_model="llava",
            read_handwriting=True,
            grade_written=True,
        )
    )

    async def run() -> str:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return str(app.query_one("#ai-current-summary", Static).render())

    summary = asyncio.run(run())
    assert "llama3.1" in summary
    assert "llava" in summary
    assert "Ollama" in summary


def test_ai_summary_names_no_models_when_every_feature_is_off() -> None:
    app_config.save_settings(
        app_config.AISettings(text_model="llama3.1", vision_model="llava")
    )

    async def run() -> str:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return str(app.query_one("#ai-current-summary", Static).render())

    summary = asyncio.run(run())
    assert "llama3.1" not in summary
    assert "llava" not in summary
    assert "does not read handwriting" in summary
    assert "does not grade written answers" in summary


async def _status_text(widget_id: str) -> str:
    app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        return str(app.query_one(f"#{widget_id}", Static).render())


def test_split_ai_status_reflects_the_read_handwriting_setting() -> None:
    off = asyncio.run(_status_text("split-ai-status"))
    assert "AI: off" in off
    assert "page numbers are read from the text layer" in off
    assert "AI setup" in off

    app_config.save_settings(app_config.AISettings(read_handwriting=True))
    on = asyncio.run(_status_text("split-ai-status"))
    assert "AI: off" not in on
    assert "handwriting model" in on


def test_ak_ai_status_reflects_the_read_handwriting_setting() -> None:
    off = asyncio.run(_status_text("ak-ai-status"))
    assert "AI: off" in off

    app_config.save_settings(app_config.AISettings(read_handwriting=True))
    on = asyncio.run(_status_text("ak-ai-status"))
    assert "AI: off" not in on
    assert "handwriting model" in on


def test_grade_ai_status_names_model_confidence_and_calibration_requirement() -> None:
    off = asyncio.run(_status_text("grade-ai-status"))
    assert "AI: off" in off
    assert "only answers your rubric can settle outright" in off

    app_config.save_settings(
        app_config.AISettings(
            text_model="llama3.1",
            grade_written=True,
            min_confidence=0.9,
            require_calibration=False,
        )
    )
    on = asyncio.run(_status_text("grade-ai-status"))
    assert "AI: off" not in on
    assert "llama3.1" in on
    assert "0.9" in on
    assert "no measurement is required" in on

    app_config.save_settings(
        app_config.AISettings(
            text_model="llama3.1", grade_written=True, require_calibration=True
        )
    )
    calibrated = asyncio.run(_status_text("grade-ai-status"))
    assert "a measured confidence is required" in calibrated


def test_pipe_ai_status_states_both_jobs_and_auto_grade() -> None:
    off = asyncio.run(_status_text("pipe-ai-status"))
    assert "does not read handwriting" in off
    assert "does not grade written answers" in off
    assert "auto-grade runs automatically" in off

    app_config.save_settings(
        app_config.AISettings(read_handwriting=True, grade_written=False, auto_grade=False)
    )
    mixed = asyncio.run(_status_text("pipe-ai-status"))
    assert "reads hard-to-read handwriting" in mixed
    assert "does not grade written answers" in mixed
    assert "does not run automatically" in mixed


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
                "split-ai-status",
                "ext-ai-status",
                "ak-ai-status",
                "grade-ai-status",
                "pipe-ai-status",
            )
            return tuple(
                str(app.query_one(f"#{node_id}", Static).render()) for node_id in ids
            )

    (
        summary,
        split_status,
        ext_status,
        ak_status,
        grade_status,
        pipe_status,
    ) = asyncio.run(run())
    assert "llama3.1" in summary
    assert "AI: off" not in split_status
    assert "AI: off" not in ext_status
    assert "AI: off" not in ak_status
    assert "AI: off" not in grade_status
    assert "reads hard-to-read handwriting" in pipe_status


def test_no_step_ever_emits_a_model_host_confidence_or_feature_flag(
    monkeypatch, tmp_path
) -> None:
    captured: dict[str, list[str]] = {}

    def _capture(name: str):
        def _fn(argv, **kwargs) -> None:  # noqa: ANN001
            captured[name] = list(argv)

        return _fn

    monkeypatch.setattr(setup_screen, "run_splitter_cli", _capture("split"))
    monkeypatch.setattr(setup_screen, "run_extractor_cli", _capture("extract"))
    monkeypatch.setattr(setup_screen, "run_answer_key_cli", _capture("answer_key"))
    monkeypatch.setattr(setup_screen, "run_grading_cli", _capture("grade"))
    monkeypatch.setattr(
        app_config, "ensure_ready", lambda resolved=None, *, purpose="text": None
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

    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    ak_input = tmp_path / "assignment.md"
    ak_input.write_text("# stub")

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)

            app.query_one("#split-input", Input).value = str(pdf)
            app.query_one("#split-output", Input).value = str(tmp_path / "split-out")
            await pilot.pause()
            screen._run_splitter()
            await app.workers.wait_for_complete()
            await pilot.pause()

            app.query_one("#ext-input", Input).value = str(tmp_path / "split-out")
            app.query_one("#ext-output", Input).value = str(tmp_path / "ext-out")
            await pilot.pause()
            screen._run_extractor()
            await app.workers.wait_for_complete()
            await pilot.pause()

            app.query_one("#ak-input", Input).value = str(ak_input)
            app.query_one("#ak-output", Input).value = str(tmp_path / "qa.md")
            await pilot.pause()
            screen._run_answer_key()
            await app.workers.wait_for_complete()
            await pilot.pause()

            app.query_one("#grade-solutions", Input).value = str(tmp_path / "qa.md")
            app.query_one("#grade-input", Input).value = str(tmp_path / "ext-out")
            await pilot.pause()
            screen._run_grading()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())

    assert set(captured) == {"split", "extract", "answer_key", "grade"}
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
    for step, argv in captured.items():
        for flag in banned:
            assert flag not in argv, f"{step} argv unexpectedly carries {flag}: {argv}"


def test_the_llm_readiness_probe_runs_off_the_ui_thread(monkeypatch) -> None:
    import threading

    seen: dict[str, object] = {}

    def _probe(resolved=None, *, purpose="text"):  # noqa: ANN001, ANN202
        seen["on_main_thread"] = (
            threading.current_thread() is threading.main_thread()
        )

    monkeypatch.setattr(setup_screen, "run_grading_cli", lambda argv, **kwargs: None)
    monkeypatch.setattr(app_config, "ensure_ready", _probe)
    app_config.save_settings(app_config.AISettings(grade_written=True))

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#grade-solutions", Input).value = "/tmp/sol.md"
            app.query_one("#grade-input", Input).value = "/tmp/subs"
            screen._run_grading()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())

    assert seen.get("on_main_thread") is False, (
        "the readiness probe ran on the UI thread — a slow or black-holed host "
        "would freeze the whole app"
    )


def test_the_llm_probe_is_skipped_entirely_when_the_ai_is_not_wanted(monkeypatch) -> None:
    calls: list[object] = []

    monkeypatch.setattr(setup_screen, "run_grading_cli", lambda argv, **kwargs: None)
    monkeypatch.setattr(
        app_config,
        "ensure_ready",
        lambda resolved=None, *, purpose="text": calls.append(resolved),
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#grade-solutions", Input).value = "/tmp/sol.md"
            app.query_one("#grade-input", Input).value = "/tmp/subs"
            screen._run_grading()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())

    assert calls == []
