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


def test_the_help_screen_has_row_tables_to_check() -> None:
    tables = _row_tables()
    assert len(tables) >= 6
    assert sum(len(rows) for rows in tables.values()) > 50


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


def test_literal_brackets_that_belong_there_are_left_alone() -> None:
    joined = " ".join(
        description for rows in _row_tables().values() for _, description in rows
    )
    assert "[AI · NN%]" in joined
    assert "**Outcome**" in joined


def test_help_documents_the_handoff_and_backup_tools() -> None:
    from opengrader.tui.ui.screens.help_screen import _SCRIPT_ROWS

    titles = {title for title, _ in _SCRIPT_ROWS}
    assert "Share with a grader" in titles
    assert "Backup & integrity" in titles

    by_title = dict(_SCRIPT_ROWS)
    assert "grade-restore --without-actor merge:" in by_title["Share with a grader"]
    assert "grade-merge" in by_title["Share with a grader"]
    assert "grade-backup" in by_title["Backup & integrity"]


def test_sharing_hotkeys_have_their_own_section_not_the_audit_one() -> None:
    from opengrader.tui.ui.screens.help_screen import _REPORT_ROWS, _SHARING_ROWS

    sharing_keys = [key for key, _ in _SHARING_ROWS]
    assert sharing_keys == ["shift+E", "shift+M", "shift+B", "shift+L"]

    report_keys = {key for key, _ in _REPORT_ROWS}
    assert not report_keys & set(sharing_keys)


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


def test_report_cross_references_use_the_shift_form() -> None:
    import re

    from opengrader.tui.ui.screens.help_screen import _SCRIPT_ROWS

    report_letters = "ABCDEFGHILMPRSUVW"
    stale = [
        (title, match.group(0))
        for title, description in _SCRIPT_ROWS
        for match in re.finditer(r"the ([a-zA-Z]) pop-up", description)
        if match.group(1).upper() in report_letters and match.group(1).islower()
        and "QA picker" not in description
    ]
    assert stale == [], f"stale lowercase pop-up references: {stale}"


def test_help_documents_the_scan_intake_and_region_map_tools() -> None:
    from opengrader.tui.ui.screens.help_screen import _SCRIPT_ROWS

    by_title = dict(_SCRIPT_ROWS)
    assert "Photo import" in by_title
    assert "Region map" in by_title

    assert "photo-import" in by_title["Photo import"]
    assert "HEIC" in by_title["Photo import"]
    assert "region-map" in by_title["Region map"]
    assert "--region-map" in by_title["Region map"]


def test_help_documents_grading_while_extraction_runs() -> None:
    from opengrader.tui.ui.screens.help_screen import _PIPELINE_ROWS

    extract = dict(_PIPELINE_ROWS)["2 · Extract"]
    assert "Grade what's ready" in extract


def test_help_documents_randomized_traversal_order() -> None:
    from opengrader.tui.ui.screens.help_screen import _SCRIPT_ROWS

    policy = dict(_SCRIPT_ROWS)["Grading policy"]
    assert "Randomize student order per grader" in policy
    assert "stable" in policy


def _all_descriptions() -> str:
    return " ".join(
        description for rows in _row_tables().values() for _, description in rows
    )


def test_every_cli_the_help_names_is_a_real_command() -> None:
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    scripts = set(
        tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
            "scripts"
        ]
    )
    named = set(re.findall(r"CLI: ([a-z0-9-]+)", _all_descriptions()))

    assert named, "the help stopped naming any CLI at all"
    assert named - scripts == set(), f"help names commands that do not exist: {named - scripts}"


def test_every_control_the_help_quotes_exists_in_the_ui() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    ui_source = "".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src" / "opengrader" / "tui").rglob("*.py")
    )
    ui_source += (root / "src" / "opengrader" / "core" / "ai_config" / "__init__.py").read_text(
        encoding="utf-8"
    )

    quoted = {
        phrase
        for phrase in re.findall(r"'([A-Z][^']{4,60})'", _all_descriptions())
        if not phrase.startswith("Outcome ")
    }

    assert quoted, "the help stopped quoting any control at all"
    missing = sorted(phrase for phrase in quoted if phrase not in ui_source)
    assert missing == [], f"help quotes controls that no longer exist: {missing}"


def test_the_help_says_class_not_cohort() -> None:
    assert "cohort" not in _all_descriptions().lower()


def test_the_ai_section_points_at_one_setup_place() -> None:
    from opengrader.tui.ui.screens.help_screen import _LOCAL_AI_ROWS

    rows = dict(_LOCAL_AI_ROWS)
    joined = " ".join(rows.values())

    assert "Where you set it up" in rows
    assert "AI setup" in joined
    assert "Helper Tools" in joined
    assert "advanced" not in joined.lower()
    assert "model & host" not in joined


def test_the_ai_section_documents_the_settings_file_and_precedence() -> None:
    from opengrader.tui.ui.screens.help_screen import _LOCAL_AI_ROWS

    joined = " ".join(dict(_LOCAL_AI_ROWS).values())

    assert "ai.json" in joined
    assert "OPENGRADER_AI_" in joined
    assert "OPENGRADER_AI_CONFIG" in joined


def test_the_ai_section_documents_the_request_timeout() -> None:
    from opengrader.tui.ui.screens.help_screen import _LOCAL_AI_ROWS

    rows = dict(_LOCAL_AI_ROWS)
    joined = " ".join(rows.values()).lower()

    assert "How long to wait for the AI" in rows
    assert "empty" in joined
    assert "test connection" in joined


def test_the_ai_section_states_where_student_work_goes() -> None:
    from opengrader.tui.ui.screens.help_screen import _LOCAL_AI_ROWS

    joined = " ".join(dict(_LOCAL_AI_ROWS).values()).lower()

    assert "leaves" in joined or "leave" in joined
    assert "offline" in joined
