from __future__ import annotations

__all__ = ["LLMError", "TransportError"]


class LLMError(RuntimeError):
    """A provider call failed; the message has been redacted."""


class TransportError(LLMError):
    """The HTTP transport failed after retries."""
