"""`opengrader.cli.main` — the front-door dispatcher, not the tools behind it.

Every one of the thirty subcommands already has its own suite for its own
flags; what is checked here is the thin layer that sits in front of all of
them: that a name (canonical or alias) resolves to the right target, that
`dispatch()` forwards argv and exit codes faithfully without leaking
`sys.argv` state across the call, that importing this module stays cheap
enough to run on every tab completion (see the module's own docstring), and
that the registry never silently drifts from `pyproject.toml`'s
`[project.scripts]` — two lists a person edits by hand, in two different
files, with nothing else keeping them in sync.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from collections import Counter
from pathlib import Path

import pytest
from click.testing import CliRunner

from opengrader.cli import qa_file as qa_file_module
from opengrader.cli.main import (
    COMMANDS,
    EXIT_INTERRUPTED,
    PROGRAM_NAME,
    SECTION_ORDER,
    cli,
    dispatch,
    find,
)

PYPROJECT_PATH = Path(__file__).resolve().parents[3] / "pyproject.toml"


# --------------------------------------------------------------------------- #
# 1. Registry <-> pyproject never drift
# --------------------------------------------------------------------------- #


def test_every_pyproject_script_maps_onto_a_registry_row() -> None:
    """A new tool that gets a `[project.scripts]` entry but no `Subcommand`
    row would be installable and runnable standalone yet invisible from
    `opengrader --help` — exactly the kind of drift this test exists to
    catch, since the two lists live in different files and nothing else
    checks them against one another.
    """
    scripts = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]["scripts"]

    # `opengrader` is the dispatcher's own console-script entry point, not one
    # of the tools it dispatches to, so it is excluded rather than expected to
    # appear as a Subcommand.target.
    legacy_scripts = {name: target for name, target in scripts.items() if name != "opengrader"}

    registry_targets = {spec.target for spec in COMMANDS}
    missing_targets = sorted(
        name for name, target in legacy_scripts.items() if target not in registry_targets
    )
    assert missing_targets == [], f"pyproject scripts with no matching Subcommand.target: {missing_targets}"

    # And the other direction: every one of those legacy names must still
    # work when typed after `opengrader `.
    unresolved_names = sorted(name for name in legacy_scripts if find(name) is None)
    assert unresolved_names == [], f"legacy script names find() cannot resolve: {unresolved_names}"


# --------------------------------------------------------------------------- #
# 2. Names and aliases are unique
# --------------------------------------------------------------------------- #


def test_no_name_or_alias_collides_with_another() -> None:
    """`find()` returns exactly one row per string a user can type; if two
    rows ever claimed the same name or alias, one of them would become
    unreachable with no error telling anyone why.
    """
    labels = [spec.name for spec in COMMANDS]
    labels += [alias for spec in COMMANDS for alias in spec.aliases]

    counts = Counter(labels)
    collisions = sorted(label for label, count in counts.items() if count > 1)
    assert collisions == []


# --------------------------------------------------------------------------- #
# 3. Nothing heavy is imported at module import
# --------------------------------------------------------------------------- #


def test_importing_cli_main_pulls_in_no_heavy_dependency() -> None:
    """Importing `opengrader.cli.main` must stay cheap enough to run on every
    tab completion — see the module's own docstring on why `COMMANDS` stores
    targets as strings rather than functions.

    This has to run in a *fresh* interpreter (subprocess), because by the
    time this test file itself runs, earlier tests in the suite will already
    have imported half these libraries into this process's `sys.modules` —
    that would make the assertion pass even if a future change reintroduced
    a top-level heavy import in this dispatcher.
    """
    probe = (
        "import opengrader.cli.main, sys; "
        "print('cv2' in sys.modules); "
        "print('fitz' in sys.modules); "
        "print('numpy' in sys.modules); "
        "print('textual' in sys.modules); "
        "print('matplotlib' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr

    names = ("cv2", "fitz", "numpy", "textual", "matplotlib")
    loaded = dict(zip(names, (line.strip() == "True" for line in result.stdout.splitlines())))
    heavy_and_loaded = [name for name in names if loaded[name]]
    assert heavy_and_loaded == [], f"importing opengrader.cli.main pulled in: {heavy_and_loaded}"


# --------------------------------------------------------------------------- #
# 4. dispatch() forwards argv untouched and patches sys.argv
# --------------------------------------------------------------------------- #


def test_dispatch_forwards_argv_untouched_and_patches_sys_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The entry function must receive exactly the argv list dispatch() was
    given (not a copy, not a reconstruction), and while it runs
    `sys.argv[0]` must read "opengrader <name>" — so an inner argparse's own
    usage line names the command the user actually typed — with the rest of
    `sys.argv` holding the args. Afterwards `sys.argv` must be back to
    whatever it was before, whether the call returned normally or blew up.

    `qa-file` is used as the stand-in registered module here because its
    import is cheap (see test 3 above) — this test is about dispatch's own
    behaviour, not about qa-file.
    """
    spec = find("qa-file")
    assert spec is not None

    seen: dict[str, object] = {}

    def fake_run_cli(argv: list[str]) -> int:
        seen["argv"] = argv
        seen["sys_argv"] = list(sys.argv)
        return 0

    monkeypatch.setattr(qa_file_module, "run_cli", fake_run_cli)

    given = ["--check", "qa.md"]
    outer_argv = list(sys.argv)
    dispatch(spec, given)

    assert seen["argv"] is given, "argv must be forwarded as-is, not copied or rebuilt"
    assert seen["sys_argv"][0] == f"{PROGRAM_NAME} qa-file"
    assert seen["sys_argv"][1:] == given
    assert sys.argv == outer_argv, "sys.argv must be restored after a normal return"

    def exploding_run_cli(argv: list[str]) -> int:
        raise RuntimeError("entry function blew up")

    monkeypatch.setattr(qa_file_module, "run_cli", exploding_run_cli)
    with pytest.raises(RuntimeError):
        dispatch(spec, given)
    assert sys.argv == outer_argv, "sys.argv must be restored even when the entry raises"


