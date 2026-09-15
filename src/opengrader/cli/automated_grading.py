"""Automatically grade student submission files using a QA-format solutions file."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from argparse import ArgumentParser, BooleanOptionalAction, RawDescriptionHelpFormatter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from opengrader.core import ai_config
from opengrader.core.grading.answer_cache import AnswerGradeCache
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
from opengrader.core.llm import OllamaConfig, OllamaError, ensure_model_available, generate
from opengrader.core.qa_file import load_qa_markdown
from opengrader.core.grading.automated import (
    DEFAULT_LLM_MIN_CONFIDENCE, JOURNAL_ACTOR_AUTO, JOURNAL_ACTOR_LLM,
    RunJournal as _RunJournal, grade_student_file,
)


EPILOG = """
examples:
  uv run automated-grading --solutions solutions.md --input student-0001.submission.md
  uv run automated-grading --solutions solutions.md --input ./student-submissions/
  uv run automated-grading --solutions Solutions/solutions.md --input samples/student_responses.md
  uv run automated-grading --solutions Solutions/final_exam_solutions.md --input ./Solutions/student_submissions/
  uv run automated-grading --solutions solutions.md --input ./student-submissions/ --llm
  uv run automated-grading --solutions solutions.md --input ./student-submissions/ --llm --llm-model llama3.1 --llm-min-confidence 0.8
  uv run automated-grading --solutions solutions.md --input ./student-submissions/ --llm --dry-run

pick --llm-min-confidence from evidence, not from a guess:
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


def _submission_paths(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    direct = sorted(input_path.glob("*.submission.md"))
    nested = sorted(input_path.glob("*/*.submission.md"))
    return direct + nested


def setup_args() -> ArgumentParser:
    ai = ai_config.resolve()
    parser = ArgumentParser(
        description="Grade student markdown files using a solutions template.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--solutions", required=True, help="Path to the solutions markdown file"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a single student file or a directory of files",
    )
    parser.add_argument(
        "--llm",
        action=BooleanOptionalAction,
        default=ai.grade_written,
        help=(
            "Use a local LLM (Ollama) to suggest rubric grades for free "
            "responses that do not match exactly. Suggestions are written as "
            "normal grades tagged '[AI]' for you to review in the TUI. "
            "(default: %(default)s, from AI Setup)"
        ),
    )
    parser.add_argument(
        "--llm-model",
        default=ai.text_model,
        help=(
            "Ollama model to grade with (default: %(default)s, from AI Setup). "
            "Any installed chat model works, e.g. llama3.1, mistral, qwen2.5."
        ),
    )
    parser.add_argument(
        "--llm-host",
        default=ai.host,
        help="Ollama server URL (default: %(default)s, from AI Setup).",
    )
    parser.add_argument(
        "--llm-timeout",
        type=float,
        default=OllamaConfig.timeout_seconds,
        help="Per-request timeout in seconds (default: %(default)s).",
    )
    parser.add_argument(
        "--llm-min-confidence",
        type=float,
        default=ai.min_confidence,
        help=(
            "Only write AI suggestions at or above this confidence (0-1); "
            "weaker ones are left for manual grading (default: %(default)s). "
            "Run 'llm-eval' against already-graded submissions to find the "
            "value your model and exam actually support."
        ),
    )
    parser.add_argument(
        "--llm-min-transcription-confidence",
        type=float,
        default=MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
        metavar="PERCENT",
        help=(
            "Do not ask the model to grade an answer whose OCR transcription "
            "confidence is below this (0-100, default: %(default)s); those go "
            "to manual review instead, where the review queue already flags "
            "them. The model reads the transcribed text and never the image, "
            "so below this it would be grading words nobody vouched for. "
            "Answers with no measured confidence (vision-model reads) are "
            "always graded. Use 0 to disable."
        ),
    )
    parser.add_argument(
        "--auto-ollama",
        action="store_true",
        help=(
            "Before grading, start 'ollama serve' if nothing is listening and "
            "pull the grading model. Mirrors the split/extract/answer-key steps; "
            "off by default so scripted runs fail fast instead of downloading."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would change (including AI suggestions) without writing.",
    )
    parser.add_argument(
        "--no-journal",
        action="store_true",
        help=(
            "Do not record this run in the audit journal. Off by default: "
            "journalling is what lets 'grade-restore' undo a bad batch."
        ),
    )
    return parser


def _start_ollama_server_if_needed(
    config: OllamaConfig, emit: Callable[[str], None]
) -> None:
    parsed = urlparse(config.host)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 11434
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if probe.connect_ex((host, port)) == 0:
            return
    emit(f"Starting Ollama server at {config.host}…")
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
            if probe.connect_ex((host, port)) == 0:
                return
    emit("Warning: timed out waiting for Ollama to start.")


def run_cli(
    argv: list[str] | None = None,
    *,
    progress: GradingProgress | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> None:
    progress = progress or GradingProgress()

    def emit(message: str) -> None:
        print(message)
        if status_callback is not None:
            status_callback(message)

    parser = setup_args()
    args = parser.parse_args(argv)

    if args.llm:
        resolved_ai = ai_config.resolve()
        problems = resolved_ai.problems()
        if problems:
            for problem in problems:
                print(f"Error: {problem}", file=sys.stderr)
            sys.exit(2)

    solutions_path = Path(args.solutions)
    if not solutions_path.exists():
        print(f"Error: Solutions file not found at {solutions_path}")
        sys.exit(1)

    qa_doc = load_qa_markdown(solutions_path)

    input_path = Path(args.input)
    if not input_path.is_file() and not input_path.is_dir():
        print(f"Error: Input path '{args.input}' is not a file or directory.")
        sys.exit(1)

    llm_generate: GenerateFn | None = None
    llm_model = ""
    if args.llm:
        config = OllamaConfig(
            model=args.llm_model,
            host=args.llm_host,
            timeout_seconds=args.llm_timeout,
        )
        if args.auto_ollama:
            try:
                _start_ollama_server_if_needed(config, emit)
            except RuntimeError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                sys.exit(1)
            emit(
                f"Pulling model '{config.model}' if needed — "
                "first run may download several GB…"
            )
            subprocess.run(
                ["ollama", "pull", config.model],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        try:
            ensure_model_available(config)
        except OllamaError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        llm_generate = lambda prompt: generate(  # noqa: E731
            prompt=prompt, config=config, response_format=GRADE_SUGGESTION_SCHEMA
        )
        llm_model = config.model
        emit(
            f"LLM free-response grading enabled (model={config.model}, "
            f"min confidence={args.llm_min_confidence:g})."
        )

    paths = _submission_paths(input_path)
    total = len(paths)
    failures = 0

    run_journal: _RunJournal | None = None
    if not args.dry_run and not args.no_journal:
        run_journal = _RunJournal(input_path)

    answer_cache = AnswerGradeCache()

    for index, md_file in enumerate(paths, start=1):
        progress.file_start(md_file.name, index, total)
        try:
            grade_student_file(
                md_file,
                qa_doc,
                llm_generate=llm_generate,
                llm_min_confidence=args.llm_min_confidence,
                llm_model=llm_model,
                min_transcription_confidence=args.llm_min_transcription_confidence,
                answer_cache=answer_cache,
                dry_run=args.dry_run,
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


if __name__ == "__main__":
    run_cli()
