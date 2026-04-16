from __future__ import annotations

from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.ocr import OcrConfig, SpecError, load_spec
from opengrader.core.submissions import build_student_submission_document

EPILOG = """
examples:
  uv run student-submission-file --template samples/spec-vA.json --input split/ --output out/submissions/
  uv run student-submission-file --template samples/spec-vA.json --input split/student-0001.pdf --output out/submissions/ --dpi 200
"""


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Convert scanned per-student submission PDFs into markdown student "
            "submission files (frontmatter + per-question response image/text)."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--template",
        required=True,
        help="Path to template spec JSON used for OCR fields/question regions.",
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
        help="Tesseract OCR language code(s), comma-separated (default: eng).",
    )
    parser.add_argument(
        "--psm",
        type=int,
        default=6,
        choices=range(0, 14),
        help="Tesseract page segmentation mode 0-13 (default: 6).",
    )
    parser.add_argument(
        "--whitelist",
        default=None,
        help="Optional OCR whitelist for fields.",
    )
    parser.add_argument(
        "--no-deskew",
        action="store_true",
        help="Disable automatic deskewing before OCR.",
    )
    return parser


def _collect_input_pdfs(input_path: str) -> list[Path]:
    path = Path(input_path)
    if path.is_file():
        if path.suffix.lower() != ".pdf":
            raise ValueError(f"input file must be a PDF: {path}")
        return [path]

    if path.is_dir():
        pdfs = sorted(path.glob("*.pdf"))
        if not pdfs:
            raise ValueError(f"no PDF files found in directory: {path}")
        return pdfs

    raise FileNotFoundError(f"input path not found: {path}")


def run_cli() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        spec = load_spec(args.template)
    except SpecError as exc:
        raise ValueError(f"invalid template spec: {exc}") from exc

    input_pdfs = _collect_input_pdfs(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    config = OcrConfig(
        lang=args.lang,
        psm=args.psm,
        whitelist=args.whitelist,
        deskew=not args.no_deskew,
        confidence_threshold=args.confidence_threshold,
    )

    print(f"Loaded template spec: {args.template}")
    print(f"Found {len(input_pdfs)} submission PDF(s)")

    for i, pdf_path in enumerate(input_pdfs, 1):
        print(f"[{i}/{len(input_pdfs)}] Converting: {pdf_path.name}")
        build_result = build_student_submission_document(
            pdf_path=pdf_path,
            spec=spec,
            output_dir=output_dir,
            ocr_config=config,
            dpi=args.dpi,
        )
        print(f"  Wrote {build_result.markdown_path}")


if __name__ == "__main__":
    run_cli()
