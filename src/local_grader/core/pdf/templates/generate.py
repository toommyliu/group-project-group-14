from pathlib import Path
import fitz

from .draw import create_page_with_anchors, build_info_section, draw_questions_on_page
from .spec import build_layout_spec, VERSION_ANCHOR_MAP
from .constants import LETTER_W, LETTER_H, DEFAULT_FONT
from .anchor import ANCHOR_LOCATIONS

def generate_template_pdf(
    output_path: Path,
    version: str,
    questions: list[tuple[str, int, str]],
    title: str,
    all_versions: list[str] | None = None,
    page_w: float = LETTER_W,
    page_h: float = LETTER_H,
    fontname: str = DEFAULT_FONT,
) -> dict:
    filled = set(VERSION_ANCHOR_MAP.get(version, ANCHOR_LOCATIONS))
    doc = fitz.Document()
    page = create_page_with_anchors(doc, page_w, page_h, filled_anchors=filled)
    start_y, field_rects = build_info_section(page, "", "", page_w, title, fontname=fontname)

    remaining = questions[:]
    page_number = 1
    question_meta: list[tuple[str, tuple[float, float, float, float], int, int, str]] = []

    while remaining:
        if page_number > 1:
            page = create_page_with_anchors(doc, page_w, page_h, filled_anchors=filled)
            start_y = 24 + 18 + 16

        drawn_count, drawn_meta = draw_questions_on_page(
            page,
            page_number,
            remaining,
            page_w,
            page_h,
            start_y,
            fontname=fontname,
        )
        question_meta.extend(drawn_meta)
        remaining = remaining[drawn_count:]
        page_number += 1

    doc.save(str(output_path), garbage=4, deflate=True)
    doc.close()

    return build_layout_spec(
        unit_id="cmpe195-exam1-sp26",
        versions=all_versions or [version],
        page_w=page_w,
        page_h=page_h,
        field_rects=field_rects,
        question_meta=question_meta,
    )

def generate_submission_pdf(
    output_path: Path,
    version: str,
    name: str,
    student_id: str,
    questions: list[tuple[str, int, str]],
    title: str,
    page_w: float = LETTER_W,
    page_h: float = LETTER_H,
    fontname: str = DEFAULT_FONT,
) -> None:
    filled = set(VERSION_ANCHOR_MAP.get(version, ANCHOR_LOCATIONS))
    doc = fitz.Document()
    page = create_page_with_anchors(doc, page_w, page_h, filled_anchors=filled)
    start_y, _ = build_info_section(page, name, student_id, page_w, title, fontname=fontname)

    remaining = questions[:]
    page_number = 1
    while remaining:
        if page_number > 1:
            page = create_page_with_anchors(doc, page_w, page_h, filled_anchors=filled)
            start_y = 24 + 18 + 16
        drawn_count, _ = draw_questions_on_page(
            page,
            page_number,
            remaining,
            page_w,
            page_h,
            start_y,
            fontname=fontname,
        )
        remaining = remaining[drawn_count:]
        page_number += 1

    doc.save(str(output_path), garbage=4, deflate=True)
    doc.close()
