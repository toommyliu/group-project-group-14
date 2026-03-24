from .anchor import Anchor
import fitz

from .constants import (
    ANCHOR_SIZE,
    ANCHOR_MARGIN,
    BOX_LINE,
    GUIDE_LINE,
    MIN_QUESTION_BOX_HEIGHT,
    QUESTION_GAP,
    DEFAULT_FONT,
)

ANCHOR_POSITIONS = {
    Anchor.TopLeft: lambda m, s, pw, ph: (m, m),
    Anchor.TopRight: lambda m, s, pw, ph: (pw - m - s, m),
    Anchor.Bottomleft: lambda m, s, pw, ph: (m, ph - m - s),
    Anchor.BottomRight: lambda m, s, pw, ph: (pw - m - s, ph - m - s),
}

def draw_anchor(page: fitz.Page, x: float, y: float, size: float, filled: bool = False) -> None:
    r = fitz.Rect(x, y, x + size, y + size)
    if filled:
        page.draw_rect(r, color=(0.0, 0.0, 0.0), fill=(0.0, 0.0, 0.0), width=BOX_LINE)
    else:
        page.draw_rect(r, color=(0.0, 0.0, 0.0), width=BOX_LINE)
        page.draw_line(
            fitz.Point(x + 2, y + 2), fitz.Point(x + size - 2, y + size - 2), color=(0.0, 0.0, 0.0), width=0.3
        )
        page.draw_line(
            fitz.Point(x + size - 2, y + 2), fitz.Point(x + 2, y + size - 2), color=(0.0, 0.0, 0.0), width=0.3
        )

def create_page_with_anchors(
    doc: fitz.Document, page_w: float, page_h: float, filled_anchors: set[Anchor] | None = None
) -> fitz.Page:
    page = doc.new_page(width=page_w, height=page_h)
    margin = ANCHOR_MARGIN
    if filled_anchors is None:
        filled_anchors = {Anchor.TopLeft, Anchor.TopRight, Anchor.Bottomleft, Anchor.BottomRight}
    for aid, pos_fn in ANCHOR_POSITIONS.items():
        x, y = pos_fn(margin, ANCHOR_SIZE, page_w, page_h)
        draw_anchor(page, x, y, ANCHOR_SIZE, filled=aid in filled_anchors)
    return page

def draw_field_box(
    page: fitz.Page,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    label: str,
    value: str = "",
    fontname: str = DEFAULT_FONT,
) -> None:
    """
    Draw a input field box with a label and an optional value.
    """
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=(0.0, 0.0, 0.0), width=BOX_LINE)
    page.insert_text(
        fitz.Point(x0 + 3, y0 - 4),
        label,
        fontsize=9,
        fontname=fontname,
        color=(0.3, 0.3, 0.3),
    )
    if value:
        page.insert_text(
            fitz.Point(x0 + 6, y0 + 18),
            value,
            fontsize=11,
            fontname=fontname,
            color=(0.0, 0.0, 0.0),
        )

def draw_question_box(
    page: fitz.Page,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    qid: str,
    points: int,
    prompt: str,
    fontname: str = DEFAULT_FONT,
) -> None:
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=(0.0, 0.0, 0.0), width=BOX_LINE)
    # TODO: move Q from generation
    header = f"{qid.upper().replace('Q', '')}.  ({points} pts)  {prompt}"
    page.insert_textbox(
        fitz.Rect(x0 + 4, y0 + 4, x1 - 4, y0 + 36),
        header,
        fontsize=9,
        fontname=fontname,
        color=(0.2, 0.2, 0.2),
    )

def build_info_section(
    page: fitz.Page,
    name: str,
    student_id: str,
    page_w: float,
    title: str,
    fontname: str = DEFAULT_FONT,
) -> tuple[float, dict[str, tuple[float, float, float, float]]]:
    """
    Draw the info section of the template page, including the title, name, and student ID.
    """
    margin = ANCHOR_MARGIN
    cx = page_w / 2

    # Title
    lines = [line.strip() for line in title.strip().split("\n") if line.strip()]
    num_lines = len(lines)
    fontsize = 14
    line_h = fontsize * 1.4
    title_h = (num_lines * line_h) + 4

    max_title_w = page_w * 0.75
    for i, line in enumerate(lines):
        text_w = fitz.get_text_length(line, fontname=fontname, fontsize=fontsize)
        current_fs = fontsize
        if text_w > max_title_w:
            current_fs = max(6, fontsize * (max_title_w / text_w))
            text_w = fitz.get_text_length(line, fontname=fontname, fontsize=current_fs)
        page.insert_text(
            fitz.Point(cx - text_w / 2, margin + (i + 1) * line_h - 9),
            line,
            fontsize=current_fs,
            fontname=fontname,
            color=(0.0, 0.0, 0.0),
        )

    # Name and Student ID
    row_y = margin + title_h + 12
    name_w = 260
    sid_w = 200
    gap = 20
    total_w = name_w + gap + sid_w
    startX = (page_w - total_w) / 2

    name_box = (startX, row_y, startX + name_w, row_y + 32)
    sid_box = (name_box[2] + gap, row_y, name_box[2] + gap + sid_w, row_y + 32)

    draw_field_box(
        page,
        name_box[0],
        name_box[1],
        name_box[2],
        name_box[3],
        "Name",
        name,
        fontname=fontname,
    )
    draw_field_box(
        page,
        sid_box[0],
        sid_box[1],
        sid_box[2],
        sid_box[3],
        "Student ID",
        student_id,
        fontname=fontname,
    )

    q_start_y = row_y + 40
    return q_start_y, {
        "student_name": name_box,
        "student_id": sid_box,
    }


def draw_questions_on_page(
    page: fitz.Page,
    page_number: int,
    questions: list[tuple[str, int, str]],
    page_w: float,
    page_h: float,
    start_y: float,
    fontname: str = DEFAULT_FONT,
) -> tuple[int, list[tuple[str, tuple[float, float, float, float], int, int, str]]]:
    margin = ANCHOR_MARGIN
    content_top = start_y
    content_bottom = page_h - margin - ANCHOR_SIZE - 16
    if content_bottom <= content_top:
        return 0, []

    max_fit = 0
    max_fit_height = 0.0
    for candidate in range(1, len(questions) + 1):
        available = content_bottom - content_top - (candidate - 1) * QUESTION_GAP
        h = available / candidate
        if h >= MIN_QUESTION_BOX_HEIGHT:
            max_fit = candidate
            max_fit_height = h

    if max_fit == 0:
        max_fit = 1
        max_fit_height = content_bottom - content_top

    drawn_meta: list[tuple[str, tuple[float, float, float, float], int, int, str]] = []
    for i in range(max_fit):
        qid, points, prompt = questions[i]
        y0 = content_top + i * (max_fit_height + QUESTION_GAP)
        y1 = y0 + max_fit_height
        draw_question_box(
            page,
            margin,
            y0,
            page_w - margin,
            y1,
            qid,
            points,
            prompt,
            fontname=fontname,
        )
        drawn_meta.append(
            (qid, (margin, y0, page_w - margin, y1), page_number, points, prompt)
        )

    page.insert_text(
        fitz.Point(page_w / 2 - 20, page_h - 8),
        f"page {page_number}",
        fontsize=8,
        fontname=fontname,
        color=(0.5, 0.5, 0.5),
    )

    return max_fit, drawn_meta
