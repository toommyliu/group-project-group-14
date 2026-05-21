import fitz  # PyMuPDF
import pytesseract
import re
import shutil
import sys
import tempfile
import subprocess
import time
import socket
from urllib.parse import urlparse
import cv2
import numpy as np
from PIL import Image
from pathlib import Path
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from dataclasses import dataclass, field, replace
from opengrader.core.pdf_utils import (
    QUESTION_PREFIX_RE,
    SCAN_QUESTION_LINE_RE,
    safe_image_to_data,
    SCAN_QUESTION_MAX_X_NORM,
    POINTS_SPEC,
    QuestionLayoutInfo,
    BlankExamTemplate,
    ScanQuestionPosition,
    calculate_page_offsets,
    crop_norm,
    render_page_to_image,
    norm_bbox,
)
from opengrader.core.ocr.ollama_vlm import (
    OllamaVisionConfig,
    ensure_model_available,
    extract_question_prompt_and_answer,
)


"""
Takes an answer-key PDF, extracts solution text, and writes the results to a Markdown file.

1. Layout Discovery & Alignment
The script first identifies where questions are located on the page. It has two strategies:
Text Layer Analysis: If no blank exam is provided, it scans the PDF text layer (via PyMuPDF) 
                    for markers like 1., 2., and (Points). It calculates bounding boxes by measuring the vertical 
                    distance between these markers.
Template Alignment: If a --blank-exam is provided, it uses the blank exam's text layer to define the "ideal" 
                    coordinates. It then renders the answer key scan and uses Tesseract to find the actual positions 
                    of the question numbers on the paper. It calculates a median offset to account for printer/scanner 
                    shifts, ensuring crops are perfectly aligned with the handwriting.


2. Advanced Computer Vision
Answer Box Detection: The script uses OpenCV (Canny edge detection and contour approximation) 
                    to look for rectangular boxes inside the question regions. If it finds one, 
                    it assumes that anything inside or after that box is the solution text.
VLM Integration (Ollama): This is the most powerful feature. When using a Vision Language Model 
                    (like qwen2.5-vl), the script doesn't just "see" text; it "understands" context. 
                    It can accurately separate the question prompt from the written solution, even if 
                    they are visually overlapping or the layout is complex. This significantly reduces 
                    the manual "cleanup" phase for the instructor.


3. Automatic Infrastructure
With the --auto-ollama flag, the script becomes self-sufficient. It will:

Check if the Ollama server is running.
If not, start the server in the background.
Automatically download (pull) the required vision model if it’s missing.
Gracefully shut down the server process once the extraction is complete.


4. Markdown Generation
Finally, it outputs a standardized .md file containing:

Frontmatter: Metadata like the assignment title.
Question Blocks: Cleaned question prompts and associated point values.
Solutions: The extracted "Correct" answers formatted as bullet points.
Rubric Templates: A default rubric (Correct, Partially Correct, Incorrect) based on the question's point value, ready for manual refinement in the TUI.


Note: Extraction is imperfect — you’ll need to review and edit the generated Markdown to correct any errors. The edited Markdown is then used to automate grading.
"""

EPILOG = """
examples:
  uv run pdf-to-qa-solutions --input samples/answer-key.pdf --output out/template.md
  uv run pdf-to-qa-solutions --input key.pdf --output out/qa.md --title "Midterm 1 Solutions"
  uv run pdf-to-qa-solutions --input samples/exam2b-2-answers.pdf --output Solutions/final_exam_solutions.md --title "Final Exam Solutions"
  uv run pdf-to-qa-solutions --input samples/exam2b-2-answers.pdf --output solutions.md --title "Midterm 1"
  uv run pdf-to-qa-solutions --input solution_scan.pdf --output qa.md --blank-exam master_blank.pdf

"""


