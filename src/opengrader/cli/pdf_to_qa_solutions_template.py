import fitz  # PyMuPDF
import pytesseract
import re
import io
import json
import shutil
import sys
import cv2
import numpy as np
from PIL import Image
from pathlib import Path
from argparse import ArgumentParser, RawDescriptionHelpFormatter


"""
Takes an answer-key PDF, extracts solution text, and writes the results to a Markdown file.


Note: extraction is imperfect — you’ll need to review and edit the generated Markdown to correct any errors. 
The edited Markdown is then used to automate grading.
"""

EPILOG = """
examples:
  uv run pdf-to-qa --input samples/answer-key.pdf --output out/template.md
  uv run pdf-to-qa --input key.pdf --output out/qa.md --title "Midterm 1 Solutions"
  uv run pdf-to-qa --input samples/exam2b-2-answers.pdf --output Solutions/final_exam_solutions.md --title "Final Exam Solutions"
  uv run pdf-to-qa --input samples/exam2b-2-answers.pdf --output solutions.md --title "Midterm 1"

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
        # Render page to image for OCR
        scale = 300 / 72.0
        mat = fitz.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)
        img = Image.open(io.BytesIO(pix.tobytes("png")))
        img_w, img_h = img.size

        # Focus OCR on the left 30% of the page to catch numbers even with wide margins
        left_img = img.crop((0, 0, int(img_w * 0.3), img_h))

        # Use PSM 6 (uniform block of text) for better vertical list recognition
        config = "--psm 6"
        data = pytesseract.image_to_data(
            left_img, output_type=pytesseract.Output.DICT, config=config
        )

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


def find_answer_box(img_pil):
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


def extract_content(doc, boxes, max_q):
    """
    Crops the PDF based on boxes and extracts text to find Points, Question, and Answer.
    Fills in missing questions between 1 and max_q.
    """
    markdown_questions = []

    for qnum in range(1, max_q + 1):
        if qnum not in boxes:
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

        full_text = ""
        inner_box_y_frac = None
        for pg_num, y_s, y_e in boxes[qnum]:
            page = doc[pg_num - 1]

            # Define crop area
            rect = fitz.Rect(
                0, y_s * page.rect.height, page.rect.width, y_e * page.rect.height
            )

            # High-res OCR extraction
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect)
            img = Image.open(io.BytesIO(pix.tobytes("png")))

            # Check for "Double Bounding Box" (answer box)
            if inner_box_y_frac is None:
                inner_box_y_frac = find_answer_box(img)

            full_text += pytesseract.image_to_string(img) + "\n"

        # Extract points: find "(10 points)"
        pts_match = re.search(r"\((\d+)\s*points?\)", full_text)
        points = pts_match.group(1) if pts_match else "10"

        # Split logic: Priority to 1. "Solution:" keyword, 2. Inner Box
        sol_match = re.search(r"(?i)solution\s*:?\s*", full_text)

        if sol_match:
            # Question stem is everything before "solution:"
            stem_text = full_text[: sol_match.start()].strip()
            question_stem = stem_text
            answer_body = full_text[sol_match.end() :].strip()
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

    doc = fitz.open(args.input)

    # print("TEST 1: Locating question coordinates...")
    q_starts, max_q = detect_question_positions(doc)
    if not q_starts:
        print(
            "Warning: No questions were detected. Check if the PDF layout matches the expected 'N. (Points)' format."
        )

    print(f"Found {len(q_starts)} questions (Max ID: {max_q})")

    # print("TEST 2: Defining bounding boxes...")
    boxes = compute_bounding_boxes(q_starts, max_q)

    # print("TEST 3: Extracting text via OCR...")
    qa_blocks = extract_content(doc, boxes, max_q)

    # Construct final Markdown
    header = f"---\ntitle: {args.title}\ninstructions: Auto-generated\n---\n\n## Version A\n\n"
    final_md = header + "\n\n".join(qa_blocks)

    with open(output_path, "w") as f:
        f.write(final_md)

    print(f"Success! Template saved to {output_path}")


if __name__ == "__main__":
    run_cli()
