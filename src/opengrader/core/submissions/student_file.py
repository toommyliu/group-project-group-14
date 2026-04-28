from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

import numpy as np
import yaml
from PIL import Image, ImageOps

from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE, QuestionType
from opengrader.core.ocr.question_classifier import classify_question_image
from opengrader.core.ocr.constants import SUBSCRIPT_TRANSLATION
from opengrader.core.ocr.extractor import (
    OcrConfig,
    _safe_image_to_data,
    _trim_question_region,
    process_submission_pdf,
)
from opengrader.core.ocr.ollama_vlm import (
    OllamaVisionConfig,
    TitleMetadataResult,
    extract_field_text,
    extract_question_prompt_and_answer,
    extract_title_metadata,
)
from opengrader.core.ocr.scanner import _find_rectangles
from opengrader.core.ocr.utils import apply_grayscale


_TITLE_COURSE_RE = re.compile(r"\b([A-Z]{2,}\s*\d{1,4}[A-Z]?)\b")
_TITLE_CANONICAL_RE = re.compile(
    r"(?i)\b(?P<course>[A-Z]{2,}\s*\d{1,4}[A-Z]?)\s*:\s*(?P<assignment>[^\n()]{1,80}?)\s*\((?P<version>[A-Z0-9])\)"
)
_TITLE_VERSION_EXPLICIT_RE = re.compile(r"(?i)\bversion\s*([A-Z0-9]+)\b")
_TITLE_VERSION_SUFFIX_RE = re.compile(
    r"(?i)\b(?:midterm|exam|quiz|final|assignment|hw|homework)\s*\d*\s*([A-Z])\b"
)
_TITLE_VERSION_BRACKET_RE = re.compile(r"[\(\[]\s*([A-Z0-9][A-Z0-9_₀-₉ₐ-ₜ]*)\s*[\)\]]")

