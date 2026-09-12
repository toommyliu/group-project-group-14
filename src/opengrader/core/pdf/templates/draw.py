from __future__ import annotations

from typing import TYPE_CHECKING

from .constants import (
    BOX_LINE,
    CONTENT_SIDE_MARGIN,
    MIN_QUESTION_BOX_HEIGHT,
    PAGE_EDGE_MARGIN,
    PRINT_SAFE_MARGIN,
    QUESTION_GAP,
    QUESTION_HEADER_HEIGHT,
    QUESTION_INNER_PADDING,
    SUBQUESTION_GAP,
    SUBQUESTION_MIN_HEIGHT,
    COLOR_BLACK,
    COLOR_QUESTION_TEXT,
    COLOR_SUBQUESTION_TEXT,
    COLOR_FIELD_LABEL,
    ColorType,
)
from .fonts import ResolvedTemplateFonts, measure_text_width

if TYPE_CHECKING:
    import fitz

QUESTION_FONTSIZE = 11
SUBQUESTION_FONTSIZE = 9
CHOICE_ROW_GAP = 4
CHOICE_LEFT_INSET = 20
PAGE_NUMBER_FONTSIZE = 11


def _draw_centered_text(
    page: fitz.Page,
    *,
    text: str,
    center_x: float,
    baseline_y: float,
    fontsize: float,
    bold: bool,
    fonts: ResolvedTemplateFonts,
    color: ColorType = COLOR_BLACK,
) -> None:
    import fitz

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


def _choice_row_height(fontsize: float) -> float:
    return max(16.0, fontsize + 6.0)


def _choice_block_height(choice_count: int, *, fontsize: float) -> float:
    if choice_count <= 0:
        return 0.0
    row_height = _choice_row_height(fontsize)
    return row_height * choice_count + CHOICE_ROW_GAP * max(0, choice_count - 1)


def _draw_choice_rows(
    page: fitz.Page,
    *,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    choices: list[dict],
    fontsize: float,
    fonts: ResolvedTemplateFonts,
    color: ColorType,
    context: str,
) -> None:
    import fitz

    if not choices:
        return

    row_height = _choice_row_height(fontsize)
    block_height = _choice_block_height(len(choices), fontsize=fontsize)
    available_height = y1 - y0
    if block_height > available_height + 1e-6:
        raise ValueError(
            f"{context} does not fit on the page; reduce the number of answer choices"
        )

    start_y = y0 + max(2.0, (available_height - block_height) / 2.0)
    label_x = x0 + CHOICE_LEFT_INSET

    for i, choice in enumerate(choices):
        row_top = start_y + i * (row_height + CHOICE_ROW_GAP)
        row_center_y = row_top + (row_height / 2.0)
        label_text = f"{choice['label']})"
        label_width = measure_text_width(label_text, fontsize, bold=False, fonts=fonts)
        text_x = label_x + label_width + 6
        option_text = _truncate_text_to_width(
            str(choice["text"]),
            max_width=max(20.0, x1 - text_x - 2),
            fontsize=fontsize,
            bold=False,
            fonts=fonts,
        )

        baseline_y = row_center_y + (fontsize * 0.35)
        page.insert_text(
            fitz.Point(label_x, baseline_y),
            label_text,
            fontsize=fontsize,
            fontname=fonts.regular_name,
            color=color,
        )
        if option_text:
            page.insert_text(
                fitz.Point(text_x, baseline_y),
                option_text,
                fontsize=fontsize,
                fontname=fonts.regular_name,
                color=color,
            )


