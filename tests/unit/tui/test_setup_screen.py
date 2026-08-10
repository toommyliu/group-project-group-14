import asyncio

from textual.containers import VerticalScroll
from textual.widgets import Button, Checkbox, Collapsible, Input, Label, RadioButton, Static

import opengrader.tui.ui.screens.setup_screen as setup_screen
from opengrader.core import ai_config
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens.setup_screen import SetupScreen

from .fakes import DEFAULT_IMAGE_PATH
from .test_screen_conventions import CHECKBOX_LABEL_LIMIT


def test_setup_primary_action_visible_without_scrolling() -> None:
    """The launch card is the primary action and must be above the fold."""

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()

            button = app.query_one("#launch-button")
            assert button.region.height > 0
            assert button.region.y >= 0
            assert button.region.y + button.region.height <= app.size.height

    asyncio.run(run())


def test_setup_scroll_reaches_last_section() -> None:
    """Every setup section must be scrollable into view.

    Regression test: containers inside the scroll view defaulted to 1fr
    height, clipping children and truncating the scroll range so content
    at the bottom could never be reached on short terminals.

    It scrolls *to* the section rather than to the end of the page: asserting a
    named section is on screen after `scroll_end` only holds while that section
    happens to be last, so adding a Helper Tool below it would fail a test that
    is not about Helper Tools at all.
    """

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()

            content = app.query_one("#setup-content")
            children_height = sum(
                child.outer_size.height for child in content.children
            )
            # #setup-content must size to its children so the scroll
            # range covers everything (outer_size excludes margins, so
            # the content is at least as tall as the children combined).
            assert content.outer_size.height >= children_height

            section = app.query_one("#section-grading")
            section.scroll_visible(animate=False)
            await pilot.pause()

            assert section.region.height > 0
            assert section.region.y >= 0
            assert section.region.y + section.region.height <= app.size.height

    asyncio.run(run())


def test_setup_scroll_reaches_the_very_last_section() -> None:
    """The bottom of the page must be reachable, whatever is currently last.

    The companion to the test above: that one proves a *named* section can be
    scrolled to, this one proves the scroll range actually covers the end, and
    it asks the page which section is last instead of hard-coding one.
    """

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
    """There is no AI checkbox on the Auto-grade card any more — whether to
    grade written answers with the AI is the saved `grade_written` setting,
    which the CLI resolves for its own argparse default, so this screen never
    builds `--llm` itself. The readiness probe still has to fire when a model
    will actually be asked to grade, which is why `ensure_model_available`
    receives the model from `ai_config`, not from any field on this card."""
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_grading_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", argv),
    )
    monkeypatch.setattr(
        setup_screen,
        "ensure_model_available",
        lambda config: captured.__setitem__("config", config),
    )
    ai_config.save_settings(
        ai_config.AISettings(text_model="llama3.1", grade_written=True)
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)

            app.query_one("#grade-solutions", Input).value = "/tmp/sol.md"
            app.query_one("#grade-input", Input).value = "/tmp/subs"

            screen._run_grading()
            # Grading now runs in a background thread worker.
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())

    argv = captured.get("argv")
    # Never --llm: the CLI resolves whether to grade with the AI from the
    # settings file itself.
    assert argv == ["--solutions", "/tmp/sol.md", "--input", "/tmp/subs"]
    # No --llm-model either: the CLI resolves its own default from the saved
    # settings.
    assert "--llm-model" not in argv
    # But the probe still ran, and against the saved model — proof the
    # decision now comes from `grade_written`, not a deleted checkbox.
    assert captured["config"].model == "llama3.1"


def test_template_qr_checkbox_reveals_roster_and_builds_argv(monkeypatch) -> None:
    """The roster row appears only under the QR checkbox, and drives the argv."""
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

            # Roster row is hidden until QR is checked.
            assert app.query_one("#tmpl-roster-area").has_class("hidden")

            # Plain QR (no roster) → --qr.
            app.query_one("#tmpl-qr", Checkbox).value = True
            await pilot.pause()
            assert not app.query_one("#tmpl-roster-area").has_class("hidden")
            screen._run_template()
            assert "--qr" in captured["argv"]
            assert "--roster" not in captured["argv"]

            # A roster path → personalized batch (--roster, not bare --qr).
            app.query_one("#tmpl-roster", Input).value = "/tmp/roster.tsv"
            screen._run_template()
            assert captured["argv"].count("--qr") == 0
            assert "--roster" in captured["argv"]
            assert "/tmp/roster.tsv" in captured["argv"]

            # Toggling QR back off hides the row and drops the stale roster value.
            app.query_one("#tmpl-qr", Checkbox).value = False
            await pilot.pause()
            assert app.query_one("#tmpl-roster-area").has_class("hidden")
            screen._run_template()
            assert "--roster" not in captured["argv"]
            assert "--qr" not in captured["argv"]

    asyncio.run(run())


def test_setup_grading_defaults_model_when_no_ai_settings_are_saved(monkeypatch) -> None:
    """With `grade_written` on but no model ever chosen, grading resolves the
    provider's own default model — there is no "advanced" disclosure any more
    to leave off; an untouched AI setup and an untouched Auto-grade card
    behave the same."""
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_grading_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", argv),
    )
    monkeypatch.setattr(
        setup_screen,
        "ensure_model_available",
        lambda config: captured.__setitem__("config", config),
    )
    ai_config.save_settings(ai_config.AISettings(grade_written=True))

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

    assert captured["config"].model == ai_config.DEFAULT_TEXT_MODEL
    assert captured["argv"] == ["--solutions", "/tmp/sol.md", "--input", "/tmp/subs"]
    assert "--llm-model" not in captured["argv"]


