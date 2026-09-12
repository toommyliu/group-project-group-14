from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from opengrader.core.llm import ollama_transport
from opengrader.core.llm.ollama_transport import parse_json_object as _parse_json_from_response

from .constants import SUBSCRIPT_TRANSLATION


@dataclass(frozen=True)
class OllamaVisionConfig:
    model: str = "qwen2.5vl:7b"
    host: str = ollama_transport.DEFAULT_HOST
    timeout_seconds: float = ollama_transport.DEFAULT_TIMEOUT_SECONDS
    keep_alive: str = ollama_transport.DEFAULT_KEEP_ALIVE
    num_predict: int = 256


class OllamaVisionError(RuntimeError):
    pass


_AVAILABLE_MODELS: set[tuple[str, str]] = set()


@dataclass(frozen=True)
class TitleMetadataResult:
    course_code: str | None
    assignment_title: str
    assignment_version: str | None
    raw_title: str


def _image_to_base64(image_path: str | Path) -> str:
    data = Path(image_path).read_bytes()
    return base64.b64encode(data).decode("ascii")


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


def list_available_models(config: OllamaVisionConfig) -> list[str]:
    return ollama_transport.fetch_model_names(
        host=config.host,
        timeout_seconds=config.timeout_seconds,
        error_factory=OllamaVisionError,
    )


def ensure_model_available(config: OllamaVisionConfig) -> None:
    cache_key = (config.host.rstrip("/"), config.model)
    if cache_key in _AVAILABLE_MODELS:
        return

    available = list_available_models(config)
    if config.model in available:
        _AVAILABLE_MODELS.add(cache_key)
        return

    available_text = ", ".join(available) if available else "none"
    raise OllamaVisionError(
        f"Ollama model {config.model!r} is not available at {config.host.rstrip('/')}. "
        f"Available models: {available_text}. "
        f"Install it with: ollama pull {config.model}"
    )


def _generate_with_image(
    *,
    image_path: str | Path,
    prompt: str,
    config: OllamaVisionConfig,
    response_format: dict | None = None,
    num_predict: int | None = None,
) -> str:
    ensure_model_available(config)
    encoded_image = _image_to_base64(image_path)
    return ollama_transport.generate_raw(
        host=config.host,
        model=config.model,
        prompt=prompt,
        images=[encoded_image],
        response_format=response_format,
        keep_alive=config.keep_alive,
        num_predict=num_predict if num_predict is not None else config.num_predict,
        timeout_seconds=config.timeout_seconds,
        error_factory=OllamaVisionError,
    )


_VALUE_TEXT_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
}


def extract_field_text(
    *,
    image_path: str | Path,
    field_kind: Literal["name", "sid"],
    config: OllamaVisionConfig,
) -> str:
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

    raw = _generate_with_image(
        image_path=image_path,
        prompt=prompt,
        config=config,
        response_format=_VALUE_TEXT_SCHEMA,
        num_predict=128,
    )
    parsed = _parse_json_from_response(raw)
    value = str(parsed.get("value", "")).strip() if parsed is not None else raw.strip()

    if field_kind == "name":
        return _normalize_name(value)
    return _normalize_sid(value)


def extract_student_field(
    *,
    image_path: str | Path,
    field_kind: Literal["name", "sid"],
    config: OllamaVisionConfig,
) -> str:
    return extract_field_text(
        image_path=image_path,
        field_kind=field_kind,
        config=config,
    )


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


def extract_title_metadata(
    *,
    image_path: str | Path,
    config: OllamaVisionConfig,
) -> TitleMetadataResult:
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

    raw = _generate_with_image(
        image_path=image_path,
        prompt=prompt,
        config=config,
        response_format=_TITLE_METADATA_SCHEMA,
        num_predict=256,
    )
    parsed = _parse_json_from_response(raw)
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


# Answer comes first so handwriting gets the token budget before the prompt.
_QUESTION_PROMPT_AND_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "prompt": {"type": "string"},
    },
    "required": ["answer", "prompt"],
}


def extract_question_prompt_and_answer(
    *,
    image_path: str | Path,
    config: OllamaVisionConfig,
    math_latex: bool = False,
) -> tuple[str, str]:
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

    raw = _generate_with_image(
        image_path=image_path,
        prompt=prompt,
        config=config,
        response_format=_QUESTION_PROMPT_AND_ANSWER_SCHEMA,
        num_predict=2048,
    )
    parsed = _parse_json_from_response(raw)
    if parsed is None:
        return "", raw.strip()

    question_prompt = str(parsed.get("prompt", "")).strip()
    answer = str(parsed.get("answer", "")).strip()
    question_prompt = re.sub(r"\s+", " ", question_prompt)
    return question_prompt, answer


def extract_page_number(
    *,
    image_path: str | Path,
    config: OllamaVisionConfig,
) -> int | None:
    prompt = (
        "You are a strict OCR transcription engine for scanned assignments. "
        "Read only the page number visible at the bottom of the image. "
        "Return strict JSON only with this shape: {\"value\": \"...\"} "
        "where value is the integer page number as a string. "
        "If not found, return an empty string."
    )
    raw = _generate_with_image(
        image_path=image_path,
        prompt=prompt,
        config=config,
        response_format=_VALUE_TEXT_SCHEMA,
        num_predict=128,
    )
    parsed = _parse_json_from_response(raw)
    if parsed is not None:
        val = parsed.get("value")
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


_FIRST_PAGE_SCHEMA = {
    "type": "object",
    "properties": {"first_page": {"type": "boolean"}},
    "required": ["first_page"],
}


def page_has_identity_field(
    *,
    image_path: str | Path,
    config: OllamaVisionConfig,
) -> bool:
    prompt = (
        "You are inspecting one scanned assignment page. Does this page contain "
        "a student Name field/label or a Student ID (student number) field/label "
        "near the top — i.e. is it the first or cover page of a submission? "
        "Return strict JSON only with this shape: {\"first_page\": true} or "
        "{\"first_page\": false}."
    )
    raw = _generate_with_image(
        image_path=image_path,
        prompt=prompt,
        config=config,
        response_format=_FIRST_PAGE_SCHEMA,
        num_predict=128,
    )
    parsed = _parse_json_from_response(raw)
    if parsed is not None:
        value = parsed.get("first_page")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "1"}
    return False
