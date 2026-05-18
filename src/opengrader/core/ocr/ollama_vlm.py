from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib import error, request

from .constants import SUBSCRIPT_TRANSLATION

# https://docs.ollama.com/api/introduction
# TODO: https://pydantic.dev/docs/ai/overview/ if we ever support multiple providers


@dataclass(frozen=True)
class OllamaVisionConfig:
    model: str = "qwen2.5vl:7b"
    host: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 120.0


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


def _parse_json_from_response(raw: str) -> dict | None:
    text = raw.strip()
    if not text:
        return None

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return None

    candidate = text[first : last + 1]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


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
    host = config.host.rstrip("/")
    endpoint = f"{host}/api/tags"
    req = request.Request(endpoint, method="GET")

    try:
        with request.urlopen(req, timeout=config.timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        raise OllamaVisionError(f"failed to reach Ollama at {endpoint}: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaVisionError(f"invalid JSON response from Ollama: {exc}") from exc

    models = parsed.get("models", [])
    if not isinstance(models, list):
        raise OllamaVisionError(
            "invalid Ollama model list response: models is not a list"
        )

    names: list[str] = []
    for model in models:
        if not isinstance(model, dict):
            continue
        name = str(model.get("name") or model.get("model") or "").strip()
        if name:
            names.append(name)
    return sorted(set(names))


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
) -> str:
    ensure_model_available(config)
    encoded_image = _image_to_base64(image_path)
    payload = {
        "model": config.model,
        "prompt": prompt,
        "images": [encoded_image],
        "stream": False,
        "options": {"temperature": 0},
    }

    host = config.host.rstrip("/")
    endpoint = f"{host}/api/generate"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=config.timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        raise OllamaVisionError(f"failed to call Ollama at {endpoint}: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise OllamaVisionError(f"invalid JSON response from Ollama: {exc}") from exc

    return str(parsed.get("response", "")).strip()


def extract_field_text(
    *,
    image_path: str | Path,
    field_kind: Literal["name", "sid"],
    config: OllamaVisionConfig,
) -> str:
    if field_kind == "name":
        prompt = (
            "You are a strict OCR transcription engine for scanned exams. "
            "Read only visible text in the image. Extract only the student's name value. "
            "The image may include the printed label 'Name'. Do not include the label. "
            "Do not infer, complete, correct, or invent a name. "
            "If the name is unreadable or not visible, return an empty string. "
            'Return strict JSON only with this shape: {"value": "..."}.'
        )
    else:
        prompt = (
            "You are a strict OCR transcription engine for scanned exams. "
            "Read only visible text in the image. Extract only the student ID value. "
            "The image may include label text like 'Student ID'. Do not include the label. "
            "Return only digits that are visibly present. "
            "Do not infer, complete, correct, or invent digits. "
            "If the ID is unreadable or not visible, return an empty string. "
            'Return strict JSON only with this shape: {"value": "..."}.'
        )

    raw = _generate_with_image(image_path=image_path, prompt=prompt, config=config)
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

    raw = _generate_with_image(image_path=image_path, prompt=prompt, config=config)
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


def extract_question_prompt_and_answer(
    *,
    image_path: str | Path,
    config: OllamaVisionConfig,
) -> tuple[str, str]:
    prompt = (
        "You are a strict OCR transcription engine for scanned exams. "
        "Your job is to read visible text in the image, not to answer the exam question. "
        "The image may contain printed question text and a student's handwritten response. "
        "Transcribe only what is visibly written on the page. Do not infer, solve, "
        "complete, correct, or generate an answer from the printed question. "
        "If handwriting is messy, preserve your best reading of the handwriting. "
        "If a word or symbol is uncertain, write [unclear]. "
        "If no handwritten answer is visible, return an empty answer string. "
        "Return strict JSON only with this shape: "
        '{"prompt": "...", "answer": "..."}. '
        "The prompt field should contain printed question text only. "
        "The answer field should contain the student's handwritten answer only."
    )

    raw = _generate_with_image(image_path=image_path, prompt=prompt, config=config)
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
        "You are a strict OCR transcription engine for scanned exams. "
        "Read only the page number visible at the bottom of the image. "
        "Return strict JSON only with this shape: {\"value\": \"...\"} "
        "where value is the integer page number. If not found, return null."
    )
    raw = _generate_with_image(image_path=image_path, prompt=prompt, config=config)
    parsed = _parse_json_from_response(raw)
    if parsed is not None:
        val = parsed.get("value")
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None
