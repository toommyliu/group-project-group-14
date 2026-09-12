from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from opengrader.core.qr_codes import OpengraderCode, render_qr_png
from opengrader.core.roster import RosterStudent, assign_student_ids

from .constants import LETTER_H, LETTER_W
from .draw import (
    build_info_section,
    create_page,
    draw_continued_page_header,
    draw_qr_on_page,
    draw_questions_on_page,
)
from .fonts import register_page_fonts, resolve_template_fonts
from .master_key import draw_master_key_page

from .utils import QuestionInput, default_subquestion_label

if TYPE_CHECKING:
    import fitz


def _resolve_continued_page_header(title: str, override: str | None) -> str:
    if override is not None and override.strip():
        return override.strip()
    lines = [line.strip() for line in title.split("\n") if line.strip()]
    if lines:
        return lines[0]
    return "Quiz"


def _normalize_points(value: Any, *, context: str) -> int:
    if not isinstance(value, (int, float)):
        raise TypeError(f"{context}: points must be numeric, got {value!r}")
    return int(value)


def _normalize_choices(raw_choices: Any, *, context: str) -> list[dict[str, str]]:
    if raw_choices is None:
        return []
    if not isinstance(raw_choices, list):
        raise TypeError(
            f"{context}.choices must be a list, got {type(raw_choices).__name__}"
        )

    normalized: list[dict[str, str]] = []
    for i, raw_choice in enumerate(raw_choices):
        if not isinstance(raw_choice, dict):
            raise TypeError(
                f"{context}.choices[{i}] must be an object, got {type(raw_choice).__name__}"
            )

        label = str(raw_choice.get("label", "")).strip()
        text = str(raw_choice.get("text", "")).strip()
        if not label:
            raise ValueError(f"{context}.choices[{i}] missing 'label'")
        if not text:
            raise ValueError(f"{context}.choices[{i}] missing 'text'")

        normalized.append({"label": label, "text": text})

    return normalized


def _normalize_subquestions(
    parent_id: str,
    raw_subquestions: Any,
    seen_ids: set[str],
) -> list[dict[str, Any]]:
    if raw_subquestions is None:
        return []
    if not isinstance(raw_subquestions, list):
        raise TypeError(
            f"question[{parent_id!r}].subquestions must be a list, got {type(raw_subquestions).__name__}"
        )

    normalized: list[dict[str, Any]] = []
    for i, raw_sub in enumerate(raw_subquestions):
        default_label = default_subquestion_label(i)

        if isinstance(raw_sub, tuple):
            if len(raw_sub) != 3:
                raise ValueError(
                    f"subquestion tuples must be (id, points, prompt); got {raw_sub!r}"
                )
            sid, points, prompt = raw_sub
            label = default_label
        elif isinstance(raw_sub, dict):
            if "id" not in raw_sub:
                raise ValueError(
                    f"question[{parent_id!r}].subquestions[{i}] missing 'id'"
                )
            sid = raw_sub["id"]
            points = raw_sub.get("points", 0)
            prompt = raw_sub.get("prompt", "")
            label = raw_sub.get("label", default_label)
            choices = _normalize_choices(
                raw_sub.get("choices", []),
                context=f"question[{parent_id!r}].subquestions[{i}]",
            )
        else:
            raise TypeError(
                "subquestions entries must be a tuple or object; "
                f"got {type(raw_sub).__name__}"
            )
        if isinstance(raw_sub, tuple):
            choices = []

        sid_str = str(sid).strip()
        if not sid_str:
            raise ValueError(f"question[{parent_id!r}].subquestions[{i}]: empty 'id'")
        if sid_str in seen_ids:
            raise ValueError(f"duplicate question/subquestion id detected: {sid_str!r}")
        seen_ids.add(sid_str)

        normalized.append(
            {
                "id": sid_str,
                "label": str(label).strip() or default_label,
                "points": _normalize_points(
                    points,
                    context=f"question[{parent_id!r}].subquestions[{sid_str!r}]",
                ),
                "prompt": str(prompt).strip(),
                "choices": choices,
            }
        )

    return normalized


