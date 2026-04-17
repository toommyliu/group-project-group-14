import csv
import json
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.ocr import (
    OcrConfig,
    process_submission_pdf,
    results_to_dict,
)


EPILOG = """
examples:
  uv run ocr-pdf --input submissions/ --output out/
  uv run ocr-pdf --input submission1.pdf --output out/
  uv run ocr-pdf --input submissions/ --output out/ --response-format pdf --dpi 200
"""


def setup_args() -> ArgumentParser:
    parser = ArgumentParser(
        description="OCR extraction from gradeable unit PDFs.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a single PDF or a directory of PDFs.",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Output directory for results.",
    )
    parser.add_argument(
        "--response-format",
        choices=["png", "pdf"],
        default="png",
        help="Image format for question response crops (default: png).",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="Rendering DPI for PDF pages (default: 300).",
    )
    parser.add_argument(
        "--confidence-threshold",
        type=float,
        default=60.0,
        help="Minimum OCR confidence to mark a field as valid (default: 60.0).",
    )
    parser.add_argument(
        "--lang",
        default="eng",
        help="Tesseract language code(s), comma-separated (default: eng).",
    )
    parser.add_argument(
        "--psm",
        type=int,
        default=6,
        choices=range(0, 14),
        help="Tesseract page segmentation mode 0-13 (default: 6 = uniform text block).",
    )
    parser.add_argument(
        "--whitelist",
        default=None,
        help="Tesseract character whitelist (e.g. '0123456789' for SID fields).",
    )
    parser.add_argument(
        "--no-deskew",
        action="store_true",
        help="Disable automatic deskewing of scanned pages.",
    )
    parser.add_argument(
        "--skip-review",
        action="store_true",
        help="Skip writing review_queue.json (only write results.csv/jsonl).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List input files without running OCR.",
    )
    return parser


def collect_input_paths(input_path: str) -> list[Path]:
    p = Path(input_path)
    if p.is_file():
        if p.suffix.lower() != ".pdf":
            raise ValueError(f"Not a PDF: {p}")
        return [p]
    if p.is_dir():
        pdfs = sorted(p.glob("*.pdf"))
        if not pdfs:
            raise ValueError(f"No PDF files found in: {p}")
        return pdfs
    raise FileNotFoundError(f"Input path not found: {p}")


def write_results_jsonl(results: list[dict], output_dir: Path) -> None:
    path = output_dir / "results.jsonl"
    with open(path, "w") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    print(f"Wrote {path}")


def write_results_csv(results: list[dict], output_dir: Path) -> None:
    if not results:
        return
    path = output_dir / "results.csv"
    flat_rows = []
    for r in results:
        row = {
            "filename": r["filename"],
            "version_id": r.get("version_id", ""),
            "needs_review": r.get("needs_review", False),
        }
        for f in r.get("fields", []):
            row[f"field_{f['field_id']}_text"] = f.get("text", "")
            row[f"field_{f['field_id']}_confidence"] = f.get("confidence", "")
            row[f"field_{f['field_id']}_validated"] = f.get("validated", "")
        flat_rows.append(row)
    if flat_rows:
        fieldnames: list[str] = []
        seen: set[str] = set()
        for row in flat_rows:
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
        with open(path, "w", newline="") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(flat_rows)
    print(f"Wrote {path}")


def write_review_queue(results: list[dict], output_dir: Path) -> None:
    review = [r for r in results if r.get("needs_review")]
    if not review:
        print("No submissions need review.")
        return
    path = output_dir / "review_queue.json"
    with open(path, "w") as f:
        json.dump(review, f, indent=2)
    print(f"Wrote {path} ({len(review)} submissions need review)")


def run_cli() -> None:
    parser = setup_args()
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    spec = {}

    input_paths = collect_input_paths(args.input)
    print(f"Found {len(input_paths)} PDF(s) to process")

    if args.dry_run:
        print("\nDry run — skipping OCR. Files that would be processed:")
        for p in input_paths:
            print(f"  {p}")
        return

    ocr_config = OcrConfig(
        lang=args.lang,
        psm=args.psm,
        whitelist=args.whitelist,
        deskew=not args.no_deskew,
        confidence_threshold=args.confidence_threshold,
    )

    all_results: list[dict] = []
    for i, pdf_path in enumerate(input_paths, 1):
        print(f"[{i}/{len(input_paths)}] Processing: {pdf_path.name}")
        try:
            res = process_submission_pdf(
                pdf_path=str(pdf_path),
                spec=spec,
                config=ocr_config,
                output_dir=output_dir,
                response_format=args.response_format,
                dpi=args.dpi,
            )
            all_results.append(results_to_dict(res))
            status = "OK" if not res.needs_review else "REVIEW"
            version_conf = (
                res.version_confidence if res.version_confidence is not None else 0.0
            )
            detected_version = (
                res.version_id if res.version_id is not None else "(none detected)"
            )
            print(
                f"  detected version: {detected_version!r} (confidence {version_conf:.1f})"
            )
            print(
                f"  version={res.version_id!r} confidence={version_conf:.1f} [{status}]"
            )
            for fr in res.fields:
                v = "V" if fr.validated else "X"
                conf = fr.ocr_result.confidence if fr.ocr_result else 0.0
                field_text = fr.ocr_result.text if fr.ocr_result else ""
                print(f"  [{v}] {fr.field_id}: {field_text!r} ({conf:.1f})")
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            all_results.append(
                {
                    "filename": pdf_path.name,
                    "error": str(e),
                    "needs_review": True,
                    "review_reasons": [f"processing_error: {e}"],
                }
            )

    write_results_jsonl(all_results, output_dir)
    write_results_csv(all_results, output_dir)
    if not args.skip_review:
        write_review_queue(all_results, output_dir)

    review_count = sum(1 for r in all_results if r.get("needs_review"))
    print(f"\nDone. {len(all_results)} processed, {review_count} need review.")


if __name__ == "__main__":
    run_cli()