def detect_question_positions(doc):
    """
    Scans the PDF to find the coordinates of "1.", "2.", etc.
    Returns: q_starts (dict), max_q (int)
    """
    all_detections = {}
    max_q = 0

    for pg_idx in range(len(doc)):
        page = doc[pg_idx]
        img, _, _ = render_page_to_image(doc, pg_idx, dpi=300)
        img_w, img_h = img.size

        # Focus OCR on the left 30% of the page to catch numbers even with wide margins
        left_img = img.crop((0, 0, int(img_w * 0.3), img_h))

        # Use PSM 6 (uniform block of text) for better vertical list recognition
        config = "--psm 6"
        data = safe_image_to_data(left_img, config=config)

        for i, text in enumerate(data["text"]):
            text = text.strip()
            matched_qnum = None

            if re.match(r"^\d{1,2}\.$", text) or re.match(r"^\d{1,2}$", text):
                potential_num = int(text.rstrip("."))

                # If it was just a number without a dot, check if the next token is a dot
                has_dot = "." in text or (
                    i + 1 < len(data["text"]) and data["text"][i + 1].strip() == "."
                )

                # Peek ahead for "(points)" to confirm format
                full_context = " ".join(data["text"][max(0, i - 1) : i + 6])
                if has_dot and re.search(r"\(?\d+\s*points?\)?", full_context):
                    matched_qnum = potential_num

            if matched_qnum:
                y_frac = data["top"][i] / img_h
                x_left = data["left"][i]
                if matched_qnum not in all_detections:
                    all_detections[matched_qnum] = []
                all_detections[matched_qnum].append((pg_idx + 1, y_frac, x_left))
                max_q = max(max_q, matched_qnum)

    # Find the vertical column where most numbers sit (the margin)
    single_xs = [dets[0][2] for dets in all_detections.values() if len(dets) == 1]
    median_x = sorted(single_xs)[len(single_xs) // 2] if single_xs else 0

    q_starts = {}
    for qnum, dets in all_detections.items():
        # Pick the detection closest to the standard left margin
        best = min(dets, key=lambda d: abs(d[2] - median_x))
        q_starts[qnum] = (best[0], best[1])  # (page, y_frac)

    return q_starts, max_q


def compute_bounding_boxes(q_starts, max_q):
    """
    Determines the area each question covers by looking at the start of the next question.
    """

    sorted_qs = sorted(q_starts.items())
    boxes = {}

    # Don't go to the very bottom of the page
    default_end = 0.95

    for idx, (qnum, (pg, y_start)) in enumerate(sorted_qs):
        # Start slightly above the number to catch the whole line
        crop_start = max(0, y_start - 0.005)

        # The end of this question is the start of the next, or the end of the page
        if idx + 1 < len(sorted_qs):
            next_qnum, (next_pg, next_y) = sorted_qs[idx + 1]
            if next_pg == pg:
                crop_end = next_y - 0.005
            else:
                crop_end = default_end
        else:
            crop_end = default_end

        boxes[qnum] = [(pg, round(crop_start, 4), round(crop_end, 4))]

    return boxes


def find_answer_box(img_pil: Image.Image):
    """
    Uses OpenCV to find a rectangular 'answer box' inside a question area.
    Returns the relative y-fraction where the box starts, or None.
    """
    # Convert PIL to OpenCV (BGR)
    cv_img = cv2.cvtColor(np.array(img_pil), cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edged = cv2.Canny(blurred, 50, 150)

    contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    height, width = gray.shape
    for c in contours:
        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.02 * peri, True)
        if len(approx) == 4:
            x, y, w, h = cv2.boundingRect(approx)
            # If it's a reasonably large box (at least 15% of width)
            if w > width * 0.15 and h > 20:
                return y / height
    return None


def _load_blank_exam_layout(blank_exam_doc: fitz.Document) -> BlankExamTemplate:
    """
    Parses a blank exam PDF to extract question numbers, prompts, points,
    and their normalized bounding boxes from the text layer.
    """
    questions: dict[int, QuestionLayoutInfo] = {}
    current_qnum: int | None = None
    current_points: float | None = None
    current_prompt_lines: list[str] = []
    # (page, y0, x0, page_w, page_h)
    current_start: tuple[int, float, float, float, float] | None = None

    for pg_idx in range(len(blank_exam_doc)):
        page = blank_exam_doc[pg_idx]
        page_w, page_h = page.rect.width, page.rect.height
        text_blocks = page.get_text("dict")["blocks"]

        for block in text_blocks:
            if block["type"] == 0:  # Text block
                for line_dict in block["lines"]:
                    line_text = "".join(span["text"] for span in line_dict["spans"]).strip()
                    x0, y0, x1, y1 = line_dict["bbox"]

                    match = QUESTION_PREFIX_RE.match(line_text)
                    if match:
                        if current_qnum is not None and current_start is not None:
                            # Finalize previous question
                            prev_pg, prev_y0, prev_x0, prev_pw, prev_ph = current_start
                            # If next question is on a new page, the current one ends at the page bottom.
                            y_end = y0 if prev_pg == pg_idx + 1 else prev_ph
                            questions[current_qnum] = QuestionLayoutInfo(
                                qnum=current_qnum,
                                page=prev_pg,
                                y_top_norm=prev_y0 / prev_ph,
                                y_bottom_norm=y_end / prev_ph,
                                x_left_norm=prev_x0 / prev_pw,
                                x_right_norm=1.0,
                                points=current_points or 0.0,
                                prompt=" ".join(current_prompt_lines).strip(),
                            )

                        current_qnum = int(match.group(1))
                        current_points = float(match.group(2))
                        current_prompt_lines = [match.group(3).strip()]
                        current_start = (pg_idx + 1, y0, x0, page_w, page_h)
                    elif current_qnum is not None:
                        current_prompt_lines.append(line_text)

    # Finalize the last question
    if current_qnum is not None and current_start is not None:
        prev_pg, prev_y0, prev_x0, prev_pw, prev_ph = current_start
        # Assume last question on page goes to the 95% mark as a fallback
        questions[current_qnum] = QuestionLayoutInfo(
            qnum=current_qnum,
            page=prev_pg,
            y_top_norm=prev_y0 / prev_ph,
            y_bottom_norm=0.95,
            x_left_norm=prev_x0 / prev_pw,
            x_right_norm=1.0,
            points=current_points or 0.0,
            prompt=" ".join(current_prompt_lines).strip(),
        )

    # Refine y_bottom_norm for all questions based on the next question's start
    sorted_qnums = sorted(questions.keys())
    for i, qnum in enumerate(sorted_qnums):
        if i + 1 < len(sorted_qnums):
            next_qnum = sorted_qnums[i + 1]
            current_q_info = questions[qnum]
            next_q_info = questions[next_qnum]
            if current_q_info.page == next_q_info.page:
                questions[qnum] = replace(current_q_info, y_bottom_norm=next_q_info.y_top_norm)

    return BlankExamTemplate(questions=questions)


def _detect_scan_question_alignments(
    rendered_pages: dict[int, Image.Image]
) -> dict[int, list[ScanQuestionPosition]]:
    """
    Detects question prefixes on rendered scan pages using pytesseract for alignment.
    """
    question_positions: dict[int, list[ScanQuestionPosition]] = {}

    for page_number, image in sorted(rendered_pages.items()):
        width, height = image.size
        data = safe_image_to_data(image, config="--psm 6")

        for i in range(len(data["text"])):
            text = str(data["text"][i]).strip()
            if not text:
                continue

            line_left = int(data["left"][i])
            line_top = int(data["top"][i])

            question_match = SCAN_QUESTION_LINE_RE.match(text)
            if question_match is not None and (line_left / width) < SCAN_QUESTION_MAX_X_NORM:
                qnum = int(question_match.group(1))
                question_positions.setdefault(qnum, []).append(
                    ScanQuestionPosition(qnum=qnum, page=page_number, y_top_norm=line_top / height)
                )
    return question_positions


def _calculate_page_offsets(
    blank_layout: dict[int, QuestionLayoutInfo],
    scan_alignments: dict[int, list[ScanQuestionPosition]]
) -> dict[int, float]:
    """
    Calculates the median vertical offset per page between blank exam layout
    and detected scan question positions.
    """
    deltas_by_page: dict[int, list[float]] = {}
    for qnum, blank_info in blank_layout.items():
        scan_positions = scan_alignments.get(qnum)
        if scan_positions:
            # Take the first detected position for this qnum on the page
            scan_pos = scan_positions[0] 
            if blank_info.page == scan_pos.page:
                deltas_by_page.setdefault(scan_pos.page, []).append(scan_pos.y_top_norm - blank_info.y_top_norm)

    return calculate_page_offsets(deltas_by_page)


def extract_content(
    doc: fitz.Document,
    rendered_pages: dict[int, Image.Image],
    question_bboxes: dict[int, tuple[int, tuple[float, float, float, float]]], # qnum -> (page, bbox_norm)
    max_q: int,
    ollama_config: OllamaVisionConfig | None = None,
):
    """
    Crops the PDF based on boxes and extracts text to find Points, Question, and Answer.
    Fills in missing questions between 1 and max_q.
    Uses rendered_pages and normalized bboxes for extraction.
    """
    markdown_questions = []

    for qnum in range(1, max_q + 1):
        if qnum not in question_bboxes:
            # Fill in missing question gap with placeholder sections
            block = (
                f"- (10 points) [Question {qnum} - Text not detected]\n"
                "  - **Correct**\n"
                "    - No answer detected.\n"
                "  - **Rubric**\n"
                "    - Correct (+0)\n"
                "    - Partially Correct (-5)\n"
                "    - Incorrect (-10)"
            )
            markdown_questions.append(block)
            continue

        pg_num, bbox_norm = question_bboxes[qnum]
        page_image = rendered_pages.get(pg_num)
        if page_image is None:
            print(f"Warning: Page {pg_num} not rendered for question {qnum}. Skipping.", file=sys.stderr)
            continue

        cropped_image = crop_norm(page_image, bbox_norm)
        if cropped_image is None:
            print(f"Warning: Could not crop image for question {qnum}. Skipping.", file=sys.stderr)
            continue

        full_text = ""
        inner_box_y_frac = None
        
        # 1. Primary OCR Scan (Tesseract)
        inner_box_y_frac = find_answer_box(cropped_image)
        tesseract_text = pytesseract.image_to_string(cropped_image).strip()
        full_text = tesseract_text

        # 2. Optional VLM Double Check
        vlm_answer = None
        if ollama_config:
            with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                cropped_image.save(tmp.name)
                try:
                    # VLM is much better at separating prompt from answer and cleaning noise
                    vlm_prompt, vlm_answer = extract_question_prompt_and_answer(
                        image_path=Path(tmp.name), 
                        config=ollama_config
                    )
                    if vlm_prompt:
                        full_text = vlm_prompt # Use VLM's cleaner prompt extraction
                except Exception as e:
                    print(f"  Warning: Ollama check failed for Q{qnum}: {e}", file=sys.stderr)

        pts_match = re.search(POINTS_SPEC, full_text, re.IGNORECASE)
        points = pts_match.group(1) if pts_match else "10"

        # Split logic: Priority to 1. "Solution:" keyword, 2. Inner Box
        sol_match = re.search(r"(?i)solution\s*:?\s*", full_text)

        if sol_match:
            # Question stem is everything before "solution:"
            stem_text = full_text[: sol_match.start()].strip()
            question_stem = stem_text
            answer_body = full_text[sol_match.end() :].strip()
        elif vlm_answer:
            question_stem = full_text
            answer_body = vlm_answer
        elif inner_box_y_frac:
            # If no solution keyword, use detected inner box
            lines = [l.strip() for l in full_text.split("\n") if l.strip()]
            split_idx = int(len(lines) * inner_box_y_frac)
            question_stem = " ".join(lines[:split_idx])
            answer_body = " ".join(lines[split_idx:])
        else:
            # Default fallback
            lines = [l.strip() for l in full_text.split("\n") if l.strip()]
            question_stem = " ".join(lines) if lines else f"Question {qnum}"
            answer_body = "No answer detected."

        # Clean up the stem: Remove redundant "N." and "(X points)"
        question_stem = re.sub(rf"^\s*{qnum}\.?\s*", "", question_stem)
        question_stem = re.sub(
            r"\(?\d+\s*points?\)?", "", question_stem, flags=re.IGNORECASE
        ).strip()
        question_stem = re.sub(r"\s+", " ", question_stem).strip()

        # Clean up answer: Remove prefixes if they were accidentally caught in the answer crop
        answer_body = re.sub(r"(?i)^solution\s*:?\s*", "", answer_body).strip()
        # Strip leading question numbers and points if they leaked into the answer
        answer_body = re.sub(rf"^\s*{qnum}\.?\s*", "", answer_body)
        answer_body = re.sub(
            r"^\s*\(?\d+\s*points?\)?\s*", "", answer_body, flags=re.IGNORECASE
        ).strip()
        answer_body = re.sub(r"\s+", " ", answer_body).strip()

        # Calculate half points for rubric
        try:
            pts_val = int(points)
        except ValueError:
            pts_val = 10
        half_pts = pts_val / 2
        if half_pts.is_integer():
            half_pts = int(half_pts)

        # Split multiple answers delimited by slashes into a bulleted list
        answer_parts = [a.strip() for a in re.split(r'[\\/]', answer_body)]
        answer_parts = [a for a in answer_parts if a]
        correct_section = "\n".join([f"    - {a}" for a in answer_parts]) or "    - No answer detected."

        # Format into the Opengrader Markdown structure
        block = (
            f"- ({points} points) {question_stem}\n"
            f"  - **Correct**\n"
            f"{correct_section}\n"
            f"  - **Rubric**\n"
            f"    - Correct (+0)\n"
            f"    - Partially Correct (-{half_pts})\n"
            f"    - Incorrect (-{points})"
        )
        markdown_questions.append(block)

    return markdown_questions


def setup_args() -> ArgumentParser:
    """
    Configures the argument parser for the PDF to QA template conversion utility.

    This tool is designed to take a single 'Answer Key' PDF and produce a
    structured Markdown file for use with the Opengrader TUI.
    """
    parser = ArgumentParser(
        description="Convert Answer Key PDF to Markdown Template via OCR extraction.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input", required=True, help="Path to the Answer Key PDF file."
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path where the generated Markdown file will be saved.",
    )
    parser.add_argument(
        "--title",
        default="CS146: Extracted Quiz",
        help="Title to be used in the Markdown header.",
    )
    parser.add_argument(
        "--blank-exam",
        metavar="PDF",
        default=None,
        help=(
            "Path to a blank version of the exam PDF (same layout as the "
            "answer key, real text layer required). Used for layout alignment."
        ),
    )
    parser.add_argument(
        "--ollama-model",
        default="qwen2.5vl:7b",
        help="Ollama vision model for extraction verification.",
    )
    parser.add_argument(
        "--ollama-host",
        default="http://127.0.0.1:11434",
        help="Ollama server URL.",
    )
    parser.add_argument(
        "--ollama-timeout",
        type=float,
        default=120.0,
        help="Ollama request timeout in seconds.",
    )
    parser.add_argument(
        "--auto-ollama",
        action="store_true",
        help="Automatically start the Ollama server and pull the model.",
    )
    return parser


def run_cli() -> None:
    parser = setup_args()
    args = parser.parse_args()

    if not shutil.which("tesseract"):
        print(
            "Error: Tesseract OCR binary not found. Please install it (e.g., 'brew install tesseract')."
        )
        sys.exit(1)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    answer_key_doc = fitz.open(args.input)
    rendered_answer_key_pages: dict[int, Image.Image] = {}
    final_question_bboxes: dict[int, tuple[int, tuple[float, float, float, float]]] = {}
    max_q = 0
    ollama_config = None
    ollama_server_proc = None
    ollama_log_file = None

    # Handle Ollama Auto-start
    if args.auto_ollama:
        parsed_url = urlparse(args.ollama_host)
        host = parsed_url.hostname or "127.0.0.1"
        port = parsed_url.port or 11434

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
                    start_time = time.time()
                    while time.time() - start_time < 30:
                        time.sleep(1)
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
                            if s2.connect_ex((host, port)) == 0:
                                break
                    else:
                        print("Warning: Timed out waiting for Ollama to start.", file=sys.stderr)
                except Exception as e:
                    print(f"Error starting Ollama: {e}", file=sys.stderr)

        print(f"Ensuring model '{args.ollama_model}' is available...")
        subprocess.run(
            ["ollama", "pull", args.ollama_model], 
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    # Prepare Config
    if args.auto_ollama or args.ollama_model != "qwen2.5vl:7b" or args.ollama_host != "http://127.0.0.1:11434":
        ollama_config = OllamaVisionConfig(
            model=args.ollama_model,
            host=args.ollama_host,
            timeout_seconds=args.ollama_timeout,
        )
        try:
            ensure_model_available(ollama_config)
        except Exception as exc:
            print(f"Warning: Ollama VLM unavailable ({exc}). Proceeding with Tesseract only.")
            ollama_config = None

    if args.blank_exam:
        blank_exam_path = Path(args.blank_exam)
        if not blank_exam_path.exists():
            print(f"Error: Blank exam file not found at {blank_exam_path}", file=sys.stderr)
            if ollama_server_proc:
                ollama_server_proc.terminate()
            if ollama_log_file:
                ollama_log_file.close()
            sys.exit(1)
        
        blank_exam_doc = fitz.open(blank_exam_path)
        print(f"Loading blank exam layout from {blank_exam_path}...")
        blank_layout = _load_blank_exam_layout(blank_exam_doc)
        blank_exam_doc.close()

        if not blank_layout.questions:
            print("Warning: No questions detected in blank exam. Falling back to text layer extraction.", file=sys.stderr)
            # Fallback to text layer extraction if blank exam is empty
            q_starts, max_q = detect_question_positions(answer_key_doc)
            boxes = compute_bounding_boxes(q_starts, max_q)
            for qnum, page_ranges in boxes.items():
                # Assuming single page for simplicity, take the first range
                pg, y_s, y_e = page_ranges[0] 
                # Need page dimensions to normalize
                page = answer_key_doc[pg-1]
                page_w, page_h = page.rect.width, page.rect.height
                final_question_bboxes[qnum] = (pg, norm_bbox(page_w, page_h, x0=0.0, y0=y_s*page_h, x1=page_w, y1=y_e*page_h))
            
            # Render all pages of the answer key for extract_content
            for pg_idx in range(len(answer_key_doc)):
                img, _, _ = render_page_to_image(answer_key_doc, pg_idx)
                rendered_answer_key_pages[pg_idx + 1] = img

        else:
            print(f"Rendering answer key PDF {args.input} for alignment...")
            for pg_idx in range(len(answer_key_doc)):
                img, _, _ = render_page_to_image(answer_key_doc, pg_idx)
                rendered_answer_key_pages[pg_idx + 1] = img
            
            print("Detecting question alignments on answer key scan...")
            scan_alignments = _detect_scan_question_alignments(rendered_answer_key_pages)
            
            print("Calculating page offsets...")
            page_offsets = _calculate_page_offsets(blank_layout.questions, scan_alignments)

            print("Applying offsets to blank exam layout...")
            for qnum, blank_info in blank_layout.questions.items():
                offset = page_offsets.get(blank_info.page, 0.0)
                adjusted_y_top = max(0.0, min(1.0, blank_info.y_top_norm + offset))
                adjusted_y_bottom = max(adjusted_y_top + 0.01, min(1.0, blank_info.y_bottom_norm + offset))
                final_question_bboxes[qnum] = (
                    blank_info.page,
                    (blank_info.x_left_norm, adjusted_y_top, blank_info.x_right_norm - blank_info.x_left_norm, adjusted_y_bottom - adjusted_y_top)
                )
            max_q = max(final_question_bboxes.keys()) if final_question_bboxes else 0

    else:
        print("Locating question coordinates from text layer...")
        q_starts, max_q = detect_question_positions(answer_key_doc)
        boxes = compute_bounding_boxes(q_starts, max_q)
        for qnum, page_ranges in boxes.items():
            pg, y_s, y_e = page_ranges[0] # Assuming single page for simplicity, take the first range
            page = answer_key_doc[pg-1]
            page_w, page_h = page.rect.width, page.rect.height
            final_question_bboxes[qnum] = (pg, norm_bbox(page_w, page_h, x0=0.0, y0=y_s*page_h, x1=page_w, y1=y_e*page_h))
        
        # Render all pages of the answer key for extract_content
        for pg_idx in range(len(answer_key_doc)):
            img, _, _ = render_page_to_image(answer_key_doc, pg_idx)
            rendered_answer_key_pages[pg_idx + 1] = img

    if not final_question_bboxes:
        print("Warning: No questions were detected. Check if the PDF layout matches the expected 'N. (Points)' format.", file=sys.stderr)
        if ollama_server_proc:
            ollama_server_proc.terminate()
        if ollama_log_file:
            ollama_log_file.close()
        sys.exit(1)

    print(f"Found {len(final_question_bboxes)} questions (Max ID: {max_q})")
    print("Extracting content via OCR...")
    qa_blocks = extract_content(answer_key_doc, rendered_answer_key_pages, final_question_bboxes, max_q, ollama_config=ollama_config)

    # Construct final Markdown
    header = f"---\ntitle: {args.title}\ninstructions: Auto-generated\n---\n\n## Version A\n\n"
    final_md = header + "\n\n".join(qa_blocks)

    output_path.write_text(final_md)

    if ollama_server_proc:
        ollama_server_proc.terminate()
    if ollama_log_file:
        ollama_log_file.close()

    answer_key_doc.close()
    print(f"Success! Template saved to {output_path}")


if __name__ == "__main__":
    run_cli()
