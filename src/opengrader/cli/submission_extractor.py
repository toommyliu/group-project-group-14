from __future__ import annotations

import sys
import os
import tempfile
import subprocess
import time
import socket
from urllib.parse import urlparse
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.ocr import OcrConfig
from opengrader.core.ocr.ollama_vlm import (
    OllamaVisionConfig,
    ensure_model_available,
)
from opengrader.core.submissions import (
    _BlankAssignmentTemplate,
    _load_blank_assignment_template,
    _RosterEntry,
    _load_roster, # Import _load_roster
    build_student_submission_document_unified,
)

"""
Extract per-student .submission.md files from PDFs.

Without --blank-template: each --input PDF must have a real text layer. fitz
text extraction + regex pulls out the assignment title, student name/ID,
and question prompts/points. The command exits with an error if the PDF
has no text layer.

With --blank-template PATH.pdf: PATH.pdf is the blank version of the assignment
(same layout as the scans, no answers, real text layer required). Its
question prompts, points, answer-region bboxes, and name/ID field bboxes
are extracted once and applied to every scan in --input. The scans
themselves do not need a text layer; each scan page is rendered and run
through pytesseract to find the actual on-paper positions of question
prefixes (``N. (M points) ...``) and Name/ID labels, so per-question
response crops land on the real student-writing region even when the scan
sits at a slightly different vertical position than the blank template.


OPERATIONAL MODES:
1. Template-Based Alignment (The "Scan with Template" Mode)
Trigger: You provide --blank-template PATH.pdf.
Process: The script reads the "source of truth" (the blank assignment template) to know exactly where questions and Name/ID fields should be. 
         Since scanned student papers are often slightly shifted or skewed, it uses Tesseract to find the "anchor" text 
         (like "Name:" or "1. (10 points)") on the student's paper.
Result: It calculates a median vertical offset and "snaps" the crop boxes to the student's handwriting. It saves image crops but does not 
         transcribe text yet (that is left for a downstream OCR pass).

2. Digital Text Layer Extraction (The "Born-Digital" Mode)
Trigger: No blank exam is provided, and the PDF contains a selectable text layer (e.g., a PDF exported from Microsoft Word).
Process: It uses the fitz (PyMuPDF) library to pull raw text directly from the PDF. It uses Regex patterns (_QUESTION_INLINE_RE) to find 
         the question numbers and points.
Result: This is the fastest mode. It populates the .submission.md file with text immediately because the data is already digital.

3. Full OCR & Vision Extraction (The "Dark Scan" Mode)
Trigger: No blank exam provided, and the PDF has no text layer (it's just a raw image of a page).
Process: This is the most computationally expensive mode. It uses the core/ocr engine to detect the visual layout (lines, boxes, and grids). 
         If you have Ollama running with a vision model (like qwen2.5vl), it sends the crops to the AI to transcribe handwritten names, IDs, 
         and answers.
Result: A fully populated markdown file with AI-transcribed handwriting.


NOTES:
Per-question response crops are saved under ``responses/``, and Name/ID
field crops are saved under ``identifiers/`` inside each submission's
folder. The frontmatter holds ``name_image`` and ``id_image`` references
to those crops at the top, plus normalized bboxes for the Name/ID fields
under ``name_field`` / ``student_id_field``. No value-extraction OCR is
done here — populating ``name`` / ``student_id`` / per-question Response
text is the job of a downstream OCR pass.


Regardless of which strategy found the images, the unified engine then:
    - Sends the Name/ID crops to Ollama/Tesseract.
    - Fuzzy matches those results against your roster to get the "Real" Student ID.
    - Sends answer crops to Ollama for handwriting transcription.


Markdown Generation: Lastly it writes a single .submission.md file that is fully populated with canonical IDs, transcribed text, and image references.

- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
How the update flag works:
Skip PDF Rendering and Layout Detection: If --update is set, the engine assumes the .submission.md file and its associated image crops (in the identifiers/ 
and responses/ directories) already exist. It skips the rendering and "dark scan" layout detection phases entirely.


"""

EPILOG = """
examples:
  uv run submission-extractor --input split/ --output out/student-submissions/
  uv run submission-extractor --input split/submission-0001.pdf --output out/student-submissions/
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
    --blank-exam blank.pdf
"""

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


