"""Where the two halves meet: a dialect's shape over the one transport.

`ollama_client.py` is this module's older sibling — same job, one hardcoded
shape. This one takes the shape as data, which is the whole of §7.2 in a
sentence.

`DialectConfig` is a frozen dataclass of strings and numbers, and that is a
requirement rather than a style: `core/submissions/batch.py` carries a
`VisionReader` — and therefore the config inside it — into a `spawn`ed worker,
so **it must be picklable**. It holds the dialect's *name*, never the dialect
object, for the same reason `math_ocr` crosses that boundary as a backend name.

The resolved key is a field here because a worker process needs it to make a
call, and `spawn` pickles through a pipe rather than through a file. It is
`repr=False` so it cannot reach a log through a stray f-string, and it is
registered with `transport.register_secret` so that if it reaches one anyway,
redaction catches it. `ai_config.resolve_api_key`'s guarantee is unchanged:
nothing but the variable *name* is ever persisted.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import transport
from .dialects import get_dialect
from .errors import LLMError

__all__ = ["DialectConfig", "generate", "list_models"]


@dataclass(frozen=True)
class DialectConfig:
    """Everything one provider call needs, and nothing that cannot be pickled."""

    dialect: str
    host: str
    model: str
    timeout_seconds: float = transport.DEFAULT_TIMEOUT_SECONDS
    num_predict: int = 512
    keep_alive: str | None = None
    # 1 means "no retry", which is what a server on this machine gets: riding
    # out a blip is a hosted-API concern, and waiting on a local one that is
    # down just makes the instructor wait for the same message.
    max_attempts: int = 4
    api_key: str | None = field(default=None, repr=False, compare=False)
    label: str = ""

    def __post_init__(self) -> None:
        if self.api_key:
            transport.register_secret(self.api_key)

    @property
    def retry_policy(self) -> transport.RetryPolicy:
        if self.max_attempts <= 1:
            return transport.NO_RETRY
        return transport.RetryPolicy(
            max_attempts=self.max_attempts, retry_on_connection_error=True
        )

    @property
    def service(self) -> str:
        """How this provider is named in an error message a person will read."""
        return self.label or self.host


def generate(
    *,
    config: DialectConfig,
    prompt: str,
    images: list[str] | None = None,
    response_format: dict | None = None,
    num_predict: int | None = None,
    keep_alive: str | None = None,
    options: dict | None = None,
    error_factory: transport.ErrorFactory = LLMError,
) -> str:
    """One completion, in whatever shape the configured dialect speaks."""
    dialect = get_dialect(config.dialect)
    body = dialect.build_body(
        model=config.model,
        prompt=prompt,
        images=images,
        response_schema=response_format,
        num_predict=num_predict if num_predict is not None else config.num_predict,
        keep_alive=keep_alive if keep_alive is not None else config.keep_alive,
        options=options,
    )
    parsed = transport.send_json(
        url=dialect.generate_endpoint(config.host),
        payload=body,
        headers=dialect.auth_headers(config.api_key),
        service=config.service,
        timeout_seconds=config.timeout_seconds,
        error_factory=error_factory,
        retry=config.retry_policy,
    )
    reported = dialect.error_text(parsed)
    if reported:
        raise error_factory(transport.redact(reported))
    return dialect.reply_text(parsed)


def list_models(config: DialectConfig) -> list[str]:
    """Every model this service says it will answer for; empty if it will not say.

    An empty list is an absence, not an error — `ai_config.ensure_ready` reads
    it that way deliberately, because a service that answers but enumerates
    nothing is not evidence that the model is missing.
    """
    dialect = get_dialect(config.dialect)
    endpoint = dialect.models_endpoint(config.host)
    if endpoint is None:
        return []
    parsed = transport.send_json(
        url=endpoint,
        payload=None,
        headers=dialect.auth_headers(config.api_key),
        method="GET",
        service=config.service,
        timeout_seconds=config.timeout_seconds,
        error_factory=LLMError,
        retry=config.retry_policy,
    )
    try:
        return dialect.model_names(parsed)
    except ValueError as exc:
        raise LLMError(transport.redact(str(exc))) from exc
