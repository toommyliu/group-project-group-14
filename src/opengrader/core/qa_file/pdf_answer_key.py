from __future__ import annotations

import re
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable

import cv2
import fitz
import numpy as np
import pytesseract
from PIL import Image

from opengrader.core.pdf_utils import (
    QUESTION_PREFIX_RE,
    SCAN_QUESTION_LINE_RE,
    safe_image_to_data,
    SCAN_QUESTION_MAX_X_NORM,
    POINTS_SPEC,
    QuestionLayoutInfo,
    BlankAssignmentTemplate,
    ScanQuestionPosition,
    calculate_page_offsets,
    crop_norm,
    render_page_to_image,
    norm_bbox,
)

from opengrader.core.qa_file.standard_rubric import (
    DEFAULT_QUESTION_POINTS,
    format_points,
    standard_rubric_lines,
)
from opengrader.core.qa_file.text2qti import (
    ParsedAssignment,
    parse_text2qti,
    render_question,
    summarize_points,
)

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.ocr.vision_reader import VisionReader


# A question number as OCR hands it over, alone in the left margin: "3.",
# "3)", "Q3." — or a bare "3" whose mark landed in the next token. "3a)" and
# "3(a)" start question 3 too, when it is written only as its parts.
_MARGIN_NUMBER_RE = re.compile(
    r"^(?:Q\s*)?(\d{1,2})(\(?a\)?[.)]?|[.)])?$", re.IGNORECASE
)
_MARGIN_POINTS_RE = re.compile(r"\(?\d+\s*(?:points?|pts)\)?", re.IGNORECASE)
# How far (as a share of the page width) a number may sit from the margin the
# question numbers share and still be one of them.
_MARGIN_TOLERANCE = 0.03
# A question's own line on a blank template that prints no points beside it.
_LOOSE_QUESTION_LINE_RE = re.compile(r"^\s*(?:Q\s*)?(\d{1,2})\s*[.)]\s+(.*)$", re.IGNORECASE)


def _margin_numbers(doc) -> list[tuple[int, int, float, float, bool]]:
    """Every number in a left margin that could start a question:
    (number, page, y, x, has points beside it), y and x as page fractions."""
    found: list[tuple[int, int, float, float, bool]] = []
    for pg_idx in range(len(doc)):
        img, _, _ = render_page_to_image(doc, pg_idx, dpi=300)
        img_w, img_h = img.size

        left_img = img.crop((0, 0, int(img_w * 0.3), img_h))

        # PSM 6 treats the vertical question-number list as one text block.
        data = safe_image_to_data(left_img, config="--psm 6")
        tokens = [str(text).strip() for text in data["text"]]

        for i, text in enumerate(tokens):
            match = _MARGIN_NUMBER_RE.match(text)
            if match is None:
                continue
            marked = bool(match.group(2)) or (
                i + 1 < len(tokens) and tokens[i + 1] in {".", ")"}
            )
            if not marked:
                continue
            context = " ".join(tokens[max(0, i - 1) : i + 6])
            found.append(
                (
                    int(match.group(1)),
                    pg_idx + 1,
                    data["top"][i] / img_h,
                    data["left"][i] / img_w,
                    bool(_MARGIN_POINTS_RE.search(context)),
                )
            )
    return found


