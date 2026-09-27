"""The de-facto standard shape — `POST /chat/completions`.

OpenAI defined it; Azure OpenAI, Groq, Together, Fireworks, DeepSeek,
OpenRouter, vLLM, LM Studio and llama.cpp's server all clone it closely enough
that one dialect covers them. The manifest `host` is the API *base* a person
pastes in (`https://api.openai.com/v1`, `http://localhost:8000/v1`, ...) —
this module never appends `/v1` itself, because a base that already carries it
is the common case and a second one would double it.

What this shape does not cover: streaming (the transport always sends
`stream: false` and reads one response), function/tool calling, and anything
Azure-specific like `api-version` query parameters or deployment-named URLs —
an Azure endpoint that needs those is a `host` string away from this dialect,
not a reason to fork it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .base import join_url, media_type_for

__all__ = ["OpenAIChatDialect", "DIALECT"]

# Most OpenAI-compatible servers accept the modern `json_schema` response
# format; the handful that only understand the older `json_object` form (no
# schema, just "you must emit valid JSON") reject a request built this way
# with a 4xx. We do not probe for that ahead of time or send both shapes in
# one body — that is a second thing to keep in sync for zero services that
# need it. Send the shape the schema is *for*; a server that cannot take it
# fails through `transport.send_json`'s ordinary error path, and the caller
# above `dialect_client` already falls through to Tesseract/OCR on an LLM
# failure, which is the invariant this tree holds instead of a compatibility
# matrix.
_RESPONSE_FORMAT_KIND = "json_schema"


@dataclass(frozen=True)
class OpenAIChatDialect:
    name: str = "openai-chat"

    def generate_endpoint(self, host: str) -> str:
        return join_url(host, "/chat/completions")

    def models_endpoint(self, host: str) -> str | None:
        return join_url(host, "/models")

    def auth_headers(self, api_key: str | None) -> dict[str, str]:
        if not api_key:
            return {}
        return {"Authorization": f"Bearer {api_key}"}

    def build_body(
        self,
        *,
        model: str,
        prompt: str,
        system_prompt: str | None = None,
        images: list[str] | None = None,
        response_schema: dict | None = None,
        num_predict: int | None = None,
        keep_alive: str | None = None,
        options: dict | None = None,
    ) -> dict:
        content: object
        if images:
            parts: list[dict] = [{"type": "text", "text": prompt}]
            for image in images:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{media_type_for(image)};base64,{image}"
                        },
                    }
                )
            content = parts
        else:
            content = prompt

        temperature = options.get("temperature", 0) if options is not None else 0

        body: dict = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "stream": False,
            "temperature": temperature,
        }
        if num_predict is not None:
            body["max_tokens"] = num_predict
        if response_schema is not None:
            body["response_format"] = {
                "type": _RESPONSE_FORMAT_KIND,
                "json_schema": {
                    "name": "response",
                    "strict": False,
                    "schema": response_schema,
                },
            }
        # keep_alive is an Ollama residency knob; this shape has no equivalent
        # and a hosted service would only reject an unknown field.
        if system_prompt:
            body["messages"].insert(0, {"role": "system", "content": system_prompt})
        return body

    def reply_text(self, body: dict) -> str:
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if not isinstance(message, dict):
            return ""
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            texts = [
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            return "".join(texts).strip()
        return ""

    def error_text(self, body: dict) -> str | None:
        reported = body.get("error")
        if not reported:
            return None
        if isinstance(reported, dict):
            message = reported.get("message")
            return str(message) if message else str(reported)
        return str(reported)

    def model_names(self, body: dict) -> list[str]:
        data = body.get("data", [])
        if not isinstance(data, list):
            raise ValueError("invalid OpenAI model list response: data is not a list")
        names: list[str] = []
        for entry in data:
            if isinstance(entry, dict):
                name = str(entry.get("id") or "").strip()
                if name:
                    names.append(name)
        return sorted(set(names))

    def reply_usage(self, body: dict) -> tuple[int | None, int | None]:
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return (None, None)
        try:
            input_tokens = int(usage.get("prompt_tokens", 0)) or None
            output_tokens = int(usage.get("completion_tokens", 0)) or None
            return (input_tokens, output_tokens)
        except (TypeError, ValueError):
            return (None, None)


DIALECT = OpenAIChatDialect()