def _instruction_wrap(
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


def create_page(doc: fitz.Document, page_w: float, page_h: float) -> fitz.Page:
    return doc.new_page(width=page_w, height=page_h)


def draw_field_box(
    page: fitz.Page,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    value: str,
    fonts: ResolvedTemplateFonts,
) -> None:
    import fitz

    center_x = (x0 + x1) / 2.0

    underline_y = y0 + (y1 - y0) * 0.63
    page.draw_line(
        fitz.Point(x0 + 14, underline_y),
        fitz.Point(x1 - 14, underline_y),
        color=COLOR_BLACK,
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
    baseline_y = page_h - PRINT_SAFE_MARGIN - (PAGE_NUMBER_FONTSIZE * 0.35)
    _draw_centered_text(
        page,
        text=str(page_number),
        center_x=page_w / 2.0,
        baseline_y=baseline_y,
        fontsize=PAGE_NUMBER_FONTSIZE,
        bold=True,
        fonts=fonts,
    )


QR_SIZE = 60.0


def draw_qr_on_page(
    page: fitz.Page,
    png_bytes: bytes,
    page_w: float,
) -> None:
    import fitz

    x1 = page_w - PAGE_EDGE_MARGIN
    x0 = x1 - QR_SIZE
    y0 = float(PAGE_EDGE_MARGIN)
    y1 = y0 + QR_SIZE
    page.insert_image(fitz.Rect(x0, y0, x1, y1), stream=png_bytes)


def draw_continued_page_header(
    page: fitz.Page,
    page_w: float,
    header_text: str,
    fonts: ResolvedTemplateFonts,
) -> float:
    import fitz

    content_x0 = CONTENT_SIDE_MARGIN
    content_x1 = page_w - CONTENT_SIDE_MARGIN
    text = header_text.strip() or "Quiz"

    header_size = _fit_font_size(
        text,
        target_width=content_x1 - content_x0,
        base_size=10.5,
        min_size=9,
        bold=False,
        fonts=fonts,
    )
    header_baseline_y = PRINT_SAFE_MARGIN + header_size
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
        color=COLOR_BLACK,
        width=BOX_LINE,
    )

    return underline_y + 24


def _display_question_id(qid: str) -> str:
    clean = qid.strip()
    if clean.lower().startswith("q") and len(clean) > 1:
        return clean[1:]
    return clean


def _question_min_height(question: dict) -> float:
    choices = question.get("choices", [])
    subquestions = question.get("subquestions", [])
    if choices and not subquestions:
        return max(
            MIN_QUESTION_BOX_HEIGHT,
            QUESTION_HEADER_HEIGHT
            + 2 * QUESTION_INNER_PADDING
            + _choice_block_height(len(choices), fontsize=QUESTION_FONTSIZE),
        )

    if not subquestions:
        return MIN_QUESTION_BOX_HEIGHT

    sub_block_height = (
        sum(_subquestion_min_height(subquestion) for subquestion in subquestions)
        + max(0, len(subquestions) - 1) * SUBQUESTION_GAP
    )
    return max(
        MIN_QUESTION_BOX_HEIGHT,
        QUESTION_HEADER_HEIGHT + 2 * QUESTION_INNER_PADDING + sub_block_height,
    )


def _subquestion_min_height(subquestion: dict) -> float:
    choices = subquestion.get("choices", [])
    if not choices:
        return SUBQUESTION_MIN_HEIGHT
    return max(
        SUBQUESTION_MIN_HEIGHT,
        18 + 4 + _choice_block_height(len(choices), fontsize=SUBQUESTION_FONTSIZE),
    )


def _question_can_expand(question: dict) -> bool:
    return not question.get("choices", [])


def _subquestion_can_expand(subquestion: dict) -> bool:
    return not subquestion.get("choices", [])


def _question_prompt_rect(
    x0: float, y0: float, x1: float, y1: float
) -> tuple[float, float, float, float]:
    prompt_top = y0 + 3
    prompt_bottom = min(y1 - 2, y0 + QUESTION_HEADER_HEIGHT + 4)
    if prompt_bottom <= prompt_top:
        prompt_bottom = min(y1, prompt_top + 1)
    return (x0 + 6, prompt_top, x1 - 6, prompt_bottom)


def _question_answer_rect(
    x0: float, y0: float, x1: float, y1: float
) -> tuple[float, float, float, float] | None:
    answer_top = min(y1, y0 + QUESTION_HEADER_HEIGHT + 2)
    answer_bottom = y1 - QUESTION_INNER_PADDING
    if answer_bottom <= answer_top:
        return None
    return (x0 + 8, answer_top, x1 - 8, answer_bottom)


def _subquestion_prompt_rect(
    x0: float, sub_y0: float, x1: float, sub_y1: float
) -> tuple[float, float, float, float]:
    prompt_top = sub_y0 + 2
    prompt_bottom = min(sub_y1 - 1, sub_y0 + 18)
    if prompt_bottom <= prompt_top:
        prompt_bottom = min(sub_y1, prompt_top + 1)
    return (x0 + 10, prompt_top, x1 - 10, prompt_bottom)


def _subquestion_answer_rect(
    x0: float, sub_y0: float, x1: float, sub_y1: float
) -> tuple[float, float, float, float] | None:
    answer_top = min(sub_y1, sub_y0 + 18)
    answer_bottom = sub_y1 - 4
    if answer_bottom <= answer_top:
        return None
    return (x0 + 10, answer_top, x1 - 10, answer_bottom)


def _draw_answer_hint(
    page: fitz.Page,
    answer_rect: tuple[float, float, float, float],
    hint: str,
    fonts: ResolvedTemplateFonts,
) -> None:
    import fitz

    text = hint.strip()
    if not text:
        return
    ax0, ay0, ax1, _ay1 = answer_rect
    fontsize = 9.0
    trimmed = _truncate_text_to_width(
        text,
        max_width=max(20.0, ax1 - ax0 - 4),
        fontsize=fontsize,
        bold=False,
        fonts=fonts,
    )
    if not trimmed:
        return
    page.insert_text(
        fitz.Point(ax0, ay0 + fontsize),
        trimmed,
        fontsize=fontsize,
        fontname=fonts.regular_name,
        color=COLOR_FIELD_LABEL,
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
    choices: list[dict] | None = None,
    subquestions: list[dict] | None = None,
    answer_hint: str = "",
) -> list[dict]:
    import fitz


    header_prefix = f"{_display_question_id(qid)}.  ({points} pts)"
    header_y = y0 + 16
    page.insert_text(
        fitz.Point(x0 + 8, header_y),
        header_prefix,
        fontsize=QUESTION_FONTSIZE,
        fontname=fonts.bold_name,
        color=COLOR_QUESTION_TEXT,
    )

    prefix_w = measure_text_width(
        header_prefix, QUESTION_FONTSIZE, bold=True, fonts=fonts
    )
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
            color=COLOR_QUESTION_TEXT,
        )


    if not subquestions:
        if choices:
            answer_rect = _question_answer_rect(x0, y0, x1, y1)
            if answer_rect is None:
                raise ValueError(f"Question {qid!r} does not fit on the page")
            _draw_choice_rows(
                page,
                x0=answer_rect[0],
                y0=answer_rect[1],
                x1=answer_rect[2],
                y1=answer_rect[3],
                choices=choices,
                fontsize=QUESTION_FONTSIZE,
                fonts=fonts,
                color=COLOR_QUESTION_TEXT,
                context=f"Question {qid!r}",
            )
        elif answer_hint:
            answer_rect = _question_answer_rect(x0, y0, x1, y1)
            if answer_rect is not None:
                _draw_answer_hint(page, answer_rect, answer_hint, fonts)
        return []


    sub_area_top = y0 + QUESTION_HEADER_HEIGHT + QUESTION_INNER_PADDING
    sub_area_bottom = y1 - QUESTION_INNER_PADDING
    sub_count = len(subquestions)
    available = sub_area_bottom - sub_area_top - max(0, sub_count - 1) * SUBQUESTION_GAP
    if available <= 0:
        return []

    sub_min_heights = [
        _subquestion_min_height(subquestion) for subquestion in subquestions
    ]
    required = sum(sub_min_heights)
    extra_total = max(0.0, available - required)
    expandable_indices = [
        i
        for i, subquestion in enumerate(subquestions)
        if _subquestion_can_expand(subquestion)
    ]
    extra_per_subquestion = (
        extra_total / len(expandable_indices) if expandable_indices else 0.0
    )
    drawn_subquestions: list[dict] = []
    cursor_y = sub_area_top

    for i, subquestion in enumerate(subquestions):
        sub_y0 = cursor_y
        sub_y1 = (
            sub_y0
            + sub_min_heights[i]
            + (extra_per_subquestion if i in expandable_indices else 0.0)
        )
        sub_rect = (x0 + 8, sub_y0, x1 - 8, sub_y1)
        sub_content_rect = _subquestion_answer_rect(
            sub_rect[0], sub_y0, sub_rect[2], sub_y1
        )
        if sub_content_rect is None:
            raise ValueError(
                f"Question {qid!r} subquestion {subquestion['id']!r} does not fit on the page"
            )
        sub_header_x = sub_rect[0] + CHOICE_LEFT_INSET

        raw_label = str(subquestion.get("label", chr(ord("a") + i))).strip()
        label = raw_label if raw_label.endswith(")") else f"{raw_label})"
        sub_points = int(subquestion.get("points", 0))
        sub_prompt = str(subquestion.get("prompt", ""))
        sub_prefix = f"{label} ({sub_points} pts)"
        sub_header_y = sub_y0 + 13
        page.insert_text(
            fitz.Point(sub_header_x, sub_header_y),
            sub_prefix,
            fontsize=SUBQUESTION_FONTSIZE,
            fontname=fonts.regular_name,
            color=COLOR_SUBQUESTION_TEXT,
        )

        sub_prefix_w = measure_text_width(
            sub_prefix, SUBQUESTION_FONTSIZE, bold=False, fonts=fonts
        )
        sub_prompt_space = max(20.0, sub_rect[2] - sub_header_x - sub_prefix_w - 6)
        sub_prompt_text = _truncate_text_to_width(
            sub_prompt,
            max_width=sub_prompt_space,
            fontsize=SUBQUESTION_FONTSIZE,
            bold=False,
            fonts=fonts,
        )
        if sub_prompt_text:
            page.insert_text(
                fitz.Point(sub_header_x + sub_prefix_w + 5, sub_header_y),
                sub_prompt_text,
                fontsize=SUBQUESTION_FONTSIZE,
                fontname=fonts.regular_name,
                color=COLOR_SUBQUESTION_TEXT,
            )

        subquestion_meta = {
            "id": str(subquestion["id"]),
            "label": raw_label,
            "rect": sub_rect,
            "prompt_rect": _subquestion_prompt_rect(
                sub_header_x - 10, sub_y0, sub_rect[2], sub_y1
            ),
            "points": sub_points,
            "prompt": sub_prompt,
        }
        sub_choices = subquestion.get("choices", [])


        if sub_choices:
            _draw_choice_rows(
                page,
                x0=sub_content_rect[0],
                y0=sub_content_rect[1],
                x1=sub_content_rect[2],
                y1=sub_content_rect[3],
                choices=sub_choices,
                fontsize=SUBQUESTION_FONTSIZE,
                fonts=fonts,
                color=COLOR_SUBQUESTION_TEXT,
                context=(f"Question {qid!r} subquestion {subquestion_meta['id']!r}"),
            )
        else:
            subquestion_meta["answer_rect"] = sub_content_rect

        drawn_subquestions.append(subquestion_meta)
        cursor_y = sub_y1 + SUBQUESTION_GAP

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
    import fitz


    content_x0 = CONTENT_SIDE_MARGIN
    content_x1 = page_w - CONTENT_SIDE_MARGIN


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

    title_bounds_left = float("inf")
    title_bounds_top = float("inf")
    title_bounds_right = float("-inf")
    title_bounds_bottom = float("-inf")

    def include_title_line_bounds(
        text: str,
        baseline_y: float,
        fontsize: float,
        *,
        bold: bool,
    ) -> None:
        nonlocal \
            title_bounds_left, \
            title_bounds_top, \
            title_bounds_right, \
            title_bounds_bottom
        line_w = measure_text_width(text, fontsize, bold=bold, fonts=fonts)
        line_left = center_x - (line_w / 2.0)
        line_right = center_x + (line_w / 2.0)
        line_top = baseline_y - fontsize
        line_bottom = baseline_y + fontsize * 0.3
        title_bounds_left = min(title_bounds_left, line_left)
        title_bounds_top = min(title_bounds_top, line_top)
        title_bounds_right = max(title_bounds_right, line_right)
        title_bounds_bottom = max(title_bounds_bottom, line_bottom)


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
    include_title_line_bounds(title_line, cursor_y, title_size, bold=True)
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
        include_title_line_bounds(line, cursor_y, subtitle_size, bold=False)
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
        color=COLOR_FIELD_LABEL,
    )
    page.insert_text(
        fitz.Point(start_x, sid_row_y + label_baseline_offset),
        "Student ID",
        fontsize=11,
        fontname=fonts.regular_name,
        color=COLOR_FIELD_LABEL,
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

        prefix_width, first_line, remainder_lines = _instruction_wrap(
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
            color=COLOR_BLACK,
        )
        if first_line:
            page.insert_text(
                fitz.Point(content_x0 + prefix_width + 2, y),
                first_line,
                fontsize=instruction_size,
                fontname=fonts.regular_name,
                color=COLOR_BLACK,
            )

        y += line_height
        for line in remainder_lines:
            page.insert_text(
                fitz.Point(content_x0, y),
                line,
                fontsize=instruction_size,
                fontname=fonts.regular_name,
                color=COLOR_BLACK,
            )
            y += line_height
        q_start_y = y + 4

    title_box = (
        max(content_x0, title_bounds_left - 6),
        max(PAGE_EDGE_MARGIN, title_bounds_top - 3),
        min(content_x1, title_bounds_right + 6),
        min(row_y - 4, title_bounds_bottom + 3),
    )

    return q_start_y, {
        "title": title_box,
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
    content_x0 = CONTENT_SIDE_MARGIN
    content_x1 = page_w - CONTENT_SIDE_MARGIN
    content_top = start_y
    footer_top = page_h - PRINT_SAFE_MARGIN - PAGE_NUMBER_FONTSIZE
    content_bottom = min(page_h - PAGE_EDGE_MARGIN - 32, footer_top - 10)
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
    extra_total = max(0.0, available_height - required_height)
    expandable_indices = [
        i
        for i, question in enumerate(questions[:max_fit])
        if _question_can_expand(question)
    ]
    extra_per_question = (
        extra_total / len(expandable_indices) if expandable_indices else 0.0
    )

    drawn_meta: list[dict] = []
    y = content_top
    for i in range(max_fit):
        question = questions[i]
        qid = str(question["id"])
        points = int(question["points"])
        prompt = str(question["prompt"])
        question_height = question_min_heights[i] + (
            extra_per_question if i in expandable_indices else 0.0
        )
        y0 = y
        y1 = y0 + question_height

        choices = question.get("choices", [])
        subquestions = question.get("subquestions", [])
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
            choices=choices,
            subquestions=subquestions,
            answer_hint=question.get("answer_hint", ""),
        )

        question_meta = {
            "id": qid,
            "rect": (content_x0, y0, content_x1, y1),
            "prompt_rect": _question_prompt_rect(content_x0, y0, content_x1, y1),
            "page_number": page_number,
            "points": points,
            "prompt": prompt,
            "subquestions": drawn_subquestions,
        }
        if not subquestions and not choices:
            answer_rect = _question_answer_rect(content_x0, y0, content_x1, y1)
            if answer_rect is not None:
                question_meta["answer_rect"] = answer_rect

        drawn_meta.append(question_meta)
        y = y1 + QUESTION_GAP

    draw_page_number(page, page_number, page_w, page_h, fonts)

    return max_fit, drawn_meta
