from __future__ import annotations

from dataclasses import dataclass

from opengrader.core.app_config import DEFAULT_TEXT_MODEL

from . import ollama_transport
from .errors import LLMError
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
    model: str = DEFAULT_TEXT_MODEL
    host: str = ollama_transport.DEFAULT_HOST
    timeout_seconds: float = ollama_transport.DEFAULT_TIMEOUT_SECONDS
    keep_alive: str = ollama_transport.DEFAULT_KEEP_ALIVE
    num_predict: int = 512


class OllamaError(LLMError):
    # A subclass, not a fresh RuntimeError: catchable on its own by code below
    # the provider boundary, but also caught by anyone catching LLMError above it.
    pass


def list_models(config: OllamaConfig) -> list[str]:
    return ollama_transport.fetch_model_names(
        host=config.host,
        timeout_seconds=config.timeout_seconds,
        error_factory=OllamaError,
    )


def ensure_model_available(config: OllamaConfig) -> None:
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
