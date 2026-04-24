from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib import error, request

# https://docs.ollama.com/api/introduction
# TODO: https://pydantic.dev/docs/ai/overview/ if we ever support multiple providers

@dataclass(frozen=True)
class OllamaVisionConfig:
    model: str = "qwen2.5vl:7b"
    host: str = "http://127.0.0.1:11434"
    timeout_seconds: float = 120.0


class OllamaVisionError(RuntimeError):
    pass


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


def _generate_with_image(
    *,
    image_path: str | Path,
    prompt: str,
    config: OllamaVisionConfig,
) -> str:
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
            "You are OCR for scanned exams. Read the image and extract ONLY the "
            "student's name value. The image may include the label 'Name'. Return "
            "strict JSON with this shape: {\"value\": \"...\"}. Use an empty "
            "string if unreadable."
        )
    else:
        prompt = (
            "You are OCR for scanned exams. Read the image and extract ONLY the "
            "student ID value. The image may include label text like 'Student ID'. "
            "Return strict JSON with this shape: {\"value\": \"...\"}. Keep "
            "digits only when possible. Use an empty string if unreadable."
        )

    raw = _generate_with_image(image_path=image_path, prompt=prompt, config=config)
    parsed = _parse_json_from_response(raw)
    value = str(parsed.get("value", "")).strip() if parsed is not None else raw.strip()

    if field_kind == "name":
        return _normalize_name(value)
    return _normalize_sid(value)


def extract_question_prompt_and_answer(
    *,
    image_path: str | Path,
    config: OllamaVisionConfig,
) -> tuple[str, str]:
    prompt = (
        "You are OCR for scanned exams with both printed text and handwriting. "
        "Read the image and extract: (1) the printed question prompt and "
        "(2) the student's handwritten answer. Return strict JSON with this shape: "
        "{\"prompt\": \"...\", \"answer\": \"...\"}. Keep answer text faithful. "
        "Use empty strings if a field is unreadable."
    )

    raw = _generate_with_image(image_path=image_path, prompt=prompt, config=config)
    parsed = _parse_json_from_response(raw)
    if parsed is None:
        return "", raw.strip()

    question_prompt = str(parsed.get("prompt", "")).strip()
    answer = str(parsed.get("answer", "")).strip()
    question_prompt = re.sub(r"\s+", " ", question_prompt)
    return question_prompt, answer
