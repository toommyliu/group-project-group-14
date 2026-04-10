import fitz

from .anchor import Anchor
from .constants import (
    ANCHOR_MARGIN,
    ANCHOR_SIZE,
    BOX_LINE,
    CONTENT_MARGIN,
    MIN_QUESTION_BOX_HEIGHT,
    QUESTION_GAP,
    QUESTION_HEADER_HEIGHT,
    QUESTION_INNER_PADDING,
    SUBQUESTION_GAP,
    SUBQUESTION_MIN_HEIGHT,
)
from .fonts import ResolvedTemplateFonts, measure_text_width

ANCHOR_POSITIONS = {
    Anchor.TopLeft: lambda m, s, pw, ph: (m, m),
    Anchor.TopRight: lambda m, s, pw, ph: (pw - m - s, m),
    Anchor.Bottomleft: lambda m, s, pw, ph: (m, ph - m - s),
    Anchor.BottomRight: lambda m, s, pw, ph: (pw - m - s, ph - m - s),
}

QUESTION_FONTSIZE = 9
SUBQUESTION_FONTSIZE = 8.4

def _draw_centered_text(
    page: fitz.Page,
    *,
    text: str,
    center_x: float,
    baseline_y: float,
    fontsize: float,
    bold: bool,
    fonts: ResolvedTemplateFonts,
    color: tuple[float, float, float] = (0.0, 0.0, 0.0),
) -> None:
    width = measure_text_width(text, fontsize, bold=bold, fonts=fonts)
    fontname = fonts.bold_name if bold else fonts.regular_name
    page.insert_text(
        fitz.Point(center_x - width / 2.0, baseline_y),
        text,
        fontsize=fontsize,
        fontname=fontname,
        color=color,
    )


def _fit_font_size(
    text: str,
    *,
    target_width: float,
    base_size: float,
    min_size: float,
    bold: bool,
    fonts: ResolvedTemplateFonts,
) -> float:
    if not text:
        return base_size
    width = measure_text_width(text, base_size, bold=bold, fonts=fonts)
    if width <= target_width:
        return base_size
    scaled = base_size * (target_width / max(width, 1.0))
    return max(min_size, scaled)


def _wrap_text(
    text: str,
    *,
    max_width: float,
    fontsize: float,
    fonts: ResolvedTemplateFonts,
) -> list[str]:
    words = [w for w in text.split() if w]
    if not words:
        return []

    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if (
            measure_text_width(candidate, fontsize, bold=False, fonts=fonts)
            <= max_width
        ):
            current = candidate
            continue
        lines.append(current)
        current = word
    lines.append(current)
    return lines


def _truncate_text_to_width(
    text: str,
    *,
    max_width: float,
    fontsize: float,
    bold: bool,
    fonts: ResolvedTemplateFonts,
) -> str:
    clean = " ".join(text.split())
    if not clean:
        return ""

    if measure_text_width(clean, fontsize, bold=bold, fonts=fonts) <= max_width:
        return clean

    words = clean.split(" ")
    out = ""
    for word in words:
        candidate = word if not out else f"{out} {word}"
        if (
            measure_text_width(candidate + "...", fontsize, bold=bold, fonts=fonts)
            <= max_width
        ):
            out = candidate
            continue
        break

    if out:
        return out + "..."

    # Fallback: trim by characters when even one word is too wide.
    chars = ""
    for ch in clean:
        candidate = chars + ch
        if (
            measure_text_width(candidate + "...", fontsize, bold=bold, fonts=fonts)
            <= max_width
        ):
            chars = candidate
            continue
        break
    return (chars + "...") if chars else ""


