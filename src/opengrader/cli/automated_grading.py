"""Automatically grade student submission files using a QA-format solutions file."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import click

from opengrader.cli import CONTEXT_SETTINGS, ai_default, run_command
from opengrader.core import app_config
from opengrader.core.grading.answer_cache import AnswerGradeCache
from opengrader.core.grading.rubric_draft import has_partial_credit_items
from opengrader.core.grading.llm_grader import (
    GRADE_SUGGESTION_SCHEMA,
    GenerateFn,
)
from opengrader.core.grading.solutions import (
    MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
    SolutionEntry,
    flatten_solution_entries,
    make_solution_entry,
    objective_correctness,
    solution_entries_for_submission,
)
from opengrader.core.qa_file import QADocument, load_qa_markdown
from opengrader.core.grading.automated import (
    DEFAULT_LLM_MIN_CONFIDENCE, JOURNAL_ACTOR_AUTO, JOURNAL_ACTOR_LLM,
    RunJournal as _RunJournal, grade_student_file,
)


EPILOG = """\
\b
Examples:
  uv run automated-grading --solutions solutions.md --input student-0001.submission.md
  uv run automated-grading --solutions solutions.md --input ./student-submissions/
  uv run automated-grading --solutions Solutions/solutions.md --input samples/student_responses.md
  uv run automated-grading --solutions Solutions/final_exam_solutions.md --input ./Solutions/student_submissions/
  uv run automated-grading --solutions solutions.md --input ./student-submissions/ --llm
  uv run automated-grading --solutions solutions.md --input ./student-submissions/ --llm --llm-model llama3.1 --llm-min-confidence 0.8
  uv run automated-grading --solutions solutions.md --input ./student-submissions/ --llm --dry-run

\b
Pick --llm-min-confidence from evidence, not from a guess:
  uv run llm-eval --solutions solutions.md --input ./student-submissions/
