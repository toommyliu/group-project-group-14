"""Adapter kept for this module's callers.

The request/response shape used to live here; it now lives in
`core/llm/dialects/ollama.py`, and the HTTP now lives in `core/llm/transport.py`.
This module is what is left: the same four names `ollama_client.py` and
`core/ocr/ollama_vlm.py` have always called, implemented over the shared
transport with `transport.NO_RETRY` so a local server that is not answering
still fails on the first try, exactly as it did before the split (§7.3).

`request` is imported and kept as a module attribute, unused directly, purely
because `tests/unit/core/llm/test_ollama_client.py` and
`tests/unit/ocr/test_ollama_vlm_hygiene.py` monkeypatch
`ollama_transport.request.urlopen`. `urllib.request` is a single shared module
object, so patching it here patches the same object `transport.py` calls
through — the tests do not need to change for the transport to move.
"""

from __future__ import annotations

from urllib import request

from . import transport
from .dialects.ollama import DIALECT as _OLLAMA
from .transport import ErrorFactory, parse_json_object

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_KEEP_ALIVE",
    "ErrorFactory",
    "parse_json_object",
    "fetch_model_names",
    "generate_raw",
]

DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_SECONDS = 120.0
DEFAULT_KEEP_ALIVE = "10m"

# The transport reports failures against a URL; this puts the name back, so an
# error still reads "failed to reach Ollama at ..." as it always has. A person
# reading it should not have to recognise a port number.
_SERVICE = "Ollama"


def fetch_model_names(
    *,
    host: str,
    timeout_seconds: float,
    error_factory: ErrorFactory,
) -> list[str]:
    endpoint = _OLLAMA.models_endpoint(host)
    assert endpoint is not None  # the ollama dialect always has one
    parsed = transport.send_json(
        url=endpoint,
        payload=None,
        headers=_OLLAMA.auth_headers(None),
        method="GET",
        service=_SERVICE,
        timeout_seconds=timeout_seconds,
        error_factory=error_factory,
        retry=transport.NO_RETRY,
    )
    try:
        return _OLLAMA.model_names(parsed)
    except ValueError as exc:
        raise error_factory(transport.redact(str(exc))) from exc


def generate_raw(
    *,
    host: str,
    model: str,
    prompt: str,
    images: list[str] | None = None,
    options: dict | None = None,
    response_format: dict | None = None,
    keep_alive: str | None = None,
    num_predict: int | None = None,
    timeout_seconds: float,
    error_factory: ErrorFactory,
) -> str:
    body = _OLLAMA.build_body(
        model=model,
        prompt=prompt,
        images=images,
        response_schema=response_format,
        num_predict=num_predict,
        keep_alive=keep_alive,
        options=options,
    )
    parsed = transport.send_json(
        url=_OLLAMA.generate_endpoint(host),
        payload=body,
        headers=_OLLAMA.auth_headers(None),
        service=_SERVICE,
        timeout_seconds=timeout_seconds,
        error_factory=error_factory,
        retry=transport.NO_RETRY,
    )
    reported = _OLLAMA.error_text(parsed)
    if reported:
        raise error_factory(transport.redact(reported))
    return _OLLAMA.reply_text(parsed)
