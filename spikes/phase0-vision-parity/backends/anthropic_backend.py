"""The `anthropic-messages` dialect sketch — POST {host}/v1/messages.

Raw `urllib` only, no `anthropic` SDK import, per AI_README.md §7.8 Q4. This
is a throwaway sketch of what Phase 3's
`core/llm/dialects/anthropic_messages.py` will contain — request/response
shape only, no retry/backoff/redaction-as-a-layer (that's the transport in
§7.3, out of scope for a spike).

Uses the same prompt text as the Ollama backend (captured verbatim from
`ollama_vlm.extract_question_prompt_and_answer`, see README.md's note on
prompt parity and the Ollama-tuning threat to validity).
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

ANTHROPIC_VERSION = "2023-06-01"


def transcribe(image_path: Path, prompt: str, config: BackendConfig) -> CallResult:
    image_bytes = image_path.read_bytes()
    media_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded = base64.b64encode(image_bytes).decode("ascii")

    payload = {
        "model": config.model,
        "max_tokens": 2048,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": encoded,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    }

    endpoint = f"{config.host.rstrip('/')}/v1/messages"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": config.api_key or "",
            "anthropic-version": ANTHROPIC_VERSION,
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
            error=scrub(f"anthropic request failed: {exc}", config.api_key),
        )

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return CallResult(
            text=None,
            input_tokens=None,
            output_tokens=None,
            error=scrub(f"invalid JSON from anthropic: {exc}", config.api_key),
        )

    if isinstance(parsed, dict) and parsed.get("type") == "error":
        message = parsed.get("error", {}).get("message", "unknown error")
        return CallResult(
            text=None,
            input_tokens=None,
            output_tokens=None,
            error=scrub(f"anthropic error: {message}", config.api_key),
        )

    content_blocks = parsed.get("content", []) if isinstance(parsed, dict) else []
    text_out = ""
    for block in content_blocks:
        if isinstance(block, dict) and block.get("type") == "text":
            text_out += str(block.get("text", ""))

    usage = parsed.get("usage", {}) if isinstance(parsed, dict) else {}

    answer_json = parse_json_object(text_out)
    answer = str(answer_json.get("answer", "")).strip() if answer_json else text_out.strip()

    return CallResult(
        text=answer,
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        raw_prompt_used=prompt,
    )
