"""Domain-free ``urllib`` transport shared by every Ollama client.

Both `core/llm/ollama_client.py` (text grading) and `core/ocr/ollama_vlm.py`
(vision transcription) talk to the same local Ollama server over the same
HTTP API, differing only in prompts, models, and the error type each raises.
This module holds the one wire-level implementation; each caller supplies its
own ``error_factory`` so ``OllamaError`` and ``OllamaVisionError`` stay
distinct exception types — code across the codebase catches them by name, so
they must never be unified.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from urllib import error, request

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_KEEP_ALIVE = "10m"

ErrorFactory = Callable[[str], Exception]


def parse_json_object(raw: str) -> dict | None:
    """Best-effort extraction of a single JSON object from an LLM response.

    Handles bare JSON, fenced ```json blocks, and prose wrapped around an
    object. Returns ``None`` when nothing parseable is found.

    This is the *fallback* safety net now that structured calls pass a
    ``format`` JSON schema to Ollama — replies should already be valid JSON,
    but a model can still wrap output in prose or a fence, so the salvager
    stays in the loop rather than being trusted as the primary parse path.
    """
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
    try:
        parsed = json.loads(text[first : last + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def fetch_model_names(
    *,
    host: str,
    timeout_seconds: float,
    error_factory: ErrorFactory,
) -> list[str]:
    """Return the model names installed on the Ollama server at ``host``."""
    endpoint = f"{host.rstrip('/')}/api/tags"
    req = request.Request(endpoint, method="GET")
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        raise error_factory(f"failed to reach Ollama at {endpoint}: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise error_factory(f"invalid JSON response from Ollama: {exc}") from exc

    models = parsed.get("models", []) if isinstance(parsed, dict) else []
    if not isinstance(models, list):
        raise error_factory("invalid Ollama model list response: models is not a list")

    names: list[str] = []
    for model in models:
        if isinstance(model, dict):
            name = str(model.get("name") or model.get("model") or "").strip()
            if name:
                names.append(name)
    return sorted(set(names))


def generate_raw(
    *,
    host: str,
    model: str,
    prompt: str,
    images: list[str] | None = None,
    options: dict | None = None,
    response_format: dict | None = None,
    keep_alive: str | None = None,
    num_predict: int | None = None,
    timeout_seconds: float,
    error_factory: ErrorFactory,
) -> str:
    """POST one prompt to ``/api/generate`` and return the response text.

    ``options`` overrides Ollama generation options (e.g. ``temperature``);
    ``num_predict`` is merged into it when given, capping generation length.
    ``response_format`` is passed through as Ollama's ``format`` field — a
    JSON schema that constrains the reply to structurally valid JSON.
    """
    merged_options = dict(options) if options is not None else {"temperature": 0}
    if num_predict is not None:
        merged_options["num_predict"] = num_predict

    payload: dict = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": merged_options,
    }
    if images is not None:
        payload["images"] = images
    if response_format is not None:
        payload["format"] = response_format
    if keep_alive is not None:
        payload["keep_alive"] = keep_alive

    endpoint = f"{host.rstrip('/')}/api/generate"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        raise error_factory(f"failed to call Ollama at {endpoint}: {exc}") from exc

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise error_factory(f"invalid JSON response from Ollama: {exc}") from exc

    if isinstance(parsed, dict) and parsed.get("error"):
        raise error_factory(str(parsed["error"]))
    return str(parsed.get("response", "")).strip() if isinstance(parsed, dict) else ""
