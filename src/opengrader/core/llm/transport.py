"""The single HTTP layer every provider call passes through.

§7.3 of `About/AI_README.md` asks for exactly one of these, and the reason is
not tidiness. Retry policy, backoff, concurrency and — above all — *redaction*
are the kind of thing that is wrong the third time you write it. Redaction in
particular is a security control (L8): an API key must not reach the AI
settings screen's connection-test log, `run-ledger.jsonl`, or any exception
text `doctor` prints. One transport means one place that can be true.

What lives here:

* `send_json` — one request, with backoff, jitter, `Retry-After` and a bounded
  concurrency slot around it. Everything a hosted API needs and a local server
  never did, which is why `ollama_transport` asks for `NO_RETRY`: preserving
  today's behaviour exactly is the point of that dialect being a refactor.
* `redact` and `register_secret` — the one redaction point. Every error message
  this module produces passes through `redact` before it is raised, so a
  caller cannot forget.
* `parse_json_object` — shared by all three dialects, because every one of
  them asks for JSON and every model occasionally wraps it in prose or a fence.
  It used to live in `ollama_transport`; that module re-exports it.

What deliberately does **not** live here: anything that knows a request's
*shape*. That is a dialect's job (`core/llm/dialects/`), and a dialect performs
no HTTP. The two halves only meet in `core/llm/dialect_client.py`.
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from urllib import error, request

from .errors import LLMError, TransportError

__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "RetryPolicy",
    "NO_RETRY",
    "CLOUD_RETRY",
    "RETRYABLE_STATUS",
    "ErrorFactory",
    "LLMError",
    "TransportError",
    "REDACTED",
    "register_secret",
    "forget_secret",
    "secret_registered",
    "redact",
    "backoff_delay",
    "set_concurrency_limit",
    "concurrency_limit",
    "concurrency_slot",
    "send_json",
    "parse_json_object",
]

DEFAULT_TIMEOUT_SECONDS = 120.0

ErrorFactory = Callable[[str], Exception]

# Errors worth trying again: a queue that is full, a gateway that blinked, a
# rate limit. Deliberately *not* 401/403/404 — a wrong key or a wrong model
# name does not get righter on the second attempt, and retrying it just makes
# the instructor wait longer for the same message.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

# How long we will ever wait because a service asked us to. A cloud API is
# within its rights to say "come back in an hour"; a grading run is not within
# its rights to sit there for one.
MAX_RETRY_AFTER_SECONDS = 120.0

# Above this many characters, a run of base64-ish text in an error body is an
# echoed image, not a message. Squash it: a student's handwriting must not
# reach a log because a service quoted our request back at us.
_BLOB_THRESHOLD = 256

# Error bodies are quoted into exception text. Keep that bounded.
_DETAIL_LIMIT = 400


@dataclass(frozen=True)
class RetryPolicy:
    """Exponential backoff with full jitter, capped.

    `max_attempts` counts the first try, so 1 means "no retry" — which is what
    the local Ollama path uses, because retrying a server on this machine that
    is not answering wastes the user's time rather than riding out a blip.
    """

    max_attempts: int = 1
    base_seconds: float = 0.5
    cap_seconds: float = 30.0
    jitter: float = 1.0
    retry_on_connection_error: bool = False


NO_RETRY = RetryPolicy()
CLOUD_RETRY = RetryPolicy(max_attempts=4, retry_on_connection_error=True)


# ---------------------------------------------------------------------------
# Redaction — L8. The one place a key can be scrubbed.
# ---------------------------------------------------------------------------

REDACTED = "[redacted]"

_SECRETS: set[str] = set()
_SECRETS_LOCK = threading.Lock()

# Keys that announce themselves by shape, so a value we were never handed
# (one a service echoes back, say) is still caught.
_SHAPED_KEY = re.compile(r"\b(?:sk|pk|rk|ak|key)-[A-Za-z0-9_\-]{12,}")

# `Authorization: Bearer xyz`, `x-api-key: xyz`, `"api_key": "xyz"`, `?key=xyz`.
_LABELLED_KEY = re.compile(
    r"(?i)"
    r"((?:x-)?(?:api[-_ ]?keys?|access[-_ ]?tokens?|authorization|auth[-_ ]?tokens?"
    r"|secrets?|passwords?|tokens?|bearer)"
    r"[\"']?\s*[:=]\s*(?:bearer\s+)?[\"']?)"
    r"([A-Za-z0-9._\-~+/=]{8,})"
)

_BARE_BEARER = re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._\-~+/=]{8,})")

_BASE64_BLOB = re.compile(r"[A-Za-z0-9+/=]{%d,}" % _BLOB_THRESHOLD)


def register_secret(value: str | None) -> None:
    """Remember a string that must never appear in a message leaving this module.

    Called with the resolved API key wherever one is resolved. Idempotent, and
    short or blank values are ignored — redacting a three-character string
    would scrub half of every error message it appeared in.
    """
    if not value or len(value.strip()) < 8:
        return
    with _SECRETS_LOCK:
        _SECRETS.add(value.strip())


def forget_secret(value: str | None) -> None:
    if not value:
        return
    with _SECRETS_LOCK:
        _SECRETS.discard(value.strip())


def secret_registered(value: str | None) -> bool:
    if not value:
        return False
    with _SECRETS_LOCK:
        return value.strip() in _SECRETS


def redact(text: str) -> str:
    """Scrub credentials and echoed payloads out of a message.

    Belt and braces on purpose. Registered secrets are removed by exact match,
    which catches the key we were actually handed; the shape rules catch a key
    we were never told about — a second header a gateway added, a token a
    service quoted back — because the failure this guards against is a message
    reaching a log with something secret in it, not a message reaching a log
    with *our* secret in it.
    """
    if not text:
        return text

    with _SECRETS_LOCK:
        known = sorted(_SECRETS, key=len, reverse=True)
    for value in known:
        text = text.replace(value, REDACTED)

    text = _BARE_BEARER.sub(lambda m: m.group(0)[: m.start(1) - m.start(0)] + REDACTED, text)
    text = _LABELLED_KEY.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    text = _SHAPED_KEY.sub(REDACTED, text)
    text = _BASE64_BLOB.sub(lambda m: f"[{len(m.group(0))} bytes elided]", text)
    return text


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def parse_retry_after(value: str | None) -> float | None:
    """Seconds to wait, from a `Retry-After` header in either legal spelling.

    HTTP allows delta-seconds or an HTTP-date. Services use both, and a run
    that ignores the header is a run that gets rate-limited twice.
    """
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError, IndexError):
            return None
        if when is None:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - datetime.now(timezone.utc)).total_seconds()
    if seconds <= 0:
        return 0.0
    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def backoff_delay(
    *,
    attempt: int,
    policy: RetryPolicy = CLOUD_RETRY,
    retry_after: float | None = None,
    rand: Callable[[], float] = random.random,
) -> float:
    """How long to wait before attempt `attempt + 1` (attempts are 1-based).

    Full jitter over an exponentially growing window, which is the variant that
    actually spreads a cohort's worth of concurrent calls out instead of
    re-synchronising them. An explicit `Retry-After` wins: the service knows
    more than the formula does.
    """
    if retry_after is not None:
        return max(0.0, retry_after)
    window = min(policy.cap_seconds, policy.base_seconds * (2 ** max(0, attempt - 1)))
    if policy.jitter <= 0:
        return window
    floor = window * (1.0 - min(1.0, policy.jitter))
    return floor + (window - floor) * rand()


# ---------------------------------------------------------------------------
# Bounded concurrency — fed from --jobs
# ---------------------------------------------------------------------------

_limit_lock = threading.Lock()
_limit: int | None = None
_semaphore: threading.BoundedSemaphore | None = None


def set_concurrency_limit(jobs: int | None) -> None:
    """Cap how many provider calls may be in flight at once, process-wide.

    A local server is its own queue; a hosted API charges by the token and
    rate-limits by the minute, so the same `--jobs` the rest of the pipeline
    already takes has to mean something here too. None or 0 means unbounded,
    which is what the local path has always been.
    """
    global _limit, _semaphore
    with _limit_lock:
        if jobs is None or jobs <= 0:
            _limit, _semaphore = None, None
        else:
            _limit, _semaphore = jobs, threading.BoundedSemaphore(jobs)


def concurrency_limit() -> int | None:
    with _limit_lock:
        return _limit


@contextmanager
def concurrency_slot() -> Iterator[None]:
    with _limit_lock:
        gate = _semaphore
    if gate is None:
        yield
        return
    gate.acquire()
    try:
        yield
    finally:
        gate.release()


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------


def _read_body(response: object) -> str:
    raw = response.read()  # type: ignore[attr-defined]
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def _status_of(response: object) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = getattr(response, "code", None)
    try:
        return int(status) if status is not None else 200
    except (TypeError, ValueError):
        return 200


def _detail(body: str) -> str:
    cleaned = redact(body.strip())
    if len(cleaned) > _DETAIL_LIMIT:
        return cleaned[:_DETAIL_LIMIT] + "…"
    return cleaned


def send_json(
    *,
    url: str,
    payload: Mapping[str, object] | None = None,
    headers: Mapping[str, str] | None = None,
    method: str = "POST",
    service: str = "",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    error_factory: ErrorFactory = TransportError,
    retry: RetryPolicy = NO_RETRY,
    sleep: Callable[[float], None] = time.sleep,
    rand: Callable[[], float] = random.random,
) -> dict:
    """One JSON request in, one parsed JSON object out.

    Raises `error_factory(message)` on any failure, with the message already
    redacted — callers must not build their own error text from a response
    body, because that is a second redaction point and there is only meant to
    be one.

    `service` is how the provider is named to a person — "Ollama", "SJSU AI
    Gateway". It is only ever a label: a message that says only which URL
    failed makes an instructor work out for themselves which of their settings
    produced it.

    `sleep` and `rand` are injected so the retry tests run in no time at all
    and do not flake. There is no live service anywhere in the suite.
    """
    where = f"{service} at {url}" if service else url
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    sent_headers = {"Content-Type": "application/json", **(dict(headers) if headers else {})}
    if body is None:
        sent_headers.pop("Content-Type", None)

    attempts = max(1, retry.max_attempts)
    last: str = f"{where} did not answer"

    for attempt in range(1, attempts + 1):
        req = request.Request(url, data=body, headers=sent_headers, method=method)
        retry_after: float | None = None
        try:
            with concurrency_slot():
                with request.urlopen(req, timeout=timeout_seconds) as response:
                    status = _status_of(response)
                    raw = _read_body(response)
            if status >= 400:
                # A stand-in response object that reports its own failure
                # status rather than raising; handled the same way.
                last = f"{where} returned HTTP {status}: {_detail(raw)}"
                if status in RETRYABLE_STATUS and attempt < attempts:
                    sleep(backoff_delay(attempt=attempt, policy=retry, rand=rand))
                    continue
                raise error_factory(redact(last))
            return _decode(raw, where=where, error_factory=error_factory)
        except error.HTTPError as exc:
            status = int(getattr(exc, "code", 0) or 0)
            try:
                raw = _read_body(exc)
            except Exception:  # noqa: BLE001 - a body we cannot read is not the failure
                raw = ""
            retry_after = parse_retry_after(
                exc.headers.get("Retry-After") if exc.headers is not None else None
            )
            last = f"{where} returned HTTP {status}: {_detail(raw) or exc.reason}"
            if status in RETRYABLE_STATUS and attempt < attempts:
                sleep(
                    backoff_delay(
                        attempt=attempt, policy=retry, retry_after=retry_after, rand=rand
                    )
                )
                continue
            raise error_factory(redact(last)) from exc
        except error.URLError as exc:
            last = f"failed to reach {where}: {exc.reason}"
            if retry.retry_on_connection_error and attempt < attempts:
                sleep(backoff_delay(attempt=attempt, policy=retry, rand=rand))
                continue
            raise error_factory(redact(last)) from exc
        except TimeoutError as exc:
            last = f"{where} timed out after {timeout_seconds:g} s"
            if retry.retry_on_connection_error and attempt < attempts:
                sleep(backoff_delay(attempt=attempt, policy=retry, rand=rand))
                continue
            raise error_factory(redact(last)) from exc

    raise error_factory(redact(last))  # pragma: no cover - loop always returns or raises


def _decode(raw: str, *, where: str, error_factory: ErrorFactory) -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise error_factory(redact(f"invalid JSON response from {where}: {exc}")) from exc
    if not isinstance(parsed, dict):
        raise error_factory(
            redact(f"unexpected response from {where}: expected a JSON object")
        )
    return parsed


# ---------------------------------------------------------------------------
# Shared across all three dialects
# ---------------------------------------------------------------------------


def parse_json_object(raw: str) -> dict | None:
    """Pull the first JSON object out of a model's reply.

    Every dialect asks for JSON and every model occasionally wraps it in prose
    or a fence, so this is shared rather than reimplemented three times.
    """
    text = raw.strip()
    if not text:
        return None

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or last <= first:
        return None
    try:
        parsed = json.loads(text[first : last + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
