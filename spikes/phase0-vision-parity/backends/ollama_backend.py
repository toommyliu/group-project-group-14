"""The incumbent path: Ollama's local vision model, via the shipped code.

Per the task brief, this backend REUSES the real code rather than
reimplementing it:

  - `opengrader.core.ai_config` resolves host/model exactly as the TUI and
    every CLI do.
  - `opengrader.core.ocr.ollama_vlm.extract_question_prompt_and_answer` is
    the actual function the shipped pipeline calls to transcribe an answer
    crop — same prompt, same JSON schema, same num_predict.

The one thing the shipped wrapper does NOT give us is real token counts:
`ollama_vlm._generate_with_image` calls `ollama_transport.generate_raw`,
which parses Ollama's `/api/generate` response and returns only the
`response` string, discarding `prompt_eval_count` / `eval_count`. Per the
task brief ("call the transport directly rather than through the wrapper
that discards it"), this module does two things instead of reimplementing
the prompt:

  1. Calls `extract_question_prompt_and_answer` with `ollama_vlm`'s own
     `ensure_model_available` and `ollama_transport.generate_raw` swapped
     for capture stubs, so the exact prompt string and image payload the
     shipped code would have sent are captured with zero network traffic
     and zero prompt duplication.
  2. POSTs that captured request straight to `/api/generate` itself (same
     transport, same endpoint, same payload) and reads the full response
     body, which is the only way to see `prompt_eval_count` / `eval_count`.

This means the transcription text this backend measures could equally have
come from calling `extract_question_prompt_and_answer` directly (same
prompt, same server, same request) — the only difference is that this
module also keeps the token counts the wrapper throws away.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib import error, request

from opengrader.core.ocr import ollama_vlm

from .base import BackendConfig, CallResult


class _CaptureAbort(Exception):
    pass


def _capture_request(image_path: Path, config: BackendConfig) -> dict:
    """Run the real extract_question_prompt_and_answer prompt-building code,
    intercepting the two calls that would touch the network, and return the
    kwargs it would have sent to ollama_transport.generate_raw.
    """
    vision_config = ollama_vlm.OllamaVisionConfig(
        model=config.model,
        host=config.host,
        timeout_seconds=config.timeout_seconds,
    )

    captured: dict = {}

    def _fake_ensure_model_available(_config: object) -> None:
        return None

    def _fake_generate_raw(**kwargs: object) -> str:
        captured.update(kwargs)
        raise _CaptureAbort()

    real_ensure = ollama_vlm.ensure_model_available
    real_generate = ollama_vlm.ollama_transport.generate_raw
    ollama_vlm.ensure_model_available = _fake_ensure_model_available  # type: ignore[assignment]
    ollama_vlm.ollama_transport.generate_raw = _fake_generate_raw  # type: ignore[assignment]
    try:
        ollama_vlm.extract_question_prompt_and_answer(
            image_path=image_path,
            config=vision_config,
        )
    except _CaptureAbort:
        pass
    finally:
        ollama_vlm.ensure_model_available = real_ensure  # type: ignore[assignment]
        ollama_vlm.ollama_transport.generate_raw = real_generate  # type: ignore[assignment]

    return captured


def capture_prompt(image_path: Path, config: BackendConfig) -> str:
    """Expose the exact prompt string the shipped Ollama path uses, for reuse
    by the other two backends so the comparison is prompt-for-prompt identical.
    """
    return str(_capture_request(image_path, config)["prompt"])


def transcribe(image_path: Path, prompt: str, config: BackendConfig) -> CallResult:
    """Send the crop to Ollama's /api/generate directly, capturing token counts.

    `prompt` is accepted for interface symmetry with the other two backends
    but is ignored in favour of re-capturing the shipped prompt fresh from
    `ollama_vlm`, since that capture also yields the correctly base64-encoded
    image payload the real pipeline would send.
    """
    captured = _capture_request(image_path, config)

    payload = {
        "model": captured["model"],
        "prompt": captured["prompt"],
        "stream": False,
        "images": captured.get("images"),
        "options": {"temperature": 0, "num_predict": captured.get("num_predict")},
    }
    if captured.get("response_format") is not None:
        payload["format"] = captured["response_format"]
    if captured.get("keep_alive") is not None:
        payload["keep_alive"] = captured["keep_alive"]

    endpoint = f"{config.host.rstrip('/')}/api/generate"
    body = json.dumps(payload).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=config.timeout_seconds) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except error.URLError as exc:
        return CallResult(text=None, input_tokens=None, output_tokens=None, error=f"ollama request failed: {exc}")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return CallResult(text=None, input_tokens=None, output_tokens=None, error=f"invalid JSON from ollama: {exc}")

    if isinstance(parsed, dict) and parsed.get("error"):
        return CallResult(text=None, input_tokens=None, output_tokens=None, error=str(parsed["error"]))

    response_text = str(parsed.get("response", "")).strip() if isinstance(parsed, dict) else ""
    parsed_json = ollama_vlm._parse_json_from_response(response_text)
    answer = str(parsed_json.get("answer", "")).strip() if parsed_json else response_text

    return CallResult(
        text=answer,
        input_tokens=parsed.get("prompt_eval_count") if isinstance(parsed, dict) else None,
        output_tokens=parsed.get("eval_count") if isinstance(parsed, dict) else None,
        raw_prompt_used=captured["prompt"],
    )