def _question_margin(candidates: list[tuple[int, int, float, float, bool]]) -> float:
    """Where the question numbers line up: among numbers with points beside
    them when there are any, else wherever the most numbers share a margin."""
    labelled = sorted(x for _q, _pg, _y, x, strong in candidates if strong)
    if labelled:
        return labelled[len(labelled) // 2]
    xs = sorted(x for _q, _pg, _y, x, _strong in candidates)
    best = max(xs, key=lambda x: (sum(1 for other in xs if abs(other - x) <= _MARGIN_TOLERANCE), -x))
    return best


def _numbers_in_order(
    candidates: list[tuple[int, int, float, float, bool]],
) -> list[tuple[int, int, float, float, bool]]:
    """The run of numbers that reads 1, 2, 3 … down the pages.

    A number with no points beside it is only trusted as part of that run: a
    numbered list inside an answer, or a stray "7." in a margin, does not
    count upwards from the last question in step with the page. A number
    that does have points beside it weighs more, and a skipped number (one
    OCR missed) costs a little, so the run is not broken by it.
    """
    ordered = sorted(candidates, key=lambda c: (c[1], c[2]))
    best_score: list[float] = []
    previous: list[int | None] = []
    for index, (qnum, _pg, _y, _x, strong) in enumerate(ordered):
        own = 1.5 if strong else 1.0
        score, link = own - 0.6 * (qnum - 1), None
        for earlier in range(index):
            gap = qnum - ordered[earlier][0]
            if gap < 1:
                continue
            candidate = best_score[earlier] + own - 0.6 * (gap - 1)
            if candidate > score:
                score, link = candidate, earlier
        best_score.append(score)
        previous.append(link)

    if not ordered:
        return []
    at: int | None = max(range(len(ordered)), key=lambda i: best_score[i])
    run: list[tuple[int, int, float, float, bool]] = []
    while at is not None:
        run.append(ordered[at])
        at = previous[at]
    return run[::-1]


def detect_question_positions(doc):
    """Where each question starts on a scanned answer key: {number: (page, y)}.

    A question is a number in the left margin — "3.", "3)" — with its points
    beside it or not; see `_numbers_in_order` for how one without is told
    from any other number on the page.
    """
    candidates = _margin_numbers(doc)
    if not candidates:
        return {}, 0
    margin = _question_margin(candidates)
    aligned = [
        candidate
        for candidate in candidates
        if candidate[4] or abs(candidate[3] - margin) <= _MARGIN_TOLERANCE
    ]
    run = _numbers_in_order(aligned)
    q_starts = {qnum: (page, y) for qnum, page, y, _x, _strong in run}
    return q_starts, max(q_starts, default=0)


def compute_bounding_boxes(q_starts, max_q):
    sorted_qs = sorted(q_starts.items())
    boxes = {}

    default_end = 0.95

    for idx, (qnum, (pg, y_start)) in enumerate(sorted_qs):
        crop_start = max(0, y_start - 0.005)

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
    """The returned y-fraction is relative to the question area, not the page."""
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
            if w > width * 0.15 and h > 20:
                return y / height
    return None


def _template_question_line(
    line_text: str, last_qnum: int | None
) -> tuple[int, float | None, str] | None:
    """(number, points, rest of the line) when a template line starts a question.

    "3. (10 points) …" always does. A line with no points beside the number
    — "3) …", or with the points at its end — does only as the next number
    in order, so a numbered list in a question's text is not taken for one.
    """
    match = QUESTION_PREFIX_RE.match(line_text)
    if match:
        return int(match.group(1)), float(match.group(2)), match.group(3).strip()
    loose = _LOOSE_QUESTION_LINE_RE.match(line_text)
    if loose is None or int(loose.group(1)) != (last_qnum or 0) + 1:
        return None
    rest = loose.group(2)
    points = re.search(POINTS_SPEC, rest, re.IGNORECASE)
    if points is None:
        return int(loose.group(1)), None, rest.strip()
    rest = f"{rest[: points.start()]} {rest[points.end():]}"
    return int(loose.group(1)), float(points.group(1)), " ".join(rest.split())


def pdf_text_lines(pdf_path: str | Path) -> str:
    """The text a PDF carries itself, one printed line per line; empty for a
    scan. An answer written after its question on the same line
    ("… ? Solution: 4") is moved to a line of its own, which is where the
    text reader looks for it."""
    lines: list[str] = []
    with fitz.open(str(pdf_path)) as doc:
        for page in doc:
            try:
                text = page.get_text("text", sort=True) or ""
            except Exception:  # noqa: BLE001 - an unreadable page is a scan to us
                continue
            lines.append(_as_reader_lines(text))
    return "\n".join(line for line in lines if line)


def _as_reader_lines(text: str) -> str:
    """Text off a page, shaped for the text reader.

    An answer after its question on the same line gets a line of its own.
    Blank lines are dropped: on a page they are spacing, not the end of an
    answer, so an answer runs on to the next question, part or answer.
    """
    lines: list[str] = []
    for line in text.splitlines():
        lines.extend(
            piece
            for piece in _INLINE_ANSWER_RE.sub("\n", line.rstrip()).split("\n")
            if piece.strip()
        )
    return "\n".join(lines)


def _block_from_reader(
    qnum: int,
    region_text: str,
    *,
    default_points: float,
    template_points: float | None,
) -> tuple[str, bool] | None:
    """One question's block when its text reads by the text reader's rules
    — "Answer:" lines, parts "3a)", points anywhere on its line — and
    whether it was given the default points. None when it does not: no
    answer the reader recognises, or the number it reads is not `qnum`."""
    parsed = parse_text2qti(_as_reader_lines(region_text))
    if len(parsed.questions) != 1:
        return None
    question = parsed.questions[0]
    if question.number != qnum or not (question.parts or question.has_answer_block()):
        return None
    if not question.points_inline and template_points:
        # The blank template printed them; as good as on the question's line.
        question.points, question.points_inline = template_points, True
    defaulted = summarize_points(ParsedAssignment("", "", [question])).defaulted > 0
    return "\n".join(render_question(question, default_points=default_points)), defaulted


_INLINE_ANSWER_RE = re.compile(r"(?<=\S)\s+(?=(?:Solution|Answers?)\s*:)", re.IGNORECASE)


def _load_blank_template_layout(blank_template_doc: fitz.Document) -> BlankAssignmentTemplate:
    questions: dict[int, QuestionLayoutInfo] = {}
    current_qnum: int | None = None
    current_points: float | None = None
    current_prompt_lines: list[str] = []
    # (page, y0, x0, page_w, page_h).
    current_start: tuple[int, float, float, float, float] | None = None

    for pg_idx in range(len(blank_template_doc)):
        page = blank_template_doc[pg_idx]
        page_w, page_h = page.rect.width, page.rect.height
        text_blocks = page.get_text("dict")["blocks"]

        for block in text_blocks:
            if block["type"] == 0:
                for line_dict in block["lines"]:
                    line_text = "".join(span["text"] for span in line_dict["spans"]).strip()
                    x0, y0, x1, y1 = line_dict["bbox"]

                    match = _template_question_line(line_text, current_qnum)
                    if match:
                        if current_qnum is not None and current_start is not None:
                            prev_pg, prev_y0, prev_x0, prev_pw, prev_ph = current_start
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

                        current_qnum, current_points, first_line = match
                        current_prompt_lines = [first_line]
                        current_start = (pg_idx + 1, y0, x0, page_w, page_h)
                    elif current_qnum is not None:
                        current_prompt_lines.append(line_text)

    if current_qnum is not None and current_start is not None:
        prev_pg, prev_y0, prev_x0, prev_pw, prev_ph = current_start
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

    sorted_qnums = sorted(questions.keys())
    for i, qnum in enumerate(sorted_qnums):
        if i + 1 < len(sorted_qnums):
            next_qnum = sorted_qnums[i + 1]
            current_q_info = questions[qnum]
            next_q_info = questions[next_qnum]
            if current_q_info.page == next_q_info.page:
                questions[qnum] = replace(current_q_info, y_bottom_norm=next_q_info.y_top_norm)

    return BlankAssignmentTemplate(questions=questions)


def _detect_scan_question_alignments(
    rendered_pages: dict[int, Image.Image]
) -> dict[int, list[ScanQuestionPosition]]:
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
    deltas_by_page: dict[int, list[float]] = {}
    for qnum, blank_info in blank_layout.items():
        scan_positions = scan_alignments.get(qnum)
        if scan_positions:
            scan_pos = scan_positions[0] 
            if blank_info.page == scan_pos.page:
                deltas_by_page.setdefault(scan_pos.page, []).append(scan_pos.y_top_norm - blank_info.y_top_norm)

    return calculate_page_offsets(deltas_by_page)


_TEXT_LAYER_MIN_CHARS = 10

_OCR_CONFIDENCE_THRESHOLD = 60.0
_OCR_MIN_CHARS = 3


def _ocr_mean_confidence(image: Image.Image) -> float:
    data = safe_image_to_data(image)
    confidences: list[float] = []
    for conf, text in zip(data.get("conf", []), data.get("text", [])):
        if not str(text).strip():
            continue
        try:
            value = float(conf)
        except (TypeError, ValueError):
            continue
        if value >= 0:
            confidences.append(value)
    return sum(confidences) / len(confidences) if confidences else 0.0


def _ocr_looks_unreliable(text: str, mean_confidence: float) -> bool:
    if len(text.strip()) < _OCR_MIN_CHARS:
        return True
    return mean_confidence < _OCR_CONFIDENCE_THRESHOLD


def _text_from_region(
    doc: fitz.Document,
    page_num: int,
    bbox_norm: tuple[float, float, float, float],
) -> str:
    try:
        page = doc[page_num - 1]
    except (IndexError, KeyError):
        return ""
    x, y, w, h = bbox_norm
    page_w, page_h = page.rect.width, page.rect.height
    rect = fitz.Rect(x * page_w, y * page_h, (x + w) * page_w, (y + h) * page_h)
    try:
        text = page.get_text("text", clip=rect) or ""
    except Exception:
        return ""
    return text.strip()


def extract_content(
    doc: fitz.Document,
    rendered_pages: dict[int, Image.Image],
    question_bboxes: dict[int, tuple[int, tuple[float, float, float, float]]],
    max_q: int,
    vision_reader: VisionReader | None = None,
    progress: Callable[[int, int], None] | None = None,
    *,
    default_points: float = DEFAULT_QUESTION_POINTS,
    template_points: dict[int, float] | None = None,
    defaulted: list[int] | None = None,
):
    """One answer-key block per question.

    A question's points are the ones read beside it on the answer key, else
    the ones printed on the blank template (`template_points`), else
    `default_points` — and its number is then appended to `defaulted`.
    """
    markdown_questions = []
    template_points = template_points or {}

    def fallback_points(qnum: int) -> float:
        known = template_points.get(qnum)
        if known:
            return known
        if defaulted is not None:
            defaulted.append(qnum)
        return default_points

    for qnum in range(1, max_q + 1):
        if progress is not None:
            progress(qnum, max_q)
        if qnum not in question_bboxes:
            pts_val = fallback_points(qnum)
            block = "\n".join(
                [
                    f"- ({format_points(pts_val)} points) [Question {qnum} - Text not detected]",
                    "  - **Correct**",
                    "    - No answer detected.",
                    "  - **Rubric**",
                    *standard_rubric_lines(pts_val),
                ]
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

        inner_box_y_frac = None
        vlm_answer = None

        text_layer = _text_from_region(doc, pg_num, bbox_norm)
        if len(text_layer) >= _TEXT_LAYER_MIN_CHARS:
            full_text = text_layer
        else:
            inner_box_y_frac = find_answer_box(cropped_image)
            full_text = pytesseract.image_to_string(cropped_image).strip()

            if vision_reader and _ocr_looks_unreliable(
                full_text, _ocr_mean_confidence(cropped_image)
            ):
                with tempfile.NamedTemporaryFile(suffix=".png") as tmp:
                    cropped_image.save(tmp.name)
                    try:
                        vlm_prompt, vlm_answer = vision_reader.extract_question_prompt_and_answer(
                            image_path=Path(tmp.name)
                        )
                        if vlm_prompt:
                            full_text = vlm_prompt
                    except Exception as e:
                        print(f"  Warning: Ollama check failed for Q{qnum}: {e}", file=sys.stderr)

        if not vlm_answer:
            # Text that reads by the text reader's rules is taken by them, so
            # a scan and a typed file are understood the same way.
            read = _block_from_reader(
                qnum,
                full_text,
                default_points=default_points,
                template_points=template_points.get(qnum),
            )
            if read is not None:
                block, was_defaulted = read
                if was_defaulted and defaulted is not None:
                    defaulted.append(qnum)
                markdown_questions.append(block)
                continue

        pts_match = re.search(POINTS_SPEC, full_text, re.IGNORECASE)
        pts_val = float(pts_match.group(1)) if pts_match else fallback_points(qnum)
        points = format_points(pts_val)

        sol_match = re.search(r"(?i)solution\s*:?\s*", full_text)

        if sol_match:
            stem_text = full_text[: sol_match.start()].strip()
            question_stem = stem_text
            answer_body = full_text[sol_match.end() :].strip()
        elif vlm_answer:
            question_stem = full_text
            answer_body = vlm_answer
        elif inner_box_y_frac:
            lines = [l.strip() for l in full_text.split("\n") if l.strip()]
            split_idx = int(len(lines) * inner_box_y_frac)
            question_stem = " ".join(lines[:split_idx])
            answer_body = " ".join(lines[split_idx:])
        else:
            lines = [l.strip() for l in full_text.split("\n") if l.strip()]
            question_stem = " ".join(lines) if lines else f"Question {qnum}"
            answer_body = "No answer detected."

        question_stem = re.sub(rf"^\s*(?:Q\s*)?{qnum}\s*[.)]?\s*", "", question_stem)
        question_stem = re.sub(
            r"\(?\d+\s*points?\)?", "", question_stem, flags=re.IGNORECASE
        ).strip()
        question_stem = re.sub(r"\s+", " ", question_stem).strip()

        answer_body = re.sub(r"(?i)^solution\s*:?\s*", "", answer_body).strip()
        answer_body = re.sub(rf"^\s*{qnum}\s*[.)]?\s*", "", answer_body)
        answer_body = re.sub(
            r"^\s*\(?\d+\s*points?\)?\s*", "", answer_body, flags=re.IGNORECASE
        ).strip()
        answer_body = re.sub(r"\s+", " ", answer_body).strip()

        answer_parts = [a.strip() for a in re.split(r'[\\/]', answer_body)]
        answer_parts = [a for a in answer_parts if a]
        correct_section = "\n".join([f"    - {a}" for a in answer_parts]) or "    - No answer detected."

        block = (
            f"- ({points} points) {question_stem}\n"
            f"  - **Correct**\n"
            f"{correct_section}\n"
            f"  - **Rubric**\n"
        ) + "\n".join(standard_rubric_lines(pts_val))
        markdown_questions.append(block)

    return markdown_questions


def extract_qa_markdown_from_pdf(
    pdf_path: str | Path,
    *,
    title: str,
    instructions: str = "Auto-generated",
    blank_template_path: str | Path | None = None,
    vision_reader: VisionReader | None = None,
    emit: Callable[[str], None] | None = None,
    progress: Callable[[int, int], None] | None = None,
    default_points: float = DEFAULT_QUESTION_POINTS,
    on_default_points: Callable[[int, int], None] | None = None,
) -> str:
    """`on_default_points(given, total)` is called once, with how many of
    the questions had no points to read and were given `default_points`."""
    say = emit or (lambda _message: None)
    template_points: dict[int, float] = {}
    defaulted: list[int] = []

    answer_key_doc = fitz.open(str(pdf_path))
    try:
        rendered_answer_key_pages: dict[int, Image.Image] = {}
        final_question_bboxes: dict[int, tuple[int, tuple[float, float, float, float]]] = {}
        max_q = 0

        if blank_template_path:
            blank_template_path = Path(blank_template_path)
            if not blank_template_path.exists():
                raise FileNotFoundError(f"blank template not found: {blank_template_path}")

            blank_template_doc = fitz.open(str(blank_template_path))
            say(f"Loading blank template layout from {blank_template_path}...")
            blank_layout = _load_blank_template_layout(blank_template_doc)
            blank_template_doc.close()
            template_points = {
                qnum: info.points for qnum, info in blank_layout.questions.items()
            }

            if not blank_layout.questions:
                say("Warning: No questions detected in blank template. Falling back to text layer extraction.")
                q_starts, max_q = detect_question_positions(answer_key_doc)
                boxes = compute_bounding_boxes(q_starts, max_q)
                for qnum, page_ranges in boxes.items():
                    pg, y_s, y_e = page_ranges[0]
                    page = answer_key_doc[pg - 1]
                    page_w, page_h = page.rect.width, page.rect.height
                    final_question_bboxes[qnum] = (pg, norm_bbox(page_w, page_h, x0=0.0, y0=y_s * page_h, x1=page_w, y1=y_e * page_h))
                for pg_idx in range(len(answer_key_doc)):
                    img, _, _ = render_page_to_image(answer_key_doc, pg_idx)
                    rendered_answer_key_pages[pg_idx + 1] = img
            else:
                say("Rendering answer key PDF for alignment...")
                for pg_idx in range(len(answer_key_doc)):
                    img, _, _ = render_page_to_image(answer_key_doc, pg_idx)
                    rendered_answer_key_pages[pg_idx + 1] = img

                say("Detecting question alignments on answer key scan...")
                scan_alignments = _detect_scan_question_alignments(rendered_answer_key_pages)

                say("Calculating page offsets...")
                page_offsets = _calculate_page_offsets(blank_layout.questions, scan_alignments)

                say("Applying offsets to blank assignment layout...")
                for qnum, blank_info in blank_layout.questions.items():
                    offset = page_offsets.get(blank_info.page, 0.0)
                    adjusted_y_top = max(0.0, min(1.0, blank_info.y_top_norm + offset))
                    adjusted_y_bottom = max(adjusted_y_top + 0.01, min(1.0, blank_info.y_bottom_norm + offset))
                    final_question_bboxes[qnum] = (
                        blank_info.page,
                        (blank_info.x_left_norm, adjusted_y_top, blank_info.x_right_norm - blank_info.x_left_norm, adjusted_y_bottom - adjusted_y_top),
                    )
                max_q = max(final_question_bboxes.keys()) if final_question_bboxes else 0
        else:
            say("Locating question coordinates from text layer...")
            q_starts, max_q = detect_question_positions(answer_key_doc)
            boxes = compute_bounding_boxes(q_starts, max_q)
            for qnum, page_ranges in boxes.items():
                pg, y_s, y_e = page_ranges[0]
                page = answer_key_doc[pg - 1]
                page_w, page_h = page.rect.width, page.rect.height
                final_question_bboxes[qnum] = (pg, norm_bbox(page_w, page_h, x0=0.0, y0=y_s * page_h, x1=page_w, y1=y_e * page_h))
            for pg_idx in range(len(answer_key_doc)):
                img, _, _ = render_page_to_image(answer_key_doc, pg_idx)
                rendered_answer_key_pages[pg_idx + 1] = img

        if not final_question_bboxes:
            raise ValueError(
                "No questions were detected. Number each question in the left "
                "margin — '1.' or '1)' — in order from 1."
            )

        say(f"Found {len(final_question_bboxes)} questions (Max ID: {max_q})")
        say("Extracting content via OCR...")
        qa_blocks = extract_content(
            answer_key_doc, rendered_answer_key_pages, final_question_bboxes, max_q,
            vision_reader=vision_reader,
            progress=progress,
            default_points=default_points,
            template_points=template_points,
            defaulted=defaulted,
        )
        if on_default_points is not None:
            on_default_points(len(defaulted), len(qa_blocks))

        header = f"---\ntitle: {title}\ninstructions: {instructions}\n---\n\n"
        return header + "\n\n".join(qa_blocks) + "\n"
    finally:
        answer_key_doc.close()