def test_dispatch_makes_a_real_argparse_usage_line_name_the_dispatched_command(
    capsys: pytest.CaptureFixture,
) -> None:
    """`qa-file`'s own `ArgumentParser` has no hardcoded `prog=` (it was
    deliberately removed from a handful of these modules so a dispatched run
    reads `opengrader qa-file` while the standalone script still reads
    `qa-file`) — argparse instead reads `sys.argv[0]` for the name it prints.
    This exercises the *real* module (no monkeypatching) to prove dispatch's
    `sys.argv` patch actually reaches argparse's own "usage:" line, not just
    a value this test suite made up and checked in isolation.
    """
    spec = find("qa-file")
    assert spec is not None

    with pytest.raises(SystemExit) as excinfo:
        dispatch(spec, ["--help"])
    assert excinfo.value.code == 0

    out = capsys.readouterr().out
    assert "usage: opengrader qa-file " in out


# --------------------------------------------------------------------------- #
# 5. Exit codes
# --------------------------------------------------------------------------- #


def test_dispatch_exit_codes_follow_the_entry_functions_return_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dispatch()'s return value is: the int the entry function returned
    (e.g. `doctor`, `run`, `transcribe-eval`); 0 when it returned `None`; 0
    for any other, non-int return value (mirroring `run_cli` handing back a
    `PipelineReport`); and `EXIT_INTERRUPTED` (130) on a `KeyboardInterrupt`.
    `bool` is deliberately excluded from the "it's an int" case — in Python
    `True`/`False` *are* ints, and without the exclusion `True` would
    silently become exit code 1.
    """
    spec = find("qa-file")
    assert spec is not None

    def returning(value):
        def entry(argv: list[str]):
            return value

        return entry

    monkeypatch.setattr(qa_file_module, "run_cli", returning(3))
    assert dispatch(spec, []) == 3

    monkeypatch.setattr(qa_file_module, "run_cli", returning(None))
    assert dispatch(spec, []) == 0

    monkeypatch.setattr(qa_file_module, "run_cli", returning(object()))
    assert dispatch(spec, []) == 0

    monkeypatch.setattr(qa_file_module, "run_cli", returning(True))
    assert dispatch(spec, []) == 0, "True must not become exit code 1"

    monkeypatch.setattr(qa_file_module, "run_cli", returning(False))
    assert dispatch(spec, []) == 0, "False must go through the bool exclusion, not the int case"

    def interrupting(argv: list[str]):
        raise KeyboardInterrupt

    monkeypatch.setattr(qa_file_module, "run_cli", interrupting)
    assert dispatch(spec, []) == EXIT_INTERRUPTED


# --------------------------------------------------------------------------- #
# 6. SystemExit propagates with its code
# --------------------------------------------------------------------------- #


def test_dispatch_lets_a_systemexit_from_the_entry_propagate_with_its_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `SystemExit` raised inside the entry function — the way `argparse`
    itself signals `--help` or a bad flag — must reach the caller with its
    original code untouched rather than being swallowed into some other
    0/1 result. This is the mechanism that keeps `opengrader qa-file --help`
    and a bad flag behaving exactly like the standalone `qa-file` command.
    """
    spec = find("qa-file")
    assert spec is not None

    def exits(argv: list[str]):
        raise SystemExit(7)

    monkeypatch.setattr(qa_file_module, "run_cli", exits)
    with pytest.raises(SystemExit) as excinfo:
        dispatch(spec, ["--help"])
    assert excinfo.value.code == 7


