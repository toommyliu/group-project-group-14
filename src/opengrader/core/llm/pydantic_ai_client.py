"""The optional `pydantic-ai` backend — the one place that package is called.

Opengrader talks to AI services over raw HTTP through one shared transport
rather than a vendor SDK, and that stays the rule: every service reachable with an
API-key header still goes through `core/llm/transport.py` and the three wire
dialects (`core/llm/dialects/`). This module exists for the services that
choice cannot reach at all — not because they lack a bearer-key header, but
because they authenticate a different way entirely: AWS Bedrock (SigV4 and
the AWS credential chain), Google Vertex AI (Application Default Credentials
or a service account), Gemini's native API, and the handful of other
platforms `pydantic-ai` already knows how to sign a request for. Hand-rolling
SigV4 or ADC token refresh into the raw transport would be reinventing a
vendor SDK badly; `pydantic-ai` already did it well, so this module is a
second, *additional* backend behind the same `ProviderSpec` seam
(`core/app_config/manifests.py` builds the spec; nothing here is reachable
except through it) — not a replacement for the transport, and not a path any
other provider is routed through.

Two guarantees this module exists to keep even though it does not use
`transport.send_json`:

* **Lazy and optional.** Nothing at module scope here imports `pydantic_ai`
  (or any of its provider extras) — every such import is inside a function.
  The package is an optional extra (`uv sync --extra pydantic-ai`); a machine
  without it must still start Opengrader and run every other provider.
* **Same guarantees as the transport, hand-implemented for this SDK instead
  of reused from it.** One redaction point (`transport.redact`, applied to
  every message this module raises), the same retry semantics
  (`config.retry_policy`, `transport.backoff_delay`, the same `Retry-After`
  handling), the same bounded concurrency (`transport.concurrency_slot`), and
  a key that is registered with `transport.register_secret` and handed to the
  provider object directly — never written to `os.environ`, never logged.

`build_model` is deliberately a small, separate seam: tests replace it with a
function that returns a `pydantic_ai.models.function.FunctionModel` or
`TestModel`, so nothing here ever opens a socket in the suite. `generate` is
the only function that calls `build_model` and touches the network; every
other function in this module is a pure helper around it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from . import transport
from .dialects.base import media_type_for
from .errors import LLMError
from .transport import parse_retry_after

__all__ = ["PydanticAIConfig", "build_model", "generate"]

_MISSING_PACKAGE_MESSAGE = (
    "Pydantic AI is not installed. Install it with `uv sync --extra "
    "pydantic-ai` to use this provider."
)

_DETAIL_LIMIT = 400


@dataclass(frozen=True)
class PydanticAIConfig:
    """Picklable connection data for the Pydantic AI backend.

    Mirrors `core.llm.dialect_client.DialectConfig` in shape and intent —
    `batch.ExtractionJob` carries a reader (and so its config) into a
    `spawn`ed worker, the same constraint `core/ocr/vision_reader.py` states
    for every `VisionReader`. `model` is a Pydantic AI model id, `"prefix:name"`
    (e.g. `"anthropic:claude-haiku-4-5-20251001"`); the prefix selects which
    of Pydantic AI's provider classes `build_model` constructs, and is
    validated against the allowlist in `core/app_config/manifests.py` before
    a manifest ever reaches this dataclass.
    """

    model: str
    api_key: str | None = field(default=None, repr=False, compare=False)
    base_url: str | None = None
    timeout_seconds: float = transport.DEFAULT_TIMEOUT_SECONDS
    max_attempts: int = 4
    label: str = ""

    def __post_init__(self) -> None:
        if self.api_key:
            transport.register_secret(self.api_key)

    @property
    def prefix(self) -> str:
        return self.model.split(":", 1)[0]

    @property
    def retry_policy(self) -> transport.RetryPolicy:
        if self.max_attempts <= 1:
            return transport.NO_RETRY
        return transport.RetryPolicy(
            max_attempts=self.max_attempts, retry_on_connection_error=True
        )

    @property
    def service(self) -> str:
        return self.label or self.model


# ---------------------------------------------------------------------------
# Model construction — the seam tests replace with a `FunctionModel`/`TestModel`
# ---------------------------------------------------------------------------

_MODEL_CACHE_LOCK = threading.Lock()
_MODEL_CACHE: dict[tuple[str, str | None, str | None], Any] = {}


def _cache_key(config: PydanticAIConfig) -> tuple[str, str | None, str | None]:
    return (config.model, config.api_key, config.base_url)


def build_model(config: PydanticAIConfig) -> Any:
    """Build (or reuse) the `pydantic_ai` `Model` for this config.

    Cached process-wide, keyed on everything that changes which client the
    SDK would build — a batch reads many crops through the same provider, and
    rebuilding an HTTP client (and, for Bedrock, a boto3 session) per crop
    would be needless work for a call that is already the slow part of the
    pipeline. Tests never see this cache: they monkeypatch this function
    itself with one that returns a `FunctionModel`/`TestModel`, so nothing
    below `build_model` — including this cache — runs in the suite.
    """
    key = _cache_key(config)
    with _MODEL_CACHE_LOCK:
        cached = _MODEL_CACHE.get(key)
    if cached is not None:
        return cached
    model = _build_model_uncached(config)
    with _MODEL_CACHE_LOCK:
        _MODEL_CACHE[key] = model
    return model


def _build_model_uncached(config: PydanticAIConfig) -> Any:
    try:
        from pydantic_ai.models import infer_model

        provider = _build_provider(config.prefix, config)
        return infer_model(config.model, provider_factory=lambda _name: provider)
    except ImportError as exc:
        raise LLMError(transport.redact(_MISSING_PACKAGE_MESSAGE)) from exc


def _build_provider(prefix: str, config: PydanticAIConfig) -> Any:
    """One branch per provider prefix, not a generic loop.

    Each vendor SDK's client constructor takes different keywords, and only
    some accept a `max_retries` of zero — our own retry loop (`generate`,
    below) is the one that must be trusted to count attempts, so SDK-internal
    retries are turned off wherever the client exposes a knob for it
    (OpenAI, Anthropic, Groq, Cohere: a `max_retries` argument on the vendor
    client itself; Google: a `retry_options` on the provider). Bedrock and
    Mistral do not expose one through `pydantic-ai`'s provider classes; their
    own retry policy stays on for those two, which only costs latency on a
    transient error — never correctness, because a read-only completion
    request is always safe to repeat.
    """
    api_key = config.api_key
    base_url = config.base_url

    if prefix == "openai":
        from openai import AsyncOpenAI
        from pydantic_ai.providers.openai import OpenAIProvider

        client = AsyncOpenAI(api_key=api_key or "unset", base_url=base_url, max_retries=0)
        return OpenAIProvider(openai_client=client)

    if prefix == "anthropic":
        from anthropic import AsyncAnthropic
        from pydantic_ai.providers.anthropic import AnthropicProvider

        client = AsyncAnthropic(api_key=api_key or "unset", base_url=base_url, max_retries=0)
        return AnthropicProvider(anthropic_client=client)

    if prefix == "groq":
        from groq import AsyncGroq
        from pydantic_ai.providers.groq import GroqProvider

        client = AsyncGroq(api_key=api_key or "unset", base_url=base_url, max_retries=0)
        return GroqProvider(groq_client=client)

    if prefix == "cohere":
        from cohere import AsyncClientV2
        from pydantic_ai.providers.cohere import CohereProvider

        client = AsyncClientV2(api_key=api_key or "unset", max_retries=0)
        return CohereProvider(cohere_client=client)

    if prefix in ("google", "google-cloud"):
        from google.genai.types import HttpRetryOptions
        from pydantic_ai.providers import infer_provider_class

        kwargs: dict[str, Any] = {"retry_options": HttpRetryOptions(attempts=1)}
        if api_key is not None:
            kwargs["api_key"] = api_key
        if base_url is not None:
            kwargs["base_url"] = base_url
        return infer_provider_class(prefix)(**kwargs)

    # Bedrock and Mistral: build generically. Neither `api_key` nor
    # `base_url` is forced in when unset, so Bedrock falls through to the AWS
    # credential chain and a Vertex-style ADC flow keeps working the same way
    # for any future prefix added here on the same footing.
    from pydantic_ai.providers import infer_provider_class

    kwargs = {}
    if api_key is not None:
        kwargs["api_key"] = api_key
    if base_url is not None:
        kwargs["base_url"] = base_url
    return infer_provider_class(prefix)(**kwargs)


# ---------------------------------------------------------------------------
# Running a call
# ---------------------------------------------------------------------------


def _run_sync_safely(agent: Any, user_prompt: Any) -> Any:
    """`Agent.run_sync` refuses to run on a thread whose asyncio loop is
    already running — which a Textual TUI worker thread sometimes is. Run it
    in a throwaway thread instead of letting that `RuntimeError` cross this
    boundary as a crash.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return agent.run_sync(user_prompt)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(agent.run_sync, user_prompt).result()


