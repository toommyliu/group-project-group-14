from __future__ import annotations

import base64
import json
import re
from enum import Enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

import fitz

from .generate import QuestionInput
from .utils import default_subquestion_label

_MARKER_RE = re.compile(r"^===([A-Z_]+)===$")
# "(10 points) Question prompt" -> points=10, prompt="Question prompt"
_INLINE_POINTS_RE = re.compile(r"^\((\d+(?:\.\d+)?)\s*points?\)\s*(.*)$", re.IGNORECASE)
_FRONTMATTER_RE = re.compile(r"^---$")

@dataclass
class Frontmatter(TypedDict, total=False):
    title: str
    instructions: str
    continued_page_header: str
    versions: list[str]
    questions_start_page: int

class FrontmatterMarker(str, Enum):
    TITLE = "title"
    INSTRUCTIONS = "instructions"
    CONTINUED_PAGE_HEADER = "continued_page_header"
    VERSIONS = "versions"
    QUESTIONS_START_PAGE = "questions_start_page"

class Marker(str, Enum):
    QUESTION = "QUESTION"
    POINTS = "POINTS"
    ANSWER = "ANSWER"
    IMAGE = "IMAGE"
    SUBQUESTION = "SUBQUESTION"
    END = "END"


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
    questions_start_page: int = 1

def _marker(line: str) -> str | None:
    matched = _MARKER_RE.match(line.strip())
    if matched is None:
        return None
    return matched.group(1)


def _parse_frontmatter(lines: list[str]) -> tuple[Frontmatter, int]:
    if len(lines) < 2 or not _FRONTMATTER_RE.match(lines[0].strip()):
        return {}, 0

    i = 1
    frontmatter_lines: list[str] = []
    found_end = False
    while i < len(lines):
        if _FRONTMATTER_RE.match(lines[i].strip()):
            i += 1
            found_end = True
            break
        frontmatter_lines.append(lines[i])
        i += 1

    if not found_end:
        return {}, 0

    frontmatter: Frontmatter = {}
    key: str | None = None
    value_lines: list[str] = []
    is_literal_block = False

    for line in frontmatter_lines:
        stripped = line.rstrip()

        # Check if this line starts a new key-value pair
        if ":" in stripped and not stripped.startswith(" ") and not stripped.startswith("\t"):
            if key is not None:
                value = "\n".join(value_lines).rstrip()
                if is_literal_block:
                    value = "\n".join(ln.rstrip() for ln in value_lines)
                frontmatter[key] = value

            key_part, val_part = stripped.split(":", 1)
            key = key_part.strip()
            val_part = val_part.strip()
            is_literal_block = val_part == "|"
            value_lines = []

            if val_part and not is_literal_block:
                frontmatter[key] = val_part
                key = None
        elif key is not None:
            # Continuation of the current value
            value_lines.append(stripped)

    if key is not None:
        value = "\n".join(value_lines).rstrip()
        if is_literal_block:
            value = "\n".join(ln.rstrip() for ln in value_lines)
        frontmatter[key] = value

    # For convenience, support csv format for versions too
    if "versions" in frontmatter and isinstance(frontmatter["versions"], str):
        frontmatter["versions"] = _parse_versions(frontmatter["versions"])

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
        _render_frontmatter_field(lines, FrontmatterMarker.TITLE, document.title)

    if document.instructions:
        _render_frontmatter_field(lines, FrontmatterMarker.INSTRUCTIONS, document.instructions)

    if document.continued_page_header:
        lines.append(f"{FrontmatterMarker.CONTINUED_PAGE_HEADER}: {document.continued_page_header}")

    if document.versions:
        lines.append(f"{FrontmatterMarker.VERSIONS}:")
        for version in document.versions:
            lines.append(f"  - {version}")

    if document.questions_start_page is not None:
        lines.append(f"{FrontmatterMarker.QUESTIONS_START_PAGE}: {document.questions_start_page}")

    lines.append("---")
    lines.append("")

    return "\n".join(lines)


def _read_until_next_marker(lines: list[str], start: int) -> tuple[str, int]:
    i = start
    chunk: list[str] = []
    while i < len(lines):
        if _marker(lines[i]) is not None:
            break
        chunk.append(lines[i])
        i += 1
    return "\n".join(chunk).strip(), i


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


