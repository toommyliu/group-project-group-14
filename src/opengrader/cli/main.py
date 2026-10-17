"""Dispatch Opengrader subcommands."""

from __future__ import annotations

import difflib
import importlib
from dataclasses import dataclass
from pathlib import Path

import click
from click.shell_completion import CompletionItem

from opengrader.cli import CONTEXT_SETTINGS

PROGRAM_NAME = "opengrader"

EXIT_INTERRUPTED = 130

UNADVERTISED_ALIASES = frozenset(
    {"opengrader-run", "opengrader-providers", "opengrader-data-flow", "opengrader-share"}
)


SECTION_START = "Start here"
SECTION_PREPARE = "Prepare an assignment"
SECTION_INTAKE = "Take in scans"
SECTION_GRADE = "Grade"
SECTION_CHECK = "Check the grading"
SECTION_RETURN = "Hand back"
SECTION_GRADERS = "Work with other graders"
SECTION_HISTORY = "History and safety"
SECTION_META = "About this tool"

SECTION_ORDER: tuple[str, ...] = (
    SECTION_START,
    SECTION_PREPARE,
    SECTION_INTAKE,
    SECTION_GRADE,
    SECTION_CHECK,
    SECTION_RETURN,
    SECTION_GRADERS,
    SECTION_HISTORY,
    SECTION_META,
)


@dataclass(frozen=True)
class Subcommand:
    name: str
    target: str
    summary: str
    section: str
    aliases: tuple[str, ...] = ()
    # A tool for grading *folders*. `opengrader classic` has a screen for it,
    # so the power-user listing (`--help`, `commands`, completion) leaves it
    # out to stay short. It still runs when typed, and `commands --all` lists it.
    folder_tool: bool = False

    @property
    def listed(self) -> bool:
        """Shown by `opengrader --help`, `opengrader commands` and completion."""
        return not self.folder_tool

    def load(self) -> click.Command:
        module, attribute = self.target.split(":", 1)
        return getattr(importlib.import_module(module), attribute)


