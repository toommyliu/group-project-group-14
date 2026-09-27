"""Anthropic's shape — `POST /v1/messages`.

The manifest `host` is the API base (`https://api.anthropic.com/v1`, or a
gateway that fronts it), same convention as the other hosted dialect. What
this shape does not cover: streaming, tool use, and the `anthropic-beta`
feature-flag headers some capabilities gate behind — none of which grading a
plain completion needs.

Two things make this dialect diverge from `openai_chat` beyond the obvious
path and header differences:

* `max_tokens` is required by this API, not optional the way it is for a
  completion elsewhere, so `build_body` always supplies one.
* There is no `response_format` field at all. A JSON reply here is requested
  in the prompt, not the wire shape, so `build_body` appends an instruction
  to the text block instead of setting a body key — see the comment there.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .base import join_url, media_type_for

__all__ = ["AnthropicMessagesDialect", "DIALECT"]

ANTHROPIC_VERSION = "2023-06-01"

# What num_predict becomes when the caller does not say — max_tokens is
# mandatory on this API and there is no server-side default to fall back on.
DEFAULT_MAX_TOKENS = 1024


@dataclass(frozen=True)
class AnthropicMessagesDialect:
    name: str = "anthropic-messages"

    def generate_endpoint(self, host: str) -> str:
        return join_url(host, "/messages")

    def models_endpoint(self, host: str) -> str | None:
        return join_url(host, "/models")

    def auth_headers(self, api_key: str | None) -> dict[str, str]:
        # anthropic-version is sent either way: a gateway standing in front of
        # this shape may supply its own credential and still expect the
        # version header this API keys its request parsing on.
        headers = {"anthropic-version": ANTHROPIC_VERSION}
        if api_key:
            headers["x-api-key"] = api_key
        return headers

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
        text = prompt
        if response_schema is not None:
            # No response_format on this API — the only lever is the prompt
            # itself, so the schema is embedded as an instruction rather than
            # a body field the service would not recognise.
            text = (
                f"{prompt}\n\nRespond with JSON matching this schema: "
                f"{json.dumps(response_schema)}"
            )

        content: object
        if images:
            # Images first: Anthropic's own guidance is that the model reads
            # better when the image precedes the instruction referring to it.
            blocks: list[dict] = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type_for(image),
                        "data": image,
                    },
                }
                for image in images
            ]
            blocks.append({"type": "text", "text": text})
            content = blocks
        else:
            content = text

        temperature = options.get("temperature", 0) if options is not None else 0

        body: dict = {
            "model": model,
            "max_tokens": num_predict if num_predict is not None else DEFAULT_MAX_TOKENS,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
        }
        # keep_alive has no meaning against a hosted, stateless API.
        if system_prompt:
            body["system"] = system_prompt
        return body

    def reply_text(self, body: dict) -> str:
        blocks = body.get("content")
        if not isinstance(blocks, list):
            return ""
        texts = [
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(texts).strip()

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
            raise ValueError("invalid Anthropic model list response: data is not a list")
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
            input_tokens = int(usage.get("input_tokens", 0)) or None
            output_tokens = int(usage.get("output_tokens", 0)) or None
            return (input_tokens, output_tokens)
        except (TypeError, ValueError):
            return (None, None)


DIALECT = AnthropicMessagesDialect()
