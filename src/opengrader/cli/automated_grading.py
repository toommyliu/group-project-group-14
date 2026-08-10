"""Automatically grade student submission files using a QA-format solutions file.

Exact-match grading: a response earns full credit when its stripped text
matches one of the QA question's correct answers (or the correct choice
label for multiple choice, compared case-insensitively and ignoring
trailing punctuation like "b." or "B)"). Incorrect multiple-choice
responses earn 0; incorrect free responses are left untouched (no Points,
no Analysis changes) for manual grading.

Numeric and True/False questions grade without an LLM. A numeric answer key
(e.g. ``42 ±0.5 units:mL``, ``[40, 44]``) is compared with tolerance: a match
earns full credit, while a miss is left for manual review rather than
auto-zeroed, since numeric handwriting is easy to misread. True/False (a
constrained two-option multiple choice) grades to full or zero credit and
accepts the answer as a word ("True"/"F"/"yes") or its A/B choice label.

Optional LLM assistance (``--llm``): free responses that do not match
exactly are sent to a local Ollama model, which *suggests* a rubric
selection. The point value is computed from the rubric (never invented by
the model), the rationale is written to Analysis tagged
``[AI · NN% · model]``, and only suggestions at/above
``--llm-min-confidence`` are applied — everything stays reviewable and
overridable in the grading TUI, where ``y`` confirms one.

Two rules bound what the model is asked, both in ``core/grading``:

**One call per distinct answer, not per student** (``answer_cache.py``). A
cohort's identical answers are one decision, copied — the same thing the TUI's
``b`` mode does for a human. It is the difference between 27 model calls and
200 on a class of 200, and it is what makes it *impossible* for two students
who wrote the same words to receive different AI grades.

**Nothing is graded from a transcription the OCR doubts**
(``--llm-min-transcription-confidence``). The model reads ``Response text`` and
never the image, so below the floor it would be grading a garbled string with
no way to know — the stance the numeric branch already took, applied to prose.
Those answers go to manual review, where the queue already flags them.

Every grade written here is recorded in the assignment's audit journal
(`core/history`) under its own actor — ``automated-grading`` for a
deterministic match, ``automated-grading:llm`` for an AI-applied one — so a
batch that went wrong can be undone wholesale without touching a human's
work::

    uv run grade-restore apply --submissions out/subs/ \\
        --without-actor automated-grading:llm --yes

That separation is the point: an instructor who distrusts the model's
judgement can drop only the AI's grades and keep the exact-match ones.

**Choosing ``--llm-min-confidence``**: don't guess it. ``uv run llm-eval``
replays this exact grading path over submissions a human already graded and
reports, per model and per confidence bucket, how often the model agreed — then
names the threshold that evidence supports.
"""

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

# Confidence an AI suggestion must reach before it is written as a grade. A
# starting point, not a recommendation: `llm-eval` measures a real model against
# answers a human graded and names the value the evidence supports. Named rather
# than inlined so the CLI flag and the TUI's field can never drift apart. Points
# at ai_config's copy rather than restating the number, so there is exactly one
# 0.7 in the codebase (ai_config.py asserts the two stay equal in its tests).
DEFAULT_LLM_MIN_CONFIDENCE = ai_config.DEFAULT_MIN_CONFIDENCE

# The batch grader journals under its own name rather than the person who
# launched it, so `grade-restore --without-actor` can drop a bad run without
# also dropping that person's hand grading. The LLM gets a second, narrower
# actor because "undo the AI's guesses but keep the exact matches" is a
# distinct and very reasonable thing to want.
JOURNAL_ACTOR_AUTO = "automated-grading"
JOURNAL_ACTOR_LLM = "automated-grading:llm"


