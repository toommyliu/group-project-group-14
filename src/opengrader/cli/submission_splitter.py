"""Split a scan bundle into individual student PDFs."""

from __future__ import annotations

from argparse import ArgumentParser, BooleanOptionalAction, RawDescriptionHelpFormatter
from pathlib import Path
from typing import TYPE_CHECKING, Callable, NamedTuple
import sys
import tempfile
import subprocess
import time
import socket
from urllib.parse import urlparse

from opengrader.core.submissions import (
    reorder_pdf_pages,
    page_order_fix_is_safe,
    plan_boundary_ranges,
    plan_submission_ranges,
    _load_page_number_layout,
    analyze_scan_pdf,
    apply_rotation_fixes,
    split_scanned_submission_pdf,
    summarize_issues,
    write_extra_pages_map,
)
from opengrader.core import ai_config

if TYPE_CHECKING:  # pragma: no cover
    # Annotation only — the reader is built by ai_config.vision_reader(), never
    # constructed here.
    from opengrader.core.ocr.vision_reader import VisionReader

from pypdf import PdfReader


EPILOG = """
examples:
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --start-page 3
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --reorder
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --blank-template templates/blank.pdf
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --reorder --auto-ollama
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --blank-template templates/blank.pdf --extra-work-pages --auto-ollama
  uv run submission-splitter --input scans/all-submissions.pdf --output split/   # no page count → detect boundaries by Name/ID header
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --detect-boundaries --auto-ollama --dry-run
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --fix-rotation
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --fix-rotation --fix-order
"""


