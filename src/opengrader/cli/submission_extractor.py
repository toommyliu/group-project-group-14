"""Extract student identities, answers, and response crops from PDFs."""

from __future__ import annotations

import hashlib
import contextlib
import shutil
import sys
import tempfile
import subprocess
import time
import socket
from urllib.parse import urlparse
from argparse import (
    ArgumentParser,
    ArgumentTypeError,
    BooleanOptionalAction,
    RawDescriptionHelpFormatter,
)
from pathlib import Path
from typing import Callable

from opengrader.core import app_config
from opengrader.core.ocr import OcrConfig
from opengrader.core.ocr.math_ocr import (
    MATH_BACKEND_NAMES,
    MathOcrError,
    resolve_math_transcriber,
)
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

from opengrader.core.region_map import (
    RegionMapError,
    load_verified_region_map,
    region_map_errors,
    region_map_to_blank_template,
    validate_region_map,
)
from opengrader.core.submissions import (
    AssignmentTemplate,
    _load_blank_assignment_template,
    _RosterEntry,
    _load_roster,
    ExtractionProgress,
)
from opengrader.core.submissions.batch import (
    BatchReport,
    ExtractionJob,
    ExtractionOutcome,
    resolve_worker_count,
    run_extraction_batch,
)
from opengrader.core.submissions.transcription import (
    DEFAULT_ESCALATION_FLOOR,
    SOURCE_MATH_MODEL,
    SOURCE_VISION_MODEL,
    ReadingTally,
)
from opengrader.core.submissions.manifest import (
    ExtractionRecord,
    append_record,
    file_fingerprint,
    read_manifest,
    record_is_reusable,
    settings_fingerprint,
    utc_timestamp,
)
from opengrader.core.submissions.refine import (
    DEFAULT_REFINE_FLOOR,
    REJECTION_LABELS,
    RefineCandidate,
    RefineSummary,
    merge_refined_responses,
    refine_candidate,
)
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)
from opengrader.core.history import (
    AspectValue,
    GradeJournal,
    current_actor,
    journal_payload,
    resolve_history_dir,
)


def _prompt_for_duplicate_name(
    document: StudentSubmissionDocument,
    candidates: tuple[_RosterEntry, ...],
) -> _RosterEntry | None:
    source = Path(document.source_pdf).name if document.source_pdf else "?"
    print(f"\n  Duplicate name in {source}:", file=sys.stderr)
    print(f"    Extracted name: {document.student_name!r}", file=sys.stderr)
    for index, entry in enumerate(candidates, start=1):
        print(f"    [{index}] {entry.student_id}  {entry.name}", file=sys.stderr)
    while True:
        try:
            choice = input("  Which student? [number / s to skip]: ").strip().lower()
        except EOFError:
            return None
        if choice in ("s", "skip", ""):
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(candidates):
            return candidates[int(choice) - 1]
        print("    Enter a listed number, or 's' to skip.", file=sys.stderr)


EPILOG = """
examples:
  uv run submission-extractor --input split/ --output out/student-submissions/
  uv run submission-extractor --input split/submission-0001.pdf --output out/student-submissions/
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --blank-template blank.pdf
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --region-map midterm.regions.json             # work Opengrader didn't generate
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --blank-template blank.pdf --llm-responses    # also transcribe answers with the vision model
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --blank-template blank.pdf --llm-escalate     # ...but only where Tesseract read badly
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --jobs 8                                      # 8 submissions extracted at once
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --resume                                      # pick up where an interrupted run stopped
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --refine --refine-dpi 450                     # re-read only the answers that came back shaky
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --refine --refine-model qwen2.5vl:32b         # ...escalating those to a bigger vision model
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --refine --refine-dry-run                     # what would a second attempt change?
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --llm-responses --llm-math                    # transcribe handwritten maths as LaTeX
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --llm-responses --llm-math --math-backend pix2tex   # ...with the dedicated maths model
                                                  # (needs: uv sync --extra math)
"""


def _parse_jobs(value: str) -> int | None:
    text = value.strip().lower()
    if text in ("auto", "0"):
        return None
    try:
        count = int(text)
    except ValueError:
        raise ArgumentTypeError(f"--jobs must be 'auto' or a positive integer, got {value!r}")
    if count < 1:
        raise ArgumentTypeError("--jobs must be at least 1")
    return count


def _roster_fingerprint(roster: list[_RosterEntry] | None) -> list[str]:
    if not roster:
        return []
    return sorted(f"{entry.student_id}\t{entry.name}" for entry in roster)

def _collect_input_pdfs(input_path: str) -> list[Path]:
    path = Path(input_path)
    if path.is_file():
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"input file must be a PDF: {path}")
        return [path]
    if path.is_dir():
        pdfs = sorted(p for p in path.glob("*.pdf") if not p.name.startswith("."))
        if not pdfs:
            raise ValueError(f"no PDF files found in directory: {path}")
        return pdfs
    raise FileNotFoundError(f"input path not found: {path}")


def _load_bubble_sheet_layout(qa_path: Path, *, version: str | None = None):  # noqa: ANN202
    from opengrader.core.bubble_sheet import (
        CompanionQuestion,
        build_bubble_sheet_layout,
    )
    from opengrader.core.qa_file import load_qa_markdown
    from opengrader.core.qa_file.markdown import (
        _questions_for_version,
        qa_question_type,
        qa_to_bubble_questions,
    )

    if not qa_path.exists():
        raise FileNotFoundError(f"QA file not found: {qa_path}")
    qa_doc = load_qa_markdown(qa_path)
    questions = qa_to_bubble_questions(qa_doc, version=version)
    if not questions:
        raise ValueError(
            f"{qa_path} has no multiple-choice questions, so it cannot describe "
            "a bubble sheet"
        )

    bubbled = {question.question_id for question in questions}
    companions: list[CompanionQuestion] = []
    order: list[str] = []
    for question in _questions_for_version(qa_doc, version):
        for item in question.subquestions or [question]:
            order.append(item.id)
            if item.id in bubbled:
                continue
            companions.append(
                CompanionQuestion(
                    question_id=item.id,
                    points=float(item.points),
                    question_type=qa_question_type(item),
                )
            )

    title = (qa_doc.title or "").strip().splitlines()
    return build_bubble_sheet_layout(
        questions,
        assignment_title=title[0].strip() if title else "",
        companions=companions,
        question_order=order,
    )