def _parse_subquestion(lines: list[str], start: int, default_index: int) -> tuple[QASubquestion, int]:
    sub_id = ""
    label: str | None = None
    prompt = ""
    points_raw: str | None = None
    answers: list[str] = []
    image_markdown: str | None = None
    i = start

    if i < len(lines) and _marker(lines[i]) is None:
        prompt, i = _read_until_next_marker(lines, i)

    while i < len(lines):
        current_marker = _marker(lines[i])
        if current_marker is None:
            raise ValueError(
                f"expected marker inside subquestion block at line {i + 1}, got: {lines[i]!r}"
            )

        # a SUBQUESTION or END marker is encountered ends the current subquestion block
        if current_marker in {Marker.SUBQUESTION, Marker.END}:
            break

        i += 1

        body, i = _read_until_next_marker(lines, i)
        if current_marker == Marker.QUESTION:
            prompt = body
        elif current_marker == Marker.POINTS:
            points_raw = body
        elif current_marker == Marker.ANSWER:
            answers.append(body)
        elif current_marker == Marker.IMAGE:
            image_markdown = body
        else:
            raise ValueError(f"unsupported subquestion marker: ==={current_marker}===")

    prompt, inline_points = _strip_inline_points(prompt)
    if not prompt.strip():
        raise ValueError(
            "subquestion is missing prompt text; add text after ===SUBQUESTION=== "
            "or an explicit ===QUESTION=== block"
        )

    points = _parse_points(points_raw)
    if points is None:
        points = inline_points or 0

    if label is None:
        label = default_subquestion_label(default_index)

    return (
        QASubquestion(
            id=sub_id,
            prompt=prompt,
            points=points,
            label=label,
            answers=answers,
            image_markdown=image_markdown,
        ),
        i,
    )


def _parse_question(lines: list[str], start: int, default_index: int) -> tuple[QAQuestion, int]:
    qid = ""
    prompt = ""
    points_raw: str | None = None
    answers: list[str] = []
    image_markdown: str | None = None
    subquestions: list[QASubquestion] = []
    i = start
    ended = False

    if i < len(lines) and _marker(lines[i]) is None:
        prompt, i = _read_until_next_marker(lines, i)

    while i < len(lines):
        current_marker = _marker(lines[i])
        if current_marker is None:
            raise ValueError(
                f"expected marker inside question block at line {i + 1}, got: {lines[i]!r}"
            )

        i += 1
        if current_marker == Marker.END:
            ended = True
            break
        if current_marker == Marker.SUBQUESTION:
            subquestion, i = _parse_subquestion(lines, i, len(subquestions))
            subquestions.append(subquestion)
            continue

        body, i = _read_until_next_marker(lines, i)
        if current_marker == Marker.QUESTION:
            prompt = body
        elif current_marker == Marker.POINTS:
            points_raw = body
        elif current_marker == Marker.ANSWER:
            answers.append(body)
        elif current_marker == Marker.IMAGE:
            image_markdown = body
        else:
            raise ValueError(f"unsupported question marker: ==={current_marker}===")

    if not ended:
        raise ValueError(
            "question block is missing ===END===; each ===QUESTION=== must be terminated"
        )

    prompt, inline_points = _strip_inline_points(prompt)
    points = _parse_points(points_raw)
    if points is None:
        points = inline_points or 0

    if not qid:
        qid = f"q{default_index}"

    labels_seen: set[str] = set()
    for idx, sub in enumerate(subquestions):
        if sub.label is None:
            sub.label = default_subquestion_label(idx)
        if sub.label in labels_seen:
            raise ValueError(
                f"duplicate subquestion label in question {qid!r}: {sub.label!r}; "
                "labels must be unique within a question"
            )
        labels_seen.add(sub.label)

        if not sub.id:
            sub.id = f"{qid}{sub.label}"

    return (
        QAQuestion(
            id=qid,
            prompt=prompt,
            points=points,
            answers=answers,
            image_markdown=image_markdown,
            subquestions=subquestions,
        ),
        i,
    )