def _normalize_questions(questions: list[QuestionInput]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for i, raw_question in enumerate(questions):
        answer_hint = ""
        if isinstance(raw_question, tuple):
            if len(raw_question) != 3:
                raise ValueError(
                    "question tuples must be (id, points, prompt); "
                    f"got {raw_question!r}"
                )
            qid, points, prompt = raw_question
            subquestions_raw = []
            choices = []
        elif isinstance(raw_question, dict):
            if "id" not in raw_question:
                raise ValueError(f"questions[{i}] missing 'id'")
            qid = raw_question["id"]
            points = raw_question.get("points", 0)
            prompt = raw_question.get("prompt", "")
            subquestions_raw = raw_question.get("subquestions", [])
            answer_hint = str(raw_question.get("answer_hint", "")).strip()
            choices = _normalize_choices(
                raw_question.get("choices", []),
                context=f"question[{i}]",
            )
        else:
            raise TypeError(
                "questions entries must be tuples or objects; "
                f"got {type(raw_question).__name__}"
            )

        qid_str = str(qid).strip()
        if not qid_str:
            raise ValueError(f"questions[{i}]: empty 'id'")
        if qid_str in seen_ids:
            raise ValueError(f"duplicate question/subquestion id detected: {qid_str!r}")
        seen_ids.add(qid_str)

        normalized.append(
            {
                "id": qid_str,
                "points": _normalize_points(points, context=f"question[{qid_str!r}]"),
                "prompt": str(prompt).strip(),
                "choices": choices,
                "answer_hint": answer_hint,
                "subquestions": _normalize_subquestions(
                    qid_str,
                    subquestions_raw,
                    seen_ids,
                ),
            }
        )

    return normalized


def _build_template_doc(
    name: str,
    student_id: str,
    questions: list[QuestionInput],
    title: str,
    instructions: str | None,
    continued_page_header: str | None,
    page_w: float,
    page_h: float,
    fonts,
    start_questions_on_first_page: bool,
) -> fitz.Document:
    import fitz

    normalized_questions = _normalize_questions(questions)

    continued_header_text = _resolve_continued_page_header(title, continued_page_header)
    doc = fitz.Document()
    page = create_page(doc, page_w, page_h)
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

    remaining = normalized_questions[:]
    page_number = 1
    current_page = page
    current_start_y = start_y

    if not start_questions_on_first_page and remaining:
        page_number = 2
        current_page = create_page(doc, page_w, page_h)
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
        if drawn_count <= 0:
            raise ValueError("No questions could be drawn on the current page")
        remaining = remaining[drawn_count:]
        if not remaining:
            break

        page_number += 1
        current_page = create_page(doc, page_w, page_h)
        register_page_fonts(current_page, fonts)
        current_start_y = draw_continued_page_header(
            page=current_page,
            page_w=page_w,
            header_text=continued_header_text,
            fonts=fonts,
        )

    return doc


def _stamp_qr_codes(
    doc: fitz.Document,
    *,
    version: str | None,
    student_id: str | None,
    page_w: float,
) -> None:
    total = len(doc)
    for index in range(total):
        code = OpengraderCode(
            student_id=student_id,
            version=version,
            page=index + 1,
            total_pages=total,
        )
        draw_qr_on_page(doc[index], render_qr_png(code), page_w)


def generate_template_pdf(
    output_path: Path,
    version: str,
    questions: list[QuestionInput],
    title: str,
    instructions: str | None = None,
    continued_page_header: str | None = None,
    page_w: float = LETTER_W,
    page_h: float = LETTER_H,
    font_regular_file: str | Path | None = None,
    font_bold_file: str | Path | None = None,
    start_questions_on_first_page: bool = True,
    qr: bool = False,
) -> None:
    fonts = resolve_template_fonts(
        regular_file=font_regular_file, bold_file=font_bold_file
    )
    if fonts.warning:
        print(fonts.warning, file=sys.stderr)

    doc = _build_template_doc(
        "",
        "",
        questions,
        f"{title} ({version})",
        instructions,
        continued_page_header,
        page_w,
        page_h,
        fonts,
        start_questions_on_first_page,
    )
    if qr:
        _stamp_qr_codes(doc, version=version, student_id=None, page_w=page_w)

    doc.save(str(output_path), garbage=4, deflate=True)
    doc.close()


def generate_submission_pdf(
    output_path: Path,
    version: str,
    name: str,
    student_id: str,
    questions: list[QuestionInput],
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

    doc = _build_template_doc(
        name,
        student_id,
        questions,
        title,
        instructions,
        continued_page_header,
        page_w,
        page_h,
        fonts,
        start_questions_on_first_page,
    )
    doc.save(str(output_path), garbage=4, deflate=True)
    doc.close()


def generate_personalized_batch(
    output_path: Path,
    students: list[RosterStudent],
    version: str,
    questions: list[QuestionInput],
    title: str,
    instructions: str | None = None,
    continued_page_header: str | None = None,
    page_w: float = LETTER_W,
    page_h: float = LETTER_H,
    font_regular_file: str | Path | None = None,
    font_bold_file: str | Path | None = None,
    start_questions_on_first_page: bool = True,
    include_master_key: bool = True,
) -> list[RosterStudent]:
    if not students:
        raise ValueError("a personalized batch needs at least one roster student")

    import fitz

    fonts = resolve_template_fonts(
        regular_file=font_regular_file, bold_file=font_bold_file
    )
    if fonts.warning:
        print(fonts.warning, file=sys.stderr)

    assigned = assign_student_ids(students)

    combined = fitz.Document()
    if include_master_key:
        entries = sorted(
            ((s.student_id, s.name) for s in assigned),
            key=lambda pair: pair[1].lower(),
        )
        draw_master_key_page(
            combined,
            entries,
            title=title,
            fonts=fonts,
            page_w=page_w,
            page_h=page_h,
        )

    composed_title = f"{title} ({version})"
    for student in assigned:
        student_doc = _build_template_doc(
            "",
            student.student_id,
            questions,
            composed_title,
            instructions,
            continued_page_header,
            page_w,
            page_h,
            fonts,
            start_questions_on_first_page,
        )
        _stamp_qr_codes(
            student_doc, version=version, student_id=student.student_id, page_w=page_w
        )
        combined.insert_pdf(student_doc)
        student_doc.close()

    combined.save(str(output_path), garbage=4, deflate=True)
    combined.close()
    return assigned