def _instruction_layout(
    instruction_text: str,
    *,
    label: str,
    max_width: float,
    fontsize: float,
    fonts: ResolvedTemplateFonts,
) -> tuple[float, str, list[str]]:
    prefix_width = measure_text_width(f"{label} ", fontsize, bold=True, fonts=fonts)
    first_width = max(24.0, max_width - prefix_width)

    words = [w for w in instruction_text.split() if w]
    if not words:
        return prefix_width, "", []

    first_line = ""
    consumed = 0
    for i, word in enumerate(words):
        candidate = word if not first_line else f"{first_line} {word}"
        if (
            measure_text_width(candidate, fontsize, bold=False, fonts=fonts)
            <= first_width
        ):
            first_line = candidate
            consumed = i + 1
            continue
        if not first_line:
            first_line = word
            consumed = 1
        break

    remaining = " ".join(words[consumed:]).strip()
    wrapped_remaining = _wrap_text(
        remaining,
        max_width=max_width,
        fontsize=fontsize,
        fonts=fonts,
    )
    return prefix_width, first_line, wrapped_remaining


def draw_anchor(
    page: fitz.Page, x: float, y: float, size: float, filled: bool = False
) -> None:
    """
    Draws an anchor box at the specified position.

    Args:
        page: The fitz.Page object to draw on.
        x: The x-coordinate of the top-left corner of the anchor box.
        y: The y-coordinate of the top-left corner of the anchor box.
        size: The width and height of the anchor box.
        filled: If True, the anchor box will be filled; otherwise, it will be an
            empty box with an "X" inside.
    """
    r = fitz.Rect(x, y, x + size, y + size)
    if filled:
        page.draw_rect(r, color=(0.0, 0.0, 0.0), fill=(0.0, 0.0, 0.0), width=BOX_LINE)
    else:
        page.draw_rect(r, color=(0.0, 0.0, 0.0), width=BOX_LINE)
        page.draw_line(
            fitz.Point(x + 2, y + 2),
            fitz.Point(x + size - 2, y + size - 2),
            color=(0.0, 0.0, 0.0),
            width=0.3,
        )
        page.draw_line(
            fitz.Point(x + size - 2, y + 2),
            fitz.Point(x + 2, y + size - 2),
            color=(0.0, 0.0, 0.0),
            width=0.3,
        )


def create_page_with_anchors(
    doc: fitz.Document,
    page_w: float,
    page_h: float,
    filled_anchors: set[Anchor] | None = None,
) -> fitz.Page:
    page = doc.new_page(width=page_w, height=page_h)
    margin = ANCHOR_MARGIN
    if filled_anchors is None:
        filled_anchors = {
            Anchor.TopLeft,
            Anchor.TopRight,
            Anchor.Bottomleft,
            Anchor.BottomRight,
        }
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
    value: str,
    fonts: ResolvedTemplateFonts,
) -> None:
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=(0.0, 0.0, 0.0), width=BOX_LINE)

    center_x = (x0 + x1) / 2.0

    underline_y = y0 + (y1 - y0) * 0.63
    page.draw_line(
        fitz.Point(x0 + 14, underline_y),
        fitz.Point(x1 - 14, underline_y),
        color=(0.0, 0.0, 0.0),
        width=0.55,
    )

    if value:
        _draw_centered_text(
            page,
            text=value,
            center_x=center_x,
            baseline_y=underline_y - 6,
            fontsize=13,
            bold=False,
            fonts=fonts,
        )


def draw_page_number(
    page: fitz.Page,
    page_number: int,
    page_w: float,
    page_h: float,
    fonts: ResolvedTemplateFonts,
) -> None:
    _draw_centered_text(
        page,
        text=str(page_number),
        center_x=page_w / 2.0,
        baseline_y=page_h - ANCHOR_MARGIN - 8,
        fontsize=11,
        bold=True,
        fonts=fonts,
    )