def _parse_versions(raw: str) -> list[str]:
    tokens = [token.strip() for token in re.split(r"[\s,]+", raw) if token.strip()]
    if not tokens:
        return []
    return list(dict.fromkeys(tokens))


def parse_qa_markdown(content: str) -> QADocument:
    lines = content.splitlines()
    frontmatter, i = _parse_frontmatter(lines)

    title = frontmatter.get(FrontmatterMarker.TITLE)
    instructions = frontmatter.get(FrontmatterMarker.INSTRUCTIONS)
    continued_page_header = frontmatter.get(FrontmatterMarker.CONTINUED_PAGE_HEADER)
    versions = frontmatter.get(FrontmatterMarker.VERSIONS, [])
    questions_start_page = int(frontmatter.get(FrontmatterMarker.QUESTIONS_START_PAGE, 1))

    if questions_start_page not in (1, 2):
        raise ValueError("questions_start_page must be 1 or 2")

    questions: list[QAQuestion] = []

    while i < len(lines):
        current_marker = _marker(lines[i])
        if current_marker is None:
            if lines[i].strip():
                raise ValueError(
                    f"expected marker at line {i + 1}, got: {lines[i]!r}"
                )
            i += 1
            continue

        i += 1
        if current_marker == Marker.QUESTION:
            question, i = _parse_question(lines, i, len(questions) + 1)
            questions.append(question)
            continue

        body, i = _read_until_next_marker(lines, i)
        if current_marker not in {Marker.QUESTION}:
            raise ValueError(f"unsupported top-level marker: ==={current_marker}===")

    seen_ids: set[str] = set()
    for question in questions:
        if question.id in seen_ids:
            raise ValueError(f"duplicate question/subquestion id detected: {question.id!r}")
        seen_ids.add(question.id)

        for subquestion in question.subquestions:
            if subquestion.id in seen_ids:
                raise ValueError(
                    f"duplicate question/subquestion id detected: {subquestion.id!r}"
                )
            seen_ids.add(subquestion.id)

    return QADocument(
        questions=questions,
        title=title,
        instructions=instructions,
        continued_page_header=continued_page_header,
        versions=versions,
        questions_start_page=questions_start_page,
    )


def load_qa_markdown(path: str | Path) -> QADocument:
    qa_path = Path(path)
    with open(qa_path) as f:
        return parse_qa_markdown(f.read())


def _append_block(lines: list[str], marker: str, body: str | None) -> None:
    if body is None:
        return
    if not body.strip():
        return
    lines.append(f"==={marker}===")
    lines.append(body.rstrip())
    lines.append("")


def _render_prompt_with_points(prompt: str, points: int) -> str:
    text = prompt.strip()
    if not text:
        return f"({points} points)"
    return f"({points} points) {text}"


def render_qa_markdown(document: QADocument) -> str:
    lines: list[str] = []

    lines.append(_render_frontmatter(document))

    for q_idx, question in enumerate(document.questions, start=1):
        lines.append(f"==={Marker.QUESTION}===")
        lines.append(_render_prompt_with_points(question.prompt, question.points))

        for answer in question.answers:
            lines.append(f"==={Marker.ANSWER}===")
            lines.append(answer)

        if question.image_markdown:
            lines.append(f"==={Marker.IMAGE}===")
            lines.append(question.image_markdown)

        for sub_idx, subquestion in enumerate(question.subquestions):
            lines.append(f"==={Marker.SUBQUESTION}===")
            lines.append(_render_prompt_with_points(subquestion.prompt, subquestion.points))

            for answer in subquestion.answers:
                lines.append(f"==={Marker.ANSWER}===")
                lines.append(answer)

            if subquestion.image_markdown:
                lines.append(f"==={Marker.IMAGE}===")
                lines.append(subquestion.image_markdown)

        lines.append(f"==={Marker.END}===")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def qa_to_template_questions(document: QADocument) -> list[QuestionInput]:
    questions: list[QuestionInput] = []
    for question in document.questions:
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


def _collect_question_regions(spec: dict[str, Any]) -> dict[str, tuple[int, list[float]]]:
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
) -> int:
    regions = _collect_question_regions(spec)

    ids: list[str] = []
    for question in document.questions:
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
        for question in document.questions:
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