def run_cli(argv: list[str] | None = None) -> None:
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
            "Path to a blank version of the assignment template PDF (same layout as the "
            "scans, real text layer required). When given, its question "
            "prompts/points/bboxes and name/ID field bboxes populate every "
            "scan's .submission.md, and the scans no longer need a text "
            "layer themselves. This mode does not use OCR layout detection."
        ),
    )
    parser.add_argument(
        "--id-file",
        metavar="TSV",
        default=None,
        help=(
            "Tab-separated roster file (.tsv or .ids) with one 'ID<TAB>NAME' line per student. "
            "When given, extracted name/ID values are fuzzy-matched against the "
            "roster and replaced with canonical values."
        ),
    )
    # Arguments from submission_file.py
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
        default="qwen2.5vl:7b",
        help="Ollama vision model to use for text extraction.",
    )
    parser.add_argument(
        "--ollama-host",
        default="http://127.0.0.1:11434",
        help="Ollama server URL.",
    )
    parser.add_argument(
        "--auto-ollama",
        action="store_true",
        help="Automatically start the Ollama server and pull the vision model if not available.",
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
            "Ollama will still be used for student name/ID/title extraction."
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
    args = parser.parse_args(argv)

    pdfs = _collect_input_pdfs(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    blank_assignment_template: _BlankAssignmentTemplate | None = None
    if args.blank_template:
        try:
            blank_assignment_template = _load_blank_assignment_template(Path(args.blank_template))
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
    
    roster = None
    if args.id_file:
        try:
            roster = _load_roster(Path(args.id_file))
        except Exception as exc:
            print(f"error loading roster: {exc}", file=sys.stderr)
            sys.exit(1)

    # Auto-start Ollama if requested
    ollama_server_proc = None
    ollama_log_file = None
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
                        print("Warning: Timed out waiting for Ollama to start. Extraction may fail.", file=sys.stderr)
                        if ollama_log_file:
                            ollama_log_file.seek(0) # Go to beginning of file
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

    ocr_config = None
    ollama_config = None

    # Only prepare OCR/Ollama configs if we are NOT using a blank template
    # and thus might fall back to OCR.
    if not args.blank_template:
        ocr_config = OcrConfig(
            lang=args.lang,
            deskew=not args.no_deskew,
            confidence_threshold=args.confidence_threshold,
        )

        print(f"Using Ollama vision model: {args.ollama_model}")
        ollama_config = OllamaVisionConfig(
            model=args.ollama_model,
            host=args.ollama_host,
            timeout_seconds=args.ollama_timeout,
        )
        try:
            ensure_model_available(ollama_config)
        except Exception as exc:
            print(f"Warning: Ollama model not available ({exc}). Proceeding without VLM for text extraction.", file=sys.stderr)
            ollama_config = None # Disable Ollama if not available

    print(f"Found {len(pdfs)} submission PDF(s)")
    try:
        for index, pdf_path in enumerate(pdfs, start=1):
            # Determine if we can actually perform an update or if we need a full extraction
            expected_md = output_dir / pdf_path.stem / f"{pdf_path.stem}.submission.md"
            performing_update = args.update and expected_md.exists()

            if args.update and not performing_update:
                print(f"[{index}/{len(pdfs)}] {pdf_path.name}: .submission.md missing, performing full extraction.")

            try:
                action_label = "Updating" if performing_update else "Converting"
                if performing_update and args.refresh_layout:
                    action_label = "Updating (with Layout Refresh)"

                print(f"[{index}/{len(pdfs)}] {action_label}: {pdf_path.name}")
                doc, markdown_path = build_student_submission_document_unified(
                    pdf_path=pdf_path,
                    output_dir=output_dir,
                    ocr_config=ocr_config,
                    dpi=args.dpi,
                    split_subquestions=not args.no_split_subquestions,
                    ollama_config=ollama_config,
                    skip_ollama_qa=args.no_ollama_qa,
                    save_layout_debug=args.save_layout_debug,
                    blank_assignment_template=blank_assignment_template,
                    roster=roster,
                    update=performing_update,
                    refresh_layout=args.refresh_layout,
                    progress_callback=(lambda message: print(f"  {message}")),
                )

                # Verification Feedback
                identity = f"{doc.student_name} ({doc.student_id})" if doc.student_id else "Unidentified"
                print(f"  Identity: {identity}")
                print(f"  Wrote: {markdown_path}")

            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                sys.exit(1)
            print()
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
