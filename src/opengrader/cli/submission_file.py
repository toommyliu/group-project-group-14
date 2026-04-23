from __future__ import annotations

from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.ocr import OcrConfig
from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig
from opengrader.core.submissions import build_student_submission_document

"""
Takes the individual student PDF and the OCR data and bundles them into a Markdown report for each student
"""


EPILOG = """
examples:
  uv run submission-file --input split/ --output out/submissions/
  uv run submission-file --input split/student-0001.pdf --output out/submissions/ --dpi 200
  uv run submission-file --input split/ --output out/submissions/ --text-backend ollama --ollama-model qwen2.5vl:7b
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
    parser.add_argument(
        "--no-split-subquestions",
        action="store_true",
        help=(
            "Keep each detected question as a single response image even when "
            "nested subquestion boxes are detected."
        ),
    )
    parser.add_argument(
        "--text-backend",
        choices=["tesseract", "ollama"],
        default="tesseract",
        help=(
            "Text extraction backend for student name/ID fields and "
            "question prompt/answer extraction. "
            "The ollama backend evaluates multiple header crop variants for "
            "student fields and response images "
            "(default: tesseract)."
        ),
    )
    parser.add_argument(
        "--ollama-model",
        default="qwen2.5vl:7b",
        help="Ollama vision model to use when --text-backend=ollama.",
    )
    parser.add_argument(
        "--ollama-host",
        default="http://127.0.0.1:11434",
        help="Ollama server URL when --text-backend=ollama.",
    )
    parser.add_argument(
        "--ollama-timeout",
        type=float,
        default=120.0,
        help="Ollama request timeout in seconds (default: 120).",
    )
    parser.add_argument(
        "--no-ollama-qa",
        action="store_true",
        help=(
            "Skip ollama question prompt/answer extraction. "
            "When used with --text-backend=ollama, ollama will still be used "
            "for student name/ID field extraction but question images will use local OCR."
        ),
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

    print(f"Using text backend: {args.text_backend} {'' if args.text_backend == 'tesseract' else f'(model: {args.ollama_model})'}")
    print(f"Found {len(input_pdfs)} submission PDF(s)")

    ollama_config = OllamaVisionConfig(
        model=args.ollama_model,
        host=args.ollama_host,
        timeout_seconds=args.ollama_timeout,
    )

    for i, pdf_path in enumerate(input_pdfs, 1):
        print(f"[{i}/{len(input_pdfs)}] Converting: {pdf_path.name}")
        build_result = build_student_submission_document(
            pdf_path=pdf_path,
            output_dir=output_dir,
            ocr_config=config,
            dpi=args.dpi,
            split_subquestions=not args.no_split_subquestions,
            text_backend=args.text_backend,
            ollama_config=ollama_config,
            skip_ollama_qa=args.no_ollama_qa,
            progress_callback=(lambda message: print(f"  {message}")),
        )
        print(f"  Wrote {build_result.markdown_path}")


if __name__ == "__main__":
    run_cli()