class _RunJournal:
    """Lazily-opened per-actor journals for one batch run.

    One `GradeJournal` per actor (each writes its own session file, which the
    reader merges), created on first use so a dry run or an all-skipped run
    leaves no empty files behind. Every write is best-effort: accountability
    must never fail a grade that already landed on disk, matching the TUI
    provider's rule.
    """

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
                # Naming the human who launched the run keeps attribution
                # intact even though the actor is the tool.
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
    """UI-agnostic progress reporter for automated grading.

    ``file_start`` fires once per submission just before it is graded, carrying
    the file's 1-based position and the cohort total (so a caller can size a
    progress bar and show "3/20"); ``question`` then ticks as each answer in
    that file is graded — it lingers naturally on the slow free-response LLM
    calls, so the status can show exactly which question is in flight.

    Both callbacks are optional, so a bare ``GradingProgress()`` is a no-op and
    core/CLI code can call the methods unconditionally.
    """

    on_file_start: Callable[[str, int, int], None] | None = None
    on_question: Callable[[str, str], None] | None = None

    def file_start(self, file_name: str, index: int, total: int) -> None:
        if self.on_file_start is not None:
            self.on_file_start(file_name, index, total)

    def question(self, file_name: str, question_id: str) -> None:
        if self.on_question is not None:
            self.on_question(file_name, question_id)


# The answer-key model and the rule-based grading decisions moved to
# `core/grading/solutions.py` so the `llm-eval` harness measures exactly the
# answers this CLI sends to a model. These aliases keep the historical private
# names working for callers and tests that already reach for them.
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
    """Score a submission against the QA solutions and update its file.

    Multiple choice and exact free-response matches are graded as before. When
    ``llm_generate`` is supplied, free responses that did not match exactly are
    sent to the model, which *suggests* a rubric selection; suggestions at or
    above ``llm_min_confidence`` are written (points from the rubric, rationale
    into Analysis, tagged ``[AI · NN% · model]``), and weaker ones are left for
    manual grading. ``dry_run`` prints the AI suggestions without writing.

    ``answer_cache`` is what makes a run grade the *answer* rather than the
    student: pass one shared instance across the cohort and every distinct
    answer costs exactly one model call, with identical answers necessarily
    receiving identical grades. Left unset, each call gets a private cache —
    which changes nothing, since one submission holds one answer per question.

    When a ``journal`` is given, every grade this run *changes* is recorded in
    the audit journal so the batch can be rolled back with ``grade-restore``.
    Entries are appended only after the file write succeeds — journalling a
    change that never landed would make the history lie.
    """
    document = load_student_submission_markdown(student_path)
    solutions = solution_entries_for_submission(qa_doc, document)
    version = solution_version_for_submission(qa_doc, document)
    cache = answer_cache if answer_cache is not None else AnswerGradeCache()

    content = student_path.read_text()
    updated = content
    ai_applied: list[str] = []
    ai_skipped: list[str] = []
    unreadable_skipped: list[str] = []
    # Buffered until the write succeeds; each is (actor, question, old, new).
    pending_journal: list[_PendingRecord] = []
    for question in document.questions:
        entry = solutions.get(question.question_id)
        if entry is None:
            continue

        response = question.response_text.strip()
        if not response:
            continue

        # Report the question we are about to grade — for a free response this
        # is immediately before the (slow) LLM call, so the UI can show it.
        if question_callback is not None:
            question_callback(question.question_id)

        # Objective questions grade definitively: multiple choice and
        # True/False to full or zero credit, numeric to full credit on a
        # tolerance match. A miss on a numeric or free response earns no
        # automatic grade — it is left for a human (or the LLM, for free
        # responses only).
        objective_correct = objective_correctness(entry, response, question)

        # Incorrect free responses stay untouched for manual grading:
        # points_awarded=None leaves Points as-is, analysis=None never
        # clobbers an existing Analysis. rubric_labels stays None unless the
        # LLM proposes one.
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
            # Left for manual review, not sent to the LLM, never auto-zeroed —
            # either a numeric miss (the model grades prose, not numbers) or a
            # transcription the OCR is not confident it read, which the model
            # would grade as though the garbled text were the student's words.
            # `needs_llm_grade` is shared with the `llm-eval` harness so both
            # agree on what a model is asked to grade.
            if entry.is_numeric:
                pass
            elif question.response_confidence is not None:
                unreadable_skipped.append(
                    f"{question.question_id} "
                    f"(transcription {question.response_confidence:.0f}% < "
                    f"{min_transcription_confidence:g}%)"
                )
        elif llm_generate is not None and question.points_awarded is None:
            # Never let AI overwrite an existing grade (manual or a prior AI
            # run): only unscored free responses are sent to the model.
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
            # Only after the bytes are on disk: the journal describes what the
            # file now holds, so an unwritten change must not appear in it.
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
    """One journal entry held until the submission file is safely written."""

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
    """Describe what this question's update actually changed, for the journal.

    Nothing is recorded for a no-op: ``points_awarded=None`` means the writer
    was told to leave the grade alone (an unmatched free response), and an
    identical value is not a change. A journal full of non-events would make
    ``grade-restore`` propose restores that do nothing.
    """
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
    key,  # noqa: ANN001 — AnswerGradeKey; annotated loosely to keep the import light
) -> GradeSuggestion | None:
    """Decide this answer's grade, asking the model only if it is new.

    The cache is consulted first, so an answer another student already gave
    costs nothing and — more importantly — gets the *same* decision. Transport
    errors stay a warning and are never cached: a dead server says nothing
    about the answer.
    """
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
    # Match SubmissionFilesProvider discovery: submission files may sit
    # directly in the directory or one level down in per-student folders.
    direct = sorted(input_path.glob("*.submission.md"))
    nested = sorted(input_path.glob("*/*.submission.md"))
    return direct + nested


