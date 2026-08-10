"""Guards on the help screen's text.

Descriptions are rendered with ``markup=False`` — deliberately, because they
contain literal square brackets that Rich would otherwise eat or choke on
(``[ ]``/``[*]`` checkbox syntax, ``[AI · NN%]`` grade tags, ``q1-q3`` ranges).
The cost of that decision is that any Rich style tag written into a description
is displayed to the user verbatim, which is what these tests catch.
"""

from __future__ import annotations

import re

from opengrader.tui.ui.screens import help_screen

# Rich style tags — the ones that would render as literal text in a
# markup=False Static. Deliberately a fixed list rather than "anything in
# brackets": the descriptions legitimately contain [ ], [*] and [AI · NN%].
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
    """Guard the guard: a rename that emptied this would silently pass."""
    tables = _row_tables()
    assert len(tables) >= 6
    assert sum(len(rows) for rows in tables.values()) > 50


def test_no_description_contains_rich_style_markup() -> None:
    """`markup=False` means a [b] tag is shown to the user, not applied."""
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
    """The key column is markup=False for the same reason."""
    offenders = [
        (name, key)
        for name, rows in _row_tables().items()
        for key, _ in rows
        if _STYLE_TAG.search(key)
    ]
    assert offenders == []


def test_literal_brackets_that_belong_there_are_left_alone() -> None:
    """The flip side: markup=False exists so these survive, and the tag check
    above must not tempt anyone into "fixing" them."""
    joined = " ".join(
        description for rows in _row_tables().values() for _, description in rows
    )
    assert "[AI · NN%]" in joined  # the AI grade tag
    assert "**Outcome**" in joined  # QA markdown syntax, shown as authored


def test_help_documents_the_handoff_and_backup_tools() -> None:
    """The TOOLS & SCRIPTS entries — what the feature is and its CLI names."""
    from opengrader.tui.ui.screens.help_screen import _SCRIPT_ROWS

    titles = {title for title, _ in _SCRIPT_ROWS}
    assert "Share with a grader" in titles
    assert "Backup & integrity" in titles

    by_title = dict(_SCRIPT_ROWS)
    # The two things that are useless if not written down: which command undoes
    # a merge, and that the handoff key is what makes one possible.
    assert "grade-restore --without-actor merge:" in by_title["Share with a grader"]
    assert "grade-merge" in by_title["Share with a grader"]
    assert "grade-backup" in by_title["Backup & integrity"]


def test_sharing_hotkeys_have_their_own_section_not_the_audit_one() -> None:
    """They are not reports: each one writes a folder, an archive, or grades.

    Filing them under REPORTS & AUDITS made a grader looking for "how do I give
    this to my TA" search a list of audits for it.
    """
    from opengrader.tui.ui.screens.help_screen import _REPORT_ROWS, _SHARING_ROWS

    # shift+L (release) joins them for the same reason: handing grades back
    # writes a record, it does not answer a question and change nothing.
    sharing_keys = [key for key, _ in _SHARING_ROWS]
    assert sharing_keys == ["shift+E", "shift+M", "shift+B", "shift+L"]

    report_keys = {key for key, _ in _REPORT_ROWS}
    assert not report_keys & set(sharing_keys)


def test_the_sharing_section_is_actually_rendered() -> None:
    """A section header nobody sees is not documentation."""
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


# --------------------------------------------------------------------------- #
# Staying up to date — the guards that catch a help screen drifting from the app
# --------------------------------------------------------------------------- #


def _help_text() -> str:
    return " ".join(
        f"{key} {description}"
        for rows in _row_tables().values()
        for key, description in rows
    )