def _build_parser() -> ArgumentParser:
    ai = ai_config.resolve()
    parser = ArgumentParser(
        description=(
            "Split one scanned PDF that contains many student submissions into "
            "individual per-student PDF files."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, help="Path to scanned input PDF.")
    parser.add_argument(
        "--output",
        required=True,
        help="Directory where split PDF files will be written.",
    )
    parser.add_argument(
        "--pages-per-submission",
        type=int,
        help="Fixed number of pages per student submission.",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="1-indexed start page for splitting (default: 1).",
    )
    parser.add_argument(
        "--end-page",
        type=int,
        default=None,
        help="Optional 1-indexed end page for splitting.",
    )
    parser.add_argument(
        "--filename-prefix",
        default="submission",
        help="Filename prefix for output filenames (default: submission).",
    )
    parser.add_argument(
        "--zero-pad-width",
        type=int,
        default=4,
        help="Zero padding width for output filenames (default: 4).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print planned split ranges without writing files.",
    )
    parser.add_argument(
        "--reorder",
        action="store_true",
        help=(
            "Always reorder pages within each split PDF (text layer → QR → OCR → "
            "Ollama VLM), whether or not the pre-flight saw anything wrong. Use "
            "when you know the scan is jumbled and its page numbers are hard to "
            "read; see --fix-order for the version that only acts on evidence."
        ),
    )
    parser.add_argument(
        "--detect-boundaries",
        action="store_true",
        help=(
            "Detect submission boundaries by the student Name/ID header on each "
            "first page instead of a fixed page count. Used automatically when "
            "neither --pages-per-submission nor --blank-template is given."
        ),
    )
    parser.add_argument(
        "--extra-work-pages",
        action="store_true",
        help=(
            "Submissions may contain extra student-work/scratch pages. Move any "
            "page that can't be matched to the template sequence to the end, so "
            "extraction processes the graded pages first. Implies --reorder; "
            "works best with --blank-template."
        ),
    )
    parser.add_argument(
        "--scan-check",
        action="store_true",
        help=(
            "Pre-flight the scan before splitting: report rotated pages, sheets "
            "that are out of order, blank sheets, low resolution, clipped margins "
            "and a page count that isn't a whole number of submissions. Report "
            "only — see --fix-rotation and --fix-order."
        ),
    )
    parser.add_argument(
        "--fix-rotation",
        action="store_true",
        help=(
            "Turn upside-down/sideways pages upright before splitting, so every "
            "later stage sees a readable page. Implies --scan-check. Writes a "
            "corrected copy under <output>/.opengrader/; the input scan is never "
            "modified."
        ),
    )
    parser.add_argument(
        "--fix-order",
        action="store_true",
        help=(
            "Put out-of-order pages back in order after splitting, but only where "
            "the pre-flight found a problem AND the page numbers can be trusted "
            "— that is, they came from QR codes or a PDF text layer, or a "
            "--blank-template is supplying the true page sequence. When the "
            "numbers were merely OCR'd off a scan, the finding is reported and "
            "nothing is moved, because a misread number would shift a page that "
            "was in the right place. Implies --scan-check; use --reorder to "
            "reorder regardless of evidence."
        ),
    )
    parser.add_argument(
        "--no-scan-orientation",
        action="store_true",
        help=(
            "Skip the rotation part of --scan-check (one Tesseract OSD pass per "
            "page — the slow part). The other pre-flight checks still run."
        ),
    )
    parser.add_argument(
        "--no-scan-page-order",
        action="store_true",
        help=(
            "Skip the page-order part of --scan-check (one OCR of the footer per "
            "page, on scans carrying no QR codes or text layer)."
        ),
    )
    parser.add_argument(
        "--blank-template",
        metavar="PDF",
        default=None,
        help=(
            "Path to a blank version of the assignment template PDF. "
            "Used to locate page numbers more accurately for reordering. "
            "Providing this implicitly enables --reorder."
        ),
    )
    parser.add_argument(
        "--ollama-model",
        default=ai.vision_model,
        help="Ollama vision model for reorder verification (default: %(default)s, from AI Setup).",
    )
    parser.add_argument(
        "--ollama-host",
        default=ai.host,
        help="Ollama server URL for reorder verification (default: %(default)s, from AI Setup).",
    )
    parser.add_argument(
        "--ollama-timeout",
        type=float,
        default=ai.timeout_seconds,
        help="Ollama request timeout in seconds (default: %(default)s, from AI Setup).",
    )
    parser.add_argument(
        "--auto-ollama",
        action="store_true",
        help="Automatically start the Ollama server and pull the vision model if not available.",
    )
    parser.add_argument(
        "--llm-page-numbers",
        action=BooleanOptionalAction,
        default=ai.read_handwriting,
        help=(
            "Last-resort tier for page-number footers the text recognition "
            "cannot read (handwritten or stylized). Slower than the other "
            "tiers, so it is only consulted when they come back with nothing. "
            "(default: %(default)s, from AI Setup)"
        ),
    )
    return parser


def _start_ollama_server(args, emit: "Callable[[str], None]"):
    parsed_url = urlparse(args.ollama_host)
    host = parsed_url.hostname or "127.0.0.1"
    port = parsed_url.port or 11434
    proc = None
    log_file = None

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        already_running = s.connect_ex((host, port)) == 0

    if not already_running:
        emit(f"Ollama server not found at {args.ollama_host}. Starting 'ollama serve'...")
        try:
            log_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
            proc = subprocess.Popen(["ollama", "serve"], stdout=log_file, stderr=log_file)
            start_time = time.time()
            while time.time() - start_time < 30:
                time.sleep(1)
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
                    if s2.connect_ex((host, port)) == 0:
                        break
            else:
                print("Warning: Timed out waiting for Ollama to start; the vision fallback may be unavailable.", file=sys.stderr)
        except FileNotFoundError:
            print("Error: 'ollama' binary not found. Please install Ollama first.", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:
            print(f"Error starting Ollama server: {exc}", file=sys.stderr)
            sys.exit(1)

    emit(f"Ensuring model '{args.ollama_model}' is available...")
    subprocess.run(
        ["ollama", "pull", args.ollama_model],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc, log_file


def _verified_vision_reader(args, emit: "Callable[[str], None]") -> "VisionReader | None":
    resolved = ai_config.with_overrides(
        host=args.ollama_host,
        vision_model=args.ollama_model,
        timeout_seconds=args.ollama_timeout,
    )
    reader = ai_config.vision_reader(resolved)
    try:
        ai_config.ensure_ready(resolved, purpose="vision")
    except Exception as exc:
        emit(f"Ollama vision fallback unavailable ({exc}); using Tesseract only.")
        return None
    return reader


def _expected_pages_per_submission(args) -> int | None:
    if args.pages_per_submission:
        return args.pages_per_submission
    if args.blank_template:
        try:
            return len(PdfReader(str(Path(args.blank_template))).pages)
        except Exception:
            return None
    return None


class _PreflightOutcome(NamedTuple):
    pdf: Path
    auto_reorder: bool = False


def _run_scan_preflight(
    args, input_pdf: Path, emit: "Callable[[str], None]"
) -> _PreflightOutcome:
    emit(
        "Checking the scan (rotation, page order, blanks, resolution, margins, "
        "page count)…"
    )
    report = analyze_scan_pdf(
        input_pdf,
        pages_per_submission=_expected_pages_per_submission(args),
        check_orientation=not args.no_scan_orientation,
        check_page_order=not args.no_scan_page_order,
        progress_callback=emit,
    )

    if not report.orientation_available:
        print(f"Warning: {report.orientation_note}", file=sys.stderr)
    elif report.rotation_plan.unverified_pages:
        pages = ", ".join(str(page) for page in report.rotation_plan.unverified_pages)
        emit(f"  ~ Orientation unverified (too little text) on page(s): {pages}")

    order_plan = report.page_order_plan
    if not order_plan.checked and order_plan.note:
        emit(f"  ~ {order_plan.note}")
    elif order_plan.unread_pages:
        pages = ", ".join(str(page) for page in order_plan.unread_pages)
        emit(f"  ~ No page number could be read on page(s): {pages}")

    if report.issues:
        emit(
            f"  Scan pre-flight: {summarize_issues(report.issues)} "
            f"({len(report.issues)} problem(s) in {input_pdf.name})"
        )
        print(
            f"Warning: scan pre-flight found {len(report.issues)} problem(s) in "
            f"{input_pdf.name}:",
            file=sys.stderr,
        )
        for issue in report.issues:
            location = f"page {issue.page}" if issue.page is not None else "document"
            print(f"  - [{issue.severity}] {location}: {issue.detail}", file=sys.stderr)
    else:
        emit("  ✓ Scan looks clean.")

    auto_reorder = _resolve_auto_reorder(args, report, emit)

    if not args.fix_rotation or not report.rotation_plan:
        if report.rotation_plan and not args.fix_rotation:
            emit(
                "  Re-run with --fix-rotation to turn these pages upright before "
                "splitting."
            )
        return _PreflightOutcome(input_pdf, auto_reorder)

    if args.dry_run:
        emit(
            f"  Dry run: {len(report.rotation_plan.angles)} page(s) would be "
            "rotated; no corrected copy written."
        )
        return _PreflightOutcome(input_pdf, auto_reorder)

    destination = Path(args.output) / ".opengrader" / f"{input_pdf.stem}-upright.pdf"
    turned = apply_rotation_fixes(input_pdf, destination, report.rotation_plan)
    emit(f"  Rotated {turned} page(s) upright → {destination}")
    return _PreflightOutcome(destination, auto_reorder)


def _resolve_auto_reorder(args, report, emit: "Callable[[str], None]") -> bool:
    order_plan = report.page_order_plan
    if not order_plan:
        return False

    safe = page_order_fix_is_safe(
        order_plan, has_blank_template=bool(args.blank_template)
    )
    if not args.fix_order:
        emit(
            "  Re-run with --fix-order to put these pages back in order after "
            "splitting."
            if safe
            else "  Check these pages by eye, then re-run with --reorder to sort "
            "them after splitting."
        )
        return False

    if safe:
        basis = (
            "a blank template supplies the page sequence"
            if order_plan.evidence != "exact"
            else "the page numbers are exact"
        )
        emit(f"  Pages will be put back in order after the split ({basis}).")
        return True

    emit(
        "  Not reordering automatically: these page numbers were read by OCR and "
        "one may have been misread. Check the pages, then use --reorder (or "
        "supply --blank-template) to sort them."
    )
    return False


def run_cli(
    argv: list[str] | None = None,
    *,
    status_callback: "Callable[[str], None] | None" = None,
) -> None:
    def emit(message: str) -> None:
        if status_callback is not None:
            status_callback(message)
        else:
            print(message)

    parser = _build_parser()
    args = parser.parse_args(argv)

    input_pdf = Path(args.input)
    if not input_pdf.exists():
        raise FileNotFoundError(f"input PDF not found: {input_pdf}")
    if input_pdf.suffix.lower() != ".pdf":
        raise ValueError(f"input must be a PDF: {input_pdf}")

    total_pages = len(PdfReader(str(input_pdf)).pages)
    if total_pages <= 0:
        raise ValueError(f"input PDF has no pages: {input_pdf}")

    auto_reorder = False
    if args.scan_check or args.fix_rotation or args.fix_order:
        input_pdf, auto_reorder = _run_scan_preflight(args, input_pdf, emit)

    blank_template_layout = None
    if args.blank_template:
        blank_template_path = Path(args.blank_template)
        if not blank_template_path.exists():
            print(f"Error: Blank template file not found at {blank_template_path}", file=sys.stderr)
            sys.exit(1)
        blank_template_layout = _load_page_number_layout(blank_template_path)

    use_boundary = args.detect_boundaries or (
        args.pages_per_submission is None and blank_template_layout is None
    )
    uses_ordering_pass = (
        args.reorder or args.blank_template or args.extra_work_pages or auto_reorder
    )
    may_use_vlm = args.llm_page_numbers and (use_boundary or uses_ordering_pass)

    pages_per_submission = args.pages_per_submission

    ollama_server_proc = None
    ollama_log_file = None
    try:
        if args.auto_ollama and may_use_vlm:
            provider_kind = ai_config.with_overrides(
                host=args.ollama_host,
                vision_model=args.ollama_model,
                timeout_seconds=args.ollama_timeout,
            ).provider.kind
            if provider_kind == "local":
                ollama_server_proc, ollama_log_file = _start_ollama_server(args, emit)
        vision_reader = _verified_vision_reader(args, emit) if may_use_vlm else None

        boundary_warnings: list[str] = []
        if use_boundary:
            emit("Detecting submission boundaries (student Name/ID header)…")
            ranges, boundary_warnings = plan_boundary_ranges(
                input_pdf,
                vision_reader=vision_reader,
                filename_prefix=args.filename_prefix,
                zero_pad_width=args.zero_pad_width,
                progress_callback=emit,
            )
            emit(f"Detected {len(ranges)} submission(s).")
        else:
            if pages_per_submission is None:
                pages_per_submission = blank_template_layout.total_pages
                emit(f"Inferred {pages_per_submission} pages per submission from blank template.")
            ranges = plan_submission_ranges(
                total_pages=total_pages,
                pages_per_submission=pages_per_submission,
                start_page=args.start_page,
                end_page=args.end_page,
                filename_prefix=args.filename_prefix,
                zero_pad_width=args.zero_pad_width,
            )

        emit(f"Input: {input_pdf} ({total_pages} pages)")
        emit(f"Planned student splits: {len(ranges)}")
        for page_range in ranges:
            emit(f"  - {page_range.name}: pages {page_range.start_page}-{page_range.end_page}")
        for warning in boundary_warnings:
            print(f"Warning: {warning}", file=sys.stderr)

        if args.dry_run:
            emit("Dry run complete. No files written.")
            return

        created = split_scanned_submission_pdf(
            input_pdf=input_pdf,
            output_dir=args.output,
            ranges=ranges,
        )
        emit(f"Wrote {len(created)} split PDFs to: {Path(args.output)}")

        if uses_ordering_pass:
            emit("Checking page order and reordering if necessary...")

            if args.blank_template and not blank_template_layout:
                print("Warning: Could not detect page number layout from blank template. Falling back to generic detection.", file=sys.stderr)
            if (
                blank_template_layout
                and pages_per_submission is not None
                and pages_per_submission != blank_template_layout.total_pages
            ):
                print(f"Warning: --pages-per-submission ({pages_per_submission}) does not match "
                      f"the blank template page count ({blank_template_layout.total_pages}).")

            extra_pages_by_stem: dict[str, tuple[int, ...]] = {}

            for index, pdf_path in enumerate(created, start=1):
                emit(f"[{index}/{len(created)}] Reordering {pdf_path.name}…")
                result = reorder_pdf_pages(
                    pdf_path,
                    vision_reader=vision_reader,
                    blank_template_layout=blank_template_layout,
                    extra_work_pages=args.extra_work_pages,
                )
                if result.changed:
                    emit(f"  - Reordered: {pdf_path.name}")
                else:
                    emit(f"  - Order verified: {pdf_path.name}")
                if args.extra_work_pages:
                    extra_pages_by_stem[pdf_path.stem] = result.extra_work_pages
                    if result.extra_work_pages:
                        pages = ", ".join(str(p) for p in result.extra_work_pages)
                        emit(f"  - Extra work page(s) at the end: {pages}")

            if args.extra_work_pages:
                map_path = write_extra_pages_map(Path(args.output), extra_pages_by_stem)
                marked = sum(1 for pages in extra_pages_by_stem.values() if pages)
                emit(
                    f"Recorded extra work pages for {marked} of "
                    f"{len(extra_pages_by_stem)} submission(s): {map_path}"
                )
    finally:
        if ollama_server_proc:
            emit("Stopping auto-started Ollama server...")
            ollama_server_proc.terminate()
            try:
                ollama_server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                ollama_server_proc.kill()
        if ollama_log_file:
            ollama_log_file.close()


if __name__ == "__main__":
    run_cli()
