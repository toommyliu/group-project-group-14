"""Draw a machine-readable bubble answer sheet with PyMuPDF.

The write half of the OMR path. Every coordinate comes from
`core/bubble_sheet.build_bubble_sheet_layout` — this module only turns
normalized positions into PDF drawing calls, and deliberately computes no
geometry of its own, so the sheet that gets printed and the geometry the reader
samples can never drift apart.

What ends up on the page, and why each part is there:

* **Four corner registration squares** per page. They give the reader four
  known points, which is what lets a crooked scan (or a phone photo taken at an
  angle) be mapped back onto the layout exactly instead of approximately.
* **An identity QR** on page 1, reusing the existing `core/qr_codes` payload —
  so a personalized batch identifies the student with no handwriting to read,
  and the whole sheet needs no OCR at all.
* **Name/ID fields** anyway, because a student handed the wrong sheet, or a
  reprint, still has to be identifiable by a human.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import fitz

from opengrader.core.bubble_sheet import (
    BubbleQuestion,
    BubbleSheetConfig,
    BubbleSheetLayout,
    build_bubble_sheet_layout,
    form_key_for,
    shuffle_questions,
)
from opengrader.core.qr_codes import OpengraderCode, render_qr_png

from .constants import COLOR_BLACK, COLOR_FIELD_LABEL, LETTER_H, LETTER_W
from .draw import QR_SIZE, create_page, draw_field_box
from .fonts import ResolvedTemplateFonts, register_page_fonts, resolve_template_fonts

BUBBLE_LINE_WIDTH = 0.7
BUBBLE_LABEL_FONTSIZE = 7.5
ROW_LABEL_FONTSIZE = 9.0
TITLE_FONTSIZE = 15.0
INSTRUCTION_FONTSIZE = 8.5

# A light grey, so the instruction line reads as guidance rather than content.
COLOR_INSTRUCTION = (0.35, 0.35, 0.35)

DEFAULT_INSTRUCTION = (
    "Fill the circle completely with a dark pencil or pen. "
    "Mark only one answer per question. Do not write on the corner squares."
)


def _draw_fiducials(page: fitz.Page, layout: BubbleSheetLayout, page_number: int) -> None:
    """Stamp the corner registration squares.

    Drawn as solid filled black squares: the reader looks for the darkest
    square-ish blob in each corner, and a filled shape survives a bad scan,
    a photocopy and a fax far better than an outline would.
    """
    page_w, page_h = page.rect.width, page.rect.height
    for mark in layout.fiducials_on_page(page_number):
        half = mark.size * page_w / 2.0
        cx = mark.cx * page_w
        cy = mark.cy * page_h
        page.draw_rect(
            fitz.Rect(cx - half, cy - half, cx + half, cy + half),
            color=COLOR_BLACK,
            fill=COLOR_BLACK,
            width=0.0,
        )


def _draw_rows(
    page: fitz.Page,
    layout: BubbleSheetLayout,
    page_number: int,
    fonts: ResolvedTemplateFonts,
) -> None:
    """Draw each question's row label and its empty choice circles."""
    page_w, page_h = page.rect.width, page.rect.height
    for row in layout.rows_on_page(page_number):
        first = row.bubbles[0]
        label_right = (first.cx - first.radius) * page_w - 4.0
        baseline = first.cy * page_h + ROW_LABEL_FONTSIZE * 0.35
        text = f"{row.label}."
        width = fonts.regular_metrics.text_length(text, fontsize=ROW_LABEL_FONTSIZE)
        page.insert_text(
            fitz.Point(label_right - width, baseline),
            text,
            fontsize=ROW_LABEL_FONTSIZE,
            fontname=fonts.regular_name,
            color=COLOR_BLACK,
        )

        for bubble in row.bubbles:
            cx = bubble.cx * page_w
            cy = bubble.cy * page_h
            radius = bubble.radius * page_w
            page.draw_circle(
                fitz.Point(cx, cy),
                radius,
                color=COLOR_BLACK,
                width=BUBBLE_LINE_WIDTH,
            )
            # The choice letter goes *inside* the circle, faint enough that it
            # barely moves the measured fill of an unmarked bubble but is
            # readable by the student choosing an answer.
            letter = bubble.choice_label
            letter_width = fonts.regular_metrics.text_length(
                letter, fontsize=BUBBLE_LABEL_FONTSIZE
            )
            page.insert_text(
                fitz.Point(cx - letter_width / 2.0, cy + BUBBLE_LABEL_FONTSIZE * 0.34),
                letter,
                fontsize=BUBBLE_LABEL_FONTSIZE,
                fontname=fonts.regular_name,
                color=(0.55, 0.55, 0.55),
            )


