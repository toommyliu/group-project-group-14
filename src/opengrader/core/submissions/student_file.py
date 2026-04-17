from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz
from PIL import Image

from opengrader.core.ocr.extractor import (
    OcrConfig,
    process_submission_pdf,
    render_page_to_image,
    ocr_region,
)


_TITLE_COURSE_RE = re.compile(r"\b([A-Z]{2,}\s*\d{1,4}[A-Z]?)\b")
_TITLE_CANONICAL_RE = re.compile(
    r"(?i)\b(?P<course>[A-Z]{2,}\s*\d{1,4}[A-Z]?)\s*:\s*(?P<assignment>[^\n()]{1,80}?)\s*\((?P<version>[A-Z0-9])\)"
)
_TITLE_VERSION_EXPLICIT_RE = re.compile(r"(?i)\bversion\s*([A-Z0-9]+)\b")
_TITLE_VERSION_SUFFIX_RE = re.compile(
    r"(?i)\b(?:midterm|exam|quiz|final|assignment|hw|homework)\s*\d*\s*([A-Z])\b"
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
        assignment_version = canonical_match.group("version").upper()
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
        assignment_version = matched_explicit_version.group(1).upper()
    else:
        matched_suffix_version = _TITLE_VERSION_SUFFIX_RE.search(assignment_line)
        assignment_version = (
            matched_suffix_version.group(1).upper()
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


def extract_title_text_from_submission(
    pdf_path: str | Path,
    config: OcrConfig,
    dpi: int,
) -> str:
    with fitz.open(str(pdf_path)) as doc:
        if len(doc) == 0:
            return ""

        # If searchable text exists, prefer it.
        embedded_text = doc[0].get_text().strip()
        if embedded_text:
            return "\n".join(
                line.strip() for line in embedded_text.splitlines() if line.strip()
            )

        page_image, page_w, page_h = render_page_to_image(doc, page_idx=0, dpi=dpi)

    x0 = int(round(page_w * 0.05))
    y0 = int(round(page_h * 0.02))
    x1 = int(round(page_w * 0.95))
    y1 = int(round(page_h * 0.32))
    region = page_image.crop((x0, y0, x1, y1))

    primary = ocr_region(region, config).text.strip()
    secondary = ocr_region(
        region,
        OcrConfig(
            lang=config.lang,
            psm=11,
            whitelist=None,
            deskew=config.deskew,
            confidence_threshold=config.confidence_threshold,
        ),
    ).text.strip()

    candidates = [text for text in [primary, secondary] if text]
    if not candidates:
        return ""

    def _title_score(text: str) -> float:
        score = float(len(text))
        if ":" in text:
            score += 30.0
        if "(" in text and ")" in text:
            score += 25.0
        if _TITLE_COURSE_RE.search(text):
            score += 35.0
        return score

    best = max(candidates, key=_title_score)
    return best


def ocr_image_text(path: str | Path, config: OcrConfig) -> str:
    image_path = Path(path)
    with Image.open(image_path) as image:
        result = ocr_region(image, config)

    return result.text.strip()


def _general_text_ocr_config(base: OcrConfig) -> OcrConfig:
    return OcrConfig(
        lang=base.lang,
        psm=6,
        whitelist=None,
        deskew=base.deskew,
        confidence_threshold=base.confidence_threshold,
    )


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
    )

    fields_by_id = {
        field.field_id: (field.ocr_result.text.strip() if field.ocr_result else "")
        for field in extraction_result.fields
    }

    student_name = fields_by_id.get("student_name", "")
    student_id = fields_by_id.get("student_id", "")

    text_ocr_config = _general_text_ocr_config(ocr_config)

    title_text = extract_title_text_from_submission(
        pdf_path=pdf,
        config=text_ocr_config,
        dpi=dpi,
    )
    title_meta = parse_assignment_metadata_from_title(title_text)

    questions: list[StudentSubmissionQuestion] = []

    for extracted_question in extraction_result.questions:
        qid = extracted_question.question_id

        response_image_path: str | None = None
        response_text = ""
        if extracted_question.output_path:
            image_path = Path(extracted_question.output_path)
            if image_path.exists():
                try:
                    response_image_path = str(image_path.relative_to(out_dir))
                except ValueError:
                    response_image_path = str(image_path)

                response_text = ocr_image_text(image_path, text_ocr_config)

        questions.append(
            StudentSubmissionQuestion(
                question_id=qid,
                prompt="",
                possible_points=None,
                response_image_path=response_image_path,
                response_text=response_text,
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
