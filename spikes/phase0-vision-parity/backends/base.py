"""Shared shapes for the three spike backends.

Each backend module (`ollama_backend.py`, `anthropic_backend.py`,
`openai_backend.py`) exposes one function:

    transcribe(image_path: Path, prompt: str, config: BackendConfig) -> CallResult

`config` carries host/model/api-key-env/pricing. None of the three backend
modules imports a vendor SDK — raw `urllib.request` only, per AI_README.md
§7.8 Q4. Each is a throwaway sketch of what Phase 3's
`core/llm/dialects/{ollama,openai_chat,anthropic_messages}.py` will contain;
keeping them small and separate is deliberate so that work can crib from
here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BackendConfig:
    host: str
    model: str
    api_key: str | None
    timeout_seconds: float = 120.0


@dataclass
class CallResult:
    """What one transcription call actually produced, measured not estimated."""

    text: str | None
    input_tokens: int | None
    output_tokens: int | None
    error: str | None = None
    raw_prompt_used: str | None = None