def test_setup_splitter_never_passes_a_model_and_auto_starts_per_saved_setting(
    monkeypatch, tmp_path
) -> None:
    """Split has no AI checkbox of its own any more: which model and host it
    uses is the saved AI setting (the splitter resolves both for itself), and
    whether to auto-start Ollama is likewise the saved "start automatically"
    setting, not anything typed on this card — so `--auto-ollama` follows
    `auto_start` unconditionally now."""
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
        ai_config.save_settings(
            ai_config.AISettings(
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
    # With "start automatically" off, the splitter is not told to boot Ollama
    # either — the same rule the grading and extract paths follow.
    assert "--auto-ollama" not in captured["argv"]


def test_no_checkbox_label_is_long_enough_to_be_cut_off() -> None:
    """A checkbox label must fit on one line, because it only gets one.

    Textual's ``ToggleButton.get_content_height`` returns a hard-coded 1, so a
    Checkbox can never wrap however it is styled — anything past the card's
    width is dropped, and the reader has no way to tell something was there.
    That is how "Check the scan before splitting (turn upside-down pages
    upright, …)" shipped reading as "Check the scan before splitting (turn
    upside-down pages upright, put o".

    The ceiling is what still fits inside the card on an **80-column** terminal,
    the narrowest anyone reasonably grades in. It is a backstop, **not** a
    target: the house style is a short label with a ``card-hint`` Static
    underneath carrying the detail, and a Static wraps as far as it likes.

    This checks the *rendered* labels of the screen the app opens on;
    ``test_screen_conventions.py`` applies the same ceiling across every screen
    including the modals, which this one never mounts.
    """
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
    """The scan check is on unless the user turns it off, and it *fixes*.

    Default-on because the failure it catches is silent: a stack fed in upside
    down still splits and still extracts, it just grades to zeros. An instructor
    who never finds the checkbox is exactly who needs it.
    """
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
    # Page order rides on the same box for the same reason, and is safe to
    # because it acts only on page numbers that cannot have been misread, and
    # does nothing whatever on a scan that is already in order.
    assert "--fix-order" in captured["argv"]
    # The unconditional pass is *not* implied: on OCR-read numbers a reorder can
    # move a page that was in the right place, which is a decision for a person.
    assert "--reorder" not in captured["argv"]

    asyncio.run(run(checked=False))
    assert "--fix-rotation" not in captured["argv"]
    assert "--fix-order" not in captured["argv"]


def test_setup_grading_progress_shows_file_and_question() -> None:
    """The auto-grade progress bar reveals, and its status names file + question."""
    from textual.widgets import ProgressBar, Static

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)

            # Progress area is hidden until grading starts.
            assert screen.query_one("#autograde-progress").has_class("hidden")

            # Drive the handlers the worker calls (avoids a thread race).
            screen.query_one("#autograde-progress").remove_class("hidden")
            screen._grade_file_start("bob.submission.md", 2, 5)
            screen._grade_question("q3")

            bar = screen.query_one("#autograde-progress-bar", ProgressBar)
            assert bar.total == 5
            assert bar.progress == 1  # 1 file done before file #2 starts
            status = str(screen.query_one("#autograde-progress-status", Static).render())
            assert "2/5" in status
            assert "bob.submission.md" in status
            assert "q3" in status

    asyncio.run(run())


def test_setup_grading_runs_in_background_with_progress(monkeypatch) -> None:
    """_run_grading spawns a worker, passes a GradingProgress, and hides on finish."""
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
            # Progress area hides again once grading completes.
            assert screen.query_one("#autograde-progress").has_class("hidden")

    asyncio.run(run())

    assert seen.get("has_progress") is True
    assert seen.get("has_status_callback") is True
    assert seen.get("argv") == ["--solutions", "/tmp/sol.md", "--input", "/tmp/subs"]


def test_setup_llm_unavailable_triggers_auto_ollama(monkeypatch) -> None:
    """A server/model that isn't ready makes grading auto-start Ollama, when
    `grade_written` (not a checkbox any more) says the AI is wanted."""
    from opengrader.core.llm import OllamaError

    def _raise(config) -> None:
        raise OllamaError("Ollama model 'llama3.1' is not available")

    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_grading_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", argv),
    )
    monkeypatch.setattr(setup_screen, "ensure_model_available", _raise)
    ai_config.save_settings(ai_config.AISettings(grade_written=True))

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

    # Instead of erroring, the CLI is asked to boot the server and pull.
    assert "--auto-ollama" in captured["argv"]
    # Never --llm: the CLI resolves whether to grade with the AI from the
    # settings file itself.
    assert "--llm" not in captured["argv"]


def _run_extractor_argv(monkeypatch, setup) -> list[str] | None:
    """Drive the extractor card with `setup(app)` and return the argv it built."""
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
    """Tick the parallel-jobs disclosure and type a worker count into it."""
    app.query_one("#ext-parallel", Checkbox).value = True
    app.query_one("#ext-jobs", Input).value = value


def test_parallel_jobs_field_is_hidden_until_the_box_is_ticked() -> None:
    """Same disclosure pattern as the vision-model override next to it."""

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
    """An untouched card behaves as before: the runner picks the worker count."""
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
    """The hidden input must not silently apply to a later run — the same rule
    `_ollama_values` follows for the model/host override."""

    def setup(app) -> None:
        _set_jobs(app, "4")
        app.query_one("#ext-parallel", Checkbox).value = False

    argv = _run_extractor_argv(monkeypatch, setup)
    assert argv is not None and "--jobs" not in argv