def draw_continued_page_header(
    page: fitz.Page,
    page_w: float,
    header_text: str,
    fonts: ResolvedTemplateFonts,
) -> float:
    content_x0 = CONTENT_MARGIN
    content_x1 = page_w - CONTENT_MARGIN
    text = header_text.strip() or "Quiz"

    header_baseline_y = 39.0
    header_size = _fit_font_size(
        text,
        target_width=content_x1 - content_x0,
        base_size=10.5,
        min_size=9,
        bold=False,
        fonts=fonts,
    )
    _draw_centered_text(
        page,
        text=text,
        center_x=page_w / 2.0,
        baseline_y=header_baseline_y,
        fontsize=header_size,
        bold=False,
        fonts=fonts,
    )

    underline_y = header_baseline_y + max(4.0, header_size * 0.45)
    page.draw_line(
        fitz.Point(content_x0, underline_y),
        fitz.Point(content_x1, underline_y),
        color=(0.0, 0.0, 0.0),
        width=BOX_LINE,
    )

    return underline_y + 24


def _display_question_id(qid: str) -> str:
    clean = qid.strip()
    if clean.lower().startswith("q") and len(clean) > 1:
        return clean[1:]
    return clean


def _question_min_height(question: dict) -> float:
    subquestions = question.get("subquestions", [])
    if not subquestions:
        return MIN_QUESTION_BOX_HEIGHT

    sub_count = len(subquestions)
    sub_block_height = (
        sub_count * SUBQUESTION_MIN_HEIGHT + max(0, sub_count - 1) * SUBQUESTION_GAP
    )
    return max(
        MIN_QUESTION_BOX_HEIGHT,
        QUESTION_HEADER_HEIGHT + 2 * QUESTION_INNER_PADDING + sub_block_height,
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
    fonts: ResolvedTemplateFonts,
    subquestions: list[dict] | None = None,
) -> list[dict]:
    page.draw_rect(fitz.Rect(x0, y0, x1, y1), color=(0.0, 0.0, 0.0), width=BOX_LINE)

    header_prefix = f"{_display_question_id(qid)}.  ({points} pts)"
    header_y = y0 + 16
    page.insert_text(
        fitz.Point(x0 + 8, header_y),
        header_prefix,
        fontsize=QUESTION_FONTSIZE,
        fontname=fonts.bold_name,
        color=(0.2, 0.2, 0.2),
    )

    prefix_w = measure_text_width(header_prefix, QUESTION_FONTSIZE, bold=True, fonts=fonts)
    prompt_space_w = max(24.0, (x1 - x0) - (prefix_w + 22))
    prompt_text = _truncate_text_to_width(
        prompt,
        max_width=prompt_space_w,
        fontsize=QUESTION_FONTSIZE,
        bold=False,
        fonts=fonts,
    )
    if prompt_text:
        page.insert_text(
            fitz.Point(x0 + 8 + prefix_w + 6, header_y),
            prompt_text,
            fontsize=QUESTION_FONTSIZE,
            fontname=fonts.regular_name,
            color=(0.2, 0.2, 0.2),
        )

    if not subquestions:
        return []

    sub_area_top = y0 + QUESTION_HEADER_HEIGHT + QUESTION_INNER_PADDING
    sub_area_bottom = y1 - QUESTION_INNER_PADDING
    sub_count = len(subquestions)
    available = sub_area_bottom - sub_area_top - max(0, sub_count - 1) * SUBQUESTION_GAP
    if available <= 0:
        return []

    sub_height = available / sub_count
    drawn_subquestions: list[dict] = []

    for i, subquestion in enumerate(subquestions):
        sub_y0 = sub_area_top + i * (sub_height + SUBQUESTION_GAP)
        sub_y1 = sub_y0 + sub_height
        page.draw_rect(
            fitz.Rect(x0 + 8, sub_y0, x1 - 8, sub_y1),
            color=(0.0, 0.0, 0.0),
            width=BOX_LINE,
        )

        raw_label = str(subquestion.get("label", chr(ord("a") + i))).strip()
        label = raw_label if raw_label.endswith(")") else f"{raw_label})"
        sub_points = int(subquestion.get("points", 0))
        sub_prompt = str(subquestion.get("prompt", ""))
        sub_prefix = f"{label} ({sub_points} pts)"
        sub_header_y = sub_y0 + 13
        page.insert_text(
            fitz.Point(x0 + 12, sub_header_y),
            sub_prefix,
            fontsize=SUBQUESTION_FONTSIZE,
            fontname=fonts.regular_name,
            color=(0.25, 0.25, 0.25),
        )

        sub_prefix_w = measure_text_width(sub_prefix, SUBQUESTION_FONTSIZE, bold=False, fonts=fonts)
        sub_prompt_space = max(20.0, (x1 - x0) - (sub_prefix_w + 30))
        sub_prompt_text = _truncate_text_to_width(
            sub_prompt,
            max_width=sub_prompt_space,
            fontsize=SUBQUESTION_FONTSIZE,
            bold=False,
            fonts=fonts,
        )
        if sub_prompt_text:
            page.insert_text(
                fitz.Point(x0 + 12 + sub_prefix_w + 5, sub_header_y),
                sub_prompt_text,
                fontsize=SUBQUESTION_FONTSIZE,
                fontname=fonts.regular_name,
                color=(0.25, 0.25, 0.25),
            )
        drawn_subquestions.append(
            {
                "id": str(subquestion["id"]),
                "label": raw_label,
                "rect": (x0 + 8, sub_y0, x1 - 8, sub_y1),
                "points": sub_points,
                "prompt": sub_prompt,
            }
        )

    return drawn_subquestions