def _draw_header(
    page: fitz.Page,
    *,
    title: str,
    instructions: str | None,
    student_name: str,
    student_id: str,
    fonts: ResolvedTemplateFonts,
) -> None:
    """Title, instruction line and the name/id fields on page 1."""
    page_w = page.rect.width
    title_width = fonts.bold_metrics.text_length(title, fontsize=TITLE_FONTSIZE)
    page.insert_text(
        fitz.Point((page_w - title_width) / 2.0, 74.0),
        title,
        fontsize=TITLE_FONTSIZE,
        fontname=fonts.bold_name,
        color=COLOR_BLACK,
    )

    text = instructions if instructions is not None else DEFAULT_INSTRUCTION
    if text:
        # Wrapped by hand into at most two centered lines: the instruction is
        # fixed-length boilerplate, so a full text-layout engine would be
        # ceremony for no gain. The width stops short of the QR block on the
        # right (and the same distance in on the left, to stay centered).
        instruction_width = page_w - 2 * (QR_SIZE + 60.0)
        for offset, line in enumerate(_wrap_centered(text, fonts, instruction_width)):
            line_width = fonts.regular_metrics.text_length(
                line, fontsize=INSTRUCTION_FONTSIZE
            )
            page.insert_text(
                fitz.Point((page_w - line_width) / 2.0, 92.0 + offset * 11.0),
                line,
                fontsize=INSTRUCTION_FONTSIZE,
                fontname=fonts.regular_name,
                color=COLOR_INSTRUCTION,
            )

    field_top = 118.0
    field_bottom = field_top + 30.0
    mid = page_w / 2.0
    for label, value, x0, x1 in (
        ("Name", student_name, 74.0, mid - 12.0),
        ("Student ID", student_id, mid + 12.0, page_w - 74.0),
    ):
        page.insert_text(
            fitz.Point(x0, field_top - 2.0),
            label,
            fontsize=8.5,
            fontname=fonts.regular_name,
            color=COLOR_FIELD_LABEL,
        )
        draw_field_box(page, x0, field_top, x1, field_bottom, value, fonts)


def _wrap_centered(
    text: str, fonts: ResolvedTemplateFonts, max_width: float
) -> list[str]:
    """Greedy word wrap for the short instruction line."""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if (
            fonts.regular_metrics.text_length(candidate, fontsize=INSTRUCTION_FONTSIZE)
            > max_width
            and current
        ):
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:2]


def _draw_qr(
    page: fitz.Page,
    *,
    student_id: str | None,
    version: str | None,
    page_number: int,
    total_pages: int,
    form_key: str | None = None,
) -> None:
    """Stamp the identity/page QR in the top-right, clear of the fiducials.

    Placed below the corner square rather than in it: the registration mark has
    to be the darkest thing in that corner for the reader to lock onto it.
    """
    code = OpengraderCode(
        student_id=student_id,
        version=version,
        page=page_number,
        total_pages=total_pages,
        form=form_key,
    )
    x1 = page.rect.width - 40.0
    x0 = x1 - QR_SIZE
    y0 = 46.0
    page.insert_image(
        fitz.Rect(x0, y0, x1, y0 + QR_SIZE), stream=render_qr_png(code)
    )