def test_extractor_rejects_a_nonsense_job_count(monkeypatch) -> None:
    """Caught in the UI, so the user gets a notification instead of a crash in
    the background worker."""
    argv = _run_extractor_argv(monkeypatch, lambda app: _set_jobs(app, "-2"))
    assert argv is None  # the run never started


def test_roster_argv_one_section_uses_named_id_file() -> None:
    """One-section mode emits a single --id-file with the section name."""

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
    """Revealing the roster sub-form must not stretch to the bottom and push
    Run Extractor out of reach (regression: bare Verticals default to 1fr)."""

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()

            app.query_one("#section-extractor", Collapsible).collapsed = False
            app.query_one("#ext-use-id", Checkbox).value = True
            await pilot.pause()
            roster = app.query_one("#ext-roster-area")
            assert not roster.has_class("hidden")

            # The direct guard: the revealed sub-form sizes to its children, not
            # to a 1fr slab that swallows the rest of the card. Asserted on the
            # height itself rather than inferred from what scrolled where, so
            # adding an unrelated section further down the page can't break it.
            assert roster.region.height < app.size.height

            # ...and the button below it is still reachable by scrolling to it.
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
    """Revealing the parallel-jobs field must not stretch to the bottom of the
    card (regression: bare Verticals default to 1fr, which hid the anonymity
    checkbox and pushed Run Extractor out of reach)."""

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()

            app.query_one("#section-extractor", Collapsible).collapsed = False
            app.query_one("#ext-parallel", Checkbox).value = True
            await pilot.pause()
            assert not app.query_one("#ext-parallel-options").has_class("hidden")

            # The revealed block sizes to its children (plus their margins), not
            # to the 1fr slab that swallowed the rest of the card — that bug
            # measured 52 rows against a 7-row content height.
            options = app.query_one("#ext-parallel-options")
            content_height = sum(child.region.height for child in options.children)
            assert content_height <= options.region.height <= content_height + 4

            # Everything that follows the disclosure is still laid out...
            for widget_id in ("#ext-resume", "#ext-lock-anon", "#run-extractor"):
                widget = app.query_one(widget_id)
                assert widget.region.height > 0, widget_id

            # ...and reachable. Scrolled to the button rather than to the end of
            # the page, so a section added below the extractor can't fail this.
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

            # First Browse pick appends a line...
            screen._append_roster_path()
            captured["cb"](Path("/tmp/mw.tsv"))
            await pilot.pause()
            # ...and a second pick appends another, not replacing the first.
            screen._append_roster_path()
            captured["cb"](Path("/tmp/tth.tsv"))
            await pilot.pause()

            text = app.query_one("#ext-roster-list", TextArea).text
            assert text == "/tmp/mw.tsv\n/tmp/tth.tsv\n"

    asyncio.run(run())


# ── bubble answer sheets ──────────────────────────────────────────────────


def test_bubble_sheet_checkbox_reveals_its_note_and_the_roster_row() -> None:
    """A bubble sheet always carries its QR, so ticking it alone must reach the
    roster row — otherwise a personalized batch would be unreachable without
    also ticking a QR box that does nothing here."""

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
            # The sheet stamps its own QR; passing --qr too would be noise.
            assert "--qr" not in captured["argv"]

            # A roster turns it into a personalized batch.
            app.query_one("#tmpl-roster", Input).value = "/tmp/roster.tsv"
            screen._run_template()
            assert "--bubble-sheet" in captured["argv"]
            assert "--roster" in captured["argv"]
            assert "--qr" not in captured["argv"]

    asyncio.run(run())


def test_unticking_bubble_sheet_drops_a_roster_left_behind(monkeypatch) -> None:
    """Same rule the other disclosures follow: a value in a hidden input can
    never silently apply to a later run."""
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
    """The CLI names skipped questions on stdout, which the TUI would otherwise
    discard. That warning is the dangerous one: an instructor who prints a sheet
    missing the essay question only finds out after the exam has been sat."""
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
    # Warning, not a cheerful success: something was left off.
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
    """Same regression guard as the other disclosures: a bare Vertical defaults
    to 1fr and would swallow the roster row and the Generate button."""

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
    """Copy count and scrambling are bubble-sheet only — a written template
    cannot be read back per-sheet, so offering them there would be a trap."""

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#tmpl-batch-area").has_class("hidden")

            app.query_one("#tmpl-qr", Checkbox).value = True
            await pilot.pause()
            # QR alone reveals the roster row but not the batch options.
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
    """Both decide how many sheets to print; picking one silently would be worse."""
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
    """One sheet cannot be scrambled against anything — say so rather than
    printing a lone sheet with a form key the user did not ask for."""
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
    """Unscrambled, every sheet is identical — a count would be asking for a
    number that a photocopier answers better. It only means something once the
    sheets differ from one another."""

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            app.query_one("#tmpl-bubble-sheet", Checkbox).value = True
            await pilot.pause()
            # The scramble checkbox is offered; the count is not.
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
    """The hidden-field rule, one level deeper: a number typed under scrambling
    must not survive turning scrambling back off."""
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


# ── AI-grading calibration (Helper Tools → min confidence) ──────────────────


def _stub_grading_cli(monkeypatch) -> dict[str, object]:  # noqa: ANN001
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        setup_screen,
        "run_grading_cli",
        lambda argv, **kwargs: captured.__setitem__("argv", argv),
    )
    monkeypatch.setattr(
        setup_screen, "ensure_model_available", lambda config: None
    )
    return captured


