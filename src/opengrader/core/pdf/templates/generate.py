import sys
from pathlib import Path
from typing import Any

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
from .utils import default_subquestion_label

QuestionInput = tuple[str, int, str] | dict[str, Any]


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


def _normalize_points(value: Any, *, context: str) -> int:
    if not isinstance(value, (int, float)):
        raise TypeError(f"{context}: points must be numeric, got {value!r}")
    return int(value)


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
        else:
            raise TypeError(
                "subquestions entries must be a tuple or object; "
                f"got {type(raw_sub).__name__}"
            )

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
            }
        )

    return normalized


def _normalize_questions(questions: list[QuestionInput]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for i, raw_question in enumerate(questions):
        if isinstance(raw_question, tuple):
            if len(raw_question) != 3:
                raise ValueError(
                    "question tuples must be (id, points, prompt); "
                    f"got {raw_question!r}"
                )
            qid, points, prompt = raw_question
            subquestions_raw = []
        elif isinstance(raw_question, dict):
            if "id" not in raw_question:
                raise ValueError(f"questions[{i}] missing 'id'")
            qid = raw_question["id"]
            points = raw_question.get("points", 0)
            prompt = raw_question.get("prompt", "")
            subquestions_raw = raw_question.get("subquestions", [])
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
                "subquestions": _normalize_subquestions(
                    qid_str,
                    subquestions_raw,
                    seen_ids,
                ),
            }
        )

    return normalized


def generate_template_pdf(
    output_path: Path,
    version: str,
    questions: list[QuestionInput],
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

    normalized_questions = _normalize_questions(questions)

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

    remaining = normalized_questions[:]
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

    question_meta: list[dict[str, Any]] = []

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
        if drawn_count <= 0:
            raise ValueError("No questions could be drawn on the current page")
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

    normalized_questions = _normalize_questions(questions)

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

    remaining = normalized_questions[:]
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
        if drawn_count <= 0:
            raise ValueError("No questions could be drawn on the current page")
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