COMMANDS: tuple[Subcommand, ...] = (
    Subcommand(name="web", target="opengrader.cli.web:open_command", summary="open Opengrader in a browser", section=SECTION_START),
    Subcommand(name="stop", target="opengrader.cli.web:stop_command", summary="stop the server for all workspaces", section=SECTION_META),
    Subcommand(
        name="serve",
        target="opengrader.cli.serve:command",
        summary="run the Opengrader server in this terminal",
        section=SECTION_START,
    ),
    Subcommand(
        name="run",
        target="opengrader.cli.run:command",
        summary="the whole pre-grading pipeline in one resumable command",
        section=SECTION_START,
        aliases=("opengrader-run", "pipeline"),
    ),
    Subcommand(
        name="tui",
        target="opengrader.tui.main:tui_command",
        summary="open a workspace in the grading app (shared with the web app)",
        section=SECTION_START,
        aliases=("grade",),
    ),
    Subcommand(
        name="classic",
        target="opengrader.tui.main:classic_command",
        summary="Tools & Settings, and grading folders with no workspace (the desktop icon)",
        section=SECTION_START,
    ),
    Subcommand(
        name="doctor",
        target="opengrader.cli.doctor:command",
        summary="is this machine set up? run this first when something breaks",
        section=SECTION_START,
    ),
    Subcommand(
        name="providers",
        target="opengrader.cli.providers:command",
        summary="add, test or remove an AI service of your own",
        section=SECTION_START,
        aliases=("opengrader-providers",),
    ),
    Subcommand(
        name="data-flow",
        target="opengrader.cli.data_flow:command",
        summary="which student data would leave this machine, and to where (FERPA)",
        section=SECTION_START,
        aliases=("opengrader-data-flow",),
    ),
    Subcommand(
        name="ai-readme",
        target="opengrader.cli.agent:ai_readme_command",
        summary="what your AI assistant needs to know to use Opengrader safely",
        section=SECTION_START,
    ),
    Subcommand(
        name="agent",
        target="opengrader.cli.agent:command",
        summary="grade written answers with your own AI assistant, with no names or IDs",
        section=SECTION_GRADE,
    ),
    Subcommand(
        name="create-answer-key",
        target="opengrader.cli.answer_key:command",
        summary="turn an existing answer key into a QA markdown draft",
        section=SECTION_PREPARE,
        aliases=("answer-key",),
        folder_tool=True,
    ),
    Subcommand(
        name="qa-file",
        target="opengrader.cli.qa_file:command",
        summary="validate a QA file; print assignment templates from it",
        section=SECTION_PREPARE,
        folder_tool=True,
    ),
    Subcommand(
        name="region-map",
        target="opengrader.cli.region_map:command",
        summary="draw answer regions on an assignment Opengrader didn't make",
        section=SECTION_PREPARE,
        folder_tool=True,
    ),
    Subcommand(
        name="scan-check",
        target="opengrader.cli.scan_check:command",
        summary="pre-flight a scan: rotation, page order, blanks, DPI, margins",
        section=SECTION_INTAKE,
        folder_tool=True,
    ),
    Subcommand(
        name="submission-splitter",
        target="opengrader.cli.submission_splitter:command",
        summary="split one scanned bundle into per-student PDFs",
        section=SECTION_INTAKE,
        aliases=("split",),
        folder_tool=True,
    ),
    Subcommand(
        name="photo-import",
        target="opengrader.cli.photo_import:command",
        summary="turn a folder of phone photos into per-student PDFs",
        section=SECTION_INTAKE,
        folder_tool=True,
    ),
    Subcommand(
        name="submission-extractor",
        target="opengrader.cli.submission_extractor:command",
        summary="read each student PDF into a .submission.md plus answer crops",
        section=SECTION_INTAKE,
        aliases=("extract",),
        folder_tool=True,
    ),
    Subcommand(
        name="share",
        target="opengrader.cli.share:command",
        summary="let other accounts on this computer grade in your workspace",
        section=SECTION_GRADERS,
        aliases=("opengrader-share",),
    ),
    Subcommand(
        name="merge-submissions",
        target="opengrader.cli.merge_submissions:command",
        summary="join a paper the splitter cut in two, keeping its grades",
        section=SECTION_INTAKE,
        folder_tool=True,
    ),
    Subcommand(
        name="draft-rubric",
        target="opengrader.cli.draft_rubric:command",
        summary="let AI draft rubric items from your students' answers",
        section=SECTION_GRADE,
        folder_tool=True,
    ),
    Subcommand(
        name="automated-grading",
        target="opengrader.cli.automated_grading:command",
        summary="grade what can be graded without a person",
        section=SECTION_GRADE,
        aliases=("auto-grade",),
        folder_tool=True,
    ),
    Subcommand(
        name="answer-groups",
        target="opengrader.cli.answer_groups:command",
        summary="how many distinct answers is this really? + proposed merges",
        section=SECTION_GRADE,
        folder_tool=True,
    ),
    Subcommand(
        name="curve",
        target="opengrader.cli.curve:command",
        summary="apply a reversible curve to totals (never edits a grade)",
        section=SECTION_GRADE,
        folder_tool=True,
    ),
    Subcommand(
        name="regrade",
        target="opengrader.cli.regrade:command",
        summary="track a grade dispute from request to resolution",
        section=SECTION_GRADE,
        folder_tool=True,
    ),
    Subcommand(
        name="review-queue",
        target="opengrader.cli.review_queue:command",
        summary="what still needs a human look, triaged",
        section=SECTION_CHECK,
        folder_tool=True,
    ),
    Subcommand(
        name="consistency-audit",
        target="opengrader.cli.consistency_audit:command",
        summary="fairness: was the same answer graded two different ways?",
        section=SECTION_CHECK,
        folder_tool=True,
    ),
    Subcommand(
        name="similarity-audit",
        target="opengrader.cli.similarity_audit:command",
        summary="answers that look copied from one another",
        section=SECTION_CHECK,
        folder_tool=True,
    ),
    Subcommand(
        name="item-analysis",
        target="opengrader.cli.item_analysis:command",
        summary="per-question difficulty, separation and outcome mastery",
        section=SECTION_CHECK,
        folder_tool=True,
    ),
    Subcommand(
        name="calibrate",
        target="opengrader.cli.calibrate:command",
        summary="do your TAs grade alike? norming rounds and leniency",
        section=SECTION_CHECK,
        folder_tool=True,
    ),
    Subcommand(
        name="grading-stats",
        target="opengrader.cli.grading_stats:command",
        summary="how long grading took, and how fast it went",
        section=SECTION_CHECK,
        folder_tool=True,
    ),
    Subcommand(
        name="llm-eval",
        target="opengrader.cli.llm_eval:command",
        summary="is the LLM grader trustworthy here, and above what confidence",
        section=SECTION_CHECK,
        folder_tool=True,
    ),
    Subcommand(
        name="transcribe-eval",
        target="opengrader.cli.transcribe_eval:command",
        summary="is the OCR trustworthy here, measured against human fixes",
        section=SECTION_CHECK,
        folder_tool=True,
    ),
    Subcommand(
        name="grade-check",
        target="opengrader.cli.grade_check:command",
        summary="ready to hand back? a read-only pre-flight",
        section=SECTION_RETURN,
    ),
    Subcommand(
        name="grade-release",
        target="opengrader.cli.grade_release:command",
        summary="record what each student was handed, and what moved since",
        section=SECTION_RETURN,
    ),
    Subcommand(
        name="canvas-export",
        target="opengrader.cli.canvas_export:command",
        summary="push scores and comments to Canvas LMS",
        section=SECTION_RETURN,
    ),
    Subcommand(
        name="grades-export",
        target="opengrader.cli.grades_export:command",
        summary="write the gradebook CSV, for everyone not using Canvas",
        section=SECTION_RETURN,
    ),
    Subcommand(
        name="return-bundle",
        target="opengrader.cli.return_bundle:command",
        summary="assemble one hand-back folder: CSV, student PDFs, report",
        section=SECTION_RETURN,
    ),
    Subcommand(
        name="course-grades",
        target="opengrader.cli.course_grades:command",
        summary="whole-semester grades, the at-risk list, outcome mastery",
        section=SECTION_RETURN,
        folder_tool=True,
    ),
    Subcommand(
        name="claim",
        target="opengrader.cli.claim:command",
        summary="divide the pile between TAs; see coverage and gaps",
        section=SECTION_GRADERS,
        folder_tool=True,
    ),
    Subcommand(
        name="grading-policy",
        target="opengrader.cli.grading_policy:command",
        summary="anonymous grading: the policy lock and name redaction",
        section=SECTION_GRADERS,
        folder_tool=True,
    ),
    Subcommand(
        name="grade-merge",
        target="opengrader.cli.grade_merge:command",
        summary="hand a redacted packet to a TA, then merge their grades back",
        section=SECTION_GRADERS,
        folder_tool=True,
    ),
    Subcommand(
        name="grade-history",
        target="opengrader.cli.grade_history:command",
        summary="the audit journal: who changed which grade, and when",
        section=SECTION_HISTORY,
        folder_tool=True,
    ),
    Subcommand(
        name="grade-restore",
        target="opengrader.cli.grade_restore:command",
        summary="rewind grading to a moment, or as if a grader never acted",
        section=SECTION_HISTORY,
        folder_tool=True,
    ),
    Subcommand(
        name="grade-backup",
        target="opengrader.cli.grade_backup:command",
        summary="snapshot, verify and restore a class's graded work",
        section=SECTION_HISTORY,
        folder_tool=True,
    ),
)