_TOP_LEVEL_QUESTION_ID_RE = re.compile(r"^q\d+$")
_SUBMISSION_QUESTION_RE = re.compile(r"^-\s+([^:\s]+):\s*$")
_SUBMISSION_FIELD_RE = re.compile(r"^  -\s+([^:]+):\s*(.*)$")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_MAX_SUBQUESTION_COUNT = 26  # A-Z
_SUBQUESTION_MIN_WIDTH_NORM = 0.45
_SUBQUESTION_MIN_HEIGHT_NORM = 0.08
_SUBQUESTION_MAX_X_SPREAD_NORM = 0.08
_SUBQUESTION_MARKER_MAX_X_NORM = 0.30
_SUBQUESTION_MARKER_TOKEN_RE = re.compile(r"^[\(\[]?([a-z])[\)\].:]?$")
_SUBMISSION_FRONTMATTER_RE = re.compile(r"^---\s*$")
_SUBMISSION_QUESTION_RE = re.compile(r"^-\s+([A-Za-z]\w*):\s*$")
_SUBMISSION_FIELD_RE = re.compile(r"^\s{2}-\s+([^:]+):\s*(.*)$")
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
_PROMPT_POINTS_RE = re.compile(
    r"^\((\d+(?:\.\d+)?)\s*(?:points?|pts)\)\s*(.*)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AssignmentTitleMetadata:
    raw_title: str
    course_code: str | None
    assignment_title: str
    assignment_version: str | None


@dataclass(frozen=True)
class StudentSubmissionQuestion:
    question_id: str
    prompt: str
    possible_points: float | None
    response_image_path: str | None
    response_text: str
    points_awarded: float | None = None
    analysis: str = ""
    question_type: QuestionType = FREE_RESPONSE


@dataclass(frozen=True)
class StudentSubmissionDocument:
    student_name: str
    student_id: str
    course_code: str | None
    assignment_title: str
    assignment_version: str | None
    source_pdf: str
    score: str = ""
    questions: list[StudentSubmissionQuestion] = field(default_factory=list)


@dataclass(frozen=True)
class StudentSubmissionBuildResult:
    document: StudentSubmissionDocument
    markdown_path: Path
    responses_dir: Path


def _parse_submission_frontmatter(content: str) -> tuple[dict[str, object], list[str]]:
    lines = content.splitlines()
    if not lines or _SUBMISSION_FRONTMATTER_RE.match(lines[0]) is None:
        return {}, lines

    for index in range(1, len(lines)):
        if _SUBMISSION_FRONTMATTER_RE.match(lines[index]) is not None:
            raw_frontmatter = "\n".join(lines[1:index])
            parsed = yaml.safe_load(raw_frontmatter) or {}
            if not isinstance(parsed, dict):
                raise ValueError("student submission frontmatter must be a mapping")
            return parsed, lines[index + 1 :]

    return {}, lines


def _submission_question_blocks(
    lines: list[str],
) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    current_id: str | None = None
    current_lines: list[str] = []

    for line in lines:
        matched = _SUBMISSION_QUESTION_RE.match(line)
        if matched is not None:
            if current_id is not None:
                blocks.append((current_id, current_lines))
            current_id = matched.group(1)
            current_lines = []
            continue

        if current_id is not None:
            current_lines.append(line)

    if current_id is not None:
        blocks.append((current_id, current_lines))

    return blocks


def _parse_submission_question_fields(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    index = 0
    while index < len(lines):
        matched = _SUBMISSION_FIELD_RE.match(lines[index])
        if matched is None:
            index += 1
            continue

        key = matched.group(1).strip()
        raw_value = matched.group(2).rstrip()
        index += 1

        if raw_value == "|":
            value_lines: list[str] = []
            while index < len(lines):
                next_line = lines[index]
                if _SUBMISSION_FIELD_RE.match(next_line) is not None:
                    break
                if next_line.startswith("    "):
                    value_lines.append(next_line[4:])
                elif next_line.strip():
                    value_lines.append(next_line.strip())
                else:
                    value_lines.append("")
                index += 1
            fields[key] = "\n".join(value_lines).rstrip()
            continue

        fields[key] = raw_value.strip()

    return fields


def _frontmatter_str(
    frontmatter: dict[str, object],
    key: str,
    *,
    default: str = "",
) -> str:
    value = frontmatter.get(key)
    if value is None:
        return default
    return str(value)


def _frontmatter_optional_str(
    frontmatter: dict[str, object],
    key: str,
) -> str | None:
    value = frontmatter.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_optional_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _strip_prompt_points(prompt: str) -> tuple[str, float | None]:
    matched = _PROMPT_POINTS_RE.match(prompt.strip())
    if matched is None:
        return prompt.strip(), None
    return matched.group(2).strip(), float(matched.group(1))


def _parse_question_type(raw: str | None) -> QuestionType:
    normalized = (raw or "").strip().lower().replace("-", " ")
    if normalized in {"multiple choice", "multiple_choice"}:
        return MULTIPLE_CHOICE
    return FREE_RESPONSE


def _parse_response_image_path(raw: str | None, base_path: Path | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None

    matched = _MARKDOWN_IMAGE_RE.search(text)
    image_path = matched.group(1).strip() if matched is not None else text
    if not image_path:
        return None

    path = Path(image_path).expanduser()
    if path.is_absolute() or base_path is None:
        return str(path)
    return str((base_path / path).resolve())


def parse_student_submission_markdown(
    content: str,
    *,
    base_path: Path | None = None,
) -> StudentSubmissionDocument:
    frontmatter, body_lines = _parse_submission_frontmatter(content)
    questions: list[StudentSubmissionQuestion] = []

    for question_id, question_lines in _submission_question_blocks(body_lines):
        fields = _parse_submission_question_fields(question_lines)
        prompt_raw = fields.get("Prompt", "")
        possible_points = _parse_optional_float(fields.get("Possible points"))
        prompt = prompt_raw.strip()
        if possible_points is None:
            stripped_prompt, inline_points = _strip_prompt_points(prompt)
            prompt = stripped_prompt
            possible_points = inline_points

        questions.append(
            StudentSubmissionQuestion(
                question_id=question_id,
                prompt=prompt,
                possible_points=possible_points,
                response_image_path=_parse_response_image_path(
                    fields.get("Response image"),
                    base_path,
                ),
                response_text=fields.get("Response text", "").strip(),
                points_awarded=_parse_optional_float(fields.get("Points")),
                analysis=fields.get("Analysis", "").strip(),
                question_type=_parse_question_type(fields.get("Question type")),
            )
        )

    return StudentSubmissionDocument(
        student_name=_frontmatter_str(frontmatter, "name"),
        student_id=_frontmatter_str(frontmatter, "student_id"),
        course_code=_frontmatter_optional_str(frontmatter, "course_code"),
        assignment_title=_frontmatter_str(frontmatter, "assignment_title"),
        assignment_version=_frontmatter_optional_str(frontmatter, "assignment_version"),
        source_pdf=_frontmatter_str(frontmatter, "source_pdf"),
        score=_frontmatter_str(frontmatter, "score"),
        questions=questions,
    )


def load_student_submission_markdown(path: str | Path) -> StudentSubmissionDocument:
    submission_path = Path(path)
    return parse_student_submission_markdown(
        submission_path.read_text(),
        base_path=submission_path.parent,
    )


def parse_assignment_metadata_from_title(raw_title: str) -> AssignmentTitleMetadata:
    normalized = "\n".join(
        line.strip() for line in raw_title.splitlines() if line.strip()
    )

    canonical_match = _TITLE_CANONICAL_RE.search(normalized)
    if canonical_match is not None:
        course_code = canonical_match.group("course").replace(" ", "")
        assignment_title = re.sub(
            r"\s+", " ", canonical_match.group("assignment")
        ).strip()
        assignment_version = _normalize_assignment_version(
            canonical_match.group("version")
        )
        return AssignmentTitleMetadata(
            raw_title=normalized,
            course_code=course_code,
            assignment_title=assignment_title,
            assignment_version=assignment_version,
        )

    first_line = ""
    if normalized:
        first_line = normalized.splitlines()[0]

    course_code: str | None = None
    matched_course = _TITLE_COURSE_RE.search(first_line)
    if matched_course is not None:
        course_code = matched_course.group(1).replace(" ", "")

    assignment_line = first_line
    if ":" in assignment_line:
        left, right = assignment_line.split(":", 1)
        if _TITLE_COURSE_RE.search(left):
            assignment_line = right.strip()

    matched_explicit_version = _TITLE_VERSION_EXPLICIT_RE.search(normalized)
    if matched_explicit_version is not None:
        assignment_version = _normalize_assignment_version(
            matched_explicit_version.group(1)
        )
    else:
        matched_bracket_version = _TITLE_VERSION_BRACKET_RE.search(assignment_line)
        if matched_bracket_version is not None:
            assignment_version = _normalize_assignment_version(
                matched_bracket_version.group(1)
            )
        else:
            matched_suffix_version = _TITLE_VERSION_SUFFIX_RE.search(assignment_line)
            assignment_version = (
                _normalize_assignment_version(matched_suffix_version.group(1))
                if matched_suffix_version is not None
                else None
            )

    assignment_title = assignment_line.strip() or "Unknown Assignment"
    assignment_title = re.sub(
        r"(?i)\s*for\s+version\s+[A-Z0-9]+\s*$", "", assignment_title
    ).strip()

    return AssignmentTitleMetadata(
        raw_title=normalized,
        course_code=course_code,
        assignment_title=assignment_title,
        assignment_version=assignment_version,
    )


def _iter_field_crop_candidates(crop_path: Path) -> list[Path]:
    return [crop_path] if crop_path.exists() else []


def _score_ollama_field_value(value: str, field_kind: Literal["name", "sid"]) -> float:
    text = value.strip()
    if not text:
        return -1.0

    if field_kind == "sid":
        digits = re.sub(r"\D", "", text)
        score = float(len(digits) * 10)
        if len(digits) == 9:
            score += 60.0
        elif 7 <= len(digits) <= 10:
            score += 20.0
        else:
            score -= 30.0
        return score

    lowered = text.lower()
    letters = len(re.findall(r"[a-z]", lowered))
    parts = [part for part in re.split(r"\s+", text) if part]
    score = float(letters * 2)
    if 2 <= len(parts) <= 4:
        score += 14.0
    elif len(parts) == 1:
        score += 4.0
    elif len(parts) > 5:
        score -= 12.0
    if any(token in lowered for token in ["name", "student id", "student"]):
        score -= 20.0
    return score


def _extract_best_ollama_field_text(
    *,
    crop_path: Path,
    field_kind: Literal["name", "sid"],
    config: OllamaVisionConfig,
    fallback_value: str,
) -> str:
    best_value = fallback_value.strip()
    best_score = _score_ollama_field_value(best_value, field_kind)

    for candidate_path in _iter_field_crop_candidates(crop_path):
        try:
            extracted = extract_field_text(
                image_path=candidate_path,
                field_kind=field_kind,
                config=config,
            ).strip()
        except Exception:
            continue

        candidate_score = _score_ollama_field_value(extracted, field_kind)
        if candidate_score > best_score:
            best_value = extracted
            best_score = candidate_score

        if field_kind == "sid" and len(re.sub(r"\D", "", extracted)) == 9:
            break
        if field_kind == "name" and candidate_score >= 24.0:
            break

    return best_value


def _emit_progress(
    callback: Callable[[str], None] | None,
    message: str,
) -> None:
    if callback is not None:
        callback(message)


def _bbox_norm_to_px(
    bbox_norm: list[float],
    image_w: int,
    image_h: int,
) -> tuple[int, int, int, int]:
    x, y, w, h = bbox_norm
    x0 = max(0, min(image_w - 1, int(round(x * image_w))))
    y0 = max(0, min(image_h - 1, int(round(y * image_h))))
    x1 = max(x0 + 1, min(image_w, int(round((x + w) * image_w))))
    y1 = max(y0 + 1, min(image_h, int(round((y + h) * image_h))))
    return x0, y0, x1, y1


def _bbox_iou_norm(a: list[float], b: list[float]) -> float:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ax1, ay1 = ax0 + aw, ay0 + ah
    bx1, by1 = bx0 + bw, by0 + bh

    ix0 = max(ax0, bx0)
    iy0 = max(ay0, by0)
    ix1 = min(ax1, bx1)
    iy1 = min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0

    inter = (ix1 - ix0) * (iy1 - iy0)
    union = (aw * ah) + (bw * bh) - inter
    if union <= 0:
        return 0.0
    return inter / union


def _bbox_contains_norm(
    container: list[float],
    inner: list[float],
    *,
    tolerance: float = 0.01,
) -> bool:
    cx, cy, cw, ch = container
    ix, iy, iw, ih = inner
    return (
        cx <= ix + tolerance
        and cy <= iy + tolerance
        and (cx + cw) >= (ix + iw) - tolerance
        and (cy + ch) >= (iy + ih) - tolerance
    )


def _dedupe_boxes_norm(boxes: list[list[float]]) -> list[list[float]]:
    deduped: list[list[float]] = []
    for box in boxes:
        if any(_bbox_iou_norm(box, existing) >= 0.85 for existing in deduped):
            continue
        deduped.append(box)
    return deduped


def _detect_subquestion_boxes_norm(question_image: Image.Image) -> list[list[float]]:
    arr = np.array(question_image)
    gray = apply_grayscale(arr)
    page_h, page_w = gray.shape[:2]
    if page_h <= 0 or page_w <= 0:
        return []

    page_area = page_h * page_w
    min_area = int(page_area * 0.008)
    max_area = int(page_area * 0.95)
    rectangles = _find_rectangles(gray, min_area=min_area, max_area=max_area)

    candidate_boxes: list[list[float]] = []
    for x_px, y_px, w_px, h_px in rectangles:
        x = x_px / page_w
        y = y_px / page_h
        w = w_px / page_w
        h = h_px / page_h

        if w < _SUBQUESTION_MIN_WIDTH_NORM:
            continue
        if h < _SUBQUESTION_MIN_HEIGHT_NORM:
            continue
        if y < 0.06:
            continue
        if y + h > 0.995:
            continue
        candidate_boxes.append([x, y, w, h])

    if len(candidate_boxes) < 2:
        return []

    candidate_boxes.sort(key=lambda box: (box[1], box[0]))
    candidate_boxes = _dedupe_boxes_norm(candidate_boxes)
    if len(candidate_boxes) < 2:
        return []

    pruned: list[list[float]] = []
    for i, box in enumerate(candidate_boxes):
        contains_count = 0
        for j, other in enumerate(candidate_boxes):
            if i == j:
                continue
            if _bbox_contains_norm(box, other):
                contains_count += 1
        if contains_count >= 2:
            continue
        pruned.append(box)

    if len(pruned) < 2:
        return []

    lefts = [box[0] for box in pruned]
    if (max(lefts) - min(lefts)) > _SUBQUESTION_MAX_X_SPREAD_NORM:
        return []

    for idx in range(len(pruned) - 1):
        y = pruned[idx][1]
        h = pruned[idx][3]
        next_y = pruned[idx + 1][1]
        if next_y - (y + h) < -0.03:
            return []

    return pruned[:_MAX_SUBQUESTION_COUNT]


def _parse_subquestion_marker_token(token: str) -> str | None:
    normalized = token.strip().lower()
    if not normalized:
        return None

    direct = _SUBQUESTION_MARKER_TOKEN_RE.fullmatch(normalized)
    if direct is not None:
        return direct.group(1)

    compact = re.sub(r"[^a-z()\[\].:]", "", normalized)
    compact_match = _SUBQUESTION_MARKER_TOKEN_RE.fullmatch(compact)
    if compact_match is not None:
        return compact_match.group(1)

    return None


def _detect_subquestion_markers_ocr(
    question_image: Image.Image,
) -> list[tuple[str, int, int]]:
    data = _safe_image_to_data(question_image, lang="eng", config="--psm 6")
    n = len(data.get("text", []))

    lines: dict[tuple[int, int, int], list[tuple[str, int, int, int]]] = {}
    for i in range(n):
        text = str(data["text"][i]).strip()
        if not text:
            continue

        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        if conf <= 0:
            continue

        line_id = (
            int(data.get("block_num", [0] * n)[i]),
            int(data.get("par_num", [0] * n)[i]),
            int(data.get("line_num", [0] * n)[i]),
        )
        lines.setdefault(line_id, []).append(
            (
                text,
                int(data["left"][i]),
                int(data["top"][i]),
                int(data["height"][i]),
            )
        )

    if not lines:
        return []

    marker_candidates: list[tuple[str, int, int]] = []
    for line_words in lines.values():
        if not line_words:
            continue
        line_words.sort(key=lambda word: word[1])

        first_text, first_left, first_top, first_height = line_words[0]
        marker = _parse_subquestion_marker_token(first_text)

        if marker is None and len(line_words) >= 2:
            second_text = line_words[1][0].strip()
            if re.fullmatch(r"[A-Za-z]", first_text.strip()) and second_text in {
                ")",
                ".",
                ":",
            }:
                marker = first_text.strip().lower()

        if marker is None:
            continue

        left_norm = first_left / max(1, question_image.width)
        if left_norm > _SUBQUESTION_MARKER_MAX_X_NORM:
            continue

        marker_candidates.append((marker, first_top, first_height))

    if not marker_candidates:
        return []

    marker_by_label: dict[str, tuple[int, int]] = {}
    for label, top, height in marker_candidates:
        existing = marker_by_label.get(label)
        if existing is None or top < existing[0]:
            marker_by_label[label] = (top, height)

    ordered: list[tuple[str, int, int]] = []
    for idx in range(_MAX_SUBQUESTION_COUNT):
        label = chr(ord("a") + idx)
        marker = marker_by_label.get(label)
        if marker is None:
            break
        top, height = marker
        ordered.append((label, top, height))

    if len(ordered) < 2:
        return []

    min_gap_px = max(36, int(question_image.height * 0.035))
    for idx in range(len(ordered) - 1):
        curr_top = ordered[idx][1]
        next_top = ordered[idx + 1][1]
        if next_top - curr_top < min_gap_px:
            return []

    return ordered


def _subquestion_boxes_from_markers(
    *,
    markers: list[tuple[str, int, int]],
    image_height: int,
) -> list[list[float]]:
    if image_height <= 0 or len(markers) < 2:
        return []

    boxes: list[list[float]] = []
    min_height_px = max(80, int(image_height * 0.05))

    for idx, (_label, top, height) in enumerate(markers):
        start_margin = max(8, int(height * 0.55))
        y0 = max(0, top - start_margin)

        if idx + 1 < len(markers):
            next_top, next_height = markers[idx + 1][1], markers[idx + 1][2]
            end_margin = max(8, int(next_height * 0.55))
            y1 = max(y0 + 1, min(image_height, next_top - end_margin))
        else:
            y1 = image_height

        if y1 - y0 < min_height_px:
            return []

        boxes.append([0.0, y0 / image_height, 1.0, (y1 - y0) / image_height])

    return boxes


def _trim_response_crop(image: Image.Image, *, margin_px: int = 8) -> Image.Image:
    return _trim_question_region(image, margin_px=margin_px)


def _split_question_response_image(
    *,
    question_id: str,
    image_path: Path,
) -> list[tuple[str, Path]]:
    if not _TOP_LEVEL_QUESTION_ID_RE.fullmatch(question_id):
        return []

    with Image.open(image_path) as source_image:
        question_image = ImageOps.exif_transpose(source_image).convert("RGB")

    boxes = _detect_subquestion_boxes_norm(question_image)
    if len(boxes) < 2:
        markers = _detect_subquestion_markers_ocr(question_image)
        boxes = _subquestion_boxes_from_markers(
            markers=markers,
            image_height=question_image.height,
        )
    if len(boxes) < 2:
        return []

    outputs: list[tuple[str, Path]] = []
    for idx, box in enumerate(boxes):
        label = chr(ord("a") + idx)
        subquestion_id = f"{question_id}{label}"

        x, y, w, h = box
        expanded_x = max(0.0, x - 0.004)
        expanded_y = max(0.0, y - 0.004)
        expanded_w = min(1.0 - expanded_x, w + 0.008)
        expanded_h = min(1.0 - expanded_y, h + 0.008)
        expanded_box = [expanded_x, expanded_y, expanded_w, expanded_h]
        x0, y0, x1, y1 = _bbox_norm_to_px(
            expanded_box,
            question_image.width,
            question_image.height,
        )

        crop = question_image.crop((x0, y0, x1, y1))
        crop = _trim_response_crop(crop)

        output_path = image_path.with_name(f"{subquestion_id}{image_path.suffix}")
        crop.save(output_path)
        outputs.append((subquestion_id, output_path))

    return outputs


def _classify_question_image_path(image_path: Path) -> QuestionType:
    try:
        with Image.open(image_path) as source_image:
            image = ImageOps.exif_transpose(source_image).convert("RGB")
            return classify_question_image(image)
    except Exception:
        return FREE_RESPONSE


def _yaml_field(lines: list[str], key: str, value: str | None) -> None:
    if value is None:
        lines.append(f"{key}: null")
        return

    if "\n" in value:
        lines.append(f"{key}: |")
        for line in value.splitlines():
            lines.append(f"  {line}")
        return

    safe = value.replace('"', '\\"')
    lines.append(f'{key}: "{safe}"')


def _render_multiline_text(lines: list[str], text: str, *, indent: int) -> None:
    prefix = " " * indent
    if not text.strip():
        lines.append(f"{prefix}- Response text: ")
        return

    lines.append(f"{prefix}- Response text: |")
    for line in text.splitlines():
        lines.append(f"{prefix}  {line}")


def _format_question_type(question_type: QuestionType) -> str:
    if question_type == MULTIPLE_CHOICE:
        return "Multiple Choice"
    return "Free Response"


def render_student_submission_markdown(document: StudentSubmissionDocument) -> str:
    lines: list[str] = ["---"]
    _yaml_field(lines, "name", document.student_name)
    _yaml_field(lines, "student_id", document.student_id)
    _yaml_field(lines, "course_code", document.course_code)
    _yaml_field(lines, "assignment_title", document.assignment_title)
    _yaml_field(lines, "assignment_version", document.assignment_version)
    _yaml_field(lines, "source_pdf", document.source_pdf)
    _yaml_field(lines, "score", document.score)
    lines.append("---")
    lines.append("")
    lines.append("")

    for question in document.questions:
        points_label = (
            f"({int(question.possible_points)} points) "
            if question.possible_points is not None
            else ""
        )
        lines.append(f"- {question.question_id}:")
        lines.append(
            f"  - Question type: {_format_question_type(question.question_type)}"
        )
        lines.append(f"  - Prompt: {points_label}{question.prompt}")

        if question.response_image_path:
            lines.append(
                f"  - Response image: ![{question.question_id} response]({question.response_image_path})"
            )
        else:
            lines.append("  - Response image: ")

        _render_multiline_text(lines, question.response_text, indent=2)

        points_text = (
            str(int(question.possible_points))
            if question.possible_points is not None
            else ""
        )
        lines.append(f"  - Possible points: {points_text}")
        points_awarded_text = (
            str(question.points_awarded) if question.points_awarded is not None else ""
        )
        lines.append(f"  - Points: {points_awarded_text}")
        lines.append(f"  - Analysis: {question.analysis}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_student_submission_document(
    *,
    pdf_path: str | Path,
    output_dir: str | Path,
    ocr_config: OcrConfig,
    dpi: int = 300,
    split_subquestions: bool = True,
    ollama_config: OllamaVisionConfig | None = None,
    skip_ollama_qa: bool = False,
    save_layout_debug: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> StudentSubmissionBuildResult:
    pdf = Path(pdf_path)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    extraction_result = process_submission_pdf(
        pdf_path=pdf,
        config=ocr_config,
        output_dir=out_dir,
        response_format="png",
        dpi=dpi,
        save_layout_debug=save_layout_debug,
    )

    fields_by_id = {
        field.field_id: (field.ocr_result.text.strip() if field.ocr_result else "")
        for field in extraction_result.fields
    }
    field_crop_by_id = {
        field.field_id: Path(field.crop_path)
        for field in extraction_result.fields
        if field.crop_path
    }

    active_ollama_config = ollama_config or OllamaVisionConfig()

    for field_id, field_kind in (("student_name", "name"), ("student_id", "sid")):
        crop_path = field_crop_by_id.get(field_id)
        if crop_path is None or not crop_path.exists():
            continue
        _emit_progress(progress_callback, f"Ollama field OCR: {field_id}")
        extracted = _extract_best_ollama_field_text(
            crop_path=crop_path,
            field_kind=field_kind,
            config=active_ollama_config,
            fallback_value=fields_by_id.get(field_id, ""),
        )
        if extracted:
            fields_by_id[field_id] = extracted

    student_name = fields_by_id.get("student_name", "")
    student_id = fields_by_id.get("student_id", "")

    title_meta: AssignmentTitleMetadata | None = None
    title_crop_path = (
        Path(extraction_result.title_crop_path)
        if extraction_result.title_crop_path
        else None
    )
    if title_crop_path is not None and title_crop_path.exists():
        _emit_progress(progress_callback, "Ollama title OCR")
        try:
            title_meta = parse_assignment_metadata_from_title(
                extract_title_metadata(
                    image_path=title_crop_path,
                    config=active_ollama_config,
                )
            )
        except Exception as exc:
            _emit_progress(
                progress_callback,
                f"Ollama title OCR failed ({exc})",
            )

    if title_meta is None:
        title_meta = parse_assignment_metadata_from_title("")

    questions: list[StudentSubmissionQuestion] = []
    question_jobs: list[tuple[str, Path | None, QuestionType]] = []

    for extracted_question in extraction_result.questions:
        qid = extracted_question.question_id
        parent_question_type = extracted_question.question_type

        question_images: list[tuple[str, Path | None, QuestionType]] = [
            (qid, None, parent_question_type)
        ]
        if extracted_question.output_path:
            image_path = Path(extracted_question.output_path)
            if image_path.exists():
                question_images = [(qid, image_path, parent_question_type)]
                if split_subquestions:
                    try:
                        split_images = _split_question_response_image(
                            question_id=qid,
                            image_path=image_path,
                        )
                    except Exception:
                        split_images = []
                    if split_images:
                        question_images = [
                            (
                                split_qid,
                                split_image_path,
                                _classify_question_image_path(split_image_path),
                            )
                            for split_qid, split_image_path in split_images
                        ]

        question_jobs.extend(question_images)

    total_question_jobs = len(question_jobs)
    for index, (question_id, image_path, question_type) in enumerate(
        question_jobs, start=1
    ):
        response_image_path: str | None = None
        prompt_text = ""
        response_text = ""

        if image_path is not None:
            try:
                response_image_path = str(image_path.relative_to(out_dir))
            except ValueError:
                response_image_path = str(image_path)

            if not skip_ollama_qa:
                _emit_progress(
                    progress_callback,
                    f"Ollama QA [{index}/{total_question_jobs}]: {question_id}",
                )
                try:
                    prompt_text, response_text = extract_question_prompt_and_answer(
                        image_path=image_path,
                        config=active_ollama_config,
                    )
                except Exception as exc:
                    _emit_progress(
                        progress_callback,
                        f"Ollama QA [{index}/{total_question_jobs}]: {question_id} failed ({exc})",
                    )
                    prompt_text, response_text = "", ""

        questions.append(
            StudentSubmissionQuestion(
                question_id=question_id,
                prompt=prompt_text,
                possible_points=None,
                response_image_path=response_image_path,
                response_text=response_text,
                question_type=question_type,
            )
        )

    document = StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code=title_meta.course_code,
        assignment_title=title_meta.assignment_title,
        assignment_version=title_meta.assignment_version,
        source_pdf=pdf.name,
        score="",
        questions=questions,
    )

    markdown = render_student_submission_markdown(document)
    markdown_path = out_dir / f"{pdf.stem}.submission.md"
    with open(markdown_path, "w") as f:
        f.write(markdown)

    responses_dir = out_dir / "responses" / pdf.stem
    return StudentSubmissionBuildResult(
        document=document,
        markdown_path=markdown_path,
        responses_dir=responses_dir,
    )
