from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest
from click.testing import CliRunner

from opengrader.cli import qa_file as qa_file_module
from opengrader.cli.main import (
    COMMANDS,
    EXIT_INTERRUPTED,
    SECTION_ORDER,
    cli,
    find,
)


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


def test_every_registry_row_loads_a_click_command() -> None:
    import click

    for spec in COMMANDS:
        assert isinstance(spec.load(), click.Command), spec.target


def test_a_subcommand_runs_natively_with_its_own_arguments(tmp_path: Path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("---\ntitle: Quiz\n---\n- (2 points) First question\n", encoding="utf-8")

    result = CliRunner().invoke(cli, ["qa-file", "validate", "--qa-file", str(qa)])

    assert result.exit_code == 0, result.output
    assert f"QA file is valid: {qa}" in result.output


def test_subcommand_help_names_the_dispatched_command() -> None:
    result = CliRunner().invoke(cli, ["qa-file", "--help"])

    assert result.exit_code == 0
    assert "Usage: opengrader qa-file " in result.output
    assert "generate-template" in result.output


def test_folder_shorthand_opens_the_tui_command(tmp_path: Path) -> None:
    folder = tmp_path / "midterm"
    folder.mkdir()

    result = CliRunner().invoke(cli, [str(folder), "--help"])

    assert result.exit_code == 0
    assert "Usage: opengrader tui" in result.output


def test_a_subcommands_exit_status_is_the_programs(monkeypatch: pytest.MonkeyPatch) -> None:
    def exits(qa_file: str) -> None:
        raise SystemExit(7)

    monkeypatch.setattr(qa_file_module.validate_command, "callback", exits)

    result = CliRunner().invoke(cli, ["qa-file", "validate", "--qa-file", "qa.md"])

    assert result.exit_code == 7


def test_an_interrupted_subcommand_exits_130(monkeypatch: pytest.MonkeyPatch) -> None:
    def interrupted(qa_file: str) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(qa_file_module.validate_command, "callback", interrupted)

    result = CliRunner().invoke(cli, ["qa-file", "validate", "--qa-file", "qa.md"])

    assert result.exit_code == EXIT_INTERRUPTED
    assert "qa-file: interrupted." in result.output


def test_aliases_resolve_to_the_same_registry_row_as_their_canonical_name() -> None:
    assert find("split") is find("submission-splitter")
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
    opened: list[dict] = []
    monkeypatch.setattr(
        tui_main.tui_command, "callback", lambda **options: opened.append(options)
    )

    result = CliRunner().invoke(cli, [])

    assert result.exit_code == 0
    assert len(opened) == 1
    assert opened[0]["folder"] is None and opened[0]["workspace"] is None


def test_bare_command_outside_a_workspace_shows_help(monkeypatch, tmp_path) -> None:
    from opengrader.tui import main as tui_main

    monkeypatch.chdir(tmp_path)
    opened: list[dict] = []
    monkeypatch.setattr(
        tui_main.tui_command, "callback", lambda **options: opened.append(options)
    )

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
            assert row_counts[spec.name] == 0, f"{spec.name!r} should not be listed"
            continue
        assert row_counts[spec.name] == 1, f"{spec.name!r} appears {row_counts[spec.name]} time(s) as a row"
        assert spec.section in SECTION_ORDER
        assert f"{spec.section}:" in listing

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


def test_a_folder_tool_still_runs_by_name_and_alias() -> None:
    for name in ("claim", "split"):  # canonical name, and an alias
        result = CliRunner().invoke(cli, [name, "--help"])
        assert result.exit_code == 0, name
        assert f"Usage: opengrader {name}" in result.output


def test_completion_offers_only_listed_commands_without_loading_them() -> None:
    probe = (
        "import json, sys; "
        "from click.shell_completion import ShellComplete; "
        "from opengrader.cli.main import cli; "
        "items = ShellComplete(cli, {}, 'opengrader', '_OPENGRADER_COMPLETE').get_completions([], ''); "
        "print(json.dumps({'names': [item.value for item in items], 'loaded': sorted("
        "name for name in sys.modules "
        "if name.startswith('opengrader.cli.') and name != 'opengrader.cli.main')}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr

    completion = json.loads(result.stdout)
    assert "tui" in completion["names"] and "classic" in completion["names"]
    assert "claim" not in completion["names"]
    assert completion["loaded"] == []


def test_an_unknown_command_still_suggests_a_folder_tool() -> None:
    result = CliRunner().invoke(cli, ["clam"])

    assert result.exit_code == 2
    assert "claim" in result.output and "commands --all" in result.output
