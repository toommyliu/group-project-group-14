from __future__ import annotations

import re

from opengrader.tui.ui.screens import help_screen

_STYLE_TAG = re.compile(
    r"\[/?(b|i|u|s|dim|bold|italic|underline|strike|reverse|blink|"
    r"red|green|blue|yellow|cyan|magenta|white|black)\]",
    re.IGNORECASE,
)


def _row_tables() -> dict[str, list[tuple[str, str]]]:
    return {
        name: value
        for name, value in vars(help_screen).items()
        if name.endswith("_ROWS") and isinstance(value, list)
    }


def test_no_description_contains_rich_style_markup() -> None:
    offenders = [
        (name, key, _STYLE_TAG.search(description).group(0))  # type: ignore[union-attr]
        for name, rows in _row_tables().items()
        for key, description in rows
        if _STYLE_TAG.search(description)
    ]
    assert offenders == [], (
        "help descriptions render with markup=False, so these tags would be "
        f"shown literally: {offenders}"
    )


def test_no_key_label_contains_rich_style_markup() -> None:
    offenders = [
        (name, key)
        for name, rows in _row_tables().items()
        for key, _ in rows
        if _STYLE_TAG.search(key)
    ]
    assert offenders == []


def test_the_sharing_section_is_actually_rendered() -> None:
    import asyncio

    from textual.app import App
    from textual.widgets import Static

    from opengrader.tui.ui.screens.help_screen import HelpScreen

    class _Host(App):
        def on_mount(self) -> None:
            self.push_screen(HelpScreen())

    async def run() -> str:
        app = _Host()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return " ".join(
                str(widget.render()) for widget in app.screen.query(Static)
            )

    rendered = asyncio.run(run())
    assert "SHARING & BACKUPS" in rendered
    for key in ("shift+E", "shift+M", "shift+B"):
        assert key in rendered


def _help_text() -> str:
    return " ".join(
        f"{key} {description}"
        for rows in _row_tables().values()
        for key, description in rows
    )


def test_every_command_line_tool_is_documented() -> None:
    from opengrader.cli.main import COMMANDS

    text = _help_text()
    undocumented = sorted(
        spec.name for spec in COMMANDS if spec.name not in text
    )
    assert undocumented == [], f"no help entry names these tools: {undocumented}"


def test_no_help_entry_names_a_tool_that_does_not_exist() -> None:
    import re

    from opengrader.cli.main import find

    named = set()
    for rows in _row_tables().values():
        for _key, description in rows:
            named.update(re.findall(r"CLI: opengrader ([a-z0-9-]+)", description))
    assert sorted(name for name in named if find(name) is None) == []


def test_every_hotkey_in_the_help_is_a_real_binding() -> None:
    from opengrader.tui.controllers.input_controller import INPUT_BINDINGS
    from opengrader.tui.ui.screens.help_screen import (
        _GLOBAL_ROWS,
        _GRADING_ROWS,
        _QA_PICKER_ROWS,
        _REPORT_ROWS,
        _SHARING_ROWS,
    )

    real: set[str] = set()
    for spec in INPUT_BINDINGS:
        for key in spec.keys.split(","):
            real.add(key)
            if len(key) == 1 and key.isupper():
                real.add(f"shift+{key}")
            if key.startswith("f") and key[1:].isdigit():
                real.add(key.upper())  # help writes function keys as F2

    spelled = {"?": "question_mark", "esc": "escape", ".": "full_stop", ",": "comma",
               "↑": "up", "↓": "down", "→": "right", "←": "left"}
    not_a_binding = {"click", "click log row", "1-9", "shift+1-9", "+", "-", "0",
                     "[", "]", "Auto-comment field"}

    unknown = []
    for rows in (_GLOBAL_ROWS, _REPORT_ROWS, _SHARING_ROWS, _QA_PICKER_ROWS, _GRADING_ROWS):
        for key, _description in rows:
            for part in (piece.strip() for piece in key.split("·")):
                if not part or part in not_a_binding:
                    continue
                if spelled.get(part, part) not in real:
                    unknown.append(part)
    assert unknown == [], f"documented but not bound: {unknown}"


def test_every_key_of_the_picker_and_grade_screen_is_in_the_help() -> None:
    # The reverse of the test above: a key bound on the screens a grader
    # uses must have a help row, or ? cannot tell them it exists.
    from opengrader.tui.controllers.input_controller import INPUT_BINDINGS

    documented = {
        part.strip()
        for rows in _row_tables().values()
        for key, _description in rows
        for part in key.split("·")
    }
    written_as = {"question_mark": "?", "escape": "esc", "comma": ",", "full_stop": "."}
    # Alternates a row names in its description rather than its key column
    # ("also ↓ / ↑", "+ · - · 0").
    in_prose = {"down", "up", "right", "left", "shift+tab", "=", "_"}

    missing = []
    for spec in INPUT_BINDINGS:
        for key in spec.keys.split(","):
            if key in in_prose:
                continue
            label = written_as.get(key, key)
            if len(key) == 1 and key.isupper():
                label = f"shift+{key}"
            elif key.startswith("f") and key[1:].isdigit():
                label = key.upper()
            if label not in documented:
                missing.append((key, spec.command.value))
    assert missing == [], f"bound but not in the help: {missing}"