def setup_args() -> ArgumentParser:
    # Resolved once, here, rather than as a module-level constant: a teacher
    # can change the saved model/host/confidence in AI Setup at any time, and
    # a module constant would freeze whatever was on disk at import time
    # instead of picking up the current choice on every run.
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
    """Start ``ollama serve`` if nothing is listening on the configured host.

    Mirrors the helpers in the split/extract/answer-key CLIs (self-contained per
    the repo convention). Raises ``RuntimeError`` when the ``ollama`` binary is
    missing; a start-up timeout is a warning (grading then fails at the model
    check with an actionable message). ``emit`` receives human-readable status
    so a UI can show progress.
    """
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
        """Print for headless runs and forward to a UI status line if present."""
        print(message)
        if status_callback is not None:
            status_callback(message)

    parser = setup_args()
    args = parser.parse_args(argv)

    # Configuration errors surface before a single file is opened, the same
    # doctor/scan-check/grade-check habit this codebase already follows: name
    # the problem with its fix before the long operation, not after.
    # `problems()` covers an unknown provider, a missing API key, and — new —
    # a confidence gate the user asked to be measured but never measured. All
    # three are configuration errors that would otherwise surface as a wrong
    # or wasted grading run. This gate can only fire for someone who opted
    # into `require_calibration` (it defaults to False), so it changes
    # nothing for existing users.
    if args.llm:
        resolved_ai = ai_config.resolve()
        problems = resolved_ai.problems()
        if problems:
            for problem in problems:
                print(f"Error: {problem}", file=sys.stderr)
            # This file has no prior non-zero exit convention to distinguish
            # "refused" from "crashed" (every sys.exit here used 1), so this
            # borrows 2 from the rest of the codebase, which already uses it
            # for exactly this shape of refusal: grade-check --fail-on-blocking
            # and claim --fail-on-overlap both exit 2 for "refused, not
            # crashed".
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
            # Convenience path (setup screen / opt-in CLI): boot the server and
            # pull the model so a first-time user doesn't have to.
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
        # Fail fast with an actionable message if the model/server is not ready,
        # rather than warning once per question mid-run.
        try:
            ensure_model_available(config)
        except OllamaError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
        llm_generate = lambda prompt: generate(  # noqa: E731
            prompt=prompt, config=config, response_format=GRADE_SUGGESTION_SCHEMA
        )
        # Recorded into every AI grade's tag, so a year later the file itself
        # says which model produced it.
        llm_model = config.model
        emit(
            f"LLM free-response grading enabled (model={config.model}, "
            f"min confidence={args.llm_min_confidence:g})."
        )

    paths = _submission_paths(input_path)
    total = len(paths)
    failures = 0

    # Journalled by default so a batch run is reversible; a dry run writes
    # nothing and so has nothing to record. `--no-journal` exists for scripted
    # regeneration of sample data, where the history would just be noise.
    run_journal: _RunJournal | None = None
    if not args.dry_run and not args.no_journal:
        run_journal = _RunJournal(input_path)

    # One cache for the whole cohort: this is what turns "one model call per
    # student" into "one per distinct answer", and what guarantees two students
    # who wrote the same thing are graded by the same decision rather than by
    # two independent generations of it.
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
        # Point the user at the undo before they need it — after a bad run is
        # the worst time to discover the tool exists.
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
