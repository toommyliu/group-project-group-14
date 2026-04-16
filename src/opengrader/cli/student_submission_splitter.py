from __future__ import annotations

from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.ocr import SpecError, load_spec
from opengrader.core.submissions import (
    infer_pages_per_submission_from_template_spec,
    plan_submission_ranges,
    split_scanned_submission_pdf,
)

from pypdf import PdfReader

EPILOG = """
examples:
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --template-spec samples/spec-vA.json
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --template-spec samples/spec-vA.json --start-page 3
"""


def _build_parser() -> ArgumentParser:
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
        "--template-spec",
        default=None,
        help=(
            "Path to template spec JSON (for example samples/spec-vA.json). "
            "If provided, pages-per-submission defaults to the number of pages in the template spec."
        ),
    )
    parser.add_argument(
        "--pages-per-submission",
        type=int,
        default=None,
        help=(
            "Fixed number of pages per student submission. "
            "If omitted, this is inferred from --template-spec."
        ),
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
    return parser


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

    inferred_pages_per_submission: int | None = None
    if args.template_spec:
        try:
            template_spec = load_spec(args.template_spec)
        except SpecError as exc:
            raise ValueError(f"invalid template spec: {exc}") from exc
        inferred_pages_per_submission = infer_pages_per_submission_from_template_spec(
            template_spec
        )

    pages_per_submission = args.pages_per_submission or inferred_pages_per_submission
    if pages_per_submission is None:
        raise ValueError(
            "provide --pages-per-submission or --template-spec to determine split size"
        )

    ranges = plan_submission_ranges(
        total_pages=total_pages,
        pages_per_submission=pages_per_submission,
        start_page=args.start_page,
        end_page=args.end_page,
        filename_prefix=args.filename_prefix,
        zero_pad_width=args.zero_pad_width,
    )

    print(f"Input: {input_pdf} ({total_pages} pages)")
    if inferred_pages_per_submission is not None and args.pages_per_submission is None:
        print(
            "Split size: "
            f"{pages_per_submission} page(s) per submission (inferred from {args.template_spec})"
        )
    else:
        print(f"Split size: {pages_per_submission} page(s) per submission")

    print(f"Planned student splits: {len(ranges)}")
    for page_range in ranges:
        print(
            f"  - {page_range.name}: pages {page_range.start_page}-{page_range.end_page}"
        )

    if args.dry_run:
        print("Dry run complete. No files written.")
        return

    created = split_scanned_submission_pdf(
        input_pdf=input_pdf,
        output_dir=args.output,
        ranges=ranges,
    )

    print(f"Wrote {len(created)} split PDFs to: {Path(args.output)}")


if __name__ == "__main__":
    run_cli()