def test_every_command_line_tool_is_documented() -> None:
    """A tool nobody can find is a tool that does not exist.

    This is the check that would have caught `photo-import` and `region-map`
    shipping with no help entry: the console scripts are the app's own list of
    what it can do, so the help has to name all of them. `tui-dev` is exempt —
    it is the devtools launcher, not something a grader runs.
    """
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
    """The other direction: a renamed or removed CLI leaves a "CLI: ..." line
    behind that sends the reader to a command that errors out."""
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
    """The key columns must match what the app actually listens for."""
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
            # An uppercase letter *is* shift+letter; the help writes the form a
            # reader can act on (see the keymap note in input_controller.py).
            if len(key) == 1 and key.isupper():
                real.add(f"shift+{key}")

    # Symbols and mouse actions the help spells the way a person would.
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
    """The TOOLS & SCRIPTS entries point back at the pop-up that shows the same
    thing, and those pointers rot silently.

    They shipped as bare lowercase letters ("the v pop-up"), which stopped being
    true when the reports moved to shift+letter — and by then `v` meant
    something else entirely on the grade screen, so the help was sending readers
    to the wrong feature rather than merely to a dead key.
    """
    import re

    from opengrader.tui.ui.screens.help_screen import _SCRIPT_ROWS

    report_letters = "ABCDEFGHILMPRSUVW"
    stale = [
        (title, match.group(0))
        for title, description in _SCRIPT_ROWS
        for match in re.finditer(r"the ([a-zA-Z]) pop-up", description)
        if match.group(1).upper() in report_letters and match.group(1).islower()
        # `c` (score curve) and `b` (return bundle) really are lowercase, on the
        # QA picker — those rows say so explicitly.
        and "QA picker" not in description
    ]
    assert stale == [], f"stale lowercase pop-up references: {stale}"


def test_help_documents_the_scan_intake_and_region_map_tools() -> None:
    """The two on-ramps for a class the pipeline did not produce itself."""
    from opengrader.tui.ui.screens.help_screen import _SCRIPT_ROWS

    by_title = dict(_SCRIPT_ROWS)
    assert "Photo import" in by_title
    assert "Region map" in by_title

    # The facts that are useless if not written down.
    assert "photo-import" in by_title["Photo import"]
    assert "HEIC" in by_title["Photo import"]
    assert "region-map" in by_title["Region map"]
    assert "--region-map" in by_title["Region map"]


def test_help_documents_grading_while_extraction_runs() -> None:
    """Discoverable only from the Extract step — nothing else would tell a
    grader they need not wait out a 200-student extraction.

    The button is named by its **real** label. A help entry that quotes a
    control that does not exist is worse than no entry: the reader hunts for
    "Grade while extracting", never finds it, and concludes the feature is
    gone. The button reads "Grade what's ready →" (setup_screen.py,
    ``#grade-while-extracting``).
    """
    from opengrader.tui.ui.screens.help_screen import _PIPELINE_ROWS

    extract = dict(_PIPELINE_ROWS)["2 · Extract"]
    assert "Grade what's ready" in extract


def test_help_documents_randomized_traversal_order() -> None:
    """It is opt-in and lives in a policy file, so the help is where a teacher
    finds out it exists at all."""
    from opengrader.tui.ui.screens.help_screen import _SCRIPT_ROWS

    policy = dict(_SCRIPT_ROWS)["Grading policy"]
    assert "Randomize student order per grader" in policy
    # The property that makes it usable rather than disorienting.
    assert "stable" in policy


# ─────────────────────────────────────────────────────────────────────────────
# Accuracy guards.
#
# The rows above check individual facts a reader would miss if they went
# missing. The three below are the general form of the same worry: help text
# drifts silently, because nothing breaks when a screen is redesigned around
# it. Each of these derives its expectation from the code rather than restating
# it, so the *next* redesign fails a test instead of shipping a wrong reference.
# ─────────────────────────────────────────────────────────────────────────────


def _all_descriptions() -> str:
    return " ".join(
        description for rows in _row_tables().values() for _, description in rows
    )


