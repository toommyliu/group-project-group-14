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
from opengrader.core.grading import suggest_free_response_grade
from opengrader.core.grading.answer_cache import AnswerGradeCache, answer_grade_key
from opengrader.core.grading.llm_grader import (
    GRADE_SUGGESTION_SCHEMA,
    GenerateFn,
    GradeSuggestion,
    format_ai_analysis,
)
from opengrader.core.grading.solutions import (
    MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
    SolutionEntry,
    flatten_solution_entries,
    make_solution_entry,
    needs_llm_grade,
    objective_correctness,
    solution_entries_for_submission,
    solution_version_for_submission,
)
from opengrader.core.llm import OllamaConfig, OllamaError, ensure_model_available, generate
from opengrader.core.qa_file import QADocument, load_qa_markdown
from opengrader.core.history import GradeJournal, current_actor, resolve_history_dir
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionGradeUpdate,
    StudentSubmissionQuestion,
    load_student_submission_markdown,
    update_student_submission_markdown,
)

DEFAULT_LLM_MIN_CONFIDENCE = ai_config.DEFAULT_MIN_CONFIDENCE

JOURNAL_ACTOR_AUTO = "automated-grading"
JOURNAL_ACTOR_LLM = "automated-grading:llm"


class _RunJournal:
    def __init__(self, submissions_path: Path, *, operator: str | None = None) -> None:
        self._dir = resolve_history_dir(submissions_path)
        self._operator = operator or current_actor()
        self._journals: dict[str, GradeJournal] = {}

    @property
    def operator(self) -> str:
        return self._operator

    def _journal_for(self, actor: str) -> GradeJournal:
        journal = self._journals.get(actor)
        if journal is None:
            journal = GradeJournal(self._dir, actor=actor)
            self._journals[actor] = journal
        return journal

    def record_grade(
        self,
        *,
        actor: str,
        document: StudentSubmissionDocument,
        question_id: str,
        summary: str,
        old_points: float | None,
        old_rubric: tuple[str, ...],
        new_points: float | None,
        new_rubric: tuple[str, ...],
    ) -> None:
        try:
            self._journal_for(actor).append(
                kind="grade",
                student_id=document.student_id,
                student_name=document.student_name,
                question_id=question_id,
                summary=f"{summary} (run by {self._operator})",
                old={"points": old_points, "rubric": list(old_rubric)},
                new={"points": new_points, "rubric": list(new_rubric)},
            )
        except OSError:
            pass

    def record_comment(
        self,
        *,
        actor: str,
        document: StudentSubmissionDocument,
        question_id: str,
        old_analysis: str,
        new_analysis: str,
    ) -> None:
        try:
            self._journal_for(actor).append(
                kind="comment",
                student_id=document.student_id,
                student_name=document.student_name,
                question_id=question_id,
                summary=f"auto analysis (run by {self._operator})",
                old={"analysis": old_analysis},
                new={"analysis": new_analysis},
            )
        except OSError:
            pass


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


def grade_student_file(
    student_path: Path,
    qa_doc: QADocument,
    *,
    llm_generate: GenerateFn | None = None,
    llm_min_confidence: float = DEFAULT_LLM_MIN_CONFIDENCE,
    llm_model: str = "",
    min_transcription_confidence: float = MIN_TRUSTED_TRANSCRIPTION_CONFIDENCE,
    answer_cache: AnswerGradeCache | None = None,
    dry_run: bool = False,
    question_callback: Callable[[str], None] | None = None,
    journal: "_RunJournal | None" = None,
) -> None:
    document = load_student_submission_markdown(student_path)
    solutions = solution_entries_for_submission(qa_doc, document)
    version = solution_version_for_submission(qa_doc, document)
    cache = answer_cache if answer_cache is not None else AnswerGradeCache()

    content = student_path.read_text()
    updated = content
    ai_applied: list[str] = []
    ai_skipped: list[str] = []
    unreadable_skipped: list[str] = []
    pending_journal: list[_PendingRecord] = []
    for question in document.questions:
        entry = solutions.get(question.question_id)
        if entry is None:
            continue

        response = question.response_text.strip()
        if not response:
            continue

        if question_callback is not None:
            question_callback(question.question_id)

        objective_correct = objective_correctness(entry, response, question)

        points_awarded: float | None = None
        rubric_labels: tuple[str, ...] | None = None
        analysis: str | None = "Correct" if objective_correct else None

        if objective_correct is not None:
            points_awarded = entry.points if objective_correct else 0.0
        elif not needs_llm_grade(
            entry,
            objective_correct,
            question,
            min_transcription_confidence=min_transcription_confidence,
        ):
            if entry.is_numeric:
                pass
            elif question.response_confidence is not None:
                unreadable_skipped.append(
                    f"{question.question_id} "
                    f"(transcription {question.response_confidence:.0f}% < "
                    f"{min_transcription_confidence:g}%)"
                )
        elif llm_generate is not None and question.points_awarded is None:
            suggestion = _suggest_or_warn(
                question_id=question.question_id,
                response=response,
                entry=entry,
                llm_generate=llm_generate,
                cache=cache,
                key=answer_grade_key(
                    question_id=question.question_id,
                    assignment_version=version,
                    response_text=response,
                    question_type=question.question_type,
                ),
            )
            if suggestion is None:
                pass
            elif suggestion.confidence >= llm_min_confidence:
                points_awarded = suggestion.points_awarded
                rubric_labels = suggestion.rubric_labels
                analysis = format_ai_analysis(suggestion, model=llm_model)
                ai_applied.append(
                    f"{question.question_id}: {', '.join(rubric_labels)} "
                    f"= {points_awarded:g}/{entry.points:g} "
                    f"({round(suggestion.confidence * 100)}%)"
                )
            else:
                ai_skipped.append(
                    f"{question.question_id} "
                    f"({round(suggestion.confidence * 100)}% < "
                    f"{round(llm_min_confidence * 100)}%)"
                )

        updated = update_student_submission_markdown(
            updated,
            StudentSubmissionGradeUpdate(
                question_id=question.question_id,
                possible_points=entry.points,
                points_awarded=points_awarded,
                rubric_labels=rubric_labels,
                analysis=analysis,
            ),
        )

        if journal is not None:
            pending_journal.extend(
                _pending_records_for_question(
                    question=question,
                    ai_applied=rubric_labels is not None,
                    points_awarded=points_awarded,
                    rubric_labels=rubric_labels,
                    analysis=analysis,
                )
            )

    if dry_run:
        print(f"[dry run] {student_path.name}: no changes written.")
    else:
        if updated != content:
            student_path.write_text(updated)
            if journal is not None:
                _flush_journal(journal, document, pending_journal)
        print(f"Successfully graded and updated {student_path.name}")

    for line in ai_applied:
        print(f"  AI graded {line}")
    for line in ai_skipped:
        print(f"  AI low confidence, left for manual review: {line}")
    for line in unreadable_skipped:
        print(f"  Transcription too weak to grade, left for manual review: {line}")


