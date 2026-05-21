from __future__ import annotations

from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path
import tempfile
import subprocess
import time
import socket
from urllib.parse import urlparse

from opengrader.core.submissions import (
    reorder_pdf_pages,
    plan_submission_ranges,
    _load_page_number_layout,
    split_scanned_submission_pdf,
)
from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig, ensure_model_available

from pypdf import PdfReader

"""
Splits one large PDF into individual student submissions
"""

EPILOG = """
examples:
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --start-page 3
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --reorder
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --blank-exam templates/blank.pdf
  uv run submission-splitter --input scans/all-submissions.pdf --output split/ --pages-per-submission 6 --reorder --auto-ollama
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
        "--pages-per-submission",
        type=int,
        required=True,
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
        help="Use OCR to detect page numbers and reorder pages within each split PDF.",
    )
    parser.add_argument(
        "--blank-exam",
        metavar="PDF",
        default=None,
        help=(
            "Path to a blank version of the exam PDF. "
            "Used to locate page numbers more accurately for reordering. "
            "Providing this implicitly enables --reorder."
        ),
    )
    parser.add_argument(
        "--ollama-model",
        default="qwen2.5vl:7b",
        help="Ollama vision model for reorder verification.",
    )
    parser.add_argument(
        "--ollama-host",
        default="http://127.0.0.1:11434",
        help="Ollama server URL for reorder verification.",
    )
    parser.add_argument(
        "--ollama-timeout",
        type=float,
        default=120.0,
        help="Ollama request timeout in seconds (default: 120).",
    )
    parser.add_argument(
        "--auto-ollama",
        action="store_true",
        help="Automatically start the Ollama server and pull the vision model if not available.",
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

    pages_per_submission = args.pages_per_submission

    ranges = plan_submission_ranges(
        total_pages=total_pages,
        pages_per_submission=pages_per_submission,
        start_page=args.start_page,
        end_page=args.end_page,
        filename_prefix=args.filename_prefix,
        zero_pad_width=args.zero_pad_width,
    )

    print(f"Input: {input_pdf} ({total_pages} pages)")
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
    
    ollama_server_proc = None
    ollama_log_file = None
    try:
        if args.auto_ollama:
            parsed_url = urlparse(args.ollama_host)
            host = parsed_url.hostname or "127.0.0.1"
            port = parsed_url.port or 11434

            # Check if something is already listening on the host/port
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
                        # Wait for server to bind
                        start_time = time.time()
                        while time.time() - start_time < 30:
                            time.sleep(1)
                            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
                                if s2.connect_ex((host, port)) == 0:
                                    break
                        else:
                            print("Warning: Timed out waiting for Ollama to start. Reordering may fall back to Tesseract.", file=sys.stderr)
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

            # Ensure model is present (pull is idempotent and cheap if already present)
            print(f"Ensuring model '{args.ollama_model}' is available...")
            subprocess.run(["ollama", "pull", args.ollama_model], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if args.reorder or args.blank_exam:
            print("Checking page order and reordering if necessary...")
            ollama_config = OllamaVisionConfig(
                model=args.ollama_model,
                host=args.ollama_host,
                timeout_seconds=args.ollama_timeout,
            )
            # Only verify model if we actually use Ollama as fallback
            try:
                ensure_model_available(ollama_config)
            except Exception as exc:
                print("\n" + "!" * 80)
                print("  OLLAMA VISION FALLBACK UNAVAILABLE")
                print(f"  Reason: {exc}")
                print("-" * 80)
                print("  Reordering will proceed using standard Tesseract OCR.")
                print("  Note: Tesseract may struggle with handwritten or stylized page numbers.")
                print("  For better accuracy, ensure Ollama is running and the model is pulled.")
                print("!" * 80 + "\n")
                ollama_config = None

            blank_exam_layout = None
            if args.blank_exam:
                blank_exam_path = Path(args.blank_exam)
                if not blank_exam_path.exists():
                    print(f"Error: Blank exam file not found at {blank_exam_path}", file=sys.stderr)
                    sys.exit(1)
                print(f"Loading page number layout from blank exam: {blank_exam_path}")
                blank_exam_layout = _load_page_number_layout(blank_exam_path)
                if blank_exam_layout is None:
                    print("Warning: Could not detect page number layout from blank exam. Falling back to generic detection.", file=sys.stderr)

            for pdf_path in created:
                if reorder_pdf_pages(pdf_path, ollama_config=ollama_config, blank_exam_page_number_layout=blank_exam_layout):
                    print(f"  - Reordered: {pdf_path.name}")
                else:
                    print(f"  - Order verified: {pdf_path.name}")
    finally:
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
