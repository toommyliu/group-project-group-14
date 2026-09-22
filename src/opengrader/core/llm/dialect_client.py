from __future__ import annotations

from dataclasses import dataclass, field

from . import transport
from .dialects import get_dialect
from .errors import LLMError

__all__ = ["DialectConfig", "generate", "list_models"]


@dataclass(frozen=True)
class DialectConfig:
    """Picklable connection data for spawned extraction workers."""

    dialect: str
    host: str
    model: str
    timeout_seconds: float = transport.DEFAULT_TIMEOUT_SECONDS
    num_predict: int = 512
    keep_alive: str | None = None
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
