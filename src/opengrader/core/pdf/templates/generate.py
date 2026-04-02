import sys
from pathlib import Path

import fitz

from .anchor import ANCHOR_LOCATIONS, Anchor
from .constants import LETTER_H, LETTER_W
from .draw import (
    build_info_section,
    create_page_with_anchors,
    draw_continued_page_header,
    draw_questions_on_page,
)
from .fonts import register_page_fonts, resolve_template_fonts
from .spec import VERSION_ANCHOR_MAP, build_layout_spec


def _filled_anchors_for_version(version: str) -> set[Anchor]:
    configured = VERSION_ANCHOR_MAP.get(version)
    if configured is not None:
        return set(configured)
    return {Anchor(anchor_id) for anchor_id in ANCHOR_LOCATIONS}


def _resolve_continued_page_header(title: str, override: str | None) -> str:
    if override is not None and override.strip():
        return override.strip()
    lines = [line.strip() for line in title.split("\n") if line.strip()]
    if lines:
        return lines[0]
    return "Quiz"


def generate_template_pdf(
    output_path: Path,
    version: str,
    questions: list[tuple[str, int, str]],
    title: str,
    instructions: str | None = None,
    continued_page_header: str | None = None,
    all_versions: list[str] | None = None,
    page_w: float = LETTER_W,
    page_h: float = LETTER_H,
    font_regular_file: str | Path | None = None,
    font_bold_file: str | Path | None = None,
    start_questions_on_first_page: bool = True,
) -> dict:
    fonts = resolve_template_fonts(
        regular_file=font_regular_file, bold_file=font_bold_file
    )
    if fonts.warning:
        print(fonts.warning, file=sys.stderr)

    filled = _filled_anchors_for_version(version)
    continued_header_text = _resolve_continued_page_header(title, continued_page_header)
    doc = fitz.Document()
    page = create_page_with_anchors(doc, page_w, page_h, filled_anchors=filled)
    register_page_fonts(page, fonts)
    start_y, field_rects = build_info_section(
        page,
        "",
        "",
        page_w,
        title,
        instructions,
        fonts,
    )

    remaining = questions[:]
    page_number = 1
    current_page = page
    current_start_y = start_y

    if not start_questions_on_first_page and remaining:
        page_number = 2
        current_page = create_page_with_anchors(
            doc, page_w, page_h, filled_anchors=filled
        )
        register_page_fonts(current_page, fonts)
        current_start_y = draw_continued_page_header(
            page=current_page,
            page_w=page_w,
            header_text=continued_header_text,
            fonts=fonts,
        )

    question_meta: list[
        tuple[str, tuple[float, float, float, float], int, int, str]
    ] = []

    while remaining:
        drawn_count, drawn_meta = draw_questions_on_page(
            current_page,
            page_number,
            remaining,
            page_w,
            page_h,
            current_start_y,
            fonts,
        )
        question_meta.extend(drawn_meta)
        remaining = remaining[drawn_count:]
        if not remaining:
            break

        page_number += 1
        current_page = create_page_with_anchors(
            doc, page_w, page_h, filled_anchors=filled
        )
        register_page_fonts(current_page, fonts)
        current_start_y = draw_continued_page_header(
            page=current_page,
            page_w=page_w,
            header_text=continued_header_text,
            fonts=fonts,
        )

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
    instructions: str | None = None,
    continued_page_header: str | None = None,
    page_w: float = LETTER_W,
    page_h: float = LETTER_H,
    font_regular_file: str | Path | None = None,
    font_bold_file: str | Path | None = None,
    start_questions_on_first_page: bool = True,
) -> None:
    fonts = resolve_template_fonts(
        regular_file=font_regular_file, bold_file=font_bold_file
    )
    if fonts.warning:
        print(fonts.warning, file=sys.stderr)

    filled = _filled_anchors_for_version(version)
    continued_header_text = _resolve_continued_page_header(title, continued_page_header)
    doc = fitz.Document()
    page = create_page_with_anchors(doc, page_w, page_h, filled_anchors=filled)
    register_page_fonts(page, fonts)
    start_y, _ = build_info_section(
        page,
        name,
        student_id,
        page_w,
        title,
        instructions,
        fonts,
    )

    remaining = questions[:]
    page_number = 1
    current_page = page
    current_start_y = start_y

    if not start_questions_on_first_page and remaining:
        page_number = 2
        current_page = create_page_with_anchors(
            doc, page_w, page_h, filled_anchors=filled
        )
        register_page_fonts(current_page, fonts)
        current_start_y = draw_continued_page_header(
            page=current_page,
            page_w=page_w,
            header_text=continued_header_text,
            fonts=fonts,
        )

    while remaining:
        drawn_count, _ = draw_questions_on_page(
            current_page,
            page_number,
            remaining,
            page_w,
            page_h,
            current_start_y,
            fonts,
        )
        remaining = remaining[drawn_count:]
        if not remaining:
            break

        page_number += 1
        current_page = create_page_with_anchors(
            doc, page_w, page_h, filled_anchors=filled
        )
        register_page_fonts(current_page, fonts)
        current_start_y = draw_continued_page_header(
            page=current_page,
            page_w=page_w,
            header_text=continued_header_text,
            fonts=fonts,
        )

    doc.save(str(output_path), garbage=4, deflate=True)
    doc.close()