@dataclass(frozen=True)
class _PendingRecord:
    actor: str
    kind: str
    question_id: str
    summary: str
    old_points: float | None = None
    old_rubric: tuple[str, ...] = ()
    new_points: float | None = None
    new_rubric: tuple[str, ...] = ()
    old_analysis: str = ""
    new_analysis: str = ""


def _pending_records_for_question(
    *,
    question: "StudentSubmissionQuestion",
    ai_applied: bool,
    points_awarded: float | None,
    rubric_labels: tuple[str, ...] | None,
    analysis: str | None,
) -> list[_PendingRecord]:
    actor = JOURNAL_ACTOR_LLM if ai_applied else JOURNAL_ACTOR_AUTO
    records: list[_PendingRecord] = []

    if points_awarded is not None:
        new_rubric = tuple(rubric_labels or ())
        old_points = question.points_awarded
        old_rubric = tuple(question.rubric_labels)
        changed = old_points != points_awarded or old_rubric != new_rubric
        if changed:
            records.append(
                _PendingRecord(
                    actor=actor,
                    kind="grade",
                    question_id=question.question_id,
                    summary=f"auto graded {points_awarded:g}",
                    old_points=old_points,
                    old_rubric=old_rubric,
                    new_points=points_awarded,
                    new_rubric=new_rubric,
                )
            )

    if analysis is not None and analysis != (question.analysis or ""):
        records.append(
            _PendingRecord(
                actor=actor,
                kind="comment",
                question_id=question.question_id,
                summary="auto analysis",
                old_analysis=question.analysis or "",
                new_analysis=analysis,
            )
        )
    return records


def _flush_journal(
    journal: "_RunJournal",
    document: StudentSubmissionDocument,
    records: list[_PendingRecord],
) -> None:
    for record in records:
        if record.kind == "grade":
            journal.record_grade(
                actor=record.actor,
                document=document,
                question_id=record.question_id,
                summary=record.summary,
                old_points=record.old_points,
                old_rubric=record.old_rubric,
                new_points=record.new_points,
                new_rubric=record.new_rubric,
            )
        else:
            journal.record_comment(
                actor=record.actor,
                document=document,
                question_id=record.question_id,
                old_analysis=record.old_analysis,
                new_analysis=record.new_analysis,
            )


def _suggest_or_warn(
    *,
    question_id: str,
    response: str,
    entry: _SolutionEntry,
    llm_generate: GenerateFn,
    cache: AnswerGradeCache,
    key,  # noqa: ANN001
) -> GradeSuggestion | None:
    try:
        return cache.suggest(
            key,
            lambda: suggest_free_response_grade(
                prompt=entry.prompt,
                expected_answers=list(entry.expected_answers),
                response_text=response,
                rubric_options=list(entry.rubric_options),
                possible_points=entry.points,
                generate=llm_generate,
            ),
        )
    except OllamaError as exc:
        print(f"  LLM grading failed for {question_id}: {exc}", file=sys.stderr)
        return None


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
