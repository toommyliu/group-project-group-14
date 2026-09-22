from __future__ import annotations

from .base import Dialect, DialectError, join_url

__all__ = [
    "Dialect",
    "DialectError",
    "join_url",
    "DIALECT_NAMES",
    "get_dialect",
    "registered_dialects",
]

DIALECT_NAMES: tuple[str, ...] = ("ollama", "openai-chat", "anthropic-messages")

_MODULES: dict[str, str] = {
    "ollama": "ollama",
    "openai-chat": "openai_chat",
    "anthropic-messages": "anthropic_messages",
}


def registered_dialects() -> tuple[str, ...]:
    return DIALECT_NAMES


def get_dialect(name: str) -> Dialect:
    key = (name or "").strip().lower()
    module_name = _MODULES.get(key)
    if module_name is None:
        known = ", ".join(DIALECT_NAMES)
        raise DialectError(
            f"unknown dialect {name!r}. Opengrader speaks: {known}."
        )
    from importlib import import_module

    module = import_module(f"{__name__}.{module_name}")
    return module.DIALECT
