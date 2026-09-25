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


def test_every_pyproject_script_maps_onto_a_registry_row() -> None:
    scripts = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))["project"]["scripts"]

    legacy_scripts = {name: target for name, target in scripts.items() if name != "opengrader"}

    registry_targets = {spec.target for spec in COMMANDS}
    missing_targets = sorted(
        name for name, target in legacy_scripts.items() if target not in registry_targets
    )
    assert missing_targets == [], f"pyproject scripts with no matching Subcommand.target: {missing_targets}"

    unresolved_names = sorted(name for name in legacy_scripts if find(name) is None)
    assert unresolved_names == [], f"legacy script names find() cannot resolve: {unresolved_names}"


def test_no_name_or_alias_collides_with_another() -> None:
    labels = [spec.name for spec in COMMANDS]
    labels += [alias for spec in COMMANDS for alias in spec.aliases]

    counts = Counter(labels)
    collisions = sorted(label for label, count in counts.items() if count > 1)
    assert collisions == []


def test_importing_cli_main_pulls_in_no_heavy_dependency() -> None:
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


def test_dispatch_forwards_argv_untouched_and_patches_sys_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    spec = find("qa-file")
    assert spec is not None

    with pytest.raises(SystemExit) as excinfo:
        dispatch(spec, ["--help"])
    assert excinfo.value.code == 0

    out = capsys.readouterr().out
    assert "usage: opengrader qa-file " in out


def test_folder_shorthand_opens_the_tui_command(tmp_path: Path) -> None:
    folder = tmp_path / "midterm"
    folder.mkdir()

    result = CliRunner().invoke(cli, [str(folder), "--help"])

    assert result.exit_code == 0
    assert "usage: opengrader tui" in result.output


def test_dispatch_exit_codes_follow_the_entry_functions_return_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_dispatch_lets_a_systemexit_from_the_entry_propagate_with_its_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = find("qa-file")
    assert spec is not None

    def exits(argv: list[str]):
        raise SystemExit(7)

    monkeypatch.setattr(qa_file_module, "run_cli", exits)
    with pytest.raises(SystemExit) as excinfo:
        dispatch(spec, ["--help"])
    assert excinfo.value.code == 7


def test_aliases_resolve_to_the_same_registry_row_as_their_canonical_name() -> None:
    assert find("split") is find("submission-splitter")
    assert find("opengrader-run") is find("run")
    assert find("pipeline") is find("run")


def test_an_unknown_command_exits_two_and_suggests_the_near_miss() -> None:
    result = CliRunner().invoke(cli, ["submission-extracter"])
    assert result.exit_code == 2
    assert "submission-extractor" in result.output


def test_bare_command_inside_a_workspace_opens_the_tui(monkeypatch, tmp_path) -> None:
    from opengrader.core.workspace.store import Workspace
    from opengrader.tui import main as tui_main

    Workspace.create(tmp_path)
    monkeypatch.chdir(tmp_path)
    opened: list[list[str]] = []
    monkeypatch.setattr(tui_main, "run_app", lambda argv: opened.append(argv))

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    assert opened == [[]]


def test_bare_command_outside_a_workspace_shows_help(monkeypatch, tmp_path) -> None:
    from opengrader.tui import main as tui_main

    monkeypatch.chdir(tmp_path)
    opened: list[list[str]] = []
    monkeypatch.setattr(tui_main, "run_app", lambda argv: opened.append(argv))

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    assert opened == []
    assert "classic" in result.output and "tui" in result.output


def _row_labels(listing: str) -> list[str]:
    labels = []
    for line in listing.splitlines():
        if not line.startswith("  ") or line.startswith("   ") or not line.strip():
            continue
        label = re.split(r"  +", line.strip(), maxsplit=1)[0]
        labels.append(label.split(" (", 1)[0])
    return labels


def test_sectioned_help_lists_every_listed_command_once_and_hides_the_rest() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    assert result.exit_code == 0

    listing = result.output.split("\n  Examples:")[0]
    row_counts = Counter(_row_labels(listing))

    for spec in COMMANDS:
        if not spec.listed:
            # Dev-only and folder tools stay out of the power-user listing.
            assert row_counts[spec.name] == 0, f"{spec.name!r} should not be listed"
            continue
        assert row_counts[spec.name] == 1, f"{spec.name!r} appears {row_counts[spec.name]} time(s) as a row"
        assert spec.section in SECTION_ORDER
        assert f"{spec.section}:" in listing

    assert "tui-dev" not in result.output
    tui_dev = find("tui-dev")
    assert tui_dev is not None
    assert tui_dev.hidden is True

    present_sections = [section for section in SECTION_ORDER if f"{section}:" in listing]
    positions = [listing.index(f"{section}:") for section in present_sections]
    assert positions == sorted(positions)


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
        assert row["folder_tool"] == spec.folder_tool


def test_the_power_user_listing_keeps_launch_workspace_and_hand_back_commands() -> None:
    listed = {spec.name for spec in COMMANDS if spec.listed}
    assert {
        "run", "tui", "classic", "serve", "web", "stop", "doctor", "providers",
        "grades-export", "return-bundle", "grade-check", "grade-release", "canvas-export",
    } <= listed
    assert {"claim", "calibrate", "grade-merge", "curve", "submission-splitter"}.isdisjoint(listed)


def test_help_points_to_the_folder_tools() -> None:
    result = CliRunner().invoke(cli, ["--help"])
    folder_tools = sum(spec.folder_tool for spec in COMMANDS)

    assert f"{folder_tools} more tools for grading folders" in result.output
    assert "commands --all" in result.output


def test_commands_lists_folder_tools_only_with_all() -> None:
    plain = CliRunner().invoke(cli, ["commands"]).output
    everything = CliRunner().invoke(cli, ["commands", "--all"]).output

    assert "claim" not in plain.split() and "tui" in plain.split()
    assert "claim" in everything.split() and "(folder tool)" in everything
    assert "tui-dev" not in everything  # dev-only stays hidden even here


def test_a_folder_tool_still_runs_by_name_and_alias(capsys) -> None:
    for name in ("claim", "split"):  # canonical name, and an alias
        result = CliRunner().invoke(cli, [name, "--help"])
        assert result.exit_code == 0, name
        assert "usage: opengrader" in result.output + capsys.readouterr().out


def test_completion_offers_only_listed_commands() -> None:
    from opengrader.cli.main import OpengraderCLI

    names = OpengraderCLI().list_commands(None)  # type: ignore[arg-type]
    assert "tui" in names and "classic" in names
    assert "claim" not in names and "tui-dev" not in names


def test_an_unknown_command_still_suggests_a_folder_tool() -> None:
    result = CliRunner().invoke(cli, ["clam"])

    assert result.exit_code == 2
    assert "claim" in result.output and "commands --all" in result.output