def _draw_form_label(
    page: fitz.Page, form_key: str, fonts: ResolvedTemplateFonts
) -> None:
    """Print the form key in plain text beside the QR.

    Redundant with the QR on purpose. The QR is what the pipeline reads, but a
    scrambled sheet whose code will not scan (a fold, a torn corner, a photo
    with glare) is otherwise ungradable — the order it was printed in would be
    unrecoverable. Six readable characters mean a human can always type it back
    in. It is also how an invigilator tells two forms apart at a glance.
    """
    text = f"Form {form_key}"
    width = fonts.bold_metrics.text_length(text, fontsize=9.0)
    x1 = page.rect.width - 40.0
    page.insert_text(
        fitz.Point(x1 - width, 46.0 + QR_SIZE + 11.0),
        text,
        fontsize=9.0,
        fontname=fonts.bold_name,
        color=COLOR_BLACK,
    )


def render_bubble_sheet(
    doc: fitz.Document,
    layout: BubbleSheetLayout,
    *,
    title: str,
    instructions: str | None = None,
    student_name: str = "",
    student_id: str = "",
    version: str | None = None,
    qr: bool = True,
    form_key: str | None = None,
    page_w: float = LETTER_W,
    page_h: float = LETTER_H,
    fonts: ResolvedTemplateFonts | None = None,
) -> None:
    """Append one complete answer sheet (all its pages) to ``doc``."""
    fonts = fonts or resolve_template_fonts()
    for page_number in range(1, layout.page_count + 1):
        page = create_page(doc, page_w, page_h)
        register_page_fonts(page, fonts)
        _draw_fiducials(page, layout, page_number)
        if page_number == 1:
            _draw_header(
                page,
                title=title,
                instructions=instructions,
                student_name=student_name,
                student_id=student_id,
                fonts=fonts,
            )
        else:
            continued = f"{title} (continued)"
            width = fonts.regular_metrics.text_length(continued, fontsize=10.0)
            page.insert_text(
                fitz.Point((page_w - width) / 2.0, 42.0),
                continued,
                fontsize=10.0,
                fontname=fonts.regular_name,
                color=COLOR_INSTRUCTION,
            )
        if qr:
            _draw_qr(
                page,
                student_id=student_id or None,
                version=version,
                page_number=page_number,
                total_pages=layout.page_count,
                form_key=form_key,
            )
        if form_key:
            _draw_form_label(page, form_key, fonts)
        _draw_rows(page, layout, page_number, fonts)


