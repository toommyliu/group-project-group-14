"""`opengrader` — one command, one place to find every tool.

Opengrader grew as a set of composable scripts, and `[project.scripts]` grew
with it: thirty-odd console commands with names like `submission-splitter` and
`grade-restore`, each installed into the environment as its own binary. That is
the right shape for the *pipeline* — a stage you can run on its own is a stage
you can debug on its own — and the wrong shape for the person in front of it.
A name you cannot guess is a name you cannot find, `--help` only helps once you
already know which of the thirty to ask, and a tool called `curve` or `claim`
sitting loose on an instructor's `PATH` says nothing about what project it
belongs to.

This module is the single front door: **`opengrader <command> [args...]`**.
It is deliberately a *dispatcher and nothing else* — every subcommand hands its
arguments, untouched, to the exact function the legacy console script already
calls, so there is one implementation of every flag and no second place for
behaviour to drift. Three properties are load-bearing:

* **No flags are re-declared here.** Each subcommand takes `nargs=-1,
  type=click.UNPROCESSED` and forwards the raw argv to the module's own
  `argparse` parser. `opengrader submission-extractor --help` is therefore the
  extractor's real help, always current, with no risk of this file describing a
  flag that was renamed a release ago.
* **Nothing is imported until it is invoked.** `COMMANDS` stores the target as
  the string `"module:function"`, and the module is imported inside
  `dispatch()`. Importing this file must stay cheap enough to run on every tab
  completion, and the alternative — importing thirty CLI modules to build the
  group — would drag in `cv2`, `fitz`, `numpy` and Textual just to print a list
  of names (see §6 *Keep a heavy dependency out of TUI startup*).
* **The legacy commands still work.** `uv run submission-splitter …` and
  `uv run opengrader submission-splitter …` run the same function with the same
  arguments; this is an addition, not a migration. Subcommand names are the
  legacy names for exactly that reason — every command in the README, in the
  `About/` map and in muscle memory transfers by typing `opengrader` in front
  of it. The one exception is `opengrader-run`, which becomes `opengrader run`
  because `opengrader opengrader-run` is not a thing anyone should type; the
  old name is kept as an alias so scripts that use it keep working.
"""

from __future__ import annotations

import difflib
import importlib
import sys
from dataclasses import dataclass

import click

# The name the user typed. Used as the `prog` half of the usage lines the inner
# argparse parsers print, so an error from a subcommand reads
# "usage: opengrader submission-splitter [-h] …" rather than naming a binary
# the user did not invoke.
PROGRAM_NAME = "opengrader"

# The exit code a shell expects when a program is killed by SIGINT (128 + 2).
# Every subcommand gets this for free, which the standalone scripts never had:
# an interrupted extraction used to exit 1 and look like a failure.
EXIT_INTERRUPTED = 130

# Aliases that keep working but are not advertised in the command list. These
# are kept for compatibility, not for reading: printing
# "run (opengrader-run, pipeline)" would recommend the very spelling this
# command exists to replace.
UNADVERTISED_ALIASES = frozenset({"opengrader-run"})


# --- The command registry ---------------------------------------------------
#
# Sections are printed in the order listed here, and the order is the workflow:
# a reader scanning `opengrader --help` top to bottom walks the same path their
# exam does, from a stack of paper to grades in Canvas. Alphabetical would be
# easier to maintain and useless to read — thirty tools sorted by name is the
# problem this command exists to solve, not the solution.

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
    """One row of the registry: a name, where its code lives, how to call it.

    `target` is a string rather than a function so that building the group
    imports nothing (see the module docstring). `takes_argv` records whether
    the entry function accepts an explicit argument list — most do, but the
    console-script entry points of `doctor` and `tui-dev` take none and read
    `sys.argv` themselves, which `dispatch()` handles by patching it.
    """

    name: str
    target: str
    summary: str
    section: str
    aliases: tuple[str, ...] = ()
    takes_argv: bool = True
    hidden: bool = False

    @property
    def module(self) -> str:
        """The dotted module path half of `target`."""
        return self.target.split(":", 1)[0]

    @property
    def attribute(self) -> str:
        """The entry-function name half of `target`."""
        return self.target.split(":", 1)[1]