def build_info_section(
    page: fitz.Page,
    name: str,
    student_id: str,
    page_w: float,
    title: str,
    instructions: str | None,
    fonts: ResolvedTemplateFonts,
) -> tuple[float, dict[str, tuple[float, float, float, float]]]:
    content_x0 = CONTENT_MARGIN
    content_x1 = page_w - CONTENT_MARGIN

    lines = [line.strip() for line in title.strip().split("\n") if line.strip()]
    if not lines:
        lines = ["Quiz"]

    title_line = lines[0]
    subtitle_lines = lines[1:]

    center_x = page_w / 2.0
    title_size = _fit_font_size(
        title_line,
        target_width=page_w * 0.82,
        base_size=16,
        min_size=14,
        bold=True,
        fonts=fonts,
    )
    # header_top_y = 232.0
    header_top_y = 116.0
    cursor_y = header_top_y + title_size
    _draw_centered_text(
        page,
        text=title_line,
        center_x=center_x,
        baseline_y=cursor_y,
        fontsize=title_size,
        bold=True,
        fonts=fonts,
    )
    cursor_y += title_size * 1.45

    for line in subtitle_lines:
        subtitle_size = _fit_font_size(
            line,
            target_width=page_w * 0.76,
            base_size=13,
            min_size=9,
            bold=False,
            fonts=fonts,
        )
        _draw_centered_text(
            page,
            text=line,
            center_x=center_x,
            baseline_y=cursor_y,
            fontsize=subtitle_size,
            bold=False,
            fonts=fonts,
        )
        cursor_y += subtitle_size * 1.32

    row_y = cursor_y
    label_w = measure_text_width("Student ID", 11, bold=False, fonts=fonts) + 1
    label_box_gap = 0
    name_field_w = 240
    sid_field_w = 240
    field_h = 34
    row_w = label_w + label_box_gap + max(name_field_w, sid_field_w)
    field_stack_gap = 16
    start_x = (page_w - row_w) / 2

    name_box_x0 = start_x + label_w + label_box_gap
    name_box = (name_box_x0, row_y, name_box_x0 + name_field_w, row_y + field_h)
    sid_row_y = row_y + field_h + field_stack_gap
    sid_box = (name_box_x0, sid_row_y, name_box_x0 + sid_field_w, sid_row_y + field_h)

    label_baseline_offset = field_h * 0.68
    page.insert_text(
        fitz.Point(start_x, row_y + label_baseline_offset),
        "Name",
        fontsize=11,
        fontname=fonts.regular_name,
        color=(0.15, 0.15, 0.15),
    )
    page.insert_text(
        fitz.Point(start_x, sid_row_y + label_baseline_offset),
        "Student ID",
        fontsize=11,
        fontname=fonts.regular_name,
        color=(0.15, 0.15, 0.15),
    )

    draw_field_box(
        page,
        name_box[0],
        name_box[1],
        name_box[2],
        name_box[3],
        name,
        fonts,
    )
    draw_field_box(
        page,
        sid_box[0],
        sid_box[1],
        sid_box[2],
        sid_box[3],
        student_id,
        fonts,
    )

    q_start_y = sid_box[3] + 22

    instruction_text = (instructions or "").strip()
    if instruction_text:
        label = "Instructions:"
        instruction_size = 10.5
        line_height = instruction_size * 1.25
        block_width = content_x1 - content_x0

        prefix_width, first_line, remainder_lines = _instruction_layout(
            instruction_text,
            label=label,
            max_width=block_width,
            fontsize=instruction_size,
            fonts=fonts,
        )

        y = q_start_y
        page.insert_text(
            fitz.Point(content_x0, y),
            label,
            fontsize=instruction_size,
            fontname=fonts.bold_name,
            color=(0.0, 0.0, 0.0),
        )
        if first_line:
            page.insert_text(
                fitz.Point(content_x0 + prefix_width + 2, y),
                first_line,
                fontsize=instruction_size,
                fontname=fonts.regular_name,
                color=(0.0, 0.0, 0.0),
            )

        y += line_height
        for line in remainder_lines:
            page.insert_text(
                fitz.Point(content_x0, y),
                line,
                fontsize=instruction_size,
                fontname=fonts.regular_name,
                color=(0.0, 0.0, 0.0),
            )
            y += line_height
        q_start_y = y + 4

    return q_start_y, {
        "student_name": name_box,
        "student_id": sid_box,
    }


