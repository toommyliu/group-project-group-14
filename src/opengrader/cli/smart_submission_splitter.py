from __future__ import annotations

from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from pypdf import PdfReader

from opengrader.core.submissions import (
    collect_page_evidence_from_pdf,
    plan_submission_distribution,
    split_distributed_submission_pdf,
    write_distribution_report,
)

"""
Split one scanned PDF into per-student submissions using page-number evidence.
"""

EPILOG = """
examples:
  uv run smart-submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6
  uv run smart-submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --report-json split-plan.json
  uv run smart-submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --dry-run
"""


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Split one scanned PDF into per-student PDFs by inferring logical "
            "page sequences, missing pages, and uncertain boundaries."
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
        required=True,
        help="Expected logical number of pages per student submission.",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="1-indexed physical start page for inference (default: 1).",
    )
    parser.add_argument(
        "--end-page",
        type=int,
        default=None,
        help="Optional 1-indexed physical end page for inference.",
    )
    parser.add_argument(
        "--filename-prefix",
        default="submission",
        help="Filename prefix for normal output files (default: submission).",
    )
    parser.add_argument(
        "--review-prefix",
        default="needs-review",
        help="Filename prefix for unknown-owner output files (default: needs-review).",
    )
    parser.add_argument(
        "--report-json",
        default=None,
        help="Optional path for a machine-readable JSON split report.",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Rendering DPI for page evidence OCR (default: 200).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print/write the inferred split plan without writing PDFs.",
    )
    parser.add_argument(
        "--review",
        action="store_true",
        help="Open an interactive TUI to inspect inferred split submissions.",
    )
    parser.add_argument(
        "--min-page-number-confidence",
        type=float,
        default=60.0,
        help="Minimum OCR confidence for trusting footer page numbers (default: 60).",
    )
    return parser


def _format_sequence(values: tuple[int | None, ...]) -> str:
    return ",".join("?" if value is None else str(value) for value in values)


def _format_pages(values: tuple[int, ...]) -> str:
    return ",".join(str(value) for value in values)


def _print_plan(
    *,
    input_pdf: Path,
    total_pages: int,
    selected_pages: int,
    pages_per_submission: int,
    plan,
) -> None:
    print(f"Input: {input_pdf} ({total_pages} total page(s))")
    print(f"Selected pages scanned: {selected_pages}")
    print(f"Expected logical pages per submission: {pages_per_submission}")
    print(f"Inferred splits: {len(plan.ranges)}")

    for page_range in plan.ranges:
        missing = _format_pages(page_range.missing_pages) or "none"
        review = "yes" if page_range.needs_review else "no"
        print(
            f"  - {page_range.name}: physical pages "
            f"{_format_pages(page_range.physical_pages)}; "
            f"logical pages {_format_sequence(page_range.logical_pages)}; "
            f"missing {missing}; confidence {page_range.confidence:.1f}; "
            f"review {review}"
        )
        for reason in page_range.review_reasons:
            print(f"      review: {reason}")

    if plan.needs_review:
        print("Review needed:")
        for reason in plan.review_reasons:
            print(f"  - {reason}")
    else:
        print("Review needed: no")


def run_cli() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    input_pdf = Path(args.input)
    if not input_pdf.exists():
        raise FileNotFoundError(f"input PDF not found: {input_pdf}")
    if input_pdf.suffix.lower() != ".pdf":
        raise ValueError(f"input must be a PDF: {input_pdf}")

    total_pages = len(PdfReader(str(input_pdf)).pages)
    if total_pages <= 0:
        raise ValueError(f"input PDF has no pages: {input_pdf}")

    evidence = collect_page_evidence_from_pdf(
        input_pdf,
        pages_per_submission=args.pages_per_submission,
        start_page=args.start_page,
        end_page=args.end_page,
        dpi=args.dpi,
        min_page_number_confidence=args.min_page_number_confidence,
    )
    plan = plan_submission_distribution(
        page_evidence=evidence,
        pages_per_submission=args.pages_per_submission,
        filename_prefix=args.filename_prefix,
        review_prefix=args.review_prefix,
    )

    _print_plan(
        input_pdf=input_pdf,
        total_pages=total_pages,
        selected_pages=len(evidence),
        pages_per_submission=args.pages_per_submission,
        plan=plan,
    )

    if args.review:
        from opengrader.tui.scan_review import ScanReviewApp

        ScanReviewApp(
            input_pdf=input_pdf,
            plan=plan,
            pages_per_submission=args.pages_per_submission,
        ).run()

    if args.report_json:
        report_path = write_distribution_report(plan, args.report_json)
        print(f"Wrote split report: {report_path}")

    if args.dry_run:
        print("Dry run complete. No files written.")
        return

    created = split_distributed_submission_pdf(
        input_pdf=input_pdf,
        output_dir=args.output,
        ranges=plan.ranges,
    )
    print(f"Wrote {len(created)} split PDFs to: {Path(args.output)}")


if __name__ == "__main__":
    run_cli()
