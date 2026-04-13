from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TypedDict

import fitz

from .generate import QuestionInput
from .utils import default_subquestion_label

# "(10 points) Question prompt" -> points=10, prompt="Question prompt"
_INLINE_POINTS_RE = re.compile(r"^\((\d+(?:\.\d+)?)\s*points?\)\s*(.*)$", re.IGNORECASE)
_FRONTMATTER_RE = re.compile(r"^---$")
_VERSION_HEADER_RE = re.compile(r"^\s*##\s+(.+?)\s*$")
_LIST_ITEM_RE = re.compile(r"^([ \t]*)-\s+(.*)$")


class Frontmatter(TypedDict, total=False):
    title: str
    instructions: str
    continued_page_header: str
    questions_start_page: int


class FrontmatterMarker(str, Enum):
    TITLE = "title"
    INSTRUCTIONS = "instructions"
    CONTINUED_PAGE_HEADER = "continued_page_header"
    QUESTIONS_START_PAGE = "questions_start_page"


@dataclass
class QASubquestion:
    id: str
    prompt: str
    points: int
    label: str | None = None
    answers: list[str] = field(default_factory=list)
    image_markdown: str | None = None


@dataclass
class QAQuestion:
    id: str
    prompt: str
    points: int
    answers: list[str] = field(default_factory=list)
    image_markdown: str | None = None
    subquestions: list[QASubquestion] = field(default_factory=list)


@dataclass
class QADocument:
    questions: list[QAQuestion]
    title: str | None = None
    instructions: str | None = None
    continued_page_header: str | None = None
    versions: list[str] = field(default_factory=list)
    version_questions: dict[str, list[QAQuestion]] = field(default_factory=dict)
    questions_start_page: int = 1


@dataclass
class _BulletItem:
    text: str
    indent: int
    line_no: int
    children: list["_BulletItem"] = field(default_factory=list)


def _indent_width(prefix: str) -> int:
    return len(prefix.expandtabs(4))


def _parse_frontmatter(lines: list[str]) -> tuple[Frontmatter, int]:
    if len(lines) < 2 or not _FRONTMATTER_RE.match(lines[0].strip()):
        return {}, 0

    i = 1
    frontmatter_lines: list[str] = []
    while i < len(lines):
        if _FRONTMATTER_RE.match(lines[i].strip()):
            i += 1
            break
        frontmatter_lines.append(lines[i])
        i += 1
    else:
        # Still haven't found a closing delimiter...
        return {}, 0

    frontmatter: Frontmatter = {}
    key: str | None = None
    value_lines: list[str] = []
    literal_block = False

    def flush_key() -> None:
        nonlocal key, value_lines, literal_block
        if key is None:
            return

        if literal_block:
            normalized = [
                line[2:] if line.startswith("  ") else line for line in value_lines
            ]
            frontmatter[key] = "\n".join(line.rstrip() for line in normalized).rstrip()
        else:
            stripped = [line.strip() for line in value_lines if line.strip()]
            if stripped and all(line.startswith("- ") for line in stripped):
                frontmatter[key] = [line[2:].strip() for line in stripped]
            else:
                frontmatter[key] = "\n".join(stripped).rstrip()

        key = None
        value_lines = []
        literal_block = False

    for raw in frontmatter_lines:
        line = raw.rstrip("\n")

        # Start of a new key must be at indentation level 0.
        if line == line.lstrip() and ":" in line:
            name, value = line.split(":", 1)
            name = name.strip()
            if name:
                flush_key()
                key = name
                value = value.strip()

                if value == "|":
                    literal_block = True
                    value_lines = []
                    continue

                if value:
                    frontmatter[key] = value
                    key = None
                    value_lines = []
                    literal_block = False
                    continue

                literal_block = False
                value_lines = []
                continue

        if key is not None:
            value_lines.append(line)

    flush_key()
    return frontmatter, i


def _render_frontmatter_field(lines: list[str], key: str, value: str) -> None:
    is_multiline = "\n" in value
    lines.append(f"{key}: {'|' if is_multiline else ''}")
    if is_multiline:
        for line in value.split("\n"):
            lines.append(f"  {line}")
    else:
        lines.append(f"  {value}")