def _version() -> str:
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("opengrader")
    except PackageNotFoundError:
        return "unknown (running from a source tree)"


def _index() -> dict[str, Subcommand]:
    table: dict[str, Subcommand] = {}
    for spec in COMMANDS:
        table[spec.name] = spec
        for alias in spec.aliases:
            table[alias] = spec
    return table


def find(name: str) -> Subcommand | None:
    return _index().get(name)


def unknown_command_message(name: str) -> str:
    close = difflib.get_close_matches(name, sorted(_index()), n=3, cutoff=0.55)
    message = f"No such command {name!r}."
    if close:
        message += " Did you mean: " + ", ".join(close) + "?"
    return message + f"\nRun `{PROGRAM_NAME} commands --all` for the full list."


class OpengraderCLI(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:
        return [spec.name for spec in COMMANDS if spec.listed] + sorted(self.commands)

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        builtin = self.commands.get(name)
        if builtin is not None:
            return builtin
        spec = find(name)
        return spec.load() if spec is not None else None

    def shell_complete(self, ctx: click.Context, incomplete: str) -> list[CompletionItem]:
        rows = [(spec.name, spec.summary) for spec in COMMANDS if spec.listed]
        rows += [
            (name, command.get_short_help_str())
            for name, command in self.commands.items()
            if not command.hidden
        ]
        items = [
            CompletionItem(name, help=summary)
            for name, summary in rows
            if name.startswith(incomplete)
        ]
        return items + click.Command.shell_complete(self, ctx, incomplete)

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except KeyboardInterrupt:
            click.echo(f"\n{ctx.invoked_subcommand or PROGRAM_NAME}: interrupted.", err=True)
            ctx.exit(EXIT_INTERRUPTED)

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        name = args[0] if args else ""
        if name and not name.startswith("-") and self.get_command(ctx, name) is None:
            if Path(name).expanduser().is_dir():
                return super().resolve_command(ctx, ["tui", name, *args[1:]])
            raise click.UsageError(unknown_command_message(name), ctx)
        return super().resolve_command(ctx, args)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        rows: dict[str, list[tuple[str, str]]] = {}
        for spec in COMMANDS:
            if not spec.listed:
                continue
            label = spec.name
            visible_aliases = [a for a in spec.aliases if a not in UNADVERTISED_ALIASES]
            if visible_aliases:
                label += " (" + ", ".join(visible_aliases) + ")"
            rows.setdefault(spec.section, []).append((label, spec.summary))

        for name in sorted(self.commands):
            command = self.commands[name]
            if command.hidden:
                continue
            rows.setdefault(SECTION_META, []).append(
                (name, command.get_short_help_str(limit=68))
            )

        for section in SECTION_ORDER:
            if section not in rows:
                continue
            with formatter.section(section):
                formatter.write_dl(rows[section])

        folder_tools = sum(spec.folder_tool for spec in COMMANDS)
        formatter.write_paragraph()
        formatter.write_text(
            f"{folder_tools} more tools for grading folders (claim, calibrate, "
            f"curve, regrade, audits, single pipeline stages, ...) live in "
            f"`{PROGRAM_NAME} classic`. They still run from here by name; "
            f"`{PROGRAM_NAME} commands --all` lists them."
        )


_EPILOG = f"""\
\b
Examples:
  {PROGRAM_NAME} doctor                       is this machine ready?
  {PROGRAM_NAME} run --scan scans/all.pdf --qa qa.md --out class/
  {PROGRAM_NAME} tui --workspace class/
  {PROGRAM_NAME} classic                      tools, settings and folder grading
  {PROGRAM_NAME} grade-check                  ready to hand back?
  {PROGRAM_NAME} help run                     that tool's own options

Every subcommand takes its own flags: `{PROGRAM_NAME} <command> --help`.
The standalone commands (`tui`, `submission-splitter`, ...) still work exactly
as before; this is one door onto the same tools, not a replacement for them.
"""


@click.group(
    PROGRAM_NAME,
    cls=OpengraderCLI,
    invoke_without_command=True,
    context_settings=CONTEXT_SETTINGS,
    epilog=_EPILOG,
)
@click.version_option(version=_version(), prog_name=PROGRAM_NAME)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Opengrader — grade paper assignments on your own machine.

    Start with `opengrader doctor` to check the machine, then
    `opengrader run` to take a stack of scans all the way to gradeable work,
    then `opengrader tui` to grade it (or `opengrader classic` for folders).
    """

    if ctx.invoked_subcommand is None:
        # Inside a workspace, a bare `opengrader` opens it; anywhere else there
        # is nothing to open, so say what there is instead of failing.
        from opengrader.core.workspace.store import discover_workspace

        if discover_workspace(Path.cwd()) is None:
            click.echo(ctx.get_help())
            return
        from opengrader.tui.main import tui_command

        ctx.invoke(tui_command)


@cli.command("help", short_help="show help for one command")
@click.argument("command", required=False)
@click.pass_context
def help_command(ctx: click.Context, command: str | None) -> None:
    """Show help for OPENGRADER, or for one COMMAND."""
    if command is None:
        click.echo(ctx.parent.get_help() if ctx.parent else cli.get_help(ctx))
        return
    target = cli.get_command(ctx, command)
    if target is None:
        raise click.UsageError(unknown_command_message(command), ctx)
    with click.Context(target, info_name=command, parent=ctx.parent) as sub_ctx:
        click.echo(sub_ctx.get_help())


@cli.command("commands", short_help="list every subcommand, one per line")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="Also list the folder tools that `opengrader classic` covers.",
)
def commands_command(as_json: bool, show_all: bool) -> None:
    """List every subcommand (for scripts, and for finding one by keyword).

    JSON always lists every row, with its `folder_tool` flag.
    """
    if as_json:
        import json

        payload = [
            {
                "name": spec.name,
                "aliases": list(spec.aliases),
                "section": spec.section,
                "summary": spec.summary,
                "target": spec.target,
                "folder_tool": spec.folder_tool,
            }
            for spec in COMMANDS
        ]
        click.echo(json.dumps(payload, indent=2))
        return
    shown = [
        spec
        for spec in COMMANDS
        if spec.listed or (show_all and spec.folder_tool)
    ]
    width = max(len(spec.name) for spec in shown)
    for spec in shown:
        suffix = "  (folder tool)" if spec.folder_tool else ""
        click.echo(f"{spec.name.ljust(width)}  {spec.summary}{suffix}")


@cli.command("completion", short_help="print a shell completion script")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completion_command(shell: str) -> None:
    """Print the shell completion script for SHELL.

    Completes subcommand names and their flags. Install it with, for bash:

    \b
      opengrader completion bash >> ~/.bashrc
    """
    from click.shell_completion import get_completion_class

    completion_class = get_completion_class(shell)
    if completion_class is None:  # pragma: no cover
        raise click.UsageError(f"No completion support for {shell!r}.")
    source = completion_class(cli, {}, PROGRAM_NAME, "_OPENGRADER_COMPLETE").source()
    click.echo(source)


def main() -> None:
    cli(prog_name=PROGRAM_NAME)


if __name__ == "__main__":  # pragma: no cover
    main()
