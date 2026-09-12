from __future__ import annotations

from typing import TYPE_CHECKING

from .constants import COLOR_BLACK, COLOR_FIELD_LABEL, CONTENT_SIDE_MARGIN, LETTER_H, LETTER_W
from .fonts import ResolvedTemplateFonts, register_page_fonts

if TYPE_CHECKING:
    import fitz

_HEADER_TOP_Y = 96.0
_ROW_HEIGHT = 20.0
_ID_COL_FONTSIZE = 11
_TITLE_FONTSIZE = 15
_NOTE_FONTSIZE = 9.5


def draw_master_key_page(
    doc: fitz.Document,
    entries: list[tuple[str, str]],
    *,
    title: str,
    fonts: ResolvedTemplateFonts,
    page_w: float = LETTER_W,
    page_h: float = LETTER_H,
) -> None:
    import fitz

    from .draw import create_page, _draw_centered_text

    content_x0 = CONTENT_SIDE_MARGIN
    id_col_x = content_x0
    name_col_x = content_x0 + 150
    bottom_limit = page_h - 48

    title_lines = [line.strip() for line in title.splitlines() if line.strip()]
    heading = title_lines[0] if title_lines else "Assignment"

    def new_key_page() -> tuple[fitz.Page, float]:
        page = create_page(doc, page_w, page_h)
        register_page_fonts(page, fonts)
        _draw_centered_text(
            page,
            text=f"Master Key — {heading}",
            center_x=page_w / 2.0,
            baseline_y=_HEADER_TOP_Y,
            fontsize=_TITLE_FONTSIZE,
            bold=True,
            fonts=fonts,
        )
        page.insert_text(
            fitz.Point(content_x0, _HEADER_TOP_Y + 18),
            "Teacher copy — keep this page and detach it before handing out the assignments.",
            fontsize=_NOTE_FONTSIZE,
            fontname=fonts.regular_name,
            color=COLOR_FIELD_LABEL,
        )
        header_y = _HEADER_TOP_Y + 44
        page.insert_text(
            fitz.Point(id_col_x, header_y),
            "Student ID",
            fontsize=_ID_COL_FONTSIZE,
            fontname=fonts.bold_name,
            color=COLOR_BLACK,
        )
        page.insert_text(
            fitz.Point(name_col_x, header_y),
            "Name",
            fontsize=_ID_COL_FONTSIZE,
            fontname=fonts.bold_name,
            color=COLOR_BLACK,
        )
        return page, header_y + _ROW_HEIGHT

    page, row_y = new_key_page()
    for student_id, name in entries:
        if row_y > bottom_limit:
            page, row_y = new_key_page()
        page.insert_text(
            fitz.Point(id_col_x, row_y),
            student_id,
            fontsize=_ID_COL_FONTSIZE,
            fontname=fonts.regular_name,
            color=COLOR_BLACK,
        )
        page.insert_text(
            fitz.Point(name_col_x, row_y),
            name,
            fontsize=_ID_COL_FONTSIZE,
            fontname=fonts.regular_name,
            color=COLOR_BLACK,
        )
        row_y += _ROW_HEIGHT