_SHUFFLE_DETECT_SAMPLE = 8


def _detect_shuffled_batch(pdfs: list[Path]) -> bool:
    from opengrader.core.submissions.processor import _read_qr_code

    for pdf_path in pdfs[:_SHUFFLE_DETECT_SAMPLE]:
        code = _read_qr_code(pdf_path)
        if code is not None and code.form:
            return True
    return False


def _bubble_layout_fingerprint(layout) -> str:  # noqa: ANN001
    if layout is None:
        return ""
    parts = [
        f"{row.question_id}:{row.page}:"
        + ",".join(f"{b.choice_label}@{b.cx:.5f},{b.cy:.5f}" for b in row.bubbles)
        for row in layout.rows
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _parse_roster_arg(value: str) -> tuple[Path, str | None]:
    if Path(value).exists() or "=" not in value:
        return Path(value), None
    path_str, name = value.rsplit("=", 1)
    return Path(path_str), (name.strip() or None)


JOURNAL_ACTOR_REFINE = "submission-extractor:refine"


def _plan_refine(
    pdfs: list[Path],
    output_dir: Path,
    *,
    floor: float,
    emit: "Callable[[str], None]",
) -> list[RefineCandidate]:
    candidates: list[RefineCandidate] = []
    missing = 0
    for pdf_path in pdfs:
        markdown_path = output_dir / pdf_path.stem / f"{pdf_path.stem}.submission.md"
        if not markdown_path.exists():
            missing += 1
            continue
        try:
            document = load_student_submission_markdown(markdown_path)
        except (OSError, ValueError) as exc:
            emit(f"{pdf_path.name}: could not read the existing extraction ({exc}); skipping.")
            continue
        candidate = refine_candidate(pdf_path.stem, markdown_path, document, floor=floor)
        if candidate is not None:
            candidates.append(candidate)
    if missing:
        emit(
            f"note: {missing} input PDF(s) have no extraction under {output_dir} yet — "
            "run without --refine first."
        )
    return candidates


def _apply_refined_document(
    candidate: RefineCandidate,
    staged_markdown: Path,
    *,
    accept_unverified: bool,
    dry_run: bool,
    journal: "GradeJournal | None",
    operator: str,
    emit: "Callable[[str], None]",
) -> int:
    try:
        refreshed = load_student_submission_markdown(staged_markdown)
        existing = load_student_submission_markdown(candidate.markdown_path)
    except (OSError, ValueError) as exc:
        emit(f"  Warning: could not merge {candidate.key} ({exc})")
        return 0

    result = merge_refined_responses(
        existing,
        refreshed,
        candidate.targets,
        accept_unverified=accept_unverified,
    )
    for question_id, reason in result.rejected:
        emit(f"  {question_id}: kept the original — {REJECTION_LABELS[reason]}")
    if not result.adopted:
        return 0

    for refined in result.adopted:
        emit(f"  {refined.question_id}: re-read ({refined.confidence_summary})")
        if dry_run:
            continue
        update_student_submission_file(
            candidate.markdown_path,
            StudentSubmissionGradeUpdate(
                question_id=refined.question_id,
                response_text=refined.new_text,
                response_confidence=refined.new_confidence,
                clear_response_confidence=refined.new_confidence is None,
                response_source=refined.new_source,
                clear_response_source=refined.new_source is None,
            ),
        )
        if journal is None:
            continue
        try:
            journal.append(
                kind="transcription",
                student_id=candidate.student_id,
                student_name=candidate.student_name,
                question_id=refined.question_id,
                summary=(
                    f"re-read at {refined.confidence_summary} (run by {operator})"
                ),
                old=journal_payload(
                    "transcription",
                    AspectValue(
                        text=refined.old_text,
                        confidence=refined.old_confidence,
                        source=refined.old_source,
                    ),
                ),
                new=journal_payload(
                    "transcription",
                    AspectValue(
                        text=refined.new_text,
                        confidence=refined.new_confidence,
                        source=refined.new_source,
                    ),
                ),
            )
        except OSError:
            pass
    return len(result.adopted)


def _print_reading_summary(
    report: "BatchReport",
    *,
    escalate_floor: float | None,
    emit: "Callable[[str], None]",
) -> None:
    tally = ReadingTally()
    for outcome in report.extracted:
        tally = tally.merge(ReadingTally(counts=outcome.reading_counts))
    if not tally.total:
        return

    emit(f"Readings: {tally.describe()}")
    if escalate_floor is None:
        return
    escalated = sum(
        tally.count_of(source) for source in (SOURCE_VISION_MODEL, SOURCE_MATH_MODEL)
    )
    emit(
        f"  Escalated {escalated} of {tally.total} "
        f"answer{'s' if tally.total != 1 else ''} to a model "
        f"(below {escalate_floor:g}% OCR confidence)."
    )


def _print_refine_summary(
    summary: RefineSummary,
    *,
    dry_run: bool,
    emit: "Callable[[str], None]",
) -> None:
    emit("")
    emit(
        f"Refine: {summary.targets} low-confidence answer(s) across "
        f"{summary.submissions_with_targets} of {summary.submissions_scanned} "
        f"submission(s) re-read."
    )
    if dry_run:
        emit(
            f"  {summary.adopted} would be replaced, {summary.unchanged} kept "
            "(dry run — nothing was written)."
        )
        return
    emit(
        f"  {summary.adopted} replaced across {summary.submissions_changed} "
        f"submission(s); {summary.unchanged} kept their original reading."
    )
    if summary.unchanged:
        emit(
            "  The ones that were kept are still flagged — see: "
            "uv run review-queue --input <output> --ocr-confidence"
        )
    if summary.adopted:
        emit(
            "  Undo this pass: uv run grade-restore apply --submissions <output> "
            f"--without-actor {JOURNAL_ACTOR_REFINE} --yes"
        )


def run_cli(
    argv: list[str] | None = None,
    *,
    progress: ExtractionProgress | None = None,
    status_callback: "Callable[[str], None] | None" = None,
    prompt_cloud_send: bool = True,
) -> None:
    # prompt_cloud_send=False: a caller that owns the terminal (the TUI) gets
    # CloudConfirmationNeeded instead of an input() prompt; see cli_confirm_cloud_send.
    ai = app_config.resolve()
    parser = ArgumentParser(
        description=(
            "Convert scanned per-student submission PDFs into markdown student "
            "submission files (frontmatter + per-question response image/text)."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to one submission PDF or a directory containing PDFs.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory for generated *.submission.md files.",
    )
    parser.add_argument(
        "--blank-template",
        metavar="PDF",
        default=None,
        help=(
            "Path to the exact blank assignment PDF that students received "
            "(same printed content and layout; real text layer required). It "
            "supplies question/name/ID regions and is aligned to each scan so "
            "printed ink can be removed. The scans themselves need no text "
            "layer. This mode does not use OCR layout detection."
        ),
    )
    parser.add_argument(
        "--region-map",
        metavar="JSON",
        default=None,
        help=(
            "Path to a region map (<assignment>.regions.json) drawn on a "
            "blank copy of your own assignment with 'region-map'. This is how "
            "you grade an assignment Opengrader didn't generate: it needs no "
            "text layer, because the mapped boxes are the answer regions and "
            "are cropped verbatim. "
            "Cannot be combined with --blank-template."
        ),
    )
    parser.add_argument(
        "--region-map-input",
        metavar="PDF",
        default=None,
        help=(
            "The blank assignment the region map was drawn on. Given, the map is "
            "verified against it before a single scan is read; the map already "
            "carries that file's fingerprint. The blank is also aligned to "
            "each scan to separate printed text from student ink."
        ),
    )
    parser.add_argument(
        "--bubble-sheet",
        metavar="QA_FILE",
        default=None,
        help=(
            "OMR mode: read scans of the bubble answer sheet generated by "
            "'qa-file generate-template --bubble-sheet' from this same QA "
            "file. The sheet's geometry is rebuilt from the QA file, so pass "
            "the file the sheets were printed from. Uses no OCR, no vision "
            "model and no Tesseract — marks are measured, not recognized — "
            "which makes it dramatically faster for large multiple-choice "
            "classes. Overrides --blank-template and the text-layer/OCR modes."
        ),
    )
    parser.add_argument(
        "--bubble-version",
        metavar="VERSION",
        default=None,
        help=(
            "Which QA version's questions the printed sheets carry. Defaults "
            "to the QA file's first version. Only relevant with --bubble-sheet "
            "on a multi-version QA file."
        ),
    )
    parser.add_argument(
        "--bubble-shuffled",
        action="store_true",
        help=(
            "These sheets were printed with --shuffle, so each has its own "
            "question order. Extraction re-derives that order from the form "
            "key on each sheet; any sheet whose form key cannot be read is "
            "left unread rather than scored against the wrong questions."
        ),
    )
    parser.add_argument(
        "--bubble-form",
        metavar="KEY",
        default=None,
        help=(
            "The form key (the 'Form XXXXXX' code printed on the sheet) for a "
            "single scan whose QR will not scan. Use with one --input file to "
            "rescue it by hand."
        ),
    )
    parser.add_argument(
        "--id-file",
        metavar="TSV[=SECTION]",
        action="append",
        default=None,
        help=(
            "Tab-separated roster file (.tsv or .ids) with one 'ID<TAB>NAME' line per student. "
            "When given, extracted name/ID values are fuzzy-matched against the "
            "roster and replaced with canonical values. A name found without a "
            "usable ID is matched to the roster and its ID filled in. "
            "Repeatable: pass one --id-file per class section to route each "
            "student to the section whose roster holds their ID (a mixed scan "
            "stack). Append '=SECTION' to name a roster's section explicitly "
            "(e.g. roster.tsv=SectionA); otherwise the section name defaults to "
            "the roster's filename. A student in no roster is tagged "
            "'unassigned'. Cannot be combined with multiple rosters and --section."
        ),
    )
    parser.add_argument(
        "--section",
        metavar="NAME",
        default=None,
        help=(
            "Tag every submission in this run with class section NAME (one scan "
            "stack per section). May be paired with a single --id-file for ID "
            "fill-in. For a mixed stack, use multiple --id-file rosters instead."
        ),
    )
    parser.add_argument(
        "--interactive-roster",
        action="store_true",
        help=(
            "When a name matches several roster students (duplicate names), "
            "prompt at the terminal to pick which one instead of leaving the ID "
            "blank. Ignored when stdin is not a TTY (e.g. batch runs)."
        ),
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="Rendering DPI for OCR/cropping (default: 300).",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=60.0,
        help="OCR confidence threshold for field validation (default: 60.0).",
    )
    parser.add_argument(
        "--lang",
        default="eng",
        help="Language code(s) for layout anchor detection, comma-separated (default: eng).",
    )
    parser.add_argument(
        "--no-deskew",
        action="store_true",
        help="Disable automatic deskewing before OCR.",
    )
    parser.add_argument(
        "--no-split-subquestions",
        action="store_true",
        help=(
            "Keep each detected question as a single response image even when "
            "nested subquestion boxes are detected."
        ),
    )
    parser.add_argument(
        "--ollama-model",
        default=ai.vision_model,
        help="Ollama vision model to use for text extraction (default: %(default)s, from AI Setup).",
    )
    parser.add_argument(
        "--ollama-host",
        default=ai.host,
        help="Ollama server URL (default: %(default)s, from AI Setup).",
    )
    parser.add_argument(
        "--auto-ollama",
        action="store_true",
        help="Automatically start the Ollama server and pull the vision model if not available.",
    )
    parser.add_argument(
        "--ollama-timeout",
        type=float,
        default=ai.timeout_seconds,
        help="Ollama request timeout in seconds (default: %(default)s, from AI Setup).",
    )
    parser.add_argument(
        "--no-ollama-qa",
        action="store_true",
        help=(
            "Skip ollama question prompt/answer extraction. "
            "Ollama will still be used for student name/ID/title extraction."
        ),
    )
    parser.add_argument(
        "--llm-responses",
        action=BooleanOptionalAction,
        default=ai.read_handwriting,
        help=(
            "With --blank-template, also transcribe each question's response "
            "from the isolated student-ink image using the Ollama vision model "
            "(Tesseract fallback). Without it, template mode still saves the "
            "ink image and every detected fragment but leaves Response text "
            "empty. Ignored for text-layer PDFs, whose typed answers are read "
            "straight from the text layer. "
            "(default: %(default)s, from AI Setup)"
        ),
    )
    parser.add_argument(
        "--llm-escalate",
        action=BooleanOptionalAction,
        default=ai.escalate_only,
        help=(
            "Read every answer with Tesseract first and ask the vision model "
            "only about the ones it read badly (empty, or below "
            "--escalate-confidence). Implies --llm-responses. On an exam whose "
            "answers are mostly short and legible this is a fraction of the "
            "model calls of --llm-responses alone, and it cannot lose an "
            "answer: a model that reads nothing leaves the OCR text standing. "
            "(default: %(default)s, from AI Setup)"
        ),
    )
    parser.add_argument(
        "--escalate-confidence",
        type=float,
        default=DEFAULT_ESCALATION_FLOOR,
        metavar="N",
        help=(
            "With --llm-escalate: re-read with the model below this OCR "
            f"confidence (0-100, default: {DEFAULT_ESCALATION_FLOOR:g} — the "
            "same floor the review queue flags at). Measure the right value "
            "for your scans with `transcribe-eval`."
        ),
    )
    parser.add_argument(
        "--llm-math",
        action=BooleanOptionalAction,
        default=ai.transcribe_math,
        help=(
            "Transcribe handwritten mathematics as LaTeX ($...$) instead of "
            "flattened plain text, so fractions/exponents/integrals survive. "
            "Uses the vision model (template or dark-scan paths); the grade "
            "screen renders the LaTeX. Best paired with --llm-responses. "
            "(default: %(default)s, from AI Setup)"
        ),
    )
    parser.add_argument(
        "--math-backend",
        choices=list(MATH_BACKEND_NAMES),
        default=ai.math_backend,
        help=(
            "Which reader turns a math crop into LaTeX, with --llm-math. "
            "'vlm' (default) is the Ollama vision model: it reads the whole "
            "answer, prose and all, and marks the maths up inline. 'pix2tex' is "
            "a dedicated image-to-LaTeX model, markedly better on dense "
            "notation but it transcribes the crop as ONE expression - right for "
            "an exam whose answers are equations, wrong for one that asks a "
            "student to explain. Install it with: uv sync --extra math"
        ),
    )
    parser.add_argument(
        "--save-layout-debug",
        action="store_true",
        help="Write detected layout JSON and overlay PNGs for each submission.",
    )
    parser.add_argument(
        "--refresh-layout",
        action="store_true",
        help="Re-run layout detection (template alignment or dark scan) even during an update.",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Only update student metadata (name/ID) and transcription in existing .submission.md files.",
    )
    parser.add_argument(
        "--jobs",
        "-j",
        metavar="N",
        type=_parse_jobs,
        default=None,
        help=(
            "Number of submissions to extract in parallel (default: auto). Each "
            "job is a separate process, so this scales across CPU cores. 'auto' "
            "picks the CPU count capped at 8, or 4 when the vision model is "
            "transcribing (Ollama serves only a few generations at once — see "
            "OLLAMA_NUM_PARALLEL). Use --jobs 1 for the old one-at-a-time "
            "behavior; that is also forced automatically by --interactive-roster, "
            "which needs the terminal."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            "Skip submissions a previous run already extracted with the same "
            "input bytes and the same settings, and whose output files are all "
            "still present. The ledger at "
            "<output>/.opengrader/extraction-manifest.jsonl is written on every "
            "run, so an interrupted run is always resumable afterwards."
        ),
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help=(
            "Stop after the first submission that fails. By default the batch "
            "runs to completion and the failures are listed at the end, so one "
            "unreadable scan doesn't discard hours of work; re-run with --resume "
            "to retry just those."
        ),
    )
    parser.add_argument(
        "--refine",
        action="store_true",
        help=(
            "Re-read only the answers that came back badly. Where --resume skips "
            "what already succeeded, --refine re-extracts the submissions holding "
            "a transcription below --refine-confidence and keeps the new reading "
            "only where it is better. Grades, comments, rubric selections, "
            "identities, crops and every answer that was already confident are "
            "left untouched. Pair with --refine-dpi and/or --refine-model to give "
            "the second attempt something the first one didn't have."
        ),
    )
    parser.add_argument(
        "--refine-confidence",
        type=float,
        metavar="NN",
        default=DEFAULT_REFINE_FLOOR,
        help=(
            "With --refine: re-read transcriptions whose confidence is below this "
            f"(0-100, default {DEFAULT_REFINE_FLOOR:g} — the same floor "
            "'review-queue --ocr-confidence' flags at). Vision-model readings "
            "carry no confidence and are never targeted; a human-corrected one "
            "sits at 100 and so is never at risk."
        ),
    )
    parser.add_argument(
        "--refine-dpi",
        type=int,
        metavar="N",
        default=None,
        help=(
            "With --refine: render pages at this DPI for the second attempt "
            "(default: the same --dpi as the first). Raising it is the cheapest "
            "thing that actually helps a cramped or faint answer box."
        ),
    )
    parser.add_argument(
        "--refine-model",
        metavar="MODEL",
        default=None,
        help=(
            "With --refine: escalate to this Ollama vision model for the second "
            "attempt (e.g. a larger one than the first run used). Implies "
            "--llm-responses for the refine pass. A model reading has no measured "
            "confidence, so the answer's Confidence line is removed rather than "
            "left showing a number that no longer describes the text."
        ),
    )
    parser.add_argument(
        "--refine-dry-run",
        action="store_true",
        help=(
            "With --refine: do the re-extraction and report what would change, "
            "but write nothing back into the submissions."
        ),
    )
    parser.add_argument(
        "--yes-send-to-cloud",
        action="store_true",
        dest="yes_send_to_cloud",
        help=(
            "Confirm sending response images to a cloud AI provider for "
            "handwriting transcription. The scale and destination are shown "
            "first; nothing is sent without this or an interactive yes."
        ),
    )
    args = parser.parse_args(argv)

    if args.refine:
        conflicting = [
            name
            for name, active in (("--resume", args.resume), ("--update", args.update))
            if active
        ]
        if conflicting:
            print(
                f"error: --refine cannot be combined with {', '.join(conflicting)}. "
                "--refine re-reads answers that came back with low confidence; "
                "--resume skips work that already finished.",
                file=sys.stderr,
            )
            sys.exit(1)
        if not 0.0 < args.refine_confidence <= 100.0:
            print(
                "error: --refine-confidence must be between 0 and 100.",
                file=sys.stderr,
            )
            sys.exit(1)
        if args.refine_dpi is not None and args.refine_dpi < 1:
            print("error: --refine-dpi must be at least 1.", file=sys.stderr)
            sys.exit(1)
    else:
        idle = [
            name
            for name, given in (
                ("--refine-dpi", args.refine_dpi is not None),
                ("--refine-model", args.refine_model is not None),
                ("--refine-dry-run", args.refine_dry_run),
            )
            if given
        ]
        if idle:
            print(
                f"note: {', '.join(idle)} does nothing without --refine.",
                file=sys.stderr,
            )

    if args.llm_escalate:
        args.llm_responses = True
        if not 0.0 < args.escalate_confidence <= 100.0:
            print(
                "error: --escalate-confidence must be between 0 and 100.",
                file=sys.stderr,
            )
            sys.exit(1)
    elif args.escalate_confidence != DEFAULT_ESCALATION_FLOOR:
        print(
            "note: --escalate-confidence does nothing without --llm-escalate.",
            file=sys.stderr,
        )
    escalate_floor = args.escalate_confidence if args.llm_escalate else None

    pdfs = _collect_input_pdfs(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    from opengrader.core import egress

    # Scoped to this process; when run_cli is invoked in-process by the
    # pipeline, output_dir is the same submissions folder the pipeline itself
    # is already recording into, so this is a no-op there.
    egress.activate(output_dir)

    if args.refine and args.refine_model:
        args.llm_responses = True
        args.ollama_model = args.refine_model
    render_dpi = args.refine_dpi if (args.refine and args.refine_dpi) else args.dpi

    if args.llm_math:
        try:
            resolve_math_transcriber(args.math_backend, strict=True)
        except MathOcrError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    elif args.math_backend != ai.math_backend:
        print(
            f"note: --math-backend {args.math_backend} does nothing without "
            "--llm-math, so no LaTeX will be transcribed.",
            file=sys.stderr,
        )

    if args.blank_template and args.region_map:
        print(
            "error: --blank-template and --region-map both describe where the "
            "answers are, so only one may be given. Keep --region-map for an "
            "assignment you drew the boxes on yourself; keep --blank-template "
            "for a blank Opengrader-generated template with a text layer.",
            file=sys.stderr,
        )
        sys.exit(1)

    blank_assignment_template: AssignmentTemplate | None = None
    if args.blank_template:
        try:
            blank_assignment_template = _load_blank_assignment_template(Path(args.blank_template))
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    elif args.region_map:
        try:
            region_map = load_verified_region_map(
                Path(args.region_map),
                Path(args.region_map_input) if args.region_map_input else None,
            )
        except RegionMapError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

        blocking = region_map_errors(validate_region_map(region_map))
        if blocking:
            print(
                f"error: {args.region_map} has {len(blocking)} problem(s) that "
                "would produce wrong crops:",
                file=sys.stderr,
            )
            for finding in blocking:
                print(f"  - {finding.message}", file=sys.stderr)
            print(
                "Fix them in the TUI's region-map editor, or check them with: "
                f"region-map validate --map {args.region_map}",
                file=sys.stderr,
            )
            sys.exit(1)

        if args.region_map_input:
            region_map_reference = Path(args.region_map_input)
        else:
            candidate = Path(args.region_map).parent / region_map.source_name
            region_map_reference = candidate if candidate.exists() else None
            if region_map_reference is not None:
                try:
                    region_map = load_verified_region_map(
                        Path(args.region_map), region_map_reference
                    )
                except RegionMapError as exc:
                    print(f"error: {exc}", file=sys.stderr)
                    sys.exit(1)

        blank_assignment_template = region_map_to_blank_template(
            region_map,
            reference_pdf_path=region_map_reference,
        )
        if region_map_reference is None:
            print(
                "note: the mapped blank PDF was not found beside the region map. "
                "Pass --region-map-input to separate printed text from student ink.",
                file=sys.stderr,
            )

    bubble_sheet_layout = None
    if args.bubble_sheet:
        try:
            bubble_sheet_layout = _load_bubble_sheet_layout(
                Path(args.bubble_sheet), version=args.bubble_version
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        if blank_assignment_template is not None:
            overridden = "--region-map" if args.region_map else "--blank-template"
            print(
                f"note: --bubble-sheet overrides {overridden}; reading marks, "
                "not handwriting.",
                file=sys.stderr,
            )
            blank_assignment_template = None

        if not args.bubble_shuffled and _detect_shuffled_batch(pdfs):
            args.bubble_shuffled = True
            print(
                "note: these sheets carry form keys, so they were printed with "
                "--shuffle; each sheet's question order will be read from its "
                "own form key."
            )


    id_file_args: list[str] = args.id_file or []
    roster: list | None = None
    default_section: str | None = args.section
    section_by_id: dict[str, str] | None = None

    if args.section and len(id_file_args) > 1:
        print(
            "error: --section cannot be combined with multiple --id-file rosters; "
            "use --section for one stack per section, or multiple --id-file "
            "rosters for a mixed stack.",
            file=sys.stderr,
        )
        sys.exit(1)

    if id_file_args:
        parsed_rosters: list[tuple[Path, str | None, list]] = []
        for raw in id_file_args:
            roster_path, explicit_name = _parse_roster_arg(raw)
            if args.section and explicit_name:
                print(
                    "error: cannot set a section name on both --id-file "
                    f"('{explicit_name}') and --section ('{args.section}').",
                    file=sys.stderr,
                )
                sys.exit(1)
            try:
                entries = _load_roster(roster_path)
            except Exception as exc:
                print(f"error loading roster {roster_path}: {exc}", file=sys.stderr)
                sys.exit(1)
            parsed_rosters.append((roster_path, explicit_name, entries))

        roster = [entry for _, _, entries in parsed_rosters for entry in entries]

        if not args.section and len(parsed_rosters) > 1:
            section_by_id = {}
            for roster_path, explicit_name, entries in parsed_rosters:
                section_name = explicit_name or roster_path.stem
                for entry in entries:
                    prior = section_by_id.get(entry.student_id)
                    if prior is not None and prior != section_name:
                        print(
                            f"warning: student id {entry.student_id} is in both "
                            f"'{prior}' and '{section_name}'; using "
                            f"'{section_name}'.",
                            file=sys.stderr,
                        )
                    section_by_id[entry.student_id] = section_name
        elif not args.section and len(parsed_rosters) == 1:
            _, explicit_name, _ = parsed_rosters[0]
            if explicit_name:
                default_section = explicit_name

    resolve_ambiguous = None
    if args.interactive_roster:
        if roster and sys.stdin.isatty():
            resolve_ambiguous = _prompt_for_duplicate_name
        elif not roster:
            print("note: --interactive-roster ignored (no --id-file).", file=sys.stderr)
        else:
            print(
                "note: --interactive-roster ignored (stdin is not a terminal).",
                file=sys.stderr,
            )

    resolved_ai = app_config.with_overrides(
        ai,
        host=args.ollama_host,
        vision_model=args.ollama_model,
        timeout_seconds=args.ollama_timeout,
    )

    ollama_server_proc = None
    ollama_log_file = None
    if (
        args.auto_ollama
        and bubble_sheet_layout is None
        and resolved_ai.provider.kind == "local"
    ):
        parsed_url = urlparse(args.ollama_host)
        host = parsed_url.hostname or "127.0.0.1"
        port = parsed_url.port or 11434

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex((host, port)) != 0:
                print(f"Ollama server not found at {args.ollama_host}. Starting 'ollama serve'...")
                try:
                    ollama_log_file = tempfile.TemporaryFile(mode='w+', encoding='utf-8')
                    ollama_server_proc = subprocess.Popen(
                        ["ollama", "serve"],
                        stdout=ollama_log_file,
                        stderr=ollama_log_file
                    )
                    start_time = time.time()
                    while time.time() - start_time < 30:
                        time.sleep(1)
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
                            if s2.connect_ex((host, port)) == 0:
                                break
                    else:
                        print("Warning: Timed out waiting for Ollama to start. Extraction may fail.", file=sys.stderr)
                        if ollama_log_file:
                            ollama_log_file.seek(0)
                            logs = ollama_log_file.read()
                            if logs:
                                print("\n--- Ollama Server Logs (failed to start) ---", file=sys.stderr)
                                print(logs, file=sys.stderr)
                                print("--------------------------------------------\n", file=sys.stderr)
                            else:
                                print("No logs captured from Ollama server.", file=sys.stderr)
                except FileNotFoundError:
                    print("Error: 'ollama' binary not found. Please install Ollama first.", file=sys.stderr)
                    sys.exit(1)
                except Exception as e:
                    print(f"Error starting Ollama server: {e}", file=sys.stderr)
                    if ollama_log_file:
                        ollama_log_file.seek(0)
                        logs = ollama_log_file.read()
                        if logs:
                            print("\n--- Ollama Server Logs (error during start) ---", file=sys.stderr)
                            print(logs, file=sys.stderr)
                            print("----------------------------------------------\n", file=sys.stderr)
                    sys.exit(1)

        print(f"Ensuring model '{args.ollama_model}' is available...")
        subprocess.run(["ollama", "pull", args.ollama_model], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ocr_config = None
    vision_reader = None

    omr_mode = bubble_sheet_layout is not None

    if not args.blank_template and not omr_mode:
        ocr_config = OcrConfig(
            lang=args.lang,
            deskew=not args.no_deskew,
            confidence_threshold=args.confidence_threshold,
        )

    # Without a blank template the extractor has always asked the vision
    # model to read answers even when handwriting reading is off. A cloud
    # provider only gets answer images when it is on (--llm-responses, the
    # AI setup toggle): that is what AI setup, the data-flow report and the
    # pipeline's pre-flight all promise. With it off, answers are read by
    # Tesseract here, and the model is not used at all.
    wants_reader = not omr_mode and (not args.blank_template or args.llm_responses)
    if wants_reader and resolved_ai.kind == "cloud" and not args.llm_responses:
        print(
            f"Handwriting reading is off, so no answer images are sent to "
            f"{resolved_ai.provider.label}; answers are read with OCR only.",
            file=sys.stderr,
        )
        wants_reader = False
    if wants_reader:
        if resolved_ai.kind == "cloud":
            from opengrader.core.cloud_preflight import CloudPreflight, cli_confirm_cloud_send

            documents = f"{len(pdfs)} submission PDF{'' if len(pdfs) == 1 else 's'}"
            preflight = CloudPreflight(
                provider_label=resolved_ai.provider.label,
                host=resolved_ai.host,
                # How many answers each scan holds is only found while it is
                # read, so the statement names what is sent per answer
                # rather than a number.
                uncounted=(
                    {
                        "answer-image": (
                            ("up to " if args.llm_escalate else "")
                            + f"one answer image per answer found in {documents}"
                        )
                    }
                    if pdfs
                    else {}
                ),
                students=len(pdfs),
            )
            cli_confirm_cloud_send(
                resolved_ai,
                preflight,
                yes_send_to_cloud=args.yes_send_to_cloud,
                emit=lambda message: print(message, file=sys.stderr),
                interactive=prompt_cloud_send,
            )
        print(f"Using Ollama vision model: {args.ollama_model}")
        try:
            vision_reader = app_config.vision_reader(resolved_ai)
            app_config.ensure_ready(resolved_ai, purpose="vision")
        except (app_config.CloudConsentRequired, app_config.CloudDisabledError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            print(
                f"Fix: run 'opengrader providers consent {resolved_ai.provider.name}' "
                "(or open AI Setup), then retry.",
                file=sys.stderr,
            )
            sys.exit(2)
        except Exception as exc:
            print(f"Warning: Ollama model not available ({exc}). Proceeding without VLM for text extraction.", file=sys.stderr)
            vision_reader = None

    print(f"Found {len(pdfs)} submission PDF(s)")

    external_progress = progress is not None
    vlm_possible = bool(vision_reader) and not args.no_ollama_qa
    progress_bar: Progress | None = None
    if vlm_possible and not external_progress:
        progress_bar = Progress(
            SpinnerColumn(),
            TextColumn("[bold]Transcribing responses"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TextColumn("elapsed"),
            TimeRemainingColumn(),
            TextColumn("left · {task.fields[current]}"),
            console=Console(),
        )
    response_task: dict[str, object] = {"id": None, "total": 0}

    def emit(message: str) -> None:
        if status_callback is not None:
            status_callback(message)
        elif progress_bar is not None:
            progress_bar.console.print(message)
        else:
            print(message)

    def progress_status(message: str) -> None:
        if status_callback is not None:
            if "warning" in message.lower() or "error" in message.lower():
                status_callback(f"  {message}")
        elif progress_bar is None:
            print(f"  {message}")
        elif "warning" in message.lower() or "error" in message.lower():
            progress_bar.console.print(f"  {message}")

    def on_file_start(_pdf_name: str, count: int) -> None:
        if progress_bar is None or count <= 0:
            return
        response_task["total"] = int(response_task["total"]) + count
        if response_task["id"] is None:
            response_task["id"] = progress_bar.add_task(
                "responses", total=response_task["total"], current=""
            )
        else:
            progress_bar.update(response_task["id"], total=response_task["total"])

    def on_response(pdf_name: str, question_id: str) -> None:
        if progress_bar is None or response_task["id"] is None:
            return
        progress_bar.update(
            response_task["id"], advance=1, current=f"{pdf_name} · {question_id}"
        )

    if external_progress:
        extraction_progress = progress
    else:
        extraction_progress = ExtractionProgress(
            on_file_start=on_file_start if vlm_possible else None,
            on_response=on_response if vlm_possible else None,
        )

    settings_id = settings_fingerprint(
        {
            "pipeline_version": 2,
            "scan_warnings": {pdf.name: file_fingerprint(pdf.with_suffix(".scan.json")) for pdf in pdfs},
            "dpi": args.dpi,
            "lang": args.lang,
            "deskew": not args.no_deskew,
            "confidence_threshold": args.confidence_threshold,
            "split_subquestions": not args.no_split_subquestions,
            "blank_template": file_fingerprint(args.blank_template) if args.blank_template else "",
            "region_map": file_fingerprint(args.region_map) if args.region_map else "",
            "bubble_sheet": _bubble_layout_fingerprint(bubble_sheet_layout),
            "bubble_shuffled": args.bubble_shuffled,
            "roster": _roster_fingerprint(roster),
            "default_section": default_section,
            "section_by_id": section_by_id,
            "interactive_roster": resolve_ambiguous is not None,
            "llm_responses": args.llm_responses,
            "llm_math": args.llm_math,
            "escalate_floor": escalate_floor,
            "math_backend": args.math_backend,
            "skip_ollama_qa": args.no_ollama_qa,
            "vlm_model": resolved_ai.vision_model if vision_reader else "",
            "save_layout_debug": args.save_layout_debug,
            "update": args.update,
            "refresh_layout": args.refresh_layout,
        }
    )
    ledger = read_manifest(output_dir) if args.resume else {}

    refine_candidates: dict[str, RefineCandidate] = {}
    refine_staging: Path | None = None
    refine_scanned = len(pdfs)
    if args.refine:
        candidates = _plan_refine(
            pdfs, output_dir, floor=args.refine_confidence, emit=emit
        )
        refine_candidates = {candidate.key: candidate for candidate in candidates}
        pdfs = [pdf for pdf in pdfs if pdf.stem in refine_candidates]
        if not candidates:
            emit(
                "Nothing to refine: no transcription is below "
                f"{args.refine_confidence:g}% confidence."
            )
        else:
            refine_staging = Path(tempfile.mkdtemp(prefix="opengrader-refine-"))
            target_total = sum(len(candidate.targets) for candidate in candidates)
            escalation = f" with {args.ollama_model}" if args.refine_model else ""
            emit(
                f"Refining {target_total} low-confidence answer(s) across "
                f"{len(candidates)} submission(s) at {render_dpi} DPI{escalation}"
            )

    job_output_dir = refine_staging or output_dir

    jobs: list[ExtractionJob] = []
    skipped: list[Path] = []
    for pdf_path in pdfs:
        source_sha256 = file_fingerprint(pdf_path)
        if args.resume and record_is_reusable(
            ledger.get(pdf_path.stem),
            output_dir=output_dir,
            source_sha256=source_sha256,
            settings_id=settings_id,
        ):
            skipped.append(pdf_path)
            continue

        expected_md = output_dir / pdf_path.stem / f"{pdf_path.stem}.submission.md"
        performing_update = args.update and expected_md.exists()
        if args.update and not performing_update:
            emit(f"{pdf_path.name}: .submission.md missing, performing full extraction.")

        jobs.append(
            ExtractionJob(
                pdf_path=pdf_path,
                output_dir=job_output_dir,
                ocr_config=ocr_config,
                dpi=render_dpi,
                split_subquestions=not args.no_split_subquestions,
                vision_reader=vision_reader,
                skip_ollama_qa=args.no_ollama_qa,
                save_layout_debug=args.save_layout_debug,
                blank_assignment_template=blank_assignment_template,
                roster=roster,
                update=performing_update,
                refresh_layout=args.refresh_layout,
                llm_responses=args.llm_responses,
                llm_math=args.llm_math,
                math_backend=args.math_backend,
                escalate_floor=escalate_floor,
                default_section=default_section,
                section_by_id=section_by_id,
                bubble_sheet_layout=bubble_sheet_layout,
                bubble_shuffled=args.bubble_shuffled,
                bubble_form_key=args.bubble_form,
                source_sha256=source_sha256,
            )
        )

    if skipped:
        emit(f"Resuming: {len(skipped)} already extracted, {len(jobs)} to do")

    if resolve_ambiguous is not None:
        workers = 1
        if args.jobs is not None and args.jobs > 1:
            print(
                "note: --interactive-roster needs the terminal, so extraction "
                "runs with --jobs 1.",
                file=sys.stderr,
            )
    else:
        workers = resolve_worker_count(args.jobs, len(jobs), vlm_active=vlm_possible)
    if workers > 1:
        emit(f"Extracting {len(jobs)} submissions across {workers} parallel workers")
    else:
        # The parallel path bounds provider calls inside each spawned worker
        # (see `_init_worker`); a sequential run makes them here instead, so
        # this is where `--jobs` reaches the transport's semaphore for it.
        from opengrader.core.llm.transport import set_concurrency_limit

        set_concurrency_limit(1)

    completed = {"count": 0}
    refine_journal = (
        GradeJournal(resolve_history_dir(output_dir), actor=JOURNAL_ACTOR_REFINE)
        if args.refine and not args.refine_dry_run
        else None
    )
    refine_operator = current_actor()
    refine_tally = {"adopted": 0, "changed": 0}

    def report_outcome(outcome: ExtractionOutcome) -> None:
        completed["count"] += 1
        position = f"[{completed['count']}/{len(jobs)}]"
        if outcome.status == "failed":
            emit(f"{position} {outcome.pdf_path.name}: FAILED — {outcome.error}")
            return

        emit(f"{position} {outcome.pdf_path.name} ({outcome.duration_seconds:.1f}s)")

        if args.refine:
            candidate = refine_candidates.get(outcome.key)
            if candidate is None or outcome.markdown_path is None:
                emit("  Warning: nothing to merge for this submission")
                return
            adopted = _apply_refined_document(
                candidate,
                outcome.markdown_path,
                accept_unverified=bool(args.refine_model),
                dry_run=args.refine_dry_run,
                journal=refine_journal,
                operator=refine_operator,
                emit=emit,
            )
            refine_tally["adopted"] += adopted
            refine_tally["changed"] += 1 if adopted else 0
            emit("")
            return

        emit(f"  Identity: {outcome.identity}")
        if outcome.section:
            emit(f"  Section: {outcome.section}")
        emit(f"  Wrote: {outcome.markdown_path}")
        emit("")
        append_record(
            output_dir,
            ExtractionRecord(
                key=outcome.key,
                source=str(outcome.pdf_path),
                source_sha256=outcome.source_sha256,
                settings_id=settings_id,
                outputs=outcome.outputs,
                student_id=outcome.student_id,
                student_name=outcome.student_name,
                completed_at=utc_timestamp(),
                duration_seconds=outcome.duration_seconds,
            ),
        )

    try:
        with (progress_bar or contextlib.nullcontext()):
            report = run_extraction_batch(
                jobs,
                workers=workers,
                progress=extraction_progress,
                status_callback=progress_status,
                on_outcome=report_outcome,
                resolve_ambiguous=resolve_ambiguous,
                fail_fast=args.fail_fast,
            )
        _print_reading_summary(report, escalate_floor=escalate_floor, emit=emit)
        if args.refine and refine_candidates:
            _print_refine_summary(
                RefineSummary(
                    submissions_scanned=refine_scanned,
                    submissions_with_targets=len(refine_candidates),
                    targets=sum(
                        len(candidate.targets) for candidate in refine_candidates.values()
                    ),
                    adopted=refine_tally["adopted"],
                    submissions_changed=refine_tally["changed"],
                ),
                dry_run=args.refine_dry_run,
                emit=emit,
            )
        failures = report.failed
        if failures:
            print(
                f"error: {len(failures)} of {len(jobs)} submission(s) failed:",
                file=sys.stderr,
            )
            for outcome in failures:
                print(f"  {outcome.pdf_path.name}: {outcome.error}", file=sys.stderr)
            print(
                "Re-run the same command with --resume to retry only these."
                if not args.refine
                else "Re-run the same --refine command to retry only these; the "
                "answers they hold are still below the floor.",
                file=sys.stderr,
            )
            sys.exit(1)
    finally:
        if refine_staging is not None:
            shutil.rmtree(refine_staging, ignore_errors=True)
        if ollama_server_proc:
            print("Stopping auto-started Ollama server...")
            ollama_server_proc.terminate()
            try:
                ollama_server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ollama_server_proc.kill()
        if ollama_log_file:
            ollama_log_file.close()


if __name__ == "__main__":
    run_cli()
