"""Split a scan bundle into individual student PDFs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple
import sys

import click

from opengrader.cli import CONTEXT_SETTINGS, resolved_ai, run_command
from opengrader.core.submissions import (
    reorder_pdf_pages,
    page_order_fix_is_safe,
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
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/   # find where each paper starts
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --blank-template templates/blank.pdf --extra-work-pages
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --split every-n --pages-per-submission 6
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --split every-n --blank-template templates/blank.pdf
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --split every-n --pages-per-submission 6 --start-page 3
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --reorder
  opengrader submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --dry-run
"""


_NO_STARTS_FOUND = (
    "Can't tell where each student's paper starts: {reason}. Give the number of pages "
    "in each paper and the scan is cut every that many pages (in the app: choose "
    "'Every N pages' and fill in Pages per submission; on the command line: "
    "--pages-per-submission N)."
)


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
            "(or open AI Setup), then retry — or turn off reading handwriting in "
            "AI Setup to skip this fallback tier.",
            file=sys.stderr,
        )
        sys.exit(2)
    except Exception as exc:
        emit(f"Vision fallback unavailable ({exc}); using Tesseract only.")
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
    has_blank_template: bool,
    dry_run: bool,
) -> _PreflightOutcome:
    """Check a scan about to be cut every N pages, turn its pages upright and
    say whether its page order can be put right after the cut. Nothing reads
    where a paper starts on that path, so a page fed in upside down or out of
    order is only caught here. Page matching turns and orders pages itself."""
    emit(
        "Checking the scan (rotation, page order, blanks, resolution, margins, "
        "page count)…"
    )
    report = analyze_scan_pdf(
        input_pdf,
        pages_per_submission=expected_pages,
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

    auto_reorder = _resolve_auto_reorder(report, emit, has_blank_template=has_blank_template)

    if not report.rotation_plan:
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
    report, emit: Callable[[str], None], *, has_blank_template: bool
) -> bool:
    """Whether to put out-of-order pages back in order after the cut: only
    where the page numbers can be trusted (QR codes, a text layer, or a blank
    template supplying the sequence). An OCR'd number may be misread, and
    moving a page that was right is worse than leaving one shuffled."""
    order_plan = report.page_order_plan
    if not order_plan:
        return False

    safe = page_order_fix_is_safe(order_plan, has_blank_template=has_blank_template)
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
) -> bool:
    """Split by matching pages. False when the scan can't show where each
    paper starts and a page count was given: the caller then cuts every
    `pages_per_submission` pages instead. With no page count it stops."""
    import os

    from opengrader.core.pages.catalog import CATALOG_DIRNAME, build_catalog, scan_pages
    from opengrader.core.pages.split import (
        number_of,
        plan_submissions,
        shared_order_note,
        template_positions,
        unclear_starts,
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
    unclear = unclear_starts(catalog)
    if unclear:
        if not pages_per_submission:
            sys.exit(_NO_STARTS_FOUND.format(reason=unclear))
        note = (f"Can't tell where each paper starts ({unclear}), so the scan is cut "
                f"every {pages_per_submission} pages instead.")
        emit(note)
        print(f"Warning: {note}", file=sys.stderr)
        if reorder_only:
            print("Warning: --reorder-only needs pages matched to each other; nothing is "
                  "sorted by it on a scan cut every N pages.", file=sys.stderr)
        return False
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
              f"{pages_per_submission} are expected; check the page count.", file=sys.stderr)
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
        return True

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
        return True
    _review_scan_order(planned, positions, emit, output=output, reorder_only=reorder_only, interactive=interactive)
    return True


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
    "--split",
    "split_method",
    type=click.Choice(["every-n", "find-starts"]),
    help=(
        "How to cut the PDF into submissions. every-n: every --pages-per-submission "
        "pages (or the blank template's length), from --start-page to --end-page; "
        "nothing is read to decide where a student starts, and the scan is checked, "
        "turned upright and, where the page numbers can be trusted, put back in "
        "order. find-starts: match pages to each other and start a paper at each "
        "page 1; papers may differ in length. Default: find-starts for a scan, "
        "every-n for a typed PDF given a page count."
    ),
)
@click.option(
    "--pages-per-submission",
    type=int,
    help=("Pages per student submission. With --split every-n, where the PDF is cut. "
          "With find-starts it checks the split (a submission of another length is "
          "flagged), and is where the scan is cut if too few page numbers can be read "
          "to tell where each paper starts. Blank: the blank template's length."),
)
@click.option(
    "--start-page",
    type=int,
    default=1,
    help="1-indexed start page for splitting (default: 1). Used only by --split every-n.",
)
@click.option(
    "--end-page",
    type=int,
    help="Optional 1-indexed end page for splitting. Used only by --split every-n.",
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
        "Sort each submission's pages without asking. Without it pages are "
        "moved only on trustworthy evidence: never, when pages were matched "
        "to each other (a submission that looks out of order is reported, and "
        "in a terminal you are asked which to sort), and with every-n only "
        "where the numbers came from QR codes, a text layer or a blank "
        "template. The order comes from --blank-template when one is given, "
        "otherwise from the page numbers read off the pages."
    ),
)
@click.option(
    "--reorder-only",
    multiple=True,
    metavar="NAME[,NAME…]",
    help=(
        "Sort only these submissions of a scan (e.g. submission-0007), and "
        "only if they look out of order. Every other submission is left as "
        "scanned. Repeat the flag or separate names with commas. Needs pages "
        "matched to each other (find-starts)."
    ),
)
@click.option(
    "--extra-work-pages",
    is_flag=True,
    help=(
        "Submissions may contain extra student-work/scratch pages. Record "
        "which pages match no printed page so extraction skips them. When the "
        "PDF is cut every N pages this needs sorting (--reorder; a typed PDF "
        "cut by default also sorts with --blank-template), and moves those "
        "pages to the end."
    ),
)
@click.option(
    "--blank-template",
    metavar="PDF",
    help=(
        "Path to a blank version of the assignment template PDF: one "
        "submission long, so its length is the page count when none is given. "
        "Pages are matched to it to judge page order (and to sort by, with "
        "--reorder). For a typed PDF cut every N pages by default, it also "
        "turns sorting on."
    ),
)
@click.pass_obj
def command(
    status_callback: Callable[[str], None] | None,
    *,
    input_path: str,
    output: str,
    split_method: str | None,
    pages_per_submission: int | None,
    start_page: int,
    end_page: int | None,
    filename_prefix: str,
    zero_pad_width: int,
    dry_run: bool,
    reorder: bool,
    reorder_only: tuple[str, ...],
    extra_work_pages: bool,
    blank_template: str | None,
) -> None:
    """Split one scanned PDF that contains many student submissions into
    individual per-student PDF files.
    """
    emit = status_callback or print

    every_n = split_method == "every-n"
    if every_n and not pages_per_submission and not blank_template:
        raise click.UsageError(
            "--split every-n needs --pages-per-submission (or a --blank-template to take it from)."
        )
    if every_n and reorder_only:
        raise click.UsageError(
            "--reorder-only needs pages matched to each other; use --reorder with --split every-n."
        )

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

    # A blank template is one submission long: with no page count given, its
    # length is the one expected. It checks the split and is the fallback cut
    # size; it decides the cut by itself only with --split every-n.
    template = _existing_template(blank_template)
    template_pages = (
        None if pages_per_submission or template is None
        else _expected_pages_per_submission(None, str(template))
    )
    if every_n and not pages_per_submission:
        if not template_pages:
            raise click.UsageError(
                "--split every-n: the blank template's page count could not be read; "
                "give --pages-per-submission."
            )
        pages_per_submission = template_pages
        emit(f"Cutting every {pages_per_submission} pages, the length of the blank template.")

    find_starts = split_method == "find-starts" or (
        not every_n and (pages_per_submission is None or _is_scan(input_pdf))
    )
    if find_starts:
        expected_length = pages_per_submission or template_pages
        split = _split_by_page_catalog(
            input_pdf,
            emit,
            output=Path(output),
            dry_run=dry_run,
            filename_prefix=filename_prefix,
            zero_pad_width=zero_pad_width,
            pages_per_submission=expected_length,
            extra_work_pages=extra_work_pages,
            reorder=reorder,
            reorder_only=_names(reorder_only),
            blank_template=template,
            # Asked only when a person ran this in a terminal: under the pipeline
            # or the app the same question is put by whoever started the run.
            interactive=status_callback is None and not dry_run and sys.stdin.isatty(),
        )
        if split:
            return
        # The scan can't show where papers start: cut every N pages instead.
        every_n = True
        pages_per_submission = expected_length
    assert pages_per_submission is not None

    # Cutting every N pages reads nothing to find where a student starts, so
    # the scan is checked first: pages turned upright, and the order put right
    # after the cut where the page numbers can be trusted.
    input_pdf, auto_reorder = _run_scan_preflight(
        input_pdf,
        emit,
        output=output,
        expected_pages=pages_per_submission,
        has_blank_template=template is not None,
        dry_run=dry_run,
    )

    blank_template_layout = (
        _load_page_number_layout(template) if template is not None else None
    )

    # A typed PDF cut by default sorts against its template; one cut every N
    # pages on request sorts only on --reorder or trustworthy evidence.
    # --extra-work-pages alone does not sort: with no template to say which pages are
    # extra, sorting only pushes pages whose number went unread to the end.
    uses_ordering_pass = reorder or auto_reorder or (
        template is not None and split_method != "every-n"
    )
    resolved = resolved_ai()
    # No cloud pre-flight here: every vision call the splitter makes (page-number
    # footers, read only to reorder pages) is a whole-page read, and
    # GuardedVisionReader (core/ocr/vision_reader.py) always redirects those to
    # a local reader instead of a cloud host. A splitter run never sends anything
    # to the cloud, so there is nothing to confirm — _verified_vision_reader
    # still exits 2 if a cloud provider is selected without consent.
    vision_reader = (
        _verified_vision_reader(resolved, emit)
        if uses_ordering_pass and resolved.read_handwriting
        else None
    )

    ranges = plan_submission_ranges(
        total_pages=total_pages,
        pages_per_submission=pages_per_submission,
        start_page=start_page,
        end_page=end_page,
        filename_prefix=filename_prefix,
        zero_pad_width=zero_pad_width,
    )
    range_warnings = [f"{group.name}: {warning}" for group in ranges for warning in group.warnings]

    emit(f"Input: {input_pdf} ({total_pages} pages)")
    emit(f"Planned student splits: {len(ranges)}")
    for page_range in ranges:
        emit(f"  - {page_range.name}: pages {page_range.start_page}-{page_range.end_page}")
    for warning in range_warnings:
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
        needs = "--reorder" if every_n else "--blank-template (or --reorder)"
        emit(f"No extra work pages recorded: telling them apart needs {needs}. Pages were left as scanned.")

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