COMMANDS: tuple[Subcommand, ...] = (
    # --- Start here ---
    Subcommand(
        name="run",
        target="opengrader.cli.run:main",
        summary="the whole pre-grading pipeline in one resumable command",
        section=SECTION_START,
        aliases=("opengrader-run", "pipeline"),
    ),
    Subcommand(
        name="tui",
        target="opengrader.tui.main:run_app",
        summary="open the grading app",
        section=SECTION_START,
        aliases=("grade",),
    ),
    Subcommand(
        name="doctor",
        target="opengrader.cli.doctor:main",
        summary="is this machine set up? run this first when something breaks",
        section=SECTION_START,
        takes_argv=False,
    ),
    # --- Prepare an assignment ---
    Subcommand(
        name="create-answer-key",
        target="opengrader.cli.answer_key:run_cli",
        summary="turn an existing answer key into a QA markdown draft",
        section=SECTION_PREPARE,
        aliases=("answer-key",),
    ),
    Subcommand(
        name="qa-file",
        target="opengrader.cli.qa_file:run_cli",
        summary="validate a QA file; print exam templates from it",
        section=SECTION_PREPARE,
    ),
    Subcommand(
        name="region-map",
        target="opengrader.cli.region_map:run_cli",
        summary="draw answer regions on an assignment Opengrader didn't make",
        section=SECTION_PREPARE,
    ),
    # --- Take in scans ---
    Subcommand(
        name="scan-check",
        target="opengrader.cli.scan_check:run_cli",
        summary="pre-flight a scan: rotation, page order, blanks, DPI, margins",
        section=SECTION_INTAKE,
    ),
    Subcommand(
        name="submission-splitter",
        target="opengrader.cli.submission_splitter:run_cli",
        summary="split one scanned bundle into per-student PDFs",
        section=SECTION_INTAKE,
        aliases=("split",),
    ),
    Subcommand(
        name="photo-import",
        target="opengrader.cli.photo_import:run_cli",
        summary="turn a folder of phone photos into per-student PDFs",
        section=SECTION_INTAKE,
    ),
    Subcommand(
        name="submission-extractor",
        target="opengrader.cli.submission_extractor:run_cli",
        summary="read each student PDF into a .submission.md plus answer crops",
        section=SECTION_INTAKE,
        aliases=("extract",),
    ),
    # --- Grade ---
    Subcommand(
        name="automated-grading",
        target="opengrader.cli.automated_grading:run_cli",
        summary="grade what can be graded without a person",
        section=SECTION_GRADE,
        aliases=("auto-grade",),
    ),
    Subcommand(
        name="answer-groups",
        target="opengrader.cli.answer_groups:run_cli",
        summary="how many distinct answers is this really? + proposed merges",
        section=SECTION_GRADE,
    ),
    Subcommand(
        name="curve",
        target="opengrader.cli.curve:run_cli",
        summary="apply a reversible curve to totals (never edits a grade)",
        section=SECTION_GRADE,
    ),
    Subcommand(
        name="regrade",
        target="opengrader.cli.regrade:run_cli",
        summary="track a grade dispute from request to resolution",
        section=SECTION_GRADE,
    ),
    # --- Check the grading ---
    Subcommand(
        name="review-queue",
        target="opengrader.cli.review_queue:run_cli",
        summary="what still needs a human look, triaged",
        section=SECTION_CHECK,
    ),
    Subcommand(
        name="consistency-audit",
        target="opengrader.cli.consistency_audit:run_cli",
        summary="fairness: was the same answer graded two different ways?",
        section=SECTION_CHECK,
    ),
    Subcommand(
        name="similarity-audit",
        target="opengrader.cli.similarity_audit:run_cli",
        summary="answers that look copied from one another",
        section=SECTION_CHECK,
    ),
    Subcommand(
        name="item-analysis",
        target="opengrader.cli.item_analysis:run_cli",
        summary="per-question difficulty, separation and outcome mastery",
        section=SECTION_CHECK,
    ),
    Subcommand(
        name="calibrate",
        target="opengrader.cli.calibrate:run_cli",
        summary="do your TAs grade alike? norming rounds and leniency",
        section=SECTION_CHECK,
    ),
    Subcommand(
        name="grading-stats",
        target="opengrader.cli.grading_stats:run_cli",
        summary="how long grading took, and how fast it went",
        section=SECTION_CHECK,
    ),
    Subcommand(
        name="llm-eval",
        target="opengrader.cli.llm_eval:main",
        summary="is the LLM grader trustworthy here, and above what confidence",
        section=SECTION_CHECK,
    ),
    Subcommand(
        name="transcribe-eval",
        target="opengrader.cli.transcribe_eval:main",
        summary="is the OCR trustworthy here, measured against human fixes",
        section=SECTION_CHECK,
    ),
    # --- Hand back ---
    Subcommand(
        name="grade-check",
        target="opengrader.cli.grade_check:main",
        summary="ready to hand back? a read-only pre-flight",
        section=SECTION_RETURN,
    ),
    Subcommand(
        name="grade-release",
        target="opengrader.cli.grade_release:run_cli",
        summary="record what each student was handed, and what moved since",
        section=SECTION_RETURN,
    ),
    Subcommand(
        name="canvas-export",
        target="opengrader.cli.canvas_export:run_cli",
        summary="push scores and comments to Canvas LMS",
        section=SECTION_RETURN,
    ),
    Subcommand(
        name="grades-export",
        target="opengrader.cli.grades_export:run_cli",
        summary="write the gradebook CSV, for everyone not using Canvas",
        section=SECTION_RETURN,
    ),
    Subcommand(
        name="return-bundle",
        target="opengrader.cli.return_bundle:run_cli",
        summary="assemble one hand-back folder: CSV, student PDFs, report",
        section=SECTION_RETURN,
    ),
    Subcommand(
        name="course-grades",
        target="opengrader.cli.course_grades:run_cli",
        summary="whole-semester grades, the at-risk list, outcome mastery",
        section=SECTION_RETURN,
    ),
    # --- Work with other graders ---
    Subcommand(
        name="claim",
        target="opengrader.cli.claim:run_cli",
        summary="divide the pile between TAs; see coverage and gaps",
        section=SECTION_GRADERS,
    ),
    Subcommand(
        name="grading-policy",
        target="opengrader.cli.grading_policy:run_cli",
        summary="anonymous grading: the policy lock and name redaction",
        section=SECTION_GRADERS,
    ),
    Subcommand(
        name="grade-merge",
        target="opengrader.cli.grade_merge:run_cli",
        summary="hand a redacted packet to a TA, then merge their grades back",
        section=SECTION_GRADERS,
    ),
    # --- History and safety ---
    Subcommand(
        name="grade-history",
        target="opengrader.cli.grade_history:run_cli",
        summary="the audit journal: who changed which grade, and when",
        section=SECTION_HISTORY,
    ),
    Subcommand(
        name="grade-restore",
        target="opengrader.cli.grade_restore:run_cli",
        summary="rewind grading to a moment, or as if a grader never acted",
        section=SECTION_HISTORY,
    ),
    Subcommand(
        name="grade-backup",
        target="opengrader.cli.grade_backup:run_cli",
        summary="snapshot, verify and restore a class's graded work",
        section=SECTION_HISTORY,
    ),
    # --- Not listed: development only ---
    Subcommand(
        name="tui-dev",
        target="opengrader.tui.main:run_app_dev",
        summary="open the grading app with Textual devtools attached",
        section=SECTION_START,
        takes_argv=False,
        hidden=True,
    ),
)