def draw_questions_on_page(
    page: fitz.Page,
    page_number: int,
    questions: list[dict],
    page_w: float,
    page_h: float,
    start_y: float,
    fonts: ResolvedTemplateFonts,
) -> tuple[int, list[dict]]:
    content_x0 = CONTENT_MARGIN
    content_x1 = page_w - CONTENT_MARGIN
    content_top = start_y
    content_bottom = page_h - ANCHOR_MARGIN - ANCHOR_SIZE - 32
    if content_bottom <= content_top:
        draw_page_number(page, page_number, page_w, page_h, fonts)
        return 0, []

    available_height = content_bottom - content_top
    max_fit = 0
    min_heights: list[float] = []

    for candidate in range(1, len(questions) + 1):
        q = questions[candidate - 1]
        min_heights.append(_question_min_height(q))
        required = sum(min_heights) + (candidate - 1) * QUESTION_GAP
        if required <= available_height:
            max_fit = candidate
        else:
            break

    if max_fit == 0:
        question_id = str(questions[0].get("id", "unknown")) if questions else "unknown"
        raise ValueError(
            "Question "
            f"{question_id!r} does not fit on page {page_number}; "
            "reduce prompt length or number of subquestions."
        )

    question_min_heights = min_heights[:max_fit]
    required_height = sum(question_min_heights) + (max_fit - 1) * QUESTION_GAP
    extra_per_question = max(0.0, available_height - required_height) / max_fit

    drawn_meta: list[dict] = []
    y = content_top
    for i in range(max_fit):
        question = questions[i]
        qid = str(question["id"])
        points = int(question["points"])
        prompt = str(question["prompt"])
        question_height = question_min_heights[i] + extra_per_question
        y0 = y
        y1 = y0 + question_height

        drawn_subquestions = draw_question_box(
            page,
            content_x0,
            y0,
            content_x1,
            y1,
            qid,
            points,
            prompt,
            fonts,
            subquestions=question.get("subquestions", []),
        )
        drawn_meta.append(
            {
                "id": qid,
                "rect": (content_x0, y0, content_x1, y1),
                "page_number": page_number,
                "points": points,
                "prompt": prompt,
                "subquestions": drawn_subquestions,
            }
        )
        y = y1 + QUESTION_GAP

    draw_page_number(page, page_number, page_w, page_h, fonts)

    return max_fit, drawn_meta
