"""Provider-agnostic LLM clients for Opengrader.

This package holds the *text* LLM integration used by grading. The vision
model client that transcribes handwriting lives in `core/ocr/ollama_vlm.py`;
the two are kept separate on purpose — vision extraction and text grading have
different prompts, models, and failure modes, and the OCR path must not be
destabilized by grading changes. Both speak to a local Ollama server, and both
now go through the shared, domain-free wire layer in
`core/llm/ollama_transport.py` — each client still raises its own error type
(`OllamaError` vs. `OllamaVisionError`) and keeps its own model-availability
caching behavior; only the HTTP plumbing and the JSON salvager are shared.
"""

from .ollama_client import (
    OllamaConfig,
    OllamaError,
    ensure_model_available,
    generate,
    list_models,
    parse_json_object,
)

__all__ = [
    "OllamaConfig",
    "OllamaError",
    "ensure_model_available",
    "generate",
    "list_models",
    "parse_json_object",
]