def _version() -> str:
    """The installed package version, or a placeholder when there is none.

    `importlib.metadata` raises when the package is not installed — which is
    exactly the case when someone runs this straight out of a source tree with
    `PYTHONPATH=src`. `--version` failing there would be a silly way to break a
    checkout, so the lookup degrades to a word instead of an exception.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("opengrader")
    except PackageNotFoundError:
        return "unknown (running from a source tree)"


def _index() -> dict[str, Subcommand]:
    """Every name a user may type — canonical and alias — mapped to its row.

    Built on demand rather than at import so the registry stays the single
    source of truth; it is thirty-odd dict inserts, which is nothing next to
    the import it is protecting.
    """
    table: dict[str, Subcommand] = {}
    for spec in COMMANDS:
        table[spec.name] = spec
        for alias in spec.aliases:
            table[alias] = spec
    return table


def find(name: str) -> Subcommand | None:
    """Resolve a typed name (canonical or alias) to its registry row."""
    return _index().get(name)


def unknown_command_message(name: str) -> str:
    """A rejection that names the near-miss, because these names are long.

    `submission-extracter` and `grade-releases` are the typos this tool
    actually gets, and a bare "no such command" sends the reader back to a
    list of thirty to hunt for the one character they got wrong. The 0.55
    cutoff is deliberately loose: a wrong suggestion costs a glance, a missing
    one costs another round trip through `--help`.
    """
    close = difflib.get_close_matches(name, sorted(_index()), n=3, cutoff=0.55)
    message = f"No such command {name!r}."
    if close:
        message += " Did you mean: " + ", ".join(close) + "?"
    return message + f"\nRun `{PROGRAM_NAME} --help` for the full list."


# --- Dispatch ---------------------------------------------------------------


def dispatch(spec: Subcommand, argv: list[str]) -> int:
    """Import the target module, run its entry function, return an exit code.

    `sys.argv` is patched for the duration of the call for two reasons. The
    obvious one is cosmetic: `argparse` takes its `prog` from `sys.argv[0]`,
    so without this a bad flag would be reported as `opengrader: error: …` and
    send the reader to `opengrader --help`, which lists commands rather than
    the flag they actually got wrong. (This is also why the five parsers that
    used to hardcode `prog=` no longer do — see the comment at each of them.)
    The load-bearing reason is that a couple of entry points (`doctor`, `tui-dev`)
    take no arguments and read `sys.argv` themselves — patching it is what lets
    them be dispatched at all, and is why they are marked `takes_argv=False`
    rather than being called with an argument they would reject.

    The exit code follows whatever the entry function already does, because
    these are the same functions the legacy console scripts call: a returned
    `int` is the code (`doctor`, `run`, `transcribe-eval`), a `SystemExit` from
    `argparse` — `--help`, or a bad flag — propagates untouched, and anything
    else means success. `bool` is excluded from the int case deliberately:
    `True` is an `int` in Python and would silently become exit code 1.
    """
    module = importlib.import_module(spec.module)
    entry = getattr(module, spec.attribute)

    saved_argv = sys.argv
    sys.argv = [f"{PROGRAM_NAME} {spec.name}", *argv]
    try:
        result = entry(argv) if spec.takes_argv else entry()
    except KeyboardInterrupt:
        # A grader pressing ctrl-c mid-extraction is ending the run, not
        # hitting a bug: say so on stderr and use the shell's own code for it,
        # rather than letting Python print a traceback over the progress bar.
        click.echo(f"\n{spec.name}: interrupted.", err=True)
        return EXIT_INTERRUPTED
    finally:
        sys.argv = saved_argv

    if isinstance(result, int) and not isinstance(result, bool):
        return result
    return 0


def _passthrough(spec: Subcommand) -> click.Command:
    """Build the click command that forwards everything to `spec`'s parser.

    `add_help_option=False` and the empty `help_option_names` are the whole
    trick: they stop click from claiming `-h`/`--help`, so the flag reaches the
    subcommand's own `argparse` and the user reads the real, current help for
    that tool instead of a summary this file would have to keep in sync.
    """

    @click.pass_context
    def callback(ctx: click.Context, args: tuple[str, ...]) -> None:
        ctx.exit(dispatch(spec, list(args)))

    return click.Command(
        name=spec.name,
        params=[click.Argument(["args"], nargs=-1, type=click.UNPROCESSED)],
        callback=callback,
        short_help=spec.summary,
        help=(
            f"{spec.summary.capitalize()}.\n\n"
            f"Arguments are passed straight through to `{spec.name}`; run "
            f"`{PROGRAM_NAME} {spec.name} --help` for its own options."
        ),
        add_help_option=False,
        context_settings={
            "ignore_unknown_options": True,
            "allow_extra_args": True,
            "help_option_names": [],
        },
    )


class OpengraderCLI(click.Group):
    """The group: lazy lookup, sectioned help, and a guess when you typo."""

    def list_commands(self, ctx: click.Context) -> list[str]:
        """Registry order, not alphabetical — see the note above `SECTION_*`."""
        return [spec.name for spec in COMMANDS if not spec.hidden] + sorted(self.commands)

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        # The handful of commands defined in this file (`help`, `commands`,
        # `completion`) are registered on the group the normal way; everything
        # else is built here, on demand, from the registry.
        builtin = self.commands.get(name)
        if builtin is not None:
            return builtin
        spec = find(name)
        return _passthrough(spec) if spec is not None else None

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        name = args[0] if args else ""
        if name and not name.startswith("-") and self.get_command(ctx, name) is None:
            raise click.UsageError(unknown_command_message(name), ctx)
        return super().resolve_command(ctx, args)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """Print the commands grouped by section instead of as one long list."""
        rows: dict[str, list[tuple[str, str]]] = {}
        for spec in COMMANDS:
            if spec.hidden:
                continue
            # Aliases ride along in the name column: someone reading the list
            # to find "how do I split a scan" should see `split` here rather
            # than discover it in a changelog.
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


_EPILOG = f"""\
\b
Examples:
  {PROGRAM_NAME} doctor                       is this machine ready?
  {PROGRAM_NAME} run --scan scans/all.pdf --qa qa.md --out class/
  {PROGRAM_NAME} tui --qa-file qa.md --submissions class/submissions/
  {PROGRAM_NAME} split --input scans/all.pdf --output split/ --pages-per-submission 6
  {PROGRAM_NAME} help submission-extractor    that tool's own options