def _render_frontmatter(document: QADocument) -> str:
    lines = ["---"]

    if document.title:
        _render_frontmatter_field(lines, FrontmatterMarker.TITLE.value, document.title)

    if document.instructions:
        _render_frontmatter_field(
            lines, FrontmatterMarker.INSTRUCTIONS.value, document.instructions
        )

    if document.continued_page_header:
        lines.append(
            f"{FrontmatterMarker.CONTINUED_PAGE_HEADER.value}: {document.continued_page_header}"
        )

    if document.questions_start_page is not None:
        lines.append(
            f"{FrontmatterMarker.QUESTIONS_START_PAGE.value}: {document.questions_start_page}"
        )

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _parse_points(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise ValueError(f"invalid points value: {raw!r}") from exc


def _strip_inline_points(prompt: str) -> tuple[str, int | None]:
    text = prompt.strip()
    matched = _INLINE_POINTS_RE.match(text)
    if matched is None:
        return text, None
    points = int(float(matched.group(1)))
    cleaned = matched.group(2).strip()
    return cleaned, points


def _normalize_section_name(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("**") and cleaned.endswith("**") and len(cleaned) >= 4:
        cleaned = cleaned[2:-2].strip()
    return cleaned.rstrip(":").strip().lower()


def _parse_version_label(header_text: str) -> str:
    label = header_text.strip()
    matched = re.match(r"(?i)^version\s+(.+)$", label)
    if matched is not None:
        label = matched.group(1).strip()
    if not label:
        raise ValueError("empty version header")
    return label


def _split_version_sections(
    lines: list[str], *, start_line: int
) -> tuple[list[tuple[str | None, list[str], int]], bool]:
    sections: list[tuple[str | None, list[str], int]] = []
    current_label: str | None = None
    current_lines: list[str] = []
    current_start = start_line
    saw_header = False

    for offset, line in enumerate(lines):
        line_no = start_line + offset
        matched = _VERSION_HEADER_RE.match(line)
        if matched is None:
            current_lines.append(line)
            continue

        saw_header = True
        if current_label is not None or current_lines:
            sections.append((current_label, current_lines, current_start))

        current_label = _parse_version_label(matched.group(1))
        current_lines = []
        current_start = line_no + 1

    if current_label is not None or current_lines:
        sections.append((current_label, current_lines, current_start))

    if not saw_header:
        return [(None, lines, start_line)], False

    cleaned: list[tuple[str | None, list[str], int]] = []
    for label, section_lines, section_start in sections:
        if label is None:
            if any(line.strip() for line in section_lines):
                raise ValueError(
                    "content found before the first version header; "
                    "start versioned QA files with '## Version X'"
                )
            continue
        cleaned.append((label, section_lines, section_start))

    return cleaned, True


def _parse_bullet_items(lines: list[str], *, start_line: int) -> list[_BulletItem]:
    roots: list[_BulletItem] = []
    stack: list[_BulletItem] = []

    for offset, raw_line in enumerate(lines):
        line_no = start_line + offset

        if not raw_line.strip():
            continue

        matched = _LIST_ITEM_RE.match(raw_line)
        if matched is not None:
            indent = _indent_width(matched.group(1))
            text = matched.group(2).strip()
            item = _BulletItem(text=text, indent=indent, line_no=line_no)

            while stack and stack[-1].indent >= indent:
                stack.pop()

            if stack:
                stack[-1].children.append(item)
            else:
                roots.append(item)

            stack.append(item)
            continue

        if not stack:
            raise ValueError(
                f"expected list item ('- ...') at line {line_no}, got: {raw_line!r}"
            )

        current_indent = _indent_width(
            raw_line[: len(raw_line) - len(raw_line.lstrip(" \t"))]
        )
        if current_indent <= stack[-1].indent:
            raise ValueError(
                f"expected list item ('- ...') at line {line_no}, got: {raw_line!r}"
            )

        continuation = raw_line.strip()
        if continuation:
            if stack[-1].text:
                stack[-1].text += "\n" + continuation
            else:
                stack[-1].text = continuation

    return roots


def _section_values(item: _BulletItem) -> list[str]:
    values: list[str] = []
    for child in item.children:
        if child.text.strip():
            values.append(child.text.strip())
    return values


def _parse_subquestion_item(
    item: _BulletItem, *, parent_id: str, index: int
) -> QASubquestion:
    prompt, inline_points = _strip_inline_points(item.text)
    if not prompt:
        raise ValueError(f"subquestion at line {item.line_no} is missing prompt text")

    answers: list[str] = []
    image_markdown: str | None = None
    unknown_nested: list[_BulletItem] = []

    for child in item.children:
        section_name = _normalize_section_name(child.text)

        if section_name in {"correct", "answer", "answers"}:
            answers.extend(_section_values(child))
            continue

        if section_name == "image":
            values = _section_values(child)
            if values:
                image_markdown = "\n".join(values)
            continue

        if section_name in {"expected", "partial", "wrong", "ungraded"}:
            continue

        unknown_nested.append(child)

    if unknown_nested:
        raise ValueError(
            f"nested subquestions are not supported (line {unknown_nested[0].line_no})"
        )

    label = default_subquestion_label(index)
    return QASubquestion(
        id=f"{parent_id}{label}",
        prompt=prompt,
        points=inline_points or 0,
        label=label,
        answers=answers,
        image_markdown=image_markdown,
    )


def _parse_question_item(item: _BulletItem, *, index: int) -> QAQuestion:
    prompt, inline_points = _strip_inline_points(item.text)
    if not prompt:
        raise ValueError(f"question at line {item.line_no} is missing prompt text")

    qid = f"q{index}"
    answers: list[str] = []
    image_markdown: str | None = None
    subquestion_items: list[_BulletItem] = []

    for child in item.children:
        section_name = _normalize_section_name(child.text)

        if section_name in {"correct", "answer", "answers"}:
            answers.extend(_section_values(child))
            continue

        if section_name == "image":
            values = _section_values(child)
            if values:
                image_markdown = "\n".join(values)
            continue

        if section_name in {"expected", "partial", "wrong", "ungraded"}:
            continue

        subquestion_items.append(child)

    if answers and subquestion_items:
        raise ValueError(
            f"question {qid!r} mixes direct answers and subquestions; choose one structure"
        )

    subquestions = [
        _parse_subquestion_item(sub, parent_id=qid, index=sub_idx)
        for sub_idx, sub in enumerate(subquestion_items)
    ]

    return QAQuestion(
        id=qid,
        prompt=prompt,
        points=inline_points or 0,
        answers=answers,
        image_markdown=image_markdown,
        subquestions=subquestions,
    )


def _parse_questions_from_bullets(
    lines: list[str], *, start_line: int
) -> list[QAQuestion]:
    items = _parse_bullet_items(lines, start_line=start_line)
    return [_parse_question_item(item, index=i + 1) for i, item in enumerate(items)]


def _parse_frontmatter_questions_start_page(frontmatter: Frontmatter) -> int:
    raw_value = frontmatter.get(FrontmatterMarker.QUESTIONS_START_PAGE, 1)
    points = _parse_points(str(raw_value))
    if points is None or points not in (1, 2):
        raise ValueError("questions_start_page must be 1 or 2")
    return points


def _validate_unique_ids(
    questions: list[QAQuestion], *, context: str | None = None
) -> None:
    seen_ids: set[str] = set()
    prefix = f"{context}: " if context else ""

    for question in questions:
        if question.id in seen_ids:
            raise ValueError(
                f"{prefix}duplicate question/subquestion id detected: {question.id!r}"
            )
        seen_ids.add(question.id)

        for subquestion in question.subquestions:
            if subquestion.id in seen_ids:
                raise ValueError(
                    f"{prefix}duplicate question/subquestion id detected: {subquestion.id!r}"
                )
            seen_ids.add(subquestion.id)


def parse_qa_markdown(content: str) -> QADocument:
    lines = content.splitlines()
    frontmatter, i = _parse_frontmatter(lines)

    title = frontmatter.get(FrontmatterMarker.TITLE)
    instructions = frontmatter.get(FrontmatterMarker.INSTRUCTIONS)
    continued_page_header = frontmatter.get(FrontmatterMarker.CONTINUED_PAGE_HEADER)
    questions_start_page = _parse_frontmatter_questions_start_page(frontmatter)

    sections, has_version_headers = _split_version_sections(lines[i:], start_line=i + 1)

    if has_version_headers:
        version_questions: dict[str, list[QAQuestion]] = {}
        versions: list[str] = []

        for label, section_lines, section_start in sections:
            if label is None:
                continue
            if label in version_questions:
                raise ValueError(f"duplicate version section: {label!r}")

            parsed = _parse_questions_from_bullets(
                section_lines, start_line=section_start
            )
            _validate_unique_ids(parsed, context=f"version {label}")
            version_questions[label] = parsed
            versions.append(label)

        if not versions:
            raise ValueError("no version sections found")

        first = versions[0]
        return QADocument(
            questions=version_questions[first],
            title=title,
            instructions=instructions,
            continued_page_header=continued_page_header,
            versions=versions,
            version_questions=version_questions,
            questions_start_page=questions_start_page,
        )

    questions = _parse_questions_from_bullets(lines[i:], start_line=i + 1)
    _validate_unique_ids(questions)

    return QADocument(
        questions=questions,
        title=title,
        instructions=instructions,
        continued_page_header=continued_page_header,
        questions_start_page=questions_start_page,
    )


def load_qa_markdown(path: str | Path) -> QADocument:
    qa_path = Path(path)
    with open(qa_path) as f:
        return parse_qa_markdown(f.read())


def _render_prompt_with_points(prompt: str, points: int) -> str:
    text = prompt.strip()
    if not text:
        return f"({points} points)"
    return f"({points} points) {text}"


def _append_bullet(lines: list[str], *, indent: int, text: str) -> None:
    stripped = text.strip()
    if not stripped:
        return

    parts = stripped.splitlines()
    lines.append(f"{' ' * indent}- {parts[0].rstrip()}")
    for continuation in parts[1:]:
        lines.append(f"{' ' * (indent + 2)}{continuation.rstrip()}")


def _render_answers(lines: list[str], answers: list[str], *, indent: int) -> None:
    if not answers:
        return

    _append_bullet(lines, indent=indent, text="**Correct**")
    for answer in answers:
        _append_bullet(lines, indent=indent + 2, text=answer)


def _render_image(lines: list[str], image_markdown: str | None, *, indent: int) -> None:
    if image_markdown is None or not image_markdown.strip():
        return

    _append_bullet(lines, indent=indent, text="**Image**")
    _append_bullet(lines, indent=indent + 2, text=image_markdown)


def _render_subquestion(
    lines: list[str], subquestion: QASubquestion, *, indent: int
) -> None:
    _append_bullet(
        lines,
        indent=indent,
        text=_render_prompt_with_points(subquestion.prompt, subquestion.points),
    )
    _render_answers(lines, subquestion.answers, indent=indent + 2)
    _render_image(lines, subquestion.image_markdown, indent=indent + 2)


def _render_question(
    lines: list[str], question: QAQuestion, *, indent: int = 0
) -> None:
    _append_bullet(
        lines,
        indent=indent,
        text=_render_prompt_with_points(question.prompt, question.points),
    )
    _render_answers(lines, question.answers, indent=indent + 2)
    _render_image(lines, question.image_markdown, indent=indent + 2)

    for subquestion in question.subquestions:
        _render_subquestion(lines, subquestion, indent=indent + 2)


def _render_questions(lines: list[str], questions: list[QAQuestion]) -> None:
    for idx, question in enumerate(questions):
        _render_question(lines, question)
        if idx < len(questions) - 1:
            lines.append("")


def render_qa_markdown(document: QADocument) -> str:
    lines: list[str] = []
    lines.append(_render_frontmatter(document))

    if document.version_questions:
        version_order = document.versions or list(document.version_questions.keys())
        for i, version in enumerate(version_order):
            lines.append(f"## Version {version}")
            lines.append("")
            _render_questions(lines, document.version_questions.get(version, []))
            if i < len(version_order) - 1:
                lines.append("")
    else:
        _render_questions(lines, document.questions)

    return "\n".join(lines).rstrip() + "\n"


def _questions_for_version(
    document: QADocument, version: str | None
) -> list[QAQuestion]:
    if not document.version_questions:
        return document.questions

    selected = version
    if selected is None:
        selected = (
            document.versions[0]
            if document.versions
            else next(iter(document.version_questions))
        )

    if selected not in document.version_questions:
        available = ", ".join(document.version_questions.keys())
        raise ValueError(
            f"version {selected!r} not found in QA file. Available versions: {available}"
        )

    return document.version_questions[selected]


def qa_to_template_questions(
    document: QADocument, version: str | None = None
) -> list[QuestionInput]:
    questions: list[QuestionInput] = []
    for question in _questions_for_version(document, version):
        if not question.subquestions:
            questions.append((question.id, question.points, question.prompt))
            continue

        questions.append(
            {
                "id": question.id,
                "points": question.points,
                "prompt": question.prompt,
                "subquestions": [
                    {
                        "id": subquestion.id,
                        "label": subquestion.label,
                        "points": subquestion.points,
                        "prompt": subquestion.prompt,
                    }
                    for subquestion in question.subquestions
                ],
            }
        )

    return questions


def _collect_question_regions(
    spec: dict[str, Any],
) -> dict[str, tuple[int, list[float]]]:
    regions: dict[str, tuple[int, list[float]]] = {}

    for page in spec.get("pages", []):
        page_number = int(page["page_number"])
        for question in page.get("questions", []):
            question_id = str(question["id"])
            bbox = question.get("prompt_bbox") or question.get("bbox")
            if isinstance(bbox, list) and len(bbox) == 4:
                regions[question_id] = (page_number, bbox)

            for subquestion in question.get("subquestions", []):
                sub_id = str(subquestion["id"])
                sub_bbox = subquestion.get("prompt_bbox") or subquestion.get("bbox")
                if isinstance(sub_bbox, list) and len(sub_bbox) == 4:
                    regions[sub_id] = (page_number, sub_bbox)

    return regions


def _crop_bbox_to_data_uri(
    doc: fitz.Document,
    page_number: int,
    bbox_norm: list[float],
    *,
    image_format: str,
    dpi: int,
) -> str:
    page = doc[page_number - 1]
    page_rect = page.rect

    x, y, w, h = bbox_norm
    clip = fitz.Rect(
        page_rect.x0 + x * page_rect.width,
        page_rect.y0 + y * page_rect.height,
        page_rect.x0 + (x + w) * page_rect.width,
        page_rect.y0 + (y + h) * page_rect.height,
    )

    pix = page.get_pixmap(clip=clip, dpi=dpi, alpha=False)

    fmt = image_format.lower()
    if fmt in {"jpg", "jpeg"}:
        encoded = pix.tobytes("jpg")
        mime = "image/jpeg"
    else:
        encoded = pix.tobytes("png")
        mime = "image/png"

    b64 = base64.b64encode(encoded).decode("ascii")
    return f"data:{mime};base64,{b64}"


def embed_prompt_snippets(
    document: QADocument,
    source_pdf: str | Path,
    spec: dict[str, Any],
    *,
    image_format: str = "png",
    dpi: int = 200,
    version: str | None = None,
) -> int:
    regions = _collect_question_regions(spec)

    questions_to_process = document.questions
    if document.version_questions:
        if version is None:
            version = document.versions[0] if document.versions else None
        if version and version in document.version_questions:
            questions_to_process = document.version_questions[version]
        elif version:
            raise ValueError(f"version {version!r} not found in QA document")

    ids: list[str] = []
    for question in questions_to_process:
        ids.append(question.id)
        ids.extend(sub.id for sub in question.subquestions)

    missing = [qid for qid in ids if qid not in regions]
    if missing:
        raise ValueError(
            "could not find bbox regions in spec for question ids: "
            + ", ".join(sorted(set(missing)))
        )

    updated = 0
    with fitz.open(str(source_pdf)) as doc:
        for question in questions_to_process:
            page_number, bbox = regions[question.id]
            uri = _crop_bbox_to_data_uri(
                doc,
                page_number,
                bbox,
                image_format=image_format,
                dpi=dpi,
            )
            question.image_markdown = f"![{question.id}]({uri})"
            updated += 1

            for subquestion in question.subquestions:
                sub_page, sub_bbox = regions[subquestion.id]
                sub_uri = _crop_bbox_to_data_uri(
                    doc,
                    sub_page,
                    sub_bbox,
                    image_format=image_format,
                    dpi=dpi,
                )
                subquestion.image_markdown = f"![{subquestion.id}]({sub_uri})"
                updated += 1

    return updated


def save_qa_markdown(document: QADocument, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(render_qa_markdown(document))


def save_spec_json(spec: dict[str, Any], path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(spec, f, indent=2)
