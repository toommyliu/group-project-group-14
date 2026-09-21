from .dialect_client import DialectConfig
from .dialects import DIALECT_NAMES, get_dialect, registered_dialects
from .errors import LLMError, TransportError
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
    "LLMError",
    "TransportError",
    "DialectConfig",
    "get_dialect",
    "registered_dialects",
    "DIALECT_NAMES",
]
