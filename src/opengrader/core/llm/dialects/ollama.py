"""Requests and responses for Ollama's local /api/generate endpoint."""

from __future__ import annotations

from dataclasses import dataclass

from .base import join_url

__all__ = ["OllamaDialect", "DIALECT"]


@dataclass(frozen=True)
class OllamaDialect:
    name: str = "ollama"

    def generate_endpoint(self, host: str) -> str:
        return join_url(host, "/api/generate")

    def models_endpoint(self, host: str) -> str | None:
        return join_url(host, "/api/tags")

    def auth_headers(self, api_key: str | None) -> dict[str, str]:
        # A server on this machine. There is nothing to authenticate to, and
        # sending a header anyway would be the first step towards treating the
        # local path as a cloud one.
        return {}

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
        merged = dict(options) if options is not None else {"temperature": 0}
        if num_predict is not None:
            merged["num_predict"] = num_predict

        body: dict = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": merged,
        }
        # Thinking is a request field, unlike the sampling options.
        if "think" in merged:
            body["think"] = merged.pop("think")
        if images is not None:
            body["images"] = images
        if response_schema is not None:
            body["format"] = response_schema
        if keep_alive is not None:
            body["keep_alive"] = keep_alive
        if system_prompt:
            body["system"] = system_prompt
        return body

    def reply_text(self, body: dict) -> str:
        return str(body.get("response", "")).strip()

    def error_text(self, body: dict) -> str | None:
        reported = body.get("error")
        return str(reported) if reported else None

    def model_names(self, body: dict) -> list[str]:
        models = body.get("models", [])
        if not isinstance(models, list):
            raise ValueError("invalid Ollama model list response: models is not a list")
        names: list[str] = []
        for entry in models:
            if isinstance(entry, dict):
                name = str(entry.get("name") or entry.get("model") or "").strip()
                if name:
                    names.append(name)
        return sorted(set(names))

    def reply_usage(self, body: dict) -> tuple[int | None, int | None]:
        try:
            input_tokens = int(body.get("prompt_eval_count", 0)) or None
            output_tokens = int(body.get("eval_count", 0)) or None
            return (input_tokens, output_tokens)
        except (TypeError, ValueError):
            return (None, None)


DIALECT = OllamaDialect()
