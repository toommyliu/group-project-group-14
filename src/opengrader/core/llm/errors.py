"""One error family for every provider call, so a consumer can catch it.

Before this module a caller that wanted to survive a failed model call had to
name `OllamaError` — which is exactly finding F1 in a different costume: a
consumer that names a provider's exception type is a consumer no other
provider can satisfy. `except OllamaError` in `cli/llm_eval.py` would have sat
there silently not catching anything the day a hosted service was configured.

So: every client below the provider boundary raises something that is an
`LLMError`. `OllamaError` and `OllamaVisionError` stay, as subclasses, because
the tests and the modules below the boundary name them and there is no reason
to churn that — but nothing above the boundary needs to know they exist.

`core.ai_config.AIUnavailableError` is deliberately *not* in this hierarchy.
It means "the pre-flight says don't bother"; these mean "the wire failed".
"""

from __future__ import annotations

__all__ = ["LLMError", "TransportError"]


class LLMError(RuntimeError):
    """A provider call failed. The message has already passed through redaction.

    Catch this above the provider boundary. Never catch a provider's own
    subclass there: the whole point is that the consumer does not know which
    provider is configured.
    """


class TransportError(LLMError):
    """The HTTP layer could not get an answer, after whatever retries applied."""