def test_every_cli_the_help_names_is_a_real_command() -> None:
    """A ``CLI: name`` that no longer exists sends the reader to a dead end.

    The help is the only complete list of these tools, so a renamed or dropped
    console script has to fail here — there is nowhere else a reader would find
    out. Checked against ``pyproject.toml`` rather than a hand-kept list, so
    adding an entry point cannot make this test stale.
    """
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
    """A quoted button/checkbox label must be one the user can actually find.

    This is the failure that made the AI rework worth guarding: the help told
    people to tick "Use a specific model & host (advanced)" for months after
    that disclosure was deleted, and nothing failed. A reader hunting for a
    control that isn't there concludes the feature was removed.

    Only capitalized single-quoted phrases are checked — the descriptions also
    quote CLI fragments and file names, which are covered by the test above.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    ui_source = "".join(
        path.read_text(encoding="utf-8")
        for path in (root / "src" / "opengrader" / "tui").rglob("*.py")
    )
    # Provider labels are UI text too, but they live with the provider records
    # rather than in a screen (`core/ai_config`), which is the whole point of
    # the registry — a screen never spells a provider's name.
    ui_source += (root / "src" / "opengrader" / "core" / "ai_config.py").read_text(
        encoding="utf-8"
    )

    quoted = {
        phrase
        for phrase in re.findall(r"'([A-Z][^']{4,60})'", _all_descriptions())
        # `Outcome CLO3` / `Outcome ABET-2` are answer-key bullet syntax the
        # instructor types into a QA file, not controls in any screen.
        if not phrase.startswith("Outcome ")
    }

    assert quoted, "the help stopped quoting any control at all"
    missing = sorted(phrase for phrase in quoted if phrase not in ui_source)
    assert missing == [], f"help quotes controls that no longer exist: {missing}"


def test_the_help_says_class_not_cohort() -> None:
    """"Cohort" is a word teachers do not use for their own students.

    It was purged from the start-up screen; the help is the other place a
    teacher reads Opengrader's own vocabulary, and one surface saying "class"
    while the other says "cohort" is how a tool teaches two names for one thing.
    """
    assert "cohort" not in _all_descriptions().lower()


def test_the_ai_section_points_at_one_setup_place() -> None:
    """The AI is configured once, and the help must not describe the old shape.

    Four steps used to ask for a model and a host of their own. Those
    disclosures were deleted precisely because they were four copies of one
    answer; help text that still sends a reader into a step to pick a model
    describes a screen that no longer exists.
    """
    from opengrader.tui.ui.screens.help_screen import _LOCAL_AI_ROWS

    rows = dict(_LOCAL_AI_ROWS)
    joined = " ".join(rows.values())

    assert "Where you set it up" in rows
    assert "AI setup" in joined
    assert "Helper Tools" in joined
    # The removed design, in the words the old help used for it.
    assert "advanced" not in joined.lower()
    assert "model & host" not in joined


def test_the_ai_section_documents_the_settings_file_and_precedence() -> None:
    """Where the settings live, and what beats what, are undiscoverable facts.

    Nothing in the UI can tell a user that ``OPENGRADER_AI_*`` outranks the
    saved file, or that the file is deliberately outside any assignment folder
    — but both matter the moment someone scripts a run or hands a folder to a
    TA, so the help is where they have to be written down.
    """
    from opengrader.tui.ui.screens.help_screen import _LOCAL_AI_ROWS

    joined = " ".join(dict(_LOCAL_AI_ROWS).values())

    assert "ai.json" in joined
    assert "OPENGRADER_AI_" in joined
    assert "OPENGRADER_AI_CONFIG" in joined


def test_the_ai_section_documents_the_request_timeout() -> None:
    """The timeout's symptom is invisible, so the help has to name it.

    A model that runs out of time does not surface an error: the answer comes
    back empty, an hour into an extraction, and the teacher has no reason to
    connect that to a number they have never seen. The setting was unreachable
    from the TUI until 2026-08-10; now that it has a box, the help is what
    stops it being a box nobody understands — so it must say both what goes
    wrong and that Test connection measures the right value rather than
    leaving it to be guessed.
    """
    from opengrader.tui.ui.screens.help_screen import _LOCAL_AI_ROWS

    rows = dict(_LOCAL_AI_ROWS)
    joined = " ".join(rows.values()).lower()

    assert "How long to wait for the AI" in rows
    # The symptom, not just the mechanism — this is the whole reason the row
    # exists, and a rewrite that dropped it would leave the setting mysterious.
    assert "empty" in joined
    assert "test connection" in joined


def test_the_ai_section_states_where_student_work_goes() -> None:
    """Offline-first is a promise, so the help states it as a fact.

    The provider is now a choice rather than an assumption, which makes "does
    my students' handwriting leave this machine?" a real question. The answer
    for every provider shipped today is no, and the help says so plainly rather
    than leaving it to be inferred from the word "local".
    """
    from opengrader.tui.ui.screens.help_screen import _LOCAL_AI_ROWS

    joined = " ".join(dict(_LOCAL_AI_ROWS).values()).lower()

    assert "leaves" in joined or "leave" in joined
    assert "offline" in joined
