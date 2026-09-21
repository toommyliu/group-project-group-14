"""The dialect registry: three shapes, looked up by name.

Imports are deferred into `get_dialect` on purpose. `core/ai_config.py` is
imported by the TUI at startup and by every CLI's argument parser, and its
contract — pinned by `tests/unit/tui/test_startup_imports.py` — is that no
client comes with it. A dialect registry that imported three modules eagerly
would break that contract on ai_config's behalf.
"""

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

# Ordered as §7.2 lists them: the local server first, then the shape most of
# the market speaks, then Anthropic's.
DIALECT_NAMES: tuple[str, ...] = ("ollama", "openai-chat", "anthropic-messages")

_MODULES: dict[str, str] = {
    "ollama": "ollama",
    "openai-chat": "openai_chat",
    "anthropic-messages": "anthropic_messages",
}


def registered_dialects() -> tuple[str, ...]:
    return DIALECT_NAMES


def get_dialect(name: str) -> Dialect:
    """The dialect called `name`, or `DialectError` naming the ones that exist.

    A manifest is user-editable text, so a typo here is a routine event rather
    than a programming error — the message has to be the fix.
    """
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