"""


@dataclass(frozen=True)
class GradingProgress:
    on_file_start: Callable[[str, int, int], None] | None = None
    on_question: Callable[[str, str], None] | None = None

    def file_start(self, file_name: str, index: int, total: int) -> None:
        if self.on_file_start is not None:
            self.on_file_start(file_name, index, total)

    def question(self, file_name: str, question_id: str) -> None:
        if self.on_question is not None:
            self.on_question(file_name, question_id)


_SolutionEntry = SolutionEntry
_make_solution_entry = make_solution_entry
_flatten_solution_entries = flatten_solution_entries
_solution_entries_for_submission = solution_entries_for_submission
_objective_correctness = objective_correctness


def questions_without_partial_credit(qa_doc: QADocument) -> list[str]:
    """Written-answer questions whose rubric has no described partial-credit items.

    For these the AI can only choose Correct, the bare Partially Correct, or
    Incorrect. Objective questions (choice, numeric, true/false) are graded
    exactly, so they never need one.
    """
    question_sets = list(qa_doc.version_questions.values()) or [qa_doc.questions]
    missing: list[str] = []
    for questions in question_sets:
        for question_id, entry in flatten_solution_entries(questions).items():
            objective = (
                entry.is_multiple_choice
                or entry.is_multiple_answer
                or entry.is_numeric
                or entry.true_false_correct is not None
            )
            if objective or has_partial_credit_items(entry.rubric_options):
                continue
            if question_id not in missing:
                missing.append(question_id)
    return missing


def _submission_paths(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    direct = sorted(input_path.glob("*.submission.md"))
    nested = sorted(input_path.glob("*/*.submission.md"))
    return direct + nested


def _start_ollama_server_if_needed(host: str, emit: Callable[[str], None]) -> None:
    parsed = urlparse(host)
    hostname = parsed.hostname or "127.0.0.1"
    port = parsed.port or 11434
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex((hostname, port)) == 0:
            return
    emit(f"Starting Ollama server at {host}…")
    try:
        subprocess.Popen(
            ["ollama", "serve"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            "'ollama' binary not found. Please install Ollama first."
        ) from exc
    deadline = time.time() + 30
    while time.time() < deadline:
        time.sleep(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex((hostname, port)) == 0:
                return
    emit("Warning: timed out waiting for Ollama to start.")


@dataclass(frozen=True)
class _Hooks:
    progress: GradingProgress | None = None
    status_callback: Callable[[str], None] | None = None


@click.command("automated-grading", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--solutions", required=True, help="Path to the solutions markdown file"
)
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Path to a single student file or a directory of files",
)
@click.option(
    "--llm/--no-llm",
    default=ai_default("grade_written"),
    show_default="from AI Setup",
    help=(
        "Use a local LLM (Ollama) to suggest rubric grades for free "
        "responses that do not match exactly. Suggestions are written as "
        "normal grades tagged '[AI]' for you to review in the TUI."
    ),
)
@click.option(
    "--llm-model",
    "text_model",
    default=ai_default("text_model"),
    show_default="from AI Setup",
    help=(
        "Ollama model to grade with. "
        "Any installed chat model works, e.g. llama3.1, mistral, qwen2.5."
    ),
)
@click.option(
    "--llm-host",
    default=ai_default("host"),
    show_default="from AI Setup",
    help="Ollama server URL.",
)
@click.option(
    "--llm-timeout",
    type=float,
    default=app_config.DEFAULT_TIMEOUT_SECONDS,
    show_default=True,
    help="Per-request timeout in seconds.",
)
@click.option(
    "--llm-min-confidence",
    type=float,
    default=ai_default("min_confidence"),
    show_default="from AI Setup",
    help=(
        "Only write AI suggestions at or above this confidence (0-1); "
        "weaker ones are left for manual grading. "
        "Run 'llm-eval' against already-graded submissions to find the "
        "value your model and exam actually support."
    ),
)
@click.option(
    "--llm-min-transcription-confidence",
    type=float,
    default=MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
    show_default=True,
    metavar="PERCENT",
    help=(
        "Do not ask the model to grade an answer whose OCR transcription "
        "confidence is below this (0-100); those go "
        "to manual review instead, where the review queue already flags "
        "them. The model reads the transcribed text and never the image, "
        "so below this it would be grading words nobody vouched for. "
        "Answers with no measured confidence (vision-model reads) are "
        "always graded. Use 0 to disable."
    ),
)
@click.option(
    "--auto-ollama",
    is_flag=True,
    help=(
        "Before grading, start 'ollama serve' if nothing is listening and "
        "pull the grading model. Mirrors the split/extract/answer-key steps; "
        "off by default so scripted runs fail fast instead of downloading."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would change (including AI suggestions) without writing.",
)
@click.option(
    "--no-journal",
    is_flag=True,
    help=(
        "Do not record this run in the audit journal. Off by default: "
        "journalling is what lets 'grade-restore' undo a bad batch."
    ),
)
@click.option(
    "--yes-send-to-cloud",
    is_flag=True,
    help=(
        "Confirm sending free-response answers to a cloud AI provider for "
        "--llm grading. The scale and destination are shown first; nothing "
        "is sent without this or an interactive yes. Never implied by --llm."
    ),
)
@click.pass_obj
def command(
    hooks: _Hooks | None,
    *,
    solutions: str,
    input_path: Path,
    llm: bool,
    text_model: str,
    llm_host: str,
    llm_timeout: float,
    llm_min_confidence: float,
    llm_min_transcription_confidence: float,
    auto_ollama: bool,
    dry_run: bool,
    no_journal: bool,
    yes_send_to_cloud: bool,
) -> None:
    """Grade student markdown files using a solutions template."""
    hooks = hooks or _Hooks()
    progress = hooks.progress or GradingProgress()

    def emit(message: str) -> None:
        print(message)
        if hooks.status_callback is not None:
            hooks.status_callback(message)

    if llm:
        resolved_ai = app_config.resolve()
        problems = resolved_ai.problems()
        if problems:
            for problem in problems:
                print(f"Error: {problem}", file=sys.stderr)
            sys.exit(2)

    solutions_path = Path(solutions)
    if not solutions_path.exists():
        print(f"Error: Solutions file not found at {solutions_path}")
        sys.exit(1)

    qa_doc = load_qa_markdown(solutions_path)

    if not input_path.is_file() and not input_path.is_dir():
        print(f"Error: Input path '{input_path}' is not a file or directory.")
        sys.exit(1)

    paths = _submission_paths(input_path)

    llm_generate: GenerateFn | None = None
    llm_model = ""
    llm_provider = ""
    if llm:
        resolved = app_config.with_overrides(
            resolved_ai,
            host=llm_host,
            text_model=text_model,
            timeout_seconds=llm_timeout,
        )
        if auto_ollama and resolved.kind == "local":
            try:
                _start_ollama_server_if_needed(resolved.host, emit)
            except RuntimeError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
            emit(
                f"Pulling model '{resolved.text_model}' if needed — "
                "first run may download several GB…"
            )
            subprocess.run(
                ["ollama", "pull", resolved.text_model],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        if resolved.kind == "cloud":
            from opengrader.core.cloud_preflight import CloudPreflight, cli_confirm_cloud_send

            # A call per written answer AI grades, not per student: the
            # ceiling is every question in the key for every submission.
            ceiling = len(paths) * max(1, len(qa_doc.questions))
            preflight = CloudPreflight(
                provider_label=resolved.provider.label,
                host=resolved.host,
                counts={"graded-text": ceiling} if paths else {},
                uncounted=(
                    {
                        "graded-text": (
                            f"up to {ceiling} graded-text call"
                            f"{'' if ceiling == 1 else 's'} (one per written answer "
                            f"AI grades, across {len(paths)} submission"
                            f"{'' if len(paths) == 1 else 's'})"
                        )
                    }
                    if paths
                    else {}
                ),
                students=len(paths),
            )
            cli_confirm_cloud_send(
                resolved,
                preflight,
                yes_send_to_cloud=yes_send_to_cloud,
                emit=lambda message: print(message, file=sys.stderr),
            )
        try:
            app_config.ensure_ready(resolved, purpose="text")
        except (app_config.CloudConsentRequired, app_config.CloudDisabledError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            print(
                f"Fix: run 'opengrader providers consent {resolved.provider.name}' "
                "(or open AI Setup), then retry.",
                file=sys.stderr,
            )
            sys.exit(2)
        except app_config.AIUnavailableError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        llm_generate = app_config.text_generate(
            resolved, response_format=GRADE_SUGGESTION_SCHEMA
        )
        llm_model = resolved.text_model
        llm_provider = resolved.provider.name
        emit(
            f"LLM free-response grading enabled (model={resolved.text_model}, "
            f"min confidence={llm_min_confidence:g})."
        )
        coarse = questions_without_partial_credit(qa_doc)
        if coarse:
            emit(
                f"Note: {', '.join(coarse)} "
                f"{'has' if len(coarse) == 1 else 'have'} no partial-credit rubric "
                "items yet, so the AI can only choose Correct, Partially Correct "
                "or Incorrect there. For finer partial credit, draft them first "
                "and review them:\n"
                f"  uv run draft-rubric --solutions {solutions_path} --input {input_path} --write"
            )

    total = len(paths)
    failures = 0

    run_journal: _RunJournal | None = None
    if not dry_run and not no_journal:
        run_journal = _RunJournal(input_path)

    answer_cache = AnswerGradeCache()

    from opengrader.core import egress

    with egress.recording(input_path):
        for index, md_file in enumerate(paths, start=1):
            progress.file_start(md_file.name, index, total)
            try:
                grade_student_file(
                    md_file,
                    qa_doc,
                    llm_generate=llm_generate,
                    llm_min_confidence=llm_min_confidence,
                    llm_model=llm_model,
                    llm_provider=llm_provider,
                    min_transcription_confidence=llm_min_transcription_confidence,
                    answer_cache=answer_cache,
                    dry_run=dry_run,
                    question_callback=lambda qid, name=md_file.name: progress.question(
                        name, qid
                    ),
                    journal=run_journal,
                )
            except ValueError as exc:
                failures += 1
                print(f"Error grading {md_file}: {exc}", file=sys.stderr)

    grouping_summary = answer_cache.summary_line()
    if grouping_summary:
        print(f"\n{grouping_summary}")

    if failures:
        print(f"{failures} file(s) could not be graded.", file=sys.stderr)

    if run_journal is not None:
        print(
            "\nThis run was recorded in the audit journal. To undo it:\n"
            f"  uv run grade-restore apply --submissions {input_path} "
            f"--without-actor {JOURNAL_ACTOR_AUTO} --yes"
            + (
                f"\n  uv run grade-restore apply --submissions {input_path} "
                f"--without-actor {JOURNAL_ACTOR_LLM} --yes   # AI grades only"
                if llm_generate is not None
                else ""
            )
        )


def run_cli(
    argv: Sequence[str],
    *,
    progress: GradingProgress | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> None:
    run_command(
        command,
        argv,
        obj=_Hooks(progress=progress, status_callback=status_callback),
    )


if __name__ == "__main__":
    command()