def _run_once(
    config: PydanticAIConfig,
    *,
    prompt: str,
    system_prompt: str | None,
    images: list[str] | None,
    response_schema: dict | None,
    num_predict: int | None,
) -> tuple[str, Any]:
    from pydantic_ai import Agent, BinaryContent, StructuredDict

    model = build_model(config)

    output_type: Any = str
    if response_schema is not None:
        output_type = StructuredDict(response_schema)

    settings: dict[str, Any] = {"temperature": 0}
    if num_predict is not None:
        settings["max_tokens"] = num_predict

    agent: Any = Agent(
        model=model,
        output_type=output_type,
        system_prompt=system_prompt or (),
        model_settings=settings,
    )
    agent.instrument = False  # no Logfire/instrumentation of any kind

    user_prompt: Any = prompt
    if images:
        parts: list[Any] = [prompt]
        for encoded in images:
            parts.append(
                BinaryContent(data=base64.b64decode(encoded), media_type=media_type_for(encoded))
            )
        user_prompt = parts

    result = _run_sync_safely(agent, user_prompt)
    output = result.output
    text = json.dumps(output) if isinstance(output, dict) else str(output)
    return text, result.usage


def _truncate(text: str) -> str:
    # Redact before cutting: a key split by the cut no longer matches the
    # registered secret exactly, and its first half would survive.
    cleaned = transport.redact(text.strip())
    if len(cleaned) > _DETAIL_LIMIT:
        return cleaned[:_DETAIL_LIMIT] + "…"
    return cleaned