# --------------------------------------------------------------------------- #
# 7. Aliases dispatch to the same target as the canonical name
# --------------------------------------------------------------------------- #


def test_aliases_resolve_to_the_same_registry_row_as_their_canonical_name() -> None:
    assert find("split") is find("submission-splitter")
    assert find("opengrader-run") is find("run")
    assert find("pipeline") is find("run")


# --------------------------------------------------------------------------- #
# 8. Unknown command is exit code 2 and suggests a near match
# --------------------------------------------------------------------------- #


def test_an_unknown_command_exits_two_and_suggests_the_near_miss() -> None:
    result = CliRunner().invoke(cli, ["submission-extracter"])
    assert result.exit_code == 2
    assert "submission-extractor" in result.output


# --------------------------------------------------------------------------- #
# 9. Sectioned help lists every visible command exactly once
# --------------------------------------------------------------------------- #


def _row_labels(listing: str) -> list[str]:
    """The canonical name at the start of each command row.

    click renders each row as a definition-list entry: the label starts
    right after exactly two leading spaces, and any wrapped continuation of
    the summary is indented *further* to line up under the description
    column (see the raw `--help` output — description continuation lines
    have well over two leading spaces, section headings have none, and
    blank lines separate sections). Filtering to exactly-two-space lines
    and taking the text before the 2+-space gap to the summary is what
    distinguishes an actual command row from a plain-English use of a
    command's name inside a summary, like "run this first" in doctor's own
    description — a naive "does the word appear" count would misfire on
    that every time.
    """
    labels = []
    for line in listing.splitlines():
        if not line.startswith("  ") or line.startswith("   ") or not line.strip():
            continue
        label = re.split(r"  +", line.strip(), maxsplit=1)[0]
        labels.append(label.split(" (", 1)[0])  # drop the "(aliases)" suffix
    return labels


def test_sectioned_help_lists_every_visible_command_once_and_hides_dev_only_ones() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0

    # The epilog's "Examples:" block deliberately repeats a handful of names
    # (doctor, run, tui, split, help) to show them in context; only the
    # section listing above it is the one-row-per-command inventory this test
    # is checking.
    listing = result.output.split("\n  Examples:")[0]
    row_counts = Counter(_row_labels(listing))

    for spec in COMMANDS:
        if spec.hidden:
            continue
        assert row_counts[spec.name] == 1, f"{spec.name!r} appears {row_counts[spec.name]} time(s) as a row"
        assert spec.section in SECTION_ORDER
        assert f"{spec.section}:" in listing

    # tui-dev is deliberately absent from --help — it launches Textual
    # devtools, not something a grader ever runs — but it must still be
    # reachable by name for whoever knows to type it.
    assert "tui-dev" not in result.output
    tui_dev = find("tui-dev")
    assert tui_dev is not None
    assert tui_dev.hidden is True

    # Section headings must appear in workflow order (SECTION_ORDER), not
    # alphabetically — a reader scanning top to bottom should walk the same
    # path their exam does.
    present_sections = [section for section in SECTION_ORDER if f"{section}:" in listing]
    positions = [listing.index(f"{section}:") for section in present_sections]
    assert positions == sorted(positions)


# --------------------------------------------------------------------------- #
# 10. `opengrader commands --json`
# --------------------------------------------------------------------------- #


def test_commands_json_emits_one_object_per_registry_row() -> None:
    result = CliRunner().invoke(cli, ["commands", "--json"])
    assert result.exit_code == 0

    payload = json.loads(result.output)
    assert len(payload) == len(COMMANDS)

    by_name = {row["name"]: row for row in payload}
    assert set(by_name) == {spec.name for spec in COMMANDS}
    for spec in COMMANDS:
        row = by_name[spec.name]
        assert row["aliases"] == list(spec.aliases)
        assert row["section"] == spec.section
        assert row["summary"] == spec.summary
        assert row["target"] == spec.target
