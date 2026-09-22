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
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    scripts = set(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"])

    text = _help_text()
    undocumented = sorted(
        name for name in scripts - {"tui-dev"} if name not in text
    )
    assert undocumented == [], f"no help entry names these tools: {undocumented}"


def test_no_help_entry_names_a_tool_that_does_not_exist() -> None:
    import re
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    scripts = set(tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["scripts"])

    named = set()
    for rows in _row_tables().values():
        for _key, description in rows:
            named.update(re.findall(r"CLI: ([a-z0-9-]+)", description))
    assert named - scripts == set()


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