Every subcommand takes its own flags: `{PROGRAM_NAME} <command> --help`.
The standalone commands (`tui`, `submission-splitter`, ...) still work exactly
as before; this is one door onto the same tools, not a replacement for them.
"""


@click.group(
    cls=OpengraderCLI,
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=_EPILOG,
)
@click.version_option(version=_version(), prog_name=PROGRAM_NAME)
def cli() -> None:
    """Opengrader — grade paper exams on your own machine.

    Start with `opengrader doctor` to check the machine, then
    `opengrader run` to take a stack of scans all the way to gradeable work,
    then `opengrader tui` to grade it.
    """


@cli.command("help", short_help="show help for one command")
@click.argument("command", required=False)
@click.pass_context
def help_command(ctx: click.Context, command: str | None) -> None:
    """Show help for OPENGRADER, or for one COMMAND."""
    if command is None:
        click.echo(ctx.parent.get_help() if ctx.parent else cli.get_help(ctx))
        return
    spec = find(command)
    if spec is None:
        builtin = cli.commands.get(command)
        if builtin is not None:
            # `info_name` is the *last* word of the usage line; click prepends
            # the parent's own name, so passing "opengrader commands" here
            # would print "opengrader opengrader commands".
            with click.Context(builtin, info_name=command, parent=ctx.parent) as sub_ctx:
                click.echo(sub_ctx.get_help())
            return
        raise click.UsageError(unknown_command_message(command), ctx)
    # Asking the tool itself rather than reprinting a summary: `help X` and
    # `X --help` must never be able to disagree.
    ctx.exit(dispatch(spec, ["--help"]))


@cli.command("commands", short_help="list every subcommand, one per line")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable output.")
def commands_command(as_json: bool) -> None:
    """List every subcommand (for scripts, and for finding one by keyword)."""
    if as_json:
        import json

        payload = [
            {
                "name": spec.name,
                "aliases": list(spec.aliases),
                "section": spec.section,
                "summary": spec.summary,
                "target": spec.target,
                "hidden": spec.hidden,
            }
            for spec in COMMANDS
        ]
        click.echo(json.dumps(payload, indent=2))
        return
    width = max(len(spec.name) for spec in COMMANDS if not spec.hidden)
    for spec in COMMANDS:
        if spec.hidden:
            continue
        click.echo(f"{spec.name.ljust(width)}  {spec.summary}")


@cli.command("completion", short_help="print a shell completion script")
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completion_command(shell: str) -> None:
    """Print the shell completion script for SHELL.

    Completes subcommand names — the flags belong to each tool's own parser,
    which this command never sees. Install it with, for bash:

    \b
      opengrader completion bash >> ~/.bashrc
    """
    from click.shell_completion import get_completion_class

    completion_class = get_completion_class(shell)
    if completion_class is None:  # pragma: no cover - click ships all three
        raise click.UsageError(f"No completion support for {shell!r}.")
    source = completion_class(cli, {}, PROGRAM_NAME, "_OPENGRADER_COMPLETE").source()
    click.echo(source)


def main() -> None:
    """Console-script entry point for `opengrader`."""
    cli(prog_name=PROGRAM_NAME)


if __name__ == "__main__":  # pragma: no cover - exercised via `python -m`
    main()
