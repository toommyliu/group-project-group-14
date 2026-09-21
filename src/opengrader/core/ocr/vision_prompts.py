"""Read-an-exam-page knowledge: prompts, JSON schemas, budgets, parsers.

`ollama_vlm.py` used to answer two questions in the same function body — *what
does a strict-OCR prompt for a name field look like* and *how do you POST that
to Ollama* — and only the second one is Ollama's. `DialectVisionReader`
(`vision_reader.py`) needs the first one too, verbatim, and it would be wrong
for a hosted-API reader to import a module named `ollama_vlm` to get it. This
module is the first question, pulled out so both readers share one copy of it
instead of a prompt drifting apart the first time someone tunes a word in one
copy and not the other.

Each operation is a *request/parse* pair rather than a function that both
calls out and interprets the reply: `field_text_request` builds the prompt,
JSON schema and token budget; `parse_field_text` turns whatever text came
back into the value a caller wants. A provider supplies only the transport in
between — `ollama_vlm._generate_with_image` for the local server,
`dialect_client.generate` for a manifest-declared one — which is the whole
point of splitting this out: neither provider owns exam-reading domain
knowledge, so neither should have to carry a second copy of it.

Every prompt string, schema and normaliser below is unchanged from
`ollama_vlm.py` — this is a move, not an edit, and the existing tests that
assert on prompt substrings are what make that a requirement rather than a
nicety.

This module names nothing from `core.llm.ollama_*` and performs no HTTP: it is
the domain half of the split, and the domain half does not know Ollama exists.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path

from opengrader.core.llm.transport import parse_json_object

from .constants import SUBSCRIPT_TRANSLATION
from .vision_reader import FieldKind, TitleMetadataResult

__all__ = [
    "VisionRequest",
    "image_to_base64",
    "field_text_request",
    "parse_field_text",
    "title_metadata_request",
    "parse_title_metadata",
    "question_prompt_and_answer_request",
    "parse_question_prompt_and_answer",
    "handwriting_only_request",
    "parse_handwriting_only",
    "page_number_request",
    "parse_page_number",
    "identity_field_request",
    "parse_identity_field",
]


@dataclass(frozen=True)
class VisionRequest:
    """Everything one vision call needs, and nothing about how to send it."""

    prompt: str
    response_schema: dict
    num_predict: int


def image_to_base64(image_path: str | Path) -> str:
    data = Path(image_path).read_bytes()
    return base64.b64encode(data).decode("ascii")


# ---------------------------------------------------------------------------
# Normalisers — post-processing applied to whatever a model handed back.
# ---------------------------------------------------------------------------


def _normalize_name(value: str) -> str:
    text = value.strip()
    text = re.sub(r"(?i)^name\s*[:\-]\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_sid(value: str) -> str:
    digits = "".join(re.findall(r"\d", value))
    if len(digits) >= 9:
        return digits[:9]
    return digits


def _normalize_version(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.translate(SUBSCRIPT_TRANSLATION)
    text = re.sub(r"(?i)^version\s+", "", text.strip())
    text = text.strip("()[]{}:;,- ")
    text = re.sub(r"\s+", "", text)
    text = text.replace("_", "")
    if not text:
        return None
    return text.upper()


# ---------------------------------------------------------------------------
# extract_field_text
# ---------------------------------------------------------------------------

_VALUE_TEXT_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
}


def field_text_request(field_kind: FieldKind) -> VisionRequest:
    if field_kind == "name":
        prompt = (
            "You are a strict OCR transcription engine for scanned assignments. "
            "Read only visible text in the image. Extract only the student's name value. "
            "The image may include the printed label 'Name'. Do not include the label. "
            "Do not infer, complete, correct, or invent a name. "
            "If the name is unreadable or not visible, return an empty string. "
            'Return strict JSON only with this shape: {"value": "..."}.'
        )
    else:
        prompt = (
            "You are a strict OCR transcription engine for scanned assignments. "
            "Read only visible text in the image. Extract only the student ID value. "
            "The image may include label text like 'Student ID'. Do not include the label. "
            "Return only digits that are visibly present. "
            "Do not infer, complete, correct, or invent digits. "
            "If the ID is unreadable or not visible, return an empty string. "
            'Return strict JSON only with this shape: {"value": "..."}.'
        )
    return VisionRequest(prompt=prompt, response_schema=_VALUE_TEXT_SCHEMA, num_predict=128)


def parse_field_text(raw: str, *, field_kind: FieldKind) -> str:
    parsed = parse_json_object(raw)
    value = str(parsed.get("value", "")).strip() if parsed is not None else raw.strip()

    if field_kind == "name":
        return _normalize_name(value)
    return _normalize_sid(value)


# ---------------------------------------------------------------------------
# extract_title_metadata
# ---------------------------------------------------------------------------

_TITLE_METADATA_SCHEMA = {
    "type": "object",
    "properties": {
        "course_code": {"type": "string"},
        "assignment_title": {"type": "string"},
        "assignment_version": {"type": "string"},
        "raw_title": {"type": "string"},
    },
    "required": [
        "course_code",
        "assignment_title",
        "assignment_version",
        "raw_title",
    ],
}


def title_metadata_request() -> VisionRequest:
    prompt = (
        "You are a strict OCR transcription engine for scanned assignment titles. "
        "Read only visible printed title text in the image. Do not infer, complete, "
        "correct, or invent missing course, title, or version values. "
        "If a value is unreadable or absent, return an empty string for that value. "
        "Return strict JSON only with this shape: "
        '{"course_code": "", "assignment_title": "", '
        '"assignment_version": "", "raw_title": ""}. '
        "For assignment_version, preserve visible version markers such as (A), "
        "[B], Version C, suffix letters, or subscript-like letters/digits."
    )
    return VisionRequest(prompt=prompt, response_schema=_TITLE_METADATA_SCHEMA, num_predict=256)


def parse_title_metadata(raw: str) -> TitleMetadataResult:
    parsed = parse_json_object(raw)
    if parsed is None:
        cleaned = re.sub(r"\s+", " ", raw).strip()
        return TitleMetadataResult(
            course_code=None,
            assignment_title=cleaned,
            assignment_version=None,
            raw_title=cleaned,
        )

    course_code = str(parsed.get("course_code", "")).strip() or None
    assignment_title = str(parsed.get("assignment_title", "")).strip()
    raw_title = str(parsed.get("raw_title", "")).strip()
    version = _normalize_version(str(parsed.get("assignment_version", "")).strip())

    if not raw_title:
        parts = [part for part in [course_code, assignment_title, version] if part]
        raw_title = " ".join(parts)

    return TitleMetadataResult(
        course_code=course_code,
        assignment_title=(
            re.sub(r"\s+", " ", assignment_title).strip() or "Unknown Assignment"
        ),
        assignment_version=version,
        raw_title=re.sub(r"\s+", " ", raw_title).strip(),
    )


# ---------------------------------------------------------------------------
# extract_question_prompt_and_answer / extract_handwriting_only
# ---------------------------------------------------------------------------

# Answer comes first so handwriting gets the token budget before the prompt.
_QUESTION_PROMPT_AND_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "prompt": {"type": "string"},
    },
    "required": ["answer", "prompt"],
}

_HANDWRITING_SCHEMA = {
    "type": "object",
    "properties": {"text": {"type": "string"}},
    "required": ["text"],
}


def question_prompt_and_answer_request(*, math_latex: bool = False) -> VisionRequest:
    prompt = (
        "You are a strict OCR transcription engine for scanned assignments. "
        "Your job is to read visible text in the image, not to answer the assignment question. "
        "The image may contain printed question text and a student's handwritten response. "
        "Transcribe only what is visibly written on the page. Do not infer, solve, "
        "complete, correct, or generate an answer from the printed question. "
        "If handwriting is messy, preserve your best reading of the handwriting. "
        "If a word or symbol is uncertain, write [unclear]. "
        "If no handwritten answer is visible, return an empty answer string. "
    )
    if math_latex:
        prompt += (
            "If the handwritten answer contains mathematical notation (fractions, "
            "exponents, subscripts, roots, integrals, summations, matrices, Greek "
            "letters, or similar), transcribe that math as LaTeX enclosed in "
            "$...$ for inline math or $$...$$ for a displayed equation. Transcribe "
            "ordinary prose as plain text; only wrap the mathematical parts in "
            "LaTeX, and never add LaTeX to non-mathematical writing. "
        )
    prompt += (
        "Return strict JSON only with this shape: "
        '{"prompt": "...", "answer": "..."}. '
        "The prompt field should contain printed question text only. "
        "The answer field should contain the student's handwritten answer only."
    )
    return VisionRequest(
        prompt=prompt,
        response_schema=_QUESTION_PROMPT_AND_ANSWER_SCHEMA,
        num_predict=2048,
    )


def parse_question_prompt_and_answer(raw: str) -> tuple[str, str]:
    parsed = parse_json_object(raw)
    if parsed is None:
        return "", raw.strip()

    question_prompt = str(parsed.get("prompt", "")).strip()
    answer = str(parsed.get("answer", "")).strip()
    question_prompt = re.sub(r"\s+", " ", question_prompt)
    return question_prompt, answer


def handwriting_only_request(*, math_latex: bool = False) -> VisionRequest:
    """Request for a crop that has already had all printed template ink removed."""

    prompt = (
        "This is a student-ink isolation layer from a printed form. Transcribe every "
        "handwritten textual mark literally, including isolated or oddly spaced words. "
        "Template mismatch can leave fragments of crisp typeset form text or page "
        "numbers; ignore those printed remnants. Preserve line breaks and do not infer, "
        "solve, correct, or complete anything. Use [unclear] for handwriting you cannot "
        "read and [mark] for a visible handwritten non-text mark. Return an empty string "
        "only when no student-added ink is visible. "
    )
    if math_latex:
        prompt += (
            "Write visible mathematical notation as LaTeX inside $...$ or $$...$$, "
            "without changing ordinary prose. "
        )
    prompt += 'Return strict JSON only with this shape: {"text": "..."}.'
    return VisionRequest(prompt=prompt, response_schema=_HANDWRITING_SCHEMA, num_predict=1024)


def parse_handwriting_only(raw: str) -> str:
    parsed = parse_json_object(raw)
    if parsed is None or not isinstance(parsed.get("text"), str):
        return raw.strip()
    return str(parsed["text"]).strip()


# ---------------------------------------------------------------------------
# extract_page_number
# ---------------------------------------------------------------------------


def page_number_request() -> VisionRequest:
    prompt = (
        "You are a strict OCR transcription engine for scanned assignments. "
        "Read only the page number visible at the bottom of the image. "
        "Return strict JSON only with this shape: {\"value\": \"...\"} "
        "where value is the integer page number as a string. "
        "If not found, return an empty string."
    )
    return VisionRequest(prompt=prompt, response_schema=_VALUE_TEXT_SCHEMA, num_predict=128)


def parse_page_number(raw: str) -> int | None:
    parsed = parse_json_object(raw)
    if parsed is not None:
        val = parsed.get("value")
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


# ---------------------------------------------------------------------------
# page_has_identity_field
# ---------------------------------------------------------------------------

_FIRST_PAGE_SCHEMA = {
    "type": "object",
    "properties": {"first_page": {"type": "boolean"}},
    "required": ["first_page"],
}


def identity_field_request() -> VisionRequest:
    prompt = (
        "You are inspecting one scanned assignment page. Does this page contain "
        "a student Name field/label or a Student ID (student number) field/label "
        "near the top — i.e. is it the first or cover page of a submission? "
        "Return strict JSON only with this shape: {\"first_page\": true} or "
        "{\"first_page\": false}."
    )
    return VisionRequest(prompt=prompt, response_schema=_FIRST_PAGE_SCHEMA, num_predict=128)


def parse_identity_field(raw: str) -> bool:
    parsed = parse_json_object(raw)
    if parsed is not None:
        value = parsed.get("first_page")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "1"}
    return False