def _is_connection_error(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, OSError)):
        return True
    name = type(exc).__name__.lower()
    return "timeout" in name or "connect" in name


def _classify(exc: Exception) -> tuple[bool, str]:
    """(is this worth retrying, the message to report) for one failed call.

    Every `pydantic_ai` model normalises an HTTP-level failure to
    `ModelHTTPError` (it carries `status_code`, the same signal
    `transport.send_json` keys its `RETRYABLE_STATUS` decision on), so this
    mirrors that decision instead of re-deriving retryability from a raw
    response. Anything that fails before an HTTP response — a DNS failure, a
    refused connection, a timeout — arrives as one of a handful of different
    exception types depending on the vendor SDK; matched by class name rather
    than an exhaustive import list of every SDK's exception hierarchy, which
    would have to grow every time a prefix is added.
    """
    try:
        from pydantic_ai import ModelHTTPError
    except ImportError:  # pragma: no cover - unreachable once build_model succeeded once
        ModelHTTPError = ()  # type: ignore[assignment]

    if isinstance(exc, ModelHTTPError):
        status = exc.status_code
        return status in transport.RETRYABLE_STATUS, f"HTTP {status}: {_truncate(str(exc))}"
    if _is_connection_error(exc):
        return True, f"failed to reach the model: {_truncate(str(exc))}"
    return False, _truncate(str(exc))


def _retry_after_of(exc: BaseException) -> float | None:
    headers = getattr(exc, "headers", None)
    if not headers:
        return None
    try:
        return parse_retry_after(headers.get("Retry-After"))
    except AttributeError:
        return None


def generate(
    *,
    config: PydanticAIConfig,
    prompt: str,
    system_prompt: str | None = None,
    images: list[str] | None = None,
    response_schema: dict | None = None,
    num_predict: int | None = None,
) -> str:
    """The one place `pydantic_ai` is called. See the module docstring.

    `images` are base64-encoded image payloads, the same convention
    `dialect_client.generate` uses — `core/ocr/pydantic_ai_reader.py` builds
    them with `vision_prompts.image_to_base64`, exactly as
    `DialectVisionReader` does. `response_schema` is a JSON schema, fed to
    `pydantic_ai.StructuredDict`; its result is re-encoded with `json.dumps`
    so every caller's existing `vision_prompts.parse_*` function (which
    expects a JSON *string*, because that is what the raw-HTTP dialects hand
    it) keeps working unchanged.

    Raises `LLMError` — never a raw SDK exception, never an unredacted
    message — after `config.retry_policy` is exhausted.
    """
    policy = config.retry_policy
    attempts = max(1, policy.max_attempts)
    last_message = f"{config.service} did not answer"

    for attempt in range(1, attempts + 1):
        try:
            with transport.concurrency_slot():
                text, run_usage = _run_once(
                    config,
                    prompt=prompt,
                    system_prompt=system_prompt,
                    images=images,
                    response_schema=response_schema,
                    num_predict=num_predict,
                )
        except Exception as exc:  # noqa: BLE001 - every SDK exception funnels through here
            retryable, detail = _classify(exc)
            last_message = f"{config.service}: {detail}"
            if retryable and attempt < attempts:
                time.sleep(
                    transport.backoff_delay(
                        attempt=attempt, policy=policy, retry_after=_retry_after_of(exc)
                    )
                )
                continue
            # `from None`: the SDK's own exception may quote request headers
            # or the key, and a traceback prints every chained exception
            # unredacted. The redacted summary above is all that travels.
            raise LLMError(transport.redact(last_message)) from None
        else:
            if run_usage is not None:
                from . import usage as usage_module

                usage_module.record_last(run_usage.input_tokens, run_usage.output_tokens)
            return text

    raise LLMError(transport.redact(last_message))  # pragma: no cover - loop always returns/raises