def _open_help(page: str | None):
    import asyncio

    from textual.app import App

    from opengrader.tui.ui.screens.help_screen import HelpScreen

    class _Host(App):
        def on_mount(self) -> None:
            self.push_screen(HelpScreen(page))

    return asyncio, _Host


def _visible_section_titles(screen) -> list[str]:  # noqa: ANN001 - HelpScreen
    return [
        str(block.query_one(".help-section").render())
        for block in screen.query(".help-block")
        if block.display
    ]


def test_help_opened_while_grading_starts_with_the_grading_keys() -> None:
    from opengrader.tui.state import PAGE_GRADE

    asyncio, host = _open_help(PAGE_GRADE)

    async def run() -> list[str]:
        app = host()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return _visible_section_titles(app.screen)

    titles = asyncio.run(run())
    assert titles[0].startswith("GRADING")
    assert "grade screen" in titles[0]
    assert titles[1].startswith("RUBRIC EDITOR")
    # Nothing is dropped, only moved: every other section is still there,
    # each once.
    assert len(titles) == len(help_screen._sections())
    assert sum(t.startswith("GRADING") for t in titles) == 1


def test_help_with_no_page_keeps_the_plain_order() -> None:
    asyncio, host = _open_help(None)

    async def run() -> list[str]:
        app = host()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            return _visible_section_titles(app.screen)

    assert asyncio.run(run()) == [s.title for s in help_screen._sections()]


def test_searching_the_help_keeps_only_matching_rows() -> None:
    from textual.widgets import Input, Static

    asyncio, host = _open_help(None)

    async def run() -> tuple[list[str], list[str], bool, bool]:
        app = host()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.press("slash")
            assert isinstance(app.focused, Input)
            # Typed into the box, so q must not quit and ? must not close.
            await pilot.press(*"rotate")
            await pilot.pause()
            keys = [
                str(row.query(Static).first().render())
                for row in app.screen.query(help_screen._HelpRow)
                if row.display
            ]
            titles = _visible_section_titles(app.screen)
            await pilot.press(*"qq?")
            await pilot.pause()
            empty = app.screen.query_one("#help-empty").display
            still_open = isinstance(app.screen, help_screen.HelpScreen)
            return keys, titles, empty, still_open

    keys, titles, empty, still_open = asyncio.run(run())
    assert keys == [", · ."]
    assert titles == ["GRADING"]
    assert empty is True
    assert still_open


async def _type_search(app, pilot, text: str):  # noqa: ANN001, ANN202
    from textual.widgets import Static

    box = app.screen.query_one("#help-search")
    box.focus()
    box.value = ""
    await pilot.press(*text)
    await pilot.pause()
    matches = app.screen.query_one("#help-key-matches")
    top = [
        (
            str(row.query(Static).first().render()),
            str(row.query(Static).last().render()),
        )
        for row in matches.query(help_screen._HelpRow)
    ]
    return matches.display, top


def test_a_short_search_lists_the_key_then_its_shift_version_first() -> None:
    from opengrader.tui.state import PAGE_GRADE

    asyncio, host = _open_help(PAGE_GRADE)

    async def run():  # noqa: ANN202
        app = host()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            shown, top = await _type_search(app, pilot, "x")
            in_sections = [
                row.key
                for row in app.screen.query(".help-block _HelpRow").results(help_screen._HelpRow)
                if row.display
            ]
            return shown, top, in_sections

    shown, top, in_sections = asyncio.run(run())

    assert shown
    assert [key for key, _ in top] == ["x", "x", "shift+X"]
    # Opened while grading: the grade screen's x comes before the picker's.
    assert top[0][1].startswith("Grade screen: Fix the transcription")
    assert top[1][1].startswith("QA picker:")
    # Listed once: not again in their own sections.
    assert "x" not in in_sections and "shift+X" not in in_sections


def test_a_capital_letter_puts_the_shift_key_first_and_a_digit_finds_the_rubric_keys() -> None:
    asyncio, host = _open_help(None)

    async def run():  # noqa: ANN202
        app = host()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            _, capital = await _type_search(app, pilot, "R")
            _, digit = await _type_search(app, pilot, "3")
            longer_shown, _ = await _type_search(app, pilot, "undo")
            return capital, digit, longer_shown

    capital, digit, longer_shown = asyncio.run(run())

    assert capital[0][0] == "shift+R"
    assert [key for key, _ in digit] == ["1-9", "shift+1-9"]
    assert longer_shown is False  # four letters is a word search
