"""The `openai-chat` dialect sketch — POST {host}/chat/completions.

Raw `urllib` only, no `openai` SDK import, per AI_README.md §7.8 Q4. Covers
the de-facto-standard shape shared by OpenAI, Azure OpenAI, and the
self-hosted servers that imitate it (vLLM, LM Studio, llama.cpp) — see
AI_README.md §7.2. A throwaway sketch for Phase 3's
`core/llm/dialects/openai_chat.py`.

Uses the same prompt text as the Ollama backend (captured verbatim from
`ollama_vlm.extract_question_prompt_and_answer`).
"""

from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from urllib import error, request

from opengrader.core.llm.ollama_transport import parse_json_object

from .base import BackendConfig, CallResult
from .redact import scrub


def transcribe(image_path: Path, prompt: str, config: BackendConfig) -> CallResult:
    image_bytes = image_path.read_bytes()
    media_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    data_uri = f"data:{media_type};base64,{encoded}"

    payload = {
        "model": config.model,
        "max_tokens": 2048,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
    }

    endpoint = f"{config.host.rstrip('/')}/chat/completions"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {config.api_key or ''}",
        },
        method="POST",
    )

    try:
        with request.urlopen(req, timeout=config.timeout_seconds) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        return CallResult(
            text=None,
            input_tokens=None,
            output_tokens=None,
            error=scrub(f"openai-chat request failed: {exc}", config.api_key),
        )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return CallResult(
            text=None,
            input_tokens=None,
            output_tokens=None,
            error=scrub(f"invalid JSON from openai-chat host: {exc}", config.api_key),
        )

    if isinstance(parsed, dict) and parsed.get("error"):
        message = parsed["error"].get("message", "unknown error") if isinstance(parsed["error"], dict) else str(parsed["error"])
        return CallResult(
            text=None,
            input_tokens=None,
            output_tokens=None,
            error=scrub(f"openai-chat error: {message}", config.api_key),
        )

    choices = parsed.get("choices", []) if isinstance(parsed, dict) else []
    text_out = ""
    if choices:
        message = choices[0].get("message", {})
        text_out = str(message.get("content", ""))

    usage = parsed.get("usage", {}) if isinstance(parsed, dict) else {}

    answer_json = parse_json_object(text_out)
    answer = str(answer_json.get("answer", "")).strip() if answer_json else text_out.strip()

    return CallResult(
        text=answer,
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        raw_prompt_used=prompt,
    )