def test_min_confidence_is_omitted_when_none_is_saved(monkeypatch) -> None:  # noqa: ANN001
    """There is no field to touch any more — the CLI always keeps its own
    default unless AI setup has saved one. Kept alongside the new tests below
    because it still names the exact invariant that matters here."""
    captured = _stub_grading_cli(monkeypatch)
    ai_config.save_settings(ai_config.AISettings(grade_written=True))

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
    """The threshold lives in the AI settings file now (`core/ai_config`); the
    CLI reads its own argparse default straight from that file. So the built
    argv must never carry --llm-min-confidence or --llm, whatever value is
    saved — two ways of telling the grader the same thing could disagree."""
    captured = _stub_grading_cli(monkeypatch)
    ai_config.save_settings(
        ai_config.AISettings(min_confidence=0.85, grade_written=True)
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
    """The whole point of the Helper Tools entry: the measured number is saved
    as the AI setting everywhere, not filed into a per-run field — so it
    reaches a later run, a different step, and even a terminal invocation,
    not just the Auto-grade card that happened to be open when it was
    measured."""

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

            # Simulate the grader accepting a recommendation.
            modal.dismiss(0.75)
            await pilot.pause()

            return solutions, submissions, ai_config.load_settings().min_confidence

    solutions, submissions, saved = asyncio.run(run())
    assert solutions == "/tmp/sol.md"
    assert submissions == "/tmp/subs"
    assert saved == 0.75


def test_calibration_accept_marks_the_confidence_measured_and_wants_ai_grading() -> None:
    """Accepting a recommendation is provenance, not just a number: it is what
    `require_calibration` checks for (`ResolvedAI.problems`), and there is no
    checkbox left to say "yes, grade with the AI" any more — so accepting the
    measurement has to say that unambiguously itself."""

    async def run() -> ai_config.AISettings:
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

            return ai_config.load_settings()

    settings = asyncio.run(run())
    assert settings.min_confidence_measured is True
    assert settings.grade_written is True


def test_calibration_refreshes_the_ai_setup_summary() -> None:
    """The Auto-grade step no longer shows a confidence field at all, so the
    only visible confirmation left is the AI setup card's summary line — it
    must actually change, not just the file on disk."""

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
    """Same rule every disclosure in this screen follows, one level up: a
    cancelled tool must not write anything."""

    async def run() -> float | None:
        ai_config.save_settings(ai_config.AISettings(min_confidence=0.6))
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            screen._open_llm_eval()
            await pilot.pause()
            app.screen.dismiss(None)
            await pilot.pause()
            return ai_config.load_settings().min_confidence

    assert asyncio.run(run()) == 0.6


def test_handoff_and_backup_tools_are_present_and_collapsed() -> None:
    """Both are optional utilities, so they must not crowd the pipeline steps."""

    async def run() -> tuple[bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            handoff = app.query_one("#section-handoff", Collapsible)
            backup = app.query_one("#section-backup", Collapsible)
            # Both live below the grader tools, where an occasional utility
            # belongs — the launch card stays the primary action.
            assert app.query_one("#run-handoff-pack", Button)
            assert app.query_one("#run-handoff-merge", Button)
            assert app.query_one("#run-backup", Button)
            return handoff.collapsed, backup.collapsed

    assert asyncio.run(run()) == (True, True)


def test_the_handoff_tool_falls_back_to_the_launch_card_folder() -> None:
    """The common case — share the class you are about to grade — needs no
    second path typed in."""

    async def run() -> str:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#tui-sub-path", Input).value = "/tmp/subs"
            app.query_one("#tui-qa-path", Input).value = "/tmp/qa.md"
            await pilot.pause()
            screen._open_grader_packet()
            await pilot.pause()
            modal = app.screen
            return f"{modal._submissions_path}|{modal._qa_file_path}"

    assert asyncio.run(run()) == "/tmp/subs|/tmp/qa.md"


def test_the_handoff_tool_prefers_its_own_folder_field() -> None:
    async def run() -> str:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#tui-sub-path", Input).value = "/tmp/launch"
            app.query_one("#handoff-submissions", Input).value = "/tmp/other"
            await pilot.pause()
            screen._open_merge()
            await pilot.pause()
            return app.screen._submissions_path

    assert asyncio.run(run()) == "/tmp/other"


def test_opening_a_handoff_tool_with_no_folder_is_refused_not_blank() -> None:
    """Better to say which folder is missing than to open a modal that cannot work."""

    async def run() -> int:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            depth_before = len(app.screen_stack)
            app.query_one(SetupScreen)._open_backup()
            await pilot.pause()
            return len(app.screen_stack) - depth_before

    assert asyncio.run(run()) == 0


# --------------------------------------------------------------------------- #
# AI-assisted transcription (`--llm-responses` / `--llm-escalate` / `--llm-math`)
# --------------------------------------------------------------------------- #


def test_extractor_never_emits_llm_responses_escalate_or_math_flags(monkeypatch) -> None:
    """Whether to transcribe with the AI, escalate only what OCR read badly,
    and read maths as LaTeX are all the saved AI settings now (`read_handwriting`
    / `escalate_only` / `transcribe_math`) — there is no checkbox left on this
    card to build any of the three flags from, whatever the settings say. The
    extractor resolves them for itself, the same way it resolves the model."""
    ai_config.save_settings(
        ai_config.AISettings(
            read_handwriting=True, escalate_only=True, transcribe_math=True
        )
    )
    argv = _run_extractor_argv(monkeypatch, lambda _app: None)
    assert argv is not None
    for flag in ("--llm-responses", "--llm-escalate", "--llm-math"):
        assert flag not in argv
    assert "--math-backend" not in argv


def test_ext_ai_status_names_escalation_and_math(monkeypatch) -> None:  # noqa: ANN001
    """The status line that replaced the three deleted checkboxes states the
    scope of transcription and whether maths becomes LaTeX, and changes with
    the saved settings rather than with anything ticked on this card."""

    async def status() -> str:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return str(app.query_one("#ext-ai-status", Static).render())

    ai_config.save_settings(ai_config.AISettings())
    off = asyncio.run(status())
    assert "AI: off" in off

    ai_config.save_settings(
        ai_config.AISettings(read_handwriting=True, escalate_only=True)
    )
    escalate_on = asyncio.run(status())
    assert "AI: off" not in escalate_on
    assert "OCR read badly" in escalate_on
    assert "LaTeX" not in escalate_on

    ai_config.save_settings(
        ai_config.AISettings(
            read_handwriting=True, escalate_only=False, transcribe_math=True
        )
    )
    math_on = asyncio.run(status())
    assert "every answer" in math_on
    assert "LaTeX" in math_on


# --------------------------------------------------------------------------- #
# Grade-while-extracting (background extraction fold-in, headline entry point)
# --------------------------------------------------------------------------- #


def test_extraction_in_progress_flag_tracks_the_extractor_worker(monkeypatch) -> None:
    """This screen is the one place that can say *with certainty* extraction
    is live (rather than guess from manifest recency) — `extraction_in_progress`
    is what `TuiApp` reads that certainty from. Fires false->true synchronously
    with the button press (before the worker even runs) and back to false the
    moment the worker reports done."""
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
            # True immediately — set on the UI thread before the worker (which
            # may take minutes) is even scheduled.
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

            # The button lives inside the progress area, so it is reachable
            # only once that area is revealed — same lifecycle as the
            # progress bar and status line right next to it.
            app.query_one("#grade-while-extracting", Button)
            assert app.query_one("#ext-progress").has_class("hidden")

            app.query_one("#ext-input", Input).value = "/tmp/scans"
            app.query_one("#ext-output", Input).value = "/tmp/subs"
            screen._run_extractor()
            # Checked before the next `await` on purpose: the fake extractor
            # is a no-op and can finish (and re-hide the progress area) the
            # instant control yields back to the event loop, so this has to
            # observe the state `_run_extractor` sets synchronously, before
            # the worker gets a chance to run.
            assert not app.query_one("#ext-progress").has_class("hidden")

            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.query_one("#ext-progress").has_class("hidden")

    asyncio.run(run())


def test_grade_while_extracting_requires_an_answer_key_path() -> None:
    """The button reads the answer key from the launch card above — a real
    setup mistake ("forgot to point at the QA file") must read as that, not
    as a crash or a silent no-op."""

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
    """The message it posts carries exactly what was in the two fields —
    ``TuiApp.on_setup_screen_grade_while_extracting_requested`` (tested in
    ``test_tui_app.py``) is what actually opens the workspace from it. Spies
    on the message's own constructor rather than intercepting Textual's
    dispatch, so the assertion doesn't depend on how many message-pump ticks
    delivery happens to take.
    """
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
            app.query_one("#tui-qa-path", Input).value = "/tmp/qa.md"
            app.query_one("#ext-output", Input).value = "/tmp/subs"
            screen._open_grade_while_extracting()
            await pilot.pause()

    SetupScreen.GradeWhileExtractingRequested.__init__ = _spy  # type: ignore[method-assign]
    try:
        asyncio.run(run())
    finally:
        SetupScreen.GradeWhileExtractingRequested.__init__ = original  # type: ignore[method-assign]

    assert events == [("/tmp/qa.md", "/tmp/subs")]


# --------------------------------------------------------------------------- #
# Photo import (Helper Tools) — the on-ramp for a class with no scanner
# --------------------------------------------------------------------------- #


def _stub_photo_import(monkeypatch, report=None, error=None) -> dict[str, object]:  # noqa: ANN001
    """Intercept `import_photos` and record the arguments the form built.

    The real thing warps megapixel photos; this is about the form, not the
    geometry (which `tests/unit/core/submissions/test_photo_intake.py` covers).
    """
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
    """Fill the photo-import form via ``fill(app)``, press Import, return the app."""
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
    """An optional on-ramp, not a numbered step: it must not crowd the pipeline."""

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
    """An untouched form must do the thing the CLI does by default."""

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert captured["pages_per_photo_set"] is None
    assert captured["paper"] == "letter"
    assert captured["perspective"] is True


def test_photo_import_refuses_a_non_numeric_page_count(monkeypatch) -> None:  # noqa: ANN001
    """A bad count would otherwise group two students' pages into one PDF —
    a mistake nothing downstream can detect."""

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"
        app.query_one("#photo-pages", Input).value = "three"

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert "input" not in captured  # never reached the importer
    assert any("whole number" in note for note in captured["notes"])


def test_photo_import_requires_both_paths(monkeypatch) -> None:  # noqa: ANN001
    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert "input" not in captured
    assert captured["notes"]


def test_photo_import_points_extract_at_what_it_wrote(monkeypatch) -> None:  # noqa: ANN001
    """Closes the loop: the next step is one click, not a re-typed path."""

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert captured["ext_input"] == "/tmp/split"


def test_photo_import_leaves_an_extract_path_the_user_typed(monkeypatch) -> None:  # noqa: ANN001
    """A path already in the field is the user's, not ours to replace."""

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"
        app.query_one("#ext-input", Input).value = "/tmp/mine"

    captured = _run_photo_form(monkeypatch, fill, report=_photo_report("/tmp/split"))

    assert captured["ext_input"] == "/tmp/mine"


def test_photo_import_reports_skipped_photos_not_only_successes(monkeypatch) -> None:  # noqa: ANN001
    """A skipped photo is a student missing a page — "2 submissions" alone
    would be a half-truth."""

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
    """Its diagnostics name the fix ("HEIC photos need …"), so wrapping them in
    a generic failure would throw away the only actionable part."""
    from opengrader.core.submissions.photo_intake import PhotoIntakeError

    def fill(app) -> None:  # noqa: ANN001
        app.query_one("#photo-input", Input).value = "/tmp/photos"
        app.query_one("#photo-output", Input).value = "/tmp/split"

    captured = _run_photo_form(
        monkeypatch, fill, error=PhotoIntakeError("no images found in /tmp/photos")
    )

    assert any("no images found in /tmp/photos" == note for note in captured["notes"])
    assert captured["ext_input"] == ""


# --------------------------------------------------------------------------- #
# Region map — the Helper Tools entry, and how it reaches Extract
# --------------------------------------------------------------------------- #


def _blank_assignment_pdf(tmp_path) -> str:  # noqa: ANN001
    """A one-page image-only PDF: an assignment Opengrader did not generate."""
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
    """Better to say which file is missing than to open an editor with no page."""
    notes: list[str] = []

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.notify = lambda message, **kwargs: notes.append(str(message))
            app.query_one(SetupScreen)._open_region_map()
            await pilot.pause()
            # No modal was pushed — the base screen is still the only one.
            assert len(app.screen_stack) == 1

    asyncio.run(run())
    assert notes


def test_a_saved_region_map_lands_in_the_extract_step(tmp_path) -> None:  # noqa: ANN001
    """The whole point of the Helper Tools entry: the map the editor wrote is
    the one extraction uses, without the instructor retyping a filename the
    editor chose for them."""
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
    # Ticked for them, and the mutually-exclusive option turned off with it.
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
    # The extractor rejects the pair outright, so the form must never send both.
    assert "--blank-template" not in argv


def test_the_two_layout_sources_untick_each_other() -> None:
    """They are two answers to "where are the boxes?", and the extractor refuses
    both at once — so the form must not let a user build that command."""

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
    """Silently dropping the flag would extract with OCR layout detection and
    produce plausible-looking crops of the wrong parts of the page."""
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


# --------------------------------------------------------------------------- #
# Grading policy — the on-ramp for randomized traversal order
# --------------------------------------------------------------------------- #


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
    """A policy built from a subset of the form would silently reset what it
    left out — locking anonymity would turn randomized order back off."""
    from opengrader.core.policy import GradingPolicy, read_policy, write_policy

    write_policy(
        tmp_path, GradingPolicy(mode="solo", anonymity_locked=False, traversal_shuffle=True)
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            # Selecting the folder loads its current state into the form, which
            # is what makes writing every field back safe.
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


# --------------------------------------------------------------------------- #
# "Run the whole pipeline" — the one-shot front end over core/pipeline.py.
# `run_pipeline`/`plan_pipeline` are monkeypatched at the point this screen
# imports them (`setup_screen.run_pipeline` / `setup_screen.plan_pipeline`),
# the same way `run_extractor_cli` etc. are patched above — a real pipeline
# run is never exercised from these tests.
# --------------------------------------------------------------------------- #


def test_the_pipeline_has_a_section_of_its_own_above_the_numbered_steps() -> None:
    """DOM order matters here: the whole-pipeline run is its **own section**,
    between "Start grading" and the numbered steps.

    Both earlier shapes were wrong in opposite directions. As a bare card above
    the first heading it read as a different *kind* of thing from everything
    below it; folded into the numbered list as "step 0" it buried the
    recommended path inside the list of alternatives to it. A section of its own
    says both true things at once — this is how you run an assignment, and it is
    made of the steps underneath.
    """

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
    """A section that styles itself reads as a different kind of thing.

    The page has three sections and they must be visually identical — that
    sameness is the entire reason the pipeline moved out of a bespoke card.
    One shared class rather than one CSS rule per id is what keeps a fourth
    section from quietly looking different.
    """

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
    """A blank required field must notify and refuse to start the worker —
    never a silent no-op and never a background-thread crash."""

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
            # `_run_pipeline` only flips this flag once validation has passed
            # and a worker is about to start, so it staying false is the proof
            # no worker was launched.
            assert getattr(screen, "extraction_in_progress", False) is False

    asyncio.run(run())


def test_pipeline_checkboxes_reveal_their_dependent_rows() -> None:
    """The two path-bearing disclosures reveal (and re-hide) exactly their own
    row, independently of each other. There is no "Let the AI help" checkbox
    left at all — which model, host and confidence to use, and whether the AI
    is even in play, is the AI setup Helper Tool's job now, stated on
    `#pipe-ai-status` rather than asked for again on this card."""

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
            # The other stays revealed — unticking roster must not touch it.
            assert not app.query_one("#pipe-template-row").has_class("hidden")

    asyncio.run(run())


def test_pipeline_request_maps_every_control(tmp_path) -> None:  # noqa: ANN001
    """Every field the form can hold reaches the `PipelineRequest` it builds,
    with every path-bearing disclosure switched on. The AI's read/grade
    toggles, host and auto-start are the saved AI settings now, not fields on
    this form — so they must come from `ai_config.resolve()`, matching what
    was saved here, rather than from any removed pipe-llm-* control. The
    model names are deliberately left blank (`LlmOptions`' own default):
    naming them again here would be a second channel for what the settings
    file already answers."""
    ai_config.save_settings(
        ai_config.AISettings(
            vision_model="llava",
            text_model="llama3.1",
            host="http://box:9999",
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
    # Model names are left for each tool's own client to resolve — never a
    # second channel for the one the settings file already answers.
    assert request.llm.vision_model == ""
    assert request.llm.text_model == ""
    assert request.llm.host == "http://box:9999"
    assert request.llm.auto_ollama is True
    # auto_grade follows the saved setting too, not the dataclass default.
    assert request.auto_grade is False


def test_pipeline_request_defaults_when_every_disclosure_is_off(tmp_path) -> None:  # noqa: ANN001
    """With every path-bearing checkbox left unticked and no AI settings ever
    saved, the optional path/roster/template fields are all `None`/default —
    matching the rule the Ollama-advanced disclosures already followed — but
    `llm` and `auto_grade` still reflect whatever `ai_config.resolve()`
    returns for an untouched settings file, because there is no checkbox left
    to gate them on any more."""

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
    resolved = ai_config.resolve()
    assert resolved.read_handwriting is False  # nothing was ever saved
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
    """The pipeline's `LlmOptions` has two independent switches — reading
    handwriting and grading written answers — and a professor's answers can
    genuinely differ (see `LlmOptions`' own docstring). Prove the two halves
    of the saved AI settings reach the built request independently, not as
    one combined "AI on/off" bit."""
    ai_config.save_settings(
        ai_config.AISettings(read_handwriting=True, grade_written=False)
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


def test_pipeline_blocked_report_does_not_open_the_grading_workspace(monkeypatch) -> None:
    """A BLOCKED outcome (the scan pre-flight found something high-severity)
    must never post `LaunchRequested` — opening the workspace on it would be
    exactly the silent-bad-grading failure the pre-flight exists to prevent."""
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
            # A blocked run is not still "in progress" — the worker ended.
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
    """A clean, ready-to-grade report posts `LaunchRequested` with the qa path
    from the form and the report's own submissions directory."""
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
            # Off, so this test is about the end-of-run launch specifically —
            # the early-launch path has its own tests below.
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
    """The ASAP checkbox needs an answer key to open anything with. Missing
    one must not silently behave as if the box were unticked — it has to say
    so, and the run still has to complete normally."""
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
            # #pipe-qa left blank; the ASAP checkbox defaults to on.
            screen._run_pipeline()
            await app.workers.wait_for_complete()
            await pilot.pause()
            return notices

    notices = asyncio.run(run())
    assert any("answer key" in message.lower() for message, _severity in notices)


def test_pipeline_preview_calls_plan_pipeline_and_writes_nothing(
    monkeypatch, tmp_path
) -> None:
    """Preview is the TUI's `--dry-run`: it shows the plan and touches
    nothing on disk — `run_pipeline` must never be called."""
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
    # Nothing was written — the class folder plan_pipeline was asked about
    # was never created.
    assert not out_dir.exists()


# --------------------------------------------------------------------------- #
# AI setup — the one place the AI is configured (core/ai_config,
# AISettingsScreen). Split/Extract/Answer-key/Auto-grade/the pipeline used to
# each ask their own "which model / which host / how confident" question;
# that entire family of per-step disclosures was deleted in favor of this one
# Helper Tool, which every CLI's own argparse defaults now resolve against.
# --------------------------------------------------------------------------- #


def test_no_user_facing_text_says_cohort() -> None:
    """"Cohort" was renamed to "class" everywhere a user reads it. This walks
    the *mounted* screen's own text — Statics (including the AI summary line,
    which is built at runtime, not a literal), checkbox/radio/button labels
    and Collapsible titles — rather than grepping source, so a string that is
    assembled rather than written out catches the same rename mistake a grep
    would miss.
    """

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
    """The environment check tells you the AI is missing; this is where you
    say which one to use — so it sits directly after "Check this computer",
    and it is an optional Helper Tool like the rest of that list, not a
    numbered step, so it starts collapsed."""

    async def run() -> bool:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.query_one("#run-ai-settings", Button)
            return app.query_one("#section-ai", Collapsible).collapsed

    assert asyncio.run(run()) is True


def test_ai_setup_button_pushes_the_ai_settings_screen() -> None:
    """The Helper Tool's button is wired to the one settings screen — the only
    door into configuring the AI now that every step's own model/host
    disclosure is gone."""

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
    """The closed AI setup card states what is actually resolved — the model
    saved here must show up there, not a hard-coded label — so "which model am
    I using?" needs no click, the question the four removed per-section
    disclosures used to answer badly, by asking it again every time. Both
    jobs have to be switched on for their models to appear at all: naming a
    model while the job that would use it is off would be misleading."""
    ai_config.save_settings(
        ai_config.AISettings(
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
    assert "Ollama" in summary  # the resolved provider's label


def test_ai_summary_names_no_models_when_every_feature_is_off() -> None:
    """A summary that named two configured models while every feature toggle
    was off would say something true and misleading at once — the whole
    point of extending it to state what the AI *does*, not only what it is
    pointed at."""
    ai_config.save_settings(
        ai_config.AISettings(text_model="llama3.1", vision_model="llava")
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


# --------------------------------------------------------------------------- #
# Per-card AI status lines (replacing the six deleted AI checkboxes)
# --------------------------------------------------------------------------- #


async def _status_text(widget_id: str) -> str:
    app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        return str(app.query_one(f"#{widget_id}", Static).render())


def test_split_ai_status_reflects_the_read_handwriting_setting() -> None:
    """Split's deleted AI checkbox is replaced by one status line naming what
    the saved setting means for it, not a control asking the same question."""
    off = asyncio.run(_status_text("split-ai-status"))
    assert "AI: off" in off
    assert "page numbers are read from the text layer" in off
    assert "AI setup" in off

    ai_config.save_settings(ai_config.AISettings(read_handwriting=True))
    on = asyncio.run(_status_text("split-ai-status"))
    assert "AI: off" not in on
    assert "handwriting model" in on


def test_ak_ai_status_reflects_the_read_handwriting_setting() -> None:
    """Answer key's deleted AI checkbox, same rule as Split's."""
    off = asyncio.run(_status_text("ak-ai-status"))
    assert "AI: off" in off

    ai_config.save_settings(ai_config.AISettings(read_handwriting=True))
    on = asyncio.run(_status_text("ak-ai-status"))
    assert "AI: off" not in on
    assert "handwriting model" in on


def test_grade_ai_status_names_model_confidence_and_calibration_requirement() -> None:
    """Auto-grade's deleted checkbox is replaced by a line naming the model,
    the confidence gate, and whether a measured confidence is required."""
    off = asyncio.run(_status_text("grade-ai-status"))
    assert "AI: off" in off
    assert "only answers your rubric can settle outright" in off

    ai_config.save_settings(
        ai_config.AISettings(
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

    ai_config.save_settings(
        ai_config.AISettings(
            text_model="llama3.1", grade_written=True, require_calibration=True
        )
    )
    calibrated = asyncio.run(_status_text("grade-ai-status"))
    assert "a measured confidence is required" in calibrated


def test_pipe_ai_status_states_both_jobs_and_auto_grade() -> None:
    """The pipeline card's deleted checkbox covered one combined "AI on" bit;
    its replacement states both jobs and the auto-grade setting separately."""
    off = asyncio.run(_status_text("pipe-ai-status"))
    assert "does not read handwriting" in off
    assert "does not grade written answers" in off
    assert "auto-grade runs automatically" in off

    ai_config.save_settings(
        ai_config.AISettings(read_handwriting=True, grade_written=False, auto_grade=False)
    )
    mixed = asyncio.run(_status_text("pipe-ai-status"))
    assert "reads hard-to-read handwriting" in mixed
    assert "does not grade written answers" in mixed
    assert "does not run automatically" in mixed


def test_refresh_ai_displays_updates_every_status_line_and_the_summary() -> None:
    """`_refresh_ai_displays` is the one method every AI-saving path calls —
    prove it actually reaches all six displays, not just the summary it grew
    out of."""

    async def run() -> tuple[str, ...]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            ai_config.save_settings(
                ai_config.AISettings(
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
    """The model, host, confidence threshold and every feature toggle
    (transcribe, escalate, math, grade) are all decided once in AI setup now;
    every CLI resolves its own argparse default from that file. If any step
    built one of these flags itself again, two settings would exist for the
    same question and could disagree — so this drives every AI-touching path
    (split, extract, answer-key, auto-grade) in one place, with every toggle
    and a confidence saved, rather than trusting each section's own (already
    covered) argv test not to regress on its own. None of the six AI
    checkboxes these steps used to carry exist any more — the AI settings
    file alone decides whether each step uses the AI at all.
    """
    captured: dict[str, list[str]] = {}

    def _capture(name: str):
        def _fn(argv, **kwargs) -> None:  # noqa: ANN001
            captured[name] = list(argv)

        return _fn

    monkeypatch.setattr(setup_screen, "run_splitter_cli", _capture("split"))
    monkeypatch.setattr(setup_screen, "run_extractor_cli", _capture("extract"))
    monkeypatch.setattr(setup_screen, "run_answer_key_cli", _capture("answer_key"))
    monkeypatch.setattr(setup_screen, "run_grading_cli", _capture("grade"))
    monkeypatch.setattr(setup_screen, "ensure_model_available", lambda config: None)

    ai_config.save_settings(
        ai_config.AISettings(
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
    """Pressing Run Auto-Grader must never freeze the app on a network call.

    `ensure_model_available` is an HTTP request carrying the configured timeout
    (120 s by default). It used to run on the UI thread, before the worker
    started — which was survivable only while the host was always localhost,
    where a dead server refuses the connection instantly. AI setup now lets a
    teacher type any host, and a typo'd or firewalled one does not refuse: it
    black-holes the request, and the whole TUI would sit frozen for the full
    timeout with no progress bar and no way to cancel.

    Everything the probe's decision needs is thread-safe (`grade_written` is
    read on the UI thread before the worker starts; the settings are a file
    read), so it belongs on the worker.
    """
    import threading

    seen: dict[str, object] = {}

    def _probe(config):  # noqa: ANN001, ANN202 - a stub for a thread check
        seen["on_main_thread"] = (
            threading.current_thread() is threading.main_thread()
        )

    monkeypatch.setattr(setup_screen, "run_grading_cli", lambda argv, **kwargs: None)
    monkeypatch.setattr(setup_screen, "ensure_model_available", _probe)
    ai_config.save_settings(ai_config.AISettings(grade_written=True))

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
    """No AI, no network call — not even to find out whether a server is there.

    The probe exists only to decide whether `--auto-ollama` is needed. An
    auto-grade run over exact matches alone must not touch the network at all,
    or an offline machine pays a timeout for a feature it did not ask for.
    """
    calls: list[object] = []

    monkeypatch.setattr(setup_screen, "run_grading_cli", lambda argv, **kwargs: None)
    monkeypatch.setattr(
        setup_screen, "ensure_model_available", lambda config: calls.append(config)
    )

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#grade-solutions", Input).value = "/tmp/sol.md"
            app.query_one("#grade-input", Input).value = "/tmp/subs"
            # No AI settings were ever saved, so `grade_written` defaults off.
            screen._run_grading()
            await app.workers.wait_for_complete()
            await pilot.pause()

    asyncio.run(run())

    assert calls == []
