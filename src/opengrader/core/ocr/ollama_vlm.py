from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from opengrader.core.llm import ollama_transport
from opengrader.core.llm.errors import LLMError
from opengrader.core.llm.ollama_transport import parse_json_object as _parse_json_from_response

from . import vision_prompts
from .vision_reader import TitleMetadataResult

__all__ = [
    "OllamaVisionConfig",
    "OllamaVisionError",
    "TitleMetadataResult",
    "ensure_model_available",
    "list_available_models",
    "extract_field_text",
    "extract_student_field",
    "extract_title_metadata",
    "extract_question_prompt_and_answer",
    "extract_handwriting_only",
    "extract_page_number",
    "page_has_identity_field",
]


@dataclass(frozen=True)
class OllamaVisionConfig:
    model: str = "qwen3.5:9b"
    host: str = ollama_transport.DEFAULT_HOST
    timeout_seconds: float = ollama_transport.DEFAULT_TIMEOUT_SECONDS
    keep_alive: str = ollama_transport.DEFAULT_KEEP_ALIVE
    num_predict: int = 256


class OllamaVisionError(LLMError):
    # A subclass, not a fresh RuntimeError: joins the shared provider-call
    # family so code above the provider boundary can catch LLMError alone.
    pass


_AVAILABLE_MODELS: set[tuple[str, str]] = set()


def list_available_models(config: OllamaVisionConfig) -> list[str]:
    return ollama_transport.fetch_model_names(
        host=config.host,
        timeout_seconds=config.timeout_seconds,
        error_factory=OllamaVisionError,
    )


def ensure_model_available(config: OllamaVisionConfig) -> None:
    cache_key = (config.host.rstrip("/"), config.model)
    if cache_key in _AVAILABLE_MODELS:
        return

    available = list_available_models(config)
    if config.model in available:
        _AVAILABLE_MODELS.add(cache_key)
        return

    available_text = ", ".join(available) if available else "none"
    raise OllamaVisionError(
        f"Ollama model {config.model!r} is not available at {config.host.rstrip('/')}. "
        f"Available models: {available_text}. "
        f"Install it with: ollama pull {config.model}"
    )


def _generate_with_image(
    *,
    image_path: str | Path,
    prompt: str,
    system_prompt: str | None = None,
    config: OllamaVisionConfig,
    response_format: dict | None = None,
    num_predict: int | None = None,
) -> str:
    ensure_model_available(config)
    encoded_image = vision_prompts.image_to_base64(image_path)
    return ollama_transport.generate_raw(
        host=config.host,
        model=config.model,
        prompt=prompt,
        system_prompt=system_prompt,
        options={"temperature": 0, "think": False},
        images=[encoded_image],
        response_format=response_format,
        keep_alive=config.keep_alive,
        num_predict=num_predict if num_predict is not None else config.num_predict,
        timeout_seconds=config.timeout_seconds,
        error_factory=OllamaVisionError,
    )


def extract_field_text(
    *,
    image_path: str | Path,
    field_kind: Literal["name", "sid"],
    config: OllamaVisionConfig,
) -> str:
    request = vision_prompts.field_text_request(field_kind)
    raw = _generate_with_image(
        image_path=image_path,
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        config=config,
        response_format=request.response_schema,
        num_predict=request.num_predict,
    )
    return vision_prompts.parse_field_text(raw, field_kind=field_kind)


def extract_student_field(
    *,
    image_path: str | Path,
    field_kind: Literal["name", "sid"],
    config: OllamaVisionConfig,
) -> str:
    return extract_field_text(
        image_path=image_path,
        field_kind=field_kind,
        config=config,
    )


def extract_title_metadata(
    *,
    image_path: str | Path,
    config: OllamaVisionConfig,
) -> TitleMetadataResult:
    request = vision_prompts.title_metadata_request()
    raw = _generate_with_image(
        image_path=image_path,
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        config=config,
        response_format=request.response_schema,
        num_predict=request.num_predict,
    )
    return vision_prompts.parse_title_metadata(raw)


def extract_question_prompt_and_answer(
    *,
    image_path: str | Path,
    config: OllamaVisionConfig,
    math_latex: bool = False,
) -> tuple[str, str]:
    request = vision_prompts.question_prompt_and_answer_request(math_latex=math_latex)
    raw = _generate_with_image(
        image_path=image_path,
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        config=config,
        response_format=request.response_schema,
        num_predict=request.num_predict,
    )
    return vision_prompts.parse_question_prompt_and_answer(raw)


def extract_handwriting_only(
    *,
    image_path: str | Path,
    config: OllamaVisionConfig,
    math_latex: bool = False,
) -> str:
    """Transcribe only student handwriting or marked choices in a response crop."""

    request = vision_prompts.handwriting_only_request(math_latex=math_latex)
    raw = _generate_with_image(
        image_path=image_path,
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        config=config,
        response_format=request.response_schema,
        num_predict=request.num_predict,
    )
    return vision_prompts.parse_handwriting_only(raw)


def extract_page_number(
    *,
    image_path: str | Path,
    config: OllamaVisionConfig,
) -> int | None:
    request = vision_prompts.page_number_request()
    raw = _generate_with_image(
        image_path=image_path,
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        config=config,
        response_format=request.response_schema,
        num_predict=request.num_predict,
    )
    return vision_prompts.parse_page_number(raw)


def page_has_identity_field(
    *,
    image_path: str | Path,
    config: OllamaVisionConfig,
) -> bool:
    request = vision_prompts.identity_field_request()
    raw = _generate_with_image(
        image_path=image_path,
        prompt=request.prompt,
        system_prompt=request.system_prompt,
        config=config,
        response_format=request.response_schema,
        num_predict=request.num_predict,
    )
    return vision_prompts.parse_identity_field(raw)
