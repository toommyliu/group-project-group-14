from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_QUESTION_HEADER_RE = re.compile(r"^- ([A-Za-z0-9]+):\s*$")
_RESPONSE_IMAGE_RE = re.compile(r"^\s*- Response image:\s*!?\[[^\]]*\]\(([^)]+)\)\s*$")


@dataclass(frozen=True)
class StudentSubmissionSummary:
    name: str
    student_id: str
    course_code: str
    assignment_title: str
    assignment_version: str
    source_pdf: str
    score: str
    markdown_path: Path


@dataclass(frozen=True)
class StudentSubmissionQuestionEntry:
    question_id: str
    response_image_path: Path | None


@dataclass(frozen=True)
class StudentSubmissionDocument:
    summary: StudentSubmissionSummary
    questions: list[StudentSubmissionQuestionEntry] = field(default_factory=list)


def load_student_submission_summary(path: str | Path) -> StudentSubmissionSummary:
    markdown_path = Path(path)
    lines = markdown_path.read_text(encoding="utf-8").splitlines()
    frontmatter = _parse_frontmatter(lines)
    return StudentSubmissionSummary(
        name=frontmatter.get("name", ""),
        student_id=frontmatter.get("student_id", ""),
        course_code=frontmatter.get("course_code", ""),
        assignment_title=frontmatter.get("assignment_title", ""),
        assignment_version=frontmatter.get("assignment_version", ""),
        source_pdf=frontmatter.get("source_pdf", ""),
        score=frontmatter.get("score", ""),
        markdown_path=markdown_path,
    )


def load_student_submission_document(path: str | Path) -> StudentSubmissionDocument:
    markdown_path = Path(path)
    lines = markdown_path.read_text(encoding="utf-8").splitlines()
    summary = load_student_submission_summary(markdown_path)
    questions: list[StudentSubmissionQuestionEntry] = []

    current_question_id: str | None = None
    for line in lines:
        header_match = _QUESTION_HEADER_RE.match(line)
        if header_match is not None:
            current_question_id = header_match.group(1).upper()
            continue

        image_match = _RESPONSE_IMAGE_RE.match(line)
        if image_match is None or current_question_id is None:
            continue

        raw_path = image_match.group(1).strip()
        response_image_path = (markdown_path.parent / raw_path).resolve()
        questions.append(
            StudentSubmissionQuestionEntry(
                question_id=current_question_id,
                response_image_path=response_image_path,
            )
        )

    return StudentSubmissionDocument(summary=summary, questions=questions)


def _parse_frontmatter(lines: list[str]) -> dict[str, str]:
    if len(lines) < 2 or lines[0].strip() != "---":
        return {}

    frontmatter: dict[str, str] = {}
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            break
        if not stripped or ":" not in line:
            continue
        key, raw_value = line.split(":", 1)
        frontmatter[key.strip()] = _strip_quotes(raw_value.strip())
    return frontmatter


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value
