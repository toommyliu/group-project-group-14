"""Minimal text-generation client for a local Ollama server.

Thin wrapper over ``core/llm/ollama_transport.py`` (the shared, domain-free
``urllib`` layer) for text-only prompts used by grading. Kept deliberately
small: config, its own error type, and a single ``generate`` call. The
JSON-object salvager (``parse_json_object``) and model-availability listing
now live in the transport module; this module re-exports the salvager since
``core/grading/llm_grader.py`` and ``core/llm/__init__.py`` import it from
here.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import ollama_transport
from .ollama_transport import parse_json_object

__all__ = [
    "OllamaConfig",
    "OllamaError",
    "parse_json_object",
    "list_models",
    "ensure_model_available",
    "generate",
]


@dataclass(frozen=True)
class OllamaConfig:
    """Connection settings for a local (or self-hosted) Ollama server."""

    model: str = "qwen2.5vl:7b"
    host: str = ollama_transport.DEFAULT_HOST
    timeout_seconds: float = ollama_transport.DEFAULT_TIMEOUT_SECONDS
    # Keeps the model resident between calls so grading a batch of answers
    # doesn't pay a reload between every one. See OllamaVisionConfig for why
    # this matters most on the vision path; text grading benefits the same way.
    keep_alive: str = ollama_transport.DEFAULT_KEEP_ALIVE
    # Caps reply length for the text-grading JSON payload
    # ({"rubric_labels": [...], "confidence": ..., "rationale": "..."}), which
    # is always short. Call sites may override for a different shape.
    num_predict: int = 512


class OllamaError(RuntimeError):
    """Raised when an Ollama request fails or returns an error payload."""


def list_models(config: OllamaConfig) -> list[str]:
    """Return the model names installed on the Ollama server."""
    return ollama_transport.fetch_model_names(
        host=config.host,
        timeout_seconds=config.timeout_seconds,
        error_factory=OllamaError,
    )


def ensure_model_available(config: OllamaConfig) -> None:
    """Raise a clear error if the configured model is not installed.

    Unlike ``ollama_vlm.ensure_model_available``, this never caches a success
    — every call re-checks the server. Tests depend on this: the text client
    re-raises on every invocation rather than remembering a prior success.
    """
    available = list_models(config)
    if config.model in available:
        return
    available_text = ", ".join(available) if available else "none"
    raise OllamaError(
        f"Ollama model {config.model!r} is not available at "
        f"{config.host.rstrip('/')}. Available models: {available_text}. "
        f"Install it with: ollama pull {config.model}"
    )


def generate(
    *,
    prompt: str,
    config: OllamaConfig,
    options: dict | None = None,
    response_format: dict | None = None,
    keep_alive: str | None = None,
    num_predict: int | None = None,
) -> str:
    """Send a text prompt to Ollama's /api/generate and return the response.

    ``options`` overrides Ollama generation options; the default pins
    ``temperature`` to 0 for reproducible grading. ``response_format``, when
    given, is passed through as Ollama's ``format`` JSON schema so the reply
    is structurally valid JSON — ``parse_json_object`` remains the fallback
    for whatever slips through. ``keep_alive``/``num_predict`` default to the
    config's values when not overridden per call.
    """
    return ollama_transport.generate_raw(
        host=config.host,
        model=config.model,
        prompt=prompt,
        options=options,
        response_format=response_format,
        keep_alive=keep_alive if keep_alive is not None else config.keep_alive,
        num_predict=num_predict if num_predict is not None else config.num_predict,
        timeout_seconds=config.timeout_seconds,
        error_factory=OllamaError,
    )
