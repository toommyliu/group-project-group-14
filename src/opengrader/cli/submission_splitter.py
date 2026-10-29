"""Split a scan bundle into individual student PDFs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple
import sys

import click

from opengrader.cli import CONTEXT_SETTINGS, ai_default, run_command
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
from opengrader.core import app_config

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.ocr.vision_reader import VisionReader

from pypdf import PdfReader


EPILOG = """\
\b
Examples:
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --start-page 3
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --reorder
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --blank-template templates/blank.pdf
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --reorder
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --blank-template templates/blank.pdf --extra-work-pages
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/   # no page count → match pages to each other, flag missing pages
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --detect-boundaries --dry-run
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --fix-rotation
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --fix-rotation --fix-order
"""


def _verified_vision_reader(
    resolved: app_config.ResolvedAI, emit: Callable[[str], None]
) -> VisionReader | None:
    try:
        reader = app_config.vision_reader(resolved)
        app_config.ensure_ready(resolved, purpose="vision")
    except (app_config.CloudConsentRequired, app_config.CloudDisabledError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(
            f"Fix: run 'opengrader providers consent {resolved.provider.name}' "
            "(or open AI Setup), then retry — or pass --no-llm-page-numbers to "
            "skip this fallback tier.",
            file=sys.stderr,
        )
        sys.exit(2)
    except Exception as exc:
        emit(f"Ollama vision fallback unavailable ({exc}); using Tesseract only.")
        return None
    return reader


def _expected_pages_per_submission(
    pages_per_submission: int | None, blank_template: str | None
) -> int | None:
    if pages_per_submission:
        return pages_per_submission
    if blank_template:
        try:
            return len(PdfReader(str(Path(blank_template))).pages)
        except Exception:
            return None
    return None


class _PreflightOutcome(NamedTuple):
    pdf: Path
    auto_reorder: bool = False


def _run_scan_preflight(
    input_pdf: Path,
    emit: Callable[[str], None],
    *,
    output: str,
    expected_pages: int | None,
    check_orientation: bool,
    check_page_order: bool,
    fix_rotation: bool,
    fix_order: bool,
    has_blank_template: bool,
    dry_run: bool,
) -> _PreflightOutcome:
    emit(
        "Checking the scan (rotation, page order, blanks, resolution, margins, "
        "page count)…"
    )
    report = analyze_scan_pdf(
        input_pdf,
        pages_per_submission=expected_pages,
        check_orientation=check_orientation,
        check_page_order=check_page_order,
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

    auto_reorder = _resolve_auto_reorder(
        report, emit, fix_order=fix_order, has_blank_template=has_blank_template
    )

    if not fix_rotation or not report.rotation_plan:
        if report.rotation_plan and not fix_rotation:
            emit(
                "  Re-run with --fix-rotation to turn these pages upright before "
                "splitting."
            )
        return _PreflightOutcome(input_pdf, auto_reorder)

    if dry_run:
        emit(
            f"  Dry run: {len(report.rotation_plan.angles)} page(s) would be "
            "rotated; no corrected copy written."
        )
        return _PreflightOutcome(input_pdf, auto_reorder)

    destination = Path(output) / ".opengrader" / f"{input_pdf.stem}-upright.pdf"
    turned = apply_rotation_fixes(input_pdf, destination, report.rotation_plan)
    emit(f"  Rotated {turned} page(s) upright → {destination}")
    return _PreflightOutcome(destination, auto_reorder)


def _resolve_auto_reorder(
    report, emit: Callable[[str], None], *, fix_order: bool, has_blank_template: bool
) -> bool:
    order_plan = report.page_order_plan
    if not order_plan:
        return False

    safe = page_order_fix_is_safe(order_plan, has_blank_template=has_blank_template)
    if not fix_order:
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


def _names(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(name.strip() for value in values for name in value.split(",") if name.strip())


def _existing_template(blank_template: str | None) -> Path | None:
    if not blank_template:
        return None
    path = Path(blank_template)
    if not path.exists():
        print(f"Error: Blank template file not found at {path}", file=sys.stderr)
        sys.exit(1)
    return path


def _is_scan(pdf: Path) -> bool:
    from opengrader.core.pages.catalog import is_scan

    return is_scan(pdf)


def _split_by_page_catalog(
    input_pdf: Path,
    emit: Callable[[str], None],
    *,
    output: Path,
    dry_run: bool,
    filename_prefix: str,
    zero_pad_width: int,
    pages_per_submission: int | None,
    extra_work_pages: bool,
    reorder: bool,
    reorder_only: tuple[str, ...],
    blank_template: Path | None,
    interactive: bool,
) -> None:
    import os

    from opengrader.core.pages.catalog import CATALOG_DIRNAME, build_catalog, scan_pages
    from opengrader.core.pages.split import (
        number_of,
        plan_submissions,
        shared_order_note,
        template_positions,
        write_sidecar,
        write_split_pdf,
    )
    from opengrader.core.pages.split_review import SplitPage, SplitPart, typical_submission
    from opengrader.core.submissions.splitter import write_scan_warnings

    emit("Matching scanned pages to each other…")
    catalog = build_catalog(
        scan_pages([input_pdf]),
        None if dry_run else output / CATALOG_DIRNAME,
        workers=min(8, os.cpu_count() or 4),
        emit=emit,
    )
    positions = None
    if blank_template is not None:
        emit("Matching the printed pages to the blank template…")
        positions = template_positions(catalog, blank_template)
        emit(f"  {len(positions)} of {len(catalog.groups)} printed page(s) found in the blank template")
        if not positions:
            print("Warning: no scanned page matched the blank template; page order is judged by the "
                  "page numbers read off the scan instead.", file=sys.stderr)
    planned = plan_submissions(catalog, prefix=filename_prefix, pad=zero_pad_width,
                               expected_count=pages_per_submission, reorder=reorder,
                               template_positions=positions)
    expected = catalog.expected_pages
    if pages_per_submission and expected and len(expected) != pages_per_submission:
        print(f"Warning: the scan shows {len(expected)} printed page(s) per submission, but "
              f"--pages-per-submission is {pages_per_submission}; check the page count.", file=sys.stderr)
    emit(f"Input: {input_pdf} ({len(catalog.matches)} pages, {len(expected)} printed page(s) per submission)")
    emit(f"Planned student splits: {len(planned)}")
    for submission in planned:
        pages = ", ".join(str(m.page.index + 1) for m in submission.pages)
        emit(f"  - {submission.name}: scan pages {pages}")
        for warning in submission.warnings:
            print(f"Warning: {submission.name}: {warning}", file=sys.stderr)
    _report_scan_order(planned, emit, shared_note=shared_order_note(planned))
    if dry_run:
        emit("Dry run complete. No files written.")
        return

    output.mkdir(parents=True, exist_ok=True)
    extra_pages: dict[str, tuple[int, ...]] = {}
    for submission in planned:
        pdf = write_split_pdf(submission, output / f"{submission.name}.pdf")
        write_scan_warnings(pdf, tuple(submission.warnings))
        write_sidecar(pdf, submission, catalog)
        extra_pages[pdf.stem] = tuple(i for i, m in enumerate(submission.pages, start=1) if m.group is None)
    if extra_work_pages:
        write_extra_pages_map(output, extra_pages)
    flagged = sum(1 for submission in planned if submission.warnings)
    emit(f"Wrote {len(planned)} split PDFs to: {output}" + (f" ({flagged} flagged for review)" if flagged else ""))
    parts = [
        SplitPart(
            submission.name,
            tuple(SplitPage(number_of(catalog, m), m.group is not None) for m in submission.pages),
        )
        for submission in planned
    ]
    usual_length, usual_pages = typical_submission(parts)
    if not _review_split(
        output, parts, pages_per_submission or usual_length, usual_pages, emit, interactive=interactive
    ):
        return
    _review_scan_order(planned, positions, emit, output=output, reorder_only=reorder_only, interactive=interactive)


def _review_split(
    output: Path,
    parts,
    expected_pages: int | None,
    printed_pages,
    emit: Callable[[str], None],
    *,
    interactive: bool,
) -> bool:
    """Note where the split looks wrong (a submission of the wrong length, a
    printed page missing or repeated) for a person to confirm before
    extraction. False when the person, asked here, chose to stop."""
    from opengrader.core.pages.split_review import (
        describe_split_pending,
        pending_split_findings,
        record_split_review,
        review_split_in_terminal,
    )

    findings = record_split_review(output, parts, expected_pages, printed_pages)
    if not findings:
        return True
    if interactive:
        return review_split_in_terminal(output, ask=input, write=emit)
    pending = pending_split_findings(output)
    print(f"Warning: {len(pending)} place(s) where the split looks wrong; extraction "
          "should wait until they are checked:", file=sys.stderr)
    for line in describe_split_pending(pending):
        print(f"  {line}", file=sys.stderr)
    return True


def _review_scan_order(
    planned,
    positions: dict[str, int] | None,
    emit: Callable[[str], None],
    *,
    output: Path,
    reorder_only: tuple[str, ...],
    interactive: bool,
) -> None:
    from opengrader.core.pages.order_review import (
        OrderFinding,
        pending_findings,
        review_in_terminal,
        sort_pages,
        write_order_review,
    )

    write_order_review(
        output,
        [
            OrderFinding(
                name=submission.name,
                keys=submission.order_keys,
                by_template=submission.order_by_template,
                shared=submission.order_shared,
                warning=submission.order_warning,
            )
            for submission in planned
            if submission.order_breaks and not submission.reordered
        ],
        positions,
    )
    if reorder_only:
        wanted = {Path(name).stem for name in reorder_only}
        done = sort_pages(output, wanted)
        if done:
            emit(f"Sorted the pages of: {', '.join(done)}")
        for name in sorted(wanted - set(done)):
            print(f"Warning: --reorder-only {name}: not a submission that looks out of order; "
                  "nothing was moved.", file=sys.stderr)
    if interactive and pending_findings(output):
        review_in_terminal(output, ask=input, write=emit)


def _report_scan_order(planned, emit: Callable[[str], None], *, shared_note: str | None) -> None:
    if shared_note:
        print(f"Warning: {shared_note}", file=sys.stderr)
    sorted_count = sum(1 for submission in planned if submission.reordered)
    if sorted_count:
        emit(f"Sorted the pages of {sorted_count} submission(s) that looked out of order.")
        return
    left = [s.name for s in planned if s.order_breaks and not s.order_shared]
    if left:
        emit(
            f"{len(left)} submission(s) look out of order and were left as scanned: "
            f"{', '.join(left)}. Check them, then re-run with --reorder to sort them "
            "(or --reorder-only to pick which)."
        )


@click.command("submission-splitter", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option("--input", "input_path", required=True, help="Path to scanned input PDF.")
@click.option(
    "--output",
    required=True,
    help="Directory where split PDF files will be written.",
)
@click.option(
    "--pages-per-submission",
    type=int,
    help=("Pages per student submission. For scans this checks the split (a submission of "
          "another length is flagged); pages are still matched to decide it. For typed PDFs, "
          "or with --detect-boundaries, it is a fixed split size."),
)
@click.option(
    "--start-page",
    type=int,
    default=1,
    help="1-indexed start page for splitting (default: 1).",
)
@click.option(
    "--end-page",
    type=int,
    help="Optional 1-indexed end page for splitting.",
)
@click.option(
    "--filename-prefix",
    default="submission",
    help="Filename prefix for output filenames (default: submission).",
)
@click.option(
    "--zero-pad-width",
    type=int,
    default=4,
    help="Zero padding width for output filenames (default: 4).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Only print planned split ranges without writing files.",
)
@click.option(
    "--reorder",
    is_flag=True,
    help=(
        "Sort each submission's pages without asking. Without it a scan's "
        "pages are never moved unprompted: a submission that looks out of "
        "order is reported and left as scanned, because a misread page number "
        "would move a page that was already right, and in a terminal you are "
        "then asked which to sort. For scans the order comes from --blank-template when "
        "one is given (pages are matched to it by their print), otherwise from "
        "the page numbers read off the scan. For typed PDFs and "
        "--detect-boundaries it reorders every split PDF (text layer → QR → "
        "OCR → Ollama VLM) whether or not the pre-flight saw anything wrong; "
        "see --fix-order for the version that only acts on evidence."
    ),
)
@click.option(
    "--reorder-only",
    multiple=True,
    metavar="NAME[,NAME…]",
    help=(
        "Sort only these submissions of a scan (e.g. submission-0007), and "
        "only if they look out of order. Every other submission is left as "
        "scanned. Repeat the flag or separate names with commas."
    ),
)
@click.option(
    "--detect-boundaries",
    is_flag=True,
    help=(
        "Legacy boundary detection from printed page numbers and Name/ID headers read "
        "with Tesseract. Without --pages-per-submission (and without this flag), pages "
        "are instead matched to each other: each student starts at a copy of page 1, "
        "pages are turned upright and put in order, and missing pages are flagged. "
        "With this flag, the page count or blank template checks completeness instead "
        "of fixing split sizes."
    ),
)
@click.option(
    "--extra-work-pages",
    is_flag=True,
    help=(
        "Submissions may contain extra student-work/scratch pages. Record "
        "which pages match no printed page so extraction skips them. For "
        "typed PDFs and --detect-boundaries this needs --blank-template (or "
        "--reorder), and moves those pages to the end."
    ),
)
@click.option(
    "--scan-check",
    is_flag=True,
    help=(
        "Pre-flight the scan before splitting: report rotated pages, sheets "
        "that are out of order, blank sheets, low resolution, clipped margins "
        "and a page count that isn't a whole number of submissions. Report "
        "only — see --fix-rotation and --fix-order."
    ),
)
@click.option(
    "--fix-rotation",
    is_flag=True,
    help=(
        "Turn upside-down/sideways pages upright before splitting, so every "
        "later stage sees a readable page. Implies --scan-check. Writes a "
        "corrected copy under <output>/.opengrader/; the input scan is never "
        "modified."
    ),
)
@click.option(
    "--fix-order",
    is_flag=True,
    help=(
        "Put out-of-order pages back in order after splitting, but only where "
        "the pre-flight found a problem AND the page numbers can be trusted "
        "— that is, they came from QR codes or a PDF text layer, or a "
        "--blank-template is supplying the true page sequence. When the "
        "numbers were merely OCR'd off a scan, the finding is reported and "
        "nothing is moved, because a misread number would shift a page that "
        "was in the right place. Implies --scan-check; use --reorder to "
        "reorder regardless of evidence. Scans split by matching their pages "
        "skip this pre-flight and are sorted only by --reorder."
    ),
)
@click.option(
    "--no-scan-orientation",
    is_flag=True,
    help=(
        "Skip the rotation part of --scan-check (one Tesseract OSD pass per "
        "page — the slow part). The other pre-flight checks still run."
    ),
)
@click.option(
    "--no-scan-page-order",
    is_flag=True,
    help=(
        "Skip the page-order part of --scan-check (one OCR of the footer per "
        "page, on scans carrying no QR codes or text layer)."
    ),
)
@click.option(
    "--blank-template",
    metavar="PDF",
    help=(
        "Path to a blank version of the assignment template PDF. "
        "For scans, pages are matched to it to judge page order (and to sort "
        "by, with --reorder). For typed PDFs and --detect-boundaries it "
        "locates page numbers more accurately and implicitly enables "
        "--reorder."
    ),
)
@click.option(
    "--ollama-model",
    default=ai_default("vision_model"),
    show_default="from AI Setup",
    help="Ollama vision model for reorder verification.",
)
@click.option(
    "--ollama-host",
    default=ai_default("host"),
    show_default="from AI Setup",
    help="Ollama server URL for reorder verification.",
)
@click.option(
    "--ollama-timeout",
    type=float,
    default=ai_default("timeout_seconds"),
    show_default="from AI Setup",
    help="Ollama request timeout in seconds.",
)
@click.option(
    "--llm-page-numbers/--no-llm-page-numbers",
    default=ai_default("read_handwriting"),
    show_default="from AI Setup",
    help=(
        "Last-resort tier for page-number footers the text recognition "
        "cannot read (handwritten or stylized). Slower than the other "
        "tiers, so it is only consulted when they come back with nothing."
    ),
)
@click.pass_obj
def command(
    status_callback: Callable[[str], None] | None,
    *,
    input_path: str,
    output: str,
    pages_per_submission: int | None,
    start_page: int,
    end_page: int | None,
    filename_prefix: str,
    zero_pad_width: int,
    dry_run: bool,
    reorder: bool,
    reorder_only: tuple[str, ...],
    detect_boundaries: bool,
    extra_work_pages: bool,
    scan_check: bool,
    fix_rotation: bool,
    fix_order: bool,
    no_scan_orientation: bool,
    no_scan_page_order: bool,
    blank_template: str | None,
    ollama_model: str,
    ollama_host: str,
    ollama_timeout: float,
    llm_page_numbers: bool,
) -> None:
    """Split one scanned PDF that contains many student submissions into
    individual per-student PDF files.
    """
    emit = status_callback or print

    input_pdf = Path(input_path)
    if not input_pdf.exists():
        raise FileNotFoundError(f"input PDF not found: {input_pdf}")
    if input_pdf.suffix.lower() != ".pdf":
        raise ValueError(f"input must be a PDF: {input_pdf}")

    total_pages = len(PdfReader(str(input_pdf)).pages)
    if total_pages <= 0:
        raise ValueError(f"input PDF has no pages: {input_pdf}")

    from opengrader.core import egress

    egress.activate(Path(output))

    if not detect_boundaries and (pages_per_submission is None or _is_scan(input_pdf)):
        _split_by_page_catalog(
            input_pdf,
            emit,
            output=Path(output),
            dry_run=dry_run,
            filename_prefix=filename_prefix,
            zero_pad_width=zero_pad_width,
            pages_per_submission=pages_per_submission,
            extra_work_pages=extra_work_pages,
            reorder=reorder,
            reorder_only=_names(reorder_only),
            blank_template=_existing_template(blank_template),
            # Asked only when a person ran this in a terminal: under the pipeline
            # or the app the same question is put by whoever started the run.
            interactive=status_callback is None and not dry_run and sys.stdin.isatty(),
        )
        return

    auto_reorder = False
    if scan_check or fix_rotation or fix_order:
        input_pdf, auto_reorder = _run_scan_preflight(
            input_pdf,
            emit,
            output=output,
            expected_pages=_expected_pages_per_submission(
                pages_per_submission, blank_template
            ),
            check_orientation=not no_scan_orientation,
            check_page_order=not no_scan_page_order,
            fix_rotation=fix_rotation,
            fix_order=fix_order,
            has_blank_template=bool(blank_template),
            dry_run=dry_run,
        )

    blank_template_layout = None
    blank_template_path = _existing_template(blank_template)
    if blank_template_path is not None:
        blank_template_layout = _load_page_number_layout(blank_template_path)

    use_boundary = detect_boundaries or pages_per_submission is None
    # --extra-work-pages alone does not sort: with no template to say which pages are
    # extra, sorting only pushes pages whose number went unread to the end.
    uses_ordering_pass = reorder or blank_template or auto_reorder
    may_use_vlm = llm_page_numbers and (use_boundary or uses_ordering_pass)

    if may_use_vlm:
        resolved_for_reorder = app_config.with_overrides(
            host=ollama_host,
            vision_model=ollama_model,
            timeout_seconds=ollama_timeout,
        )
        # No cloud pre-flight here: every vision call the splitter makes
        # (page-number footers and Name/ID headers, used only to detect
        # boundaries and reorder pages) is a whole-page read, and
        # GuardedVisionReader (core/ocr/vision_reader.py) always redirects
        # those to a local reader instead of a cloud host. A splitter run
        # never sends anything to the cloud, so there is nothing to
        # confirm — see _verified_vision_reader below, which still exits
        # 2 if a cloud provider is selected without consent.
    vision_reader = (
        _verified_vision_reader(resolved_for_reorder, emit) if may_use_vlm else None
    )

    boundary_warnings: list[str] = []
    if use_boundary:
        emit("Detecting submission boundaries (page numbers and Name/ID headers)…")
        ranges, boundary_warnings = plan_boundary_ranges(
            input_pdf,
            vision_reader=vision_reader,
            expected_pages=_expected_pages_per_submission(
                pages_per_submission, blank_template
            ),
            filename_prefix=filename_prefix,
            zero_pad_width=zero_pad_width,
            progress_callback=emit,
        )
        emit(f"Detected {len(ranges)} submission(s).")
    else:
        ranges = plan_submission_ranges(
            total_pages=total_pages,
            pages_per_submission=pages_per_submission,
            start_page=start_page,
            end_page=end_page,
            filename_prefix=filename_prefix,
            zero_pad_width=zero_pad_width,
        )
        boundary_warnings = [f"{group.name}: {warning}" for group in ranges for warning in group.warnings]

    emit(f"Input: {input_pdf} ({total_pages} pages)")
    emit(f"Planned student splits: {len(ranges)}")
    for page_range in ranges:
        emit(f"  - {page_range.name}: pages {page_range.start_page}-{page_range.end_page}")
    for warning in boundary_warnings:
        print(f"Warning: {warning}", file=sys.stderr)

    if dry_run:
        emit("Dry run complete. No files written.")
        return

    created = split_scanned_submission_pdf(
        input_pdf=input_pdf,
        output_dir=output,
        ranges=ranges,
    )
    emit(f"Wrote {len(created)} split PDFs to: {Path(output)}")

    if uses_ordering_pass:
        emit("Checking page order and reordering if necessary...")

        if blank_template and not blank_template_layout:
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
                extra_work_pages=extra_work_pages,
            )
            if result.changed:
                emit(f"  - Reordered: {pdf_path.name}")
            else:
                emit(f"  - Order verified: {pdf_path.name}")
            if extra_work_pages:
                extra_pages_by_stem[pdf_path.stem] = result.extra_work_pages
                if result.extra_work_pages:
                    pages = ", ".join(str(p) for p in result.extra_work_pages)
                    emit(f"  - Extra work page(s) at the end: {pages}")

        if extra_work_pages:
            map_path = write_extra_pages_map(Path(output), extra_pages_by_stem)
            marked = sum(1 for pages in extra_pages_by_stem.values() if pages)
            emit(
                f"Recorded extra work pages for {marked} of "
                f"{len(extra_pages_by_stem)} submission(s): {map_path}"
            )
    elif extra_work_pages:
        write_extra_pages_map(Path(output), {pdf_path.stem: () for pdf_path in created})
        emit(
            "No extra work pages recorded: telling them apart needs --blank-template "
            "(or --reorder). Pages were left as scanned."
        )

    from opengrader.core.pages.split_review import SplitPage, SplitPart, typical_submission
    from opengrader.core.submissions.extra_pages import read_extra_pages_for

    parts = []
    for pdf_path in created:
        extra = set(read_extra_pages_for(pdf_path) or ())
        count = len(PdfReader(str(pdf_path)).pages)
        parts.append(SplitPart(
            pdf_path.stem,
            tuple(SplitPage(matched=position not in extra) for position in range(1, count + 1)),
        ))
    expected = pages_per_submission or typical_submission(parts)[0]
    _review_split(
        Path(output), parts, expected, (), emit,
        interactive=status_callback is None and sys.stdin.isatty(),
    )


def run_cli(
    argv: Sequence[str], *, status_callback: Callable[[str], None] | None = None
) -> None:
    run_command(command, argv, obj=status_callback)


if __name__ == "__main__":
    command()