def generate_bubble_sheet_pdf(
    output_path: Path,
    questions: Sequence[BubbleQuestion],
    *,
    title: str,
    instructions: str | None = None,
    version: str | None = None,
    student_name: str = "",
    student_id: str = "",
    qr: bool = True,
    config: BubbleSheetConfig | None = None,
    page_w: float = LETTER_W,
    page_h: float = LETTER_H,
    font_regular_file: str | Path | None = None,
    font_bold_file: str | Path | None = None,
) -> BubbleSheetLayout:
    """Write one blank bubble sheet and return the layout it was drawn to.

    Returning the layout is deliberate: the caller needs exactly this object to
    read the sheet back, and handing it over removes any chance of rebuilding
    it from different arguments later.
    """
    layout = build_bubble_sheet_layout(
        questions, config=config, aspect=page_w / page_h
    )
    fonts = resolve_template_fonts(
        regular_file=font_regular_file, bold_file=font_bold_file
    )
    doc = fitz.Document()
    render_bubble_sheet(
        doc,
        layout,
        title=title,
        instructions=instructions,
        student_name=student_name,
        student_id=student_id,
        version=version,
        qr=qr,
        page_w=page_w,
        page_h=page_h,
        fonts=fonts,
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    doc.close()
    return layout


@dataclass(frozen=True)
class BubbleSheetForm:
    """One printed sheet in a batch: who it is for, and how it was scrambled."""

    form_key: str
    student_id: str = ""
    student_name: str = ""
    # The order this sheet's questions were printed in, as question ids. Handed
    # back so a caller can write an answer-key-per-form report if it wants one;
    # the *pipeline* never needs it, since the reader re-derives the order from
    # the form key on the sheet.
    question_order: tuple[str, ...] = ()


def _form_seed(*, assignment: str, student_id: str, index: int) -> str:
    """Seed one sheet's form key.

    A roster sheet is keyed by the **student**, so re-running the generator
    hands the same student the same form — reprint one lost sheet and it still
    matches what the reader expects. A copy-count sheet has no student, so it
    falls back to its position in the batch, which is equally stable. The
    assignment title is mixed in so two different exams do not scramble
    identically.
    """
    who = student_id.strip() or f"copy-{index}"
    return f"{assignment}|{who}"


def generate_bubble_sheet_batch(
    output_path: Path,
    questions: Sequence[BubbleQuestion],
    *,
    title: str,
    students: Sequence["RosterStudent"] | None = None,  # noqa: F821
    copies: int | None = None,
    shuffle: bool = False,
    instructions: str | None = None,
    version: str | None = None,
    config: BubbleSheetConfig | None = None,
    page_w: float = LETTER_W,
    page_h: float = LETTER_H,
    include_master_key: bool = True,
) -> tuple[BubbleSheetLayout, list[BubbleSheetForm]]:
    """Write a batch of answer sheets — one per roster student, or ``copies`` of them.

    Exactly one of ``students`` / ``copies`` decides how many sheets there are.
    They are two answers to the same question ("how many, and for whom"), and a
    roster additionally lets each sheet carry its student's id in a QR so no
    handwriting has to be read to identify it.

    With ``shuffle``, every sheet gets its own question order, derived from a
    form key printed and QR'd on the sheet — see `core/bubble_sheet`. The
    returned layout is the **unscrambled** one (the answer key's order); each
    form's own order is in its :class:`BubbleSheetForm`.
    """
    from opengrader.core.roster import assign_student_ids

    from .master_key import draw_master_key_page

    if (students is None) == (copies is None):
        raise ValueError(
            "give either a roster (one sheet per student) or a copy count, not both"
        )
    if copies is not None and copies < 1:
        raise ValueError("copy count must be at least 1")

    resolved = assign_student_ids(list(students)) if students is not None else []
    sheet_count = len(resolved) if students is not None else int(copies or 0)
    if students is not None and not resolved:
        raise ValueError("roster has no students")

    base_layout = build_bubble_sheet_layout(
        questions, config=config, aspect=page_w / page_h, assignment_title=title
    )
    fonts = resolve_template_fonts()

    doc = fitz.Document()
    # The master key names students, so it only exists for a roster batch — and
    # only that first page ever ties an id to a name, which is what lets the
    # rest of the stack be handed out anonymously.
    if include_master_key and resolved:
        entries = sorted(
            ((student.student_id, student.name) for student in resolved),
            key=lambda pair: pair[1].lower(),
        )
        draw_master_key_page(
            doc, entries, title=title, fonts=fonts, page_w=page_w, page_h=page_h
        )

    forms: list[BubbleSheetForm] = []
    for index in range(sheet_count):
        student = resolved[index] if resolved else None
        student_id = student.student_id if student else ""
        form_key = (
            form_key_for(
                _form_seed(assignment=title, student_id=student_id, index=index)
            )
            if shuffle
            else ""
        )
        sheet_questions = (
            shuffle_questions(questions, form_key) if form_key else list(questions)
        )
        sheet_layout = build_bubble_sheet_layout(
            sheet_questions,
            config=config,
            aspect=page_w / page_h,
            assignment_title=title,
        )
        render_bubble_sheet(
            doc,
            sheet_layout,
            title=title,
            instructions=instructions,
            student_name="",
            student_id=student_id,
            version=version,
            qr=True,
            form_key=form_key or None,
            page_w=page_w,
            page_h=page_h,
            fonts=fonts,
        )
        forms.append(
            BubbleSheetForm(
                form_key=form_key,
                student_id=student_id,
                student_name=student.name if student else "",
                question_order=tuple(q.question_id for q in sheet_questions),
            )
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    doc.close()
    return base_layout, forms
