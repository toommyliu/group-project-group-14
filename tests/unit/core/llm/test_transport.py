"""Retry, backoff, `Retry-After`, and redaction — driven by a fake HTTP layer.

§7.9's exit criterion for this phase is explicit: "no live service in the
suite." Every test here monkeypatches `urllib.request.urlopen` with a scripted
fake and injects `sleep`/`rand` into `send_json`, so nothing here opens a
socket and nothing here really sleeps for a retry backoff — nothing but the
handful of genuine thread-concurrency tests, which use a short real wait
purely to let threads rendezvous, never as a stand-in for a backoff delay.

Redaction (L8, §7.7) gets the most attention. The property under test is not
"redact() matches a regex" — it is "a secret that reaches this module by any
path (registered, echoed back by a service, embedded in a token shape, an
answer image quoted in an error body) never reaches the exception message a
caller logs." Several tests below exist only to pin exactly that.
"""

from __future__ import annotations

import base64
import io
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import format_datetime
from urllib.error import HTTPError, URLError

import pytest

from opengrader.core.llm import ollama_transport, transport
from opengrader.core.llm.errors import LLMError, TransportError
from opengrader.core.llm.ollama_client import OllamaConfig, OllamaError
from opengrader.core.llm.ollama_client import generate as ollama_generate
from opengrader.core.llm.transport import RetryPolicy

URL = "http://svc.example/x"


# ---------------------------------------------------------------------------
# A scripted fake HTTP layer — no network, no real sleeping
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Stands in for the object `urllib.request.urlopen` returns on success."""

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._raw = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


def _make_http_error(url: str, status: int, body: str, headers: dict | None = None) -> HTTPError:
    hdrs = Message()
    for key, value in (headers or {}).items():
        hdrs[key] = value
    return HTTPError(url, status, "error", hdrs, io.BytesIO(body.encode("utf-8")))


def _ok(body: dict):
    payload = json.dumps(body)

    def _respond(req):
        return _FakeResponse(200, payload)

    return _respond


def _raw_ok(text: str):
    def _respond(req):
        return _FakeResponse(200, text)

    return _respond


def _fails(status: int, body: str = "", headers: dict | None = None):
    def _respond(req):
        raise _make_http_error(req.full_url, status, body, headers)

    return _respond


def _connection_fails(reason: str = "connection refused"):
    def _respond(req):
        raise URLError(reason)

    return _respond


class _ScriptedTransport:
    """A fake `urlopen`: one scripted response/raise per call, in order."""

    def __init__(self, script) -> None:
        self._script = list(script)
        self.calls: list = []

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        if not self._script:
            raise AssertionError("fake transport ran out of scripted responses")
        step = self._script.pop(0)
        return step(req)


def _install(monkeypatch, script) -> _ScriptedTransport:
    fake = _ScriptedTransport(script)
    monkeypatch.setattr(transport.request, "urlopen", fake)
    return fake


@pytest.fixture
def registered_secret():
    """Register a secret and guarantee it is forgotten afterward.

    `register_secret` mutates module-level state shared by the whole process;
    without teardown a secret registered by one test would still be redacted
    (or, worse, assumed-absent) in the next one.
    """
    registered: list[str] = []

    def _register(value: str) -> str:
        transport.register_secret(value)
        registered.append(value)
        return value

    yield _register
    for value in registered:
        transport.forget_secret(value)


@pytest.fixture
def _reset_concurrency_limit():
    """`set_concurrency_limit` is process-wide state; a leak would serialise the rest of the suite."""
    yield
    transport.set_concurrency_limit(None)


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


def test_429_then_200_succeeds_and_makes_two_calls(monkeypatch) -> None:
    fake = _install(monkeypatch, [_fails(429, "rate limited"), _ok({"ok": True})])
    result = transport.send_json(
        url=URL, payload={"a": 1}, retry=transport.CLOUD_RETRY, sleep=lambda s: None, rand=lambda: 0.0
    )
    assert result == {"ok": True}
    assert len(fake.calls) == 2


def test_503_retried_up_to_max_attempts_then_raises(monkeypatch) -> None:
    policy = RetryPolicy(max_attempts=4, base_seconds=0.01, cap_seconds=0.02)
    fake = _install(monkeypatch, [_fails(503, "unavailable")] * 4)
    with pytest.raises(TransportError):
        transport.send_json(url=URL, payload={}, retry=policy, sleep=lambda s: None, rand=lambda: 0.0)
    assert len(fake.calls) == 4


def test_401_is_not_retried(monkeypatch) -> None:
    """A wrong key does not get righter on the second attempt."""
    fake = _install(monkeypatch, [_fails(401, "invalid api key")])
    with pytest.raises(TransportError):
        transport.send_json(url=URL, payload={}, retry=transport.CLOUD_RETRY, sleep=lambda s: None)
    assert len(fake.calls) == 1


def test_404_is_not_retried(monkeypatch) -> None:
    """A wrong model name does not get righter on the second attempt either."""
    fake = _install(monkeypatch, [_fails(404, "model not found")])
    with pytest.raises(TransportError):
        transport.send_json(url=URL, payload={}, retry=transport.CLOUD_RETRY, sleep=lambda s: None)
    assert len(fake.calls) == 1


def test_no_retry_policy_makes_exactly_one_attempt_even_for_a_429(monkeypatch) -> None:
    """Preserves the local Ollama path's behaviour: a server not answering fails fast."""
    fake = _install(monkeypatch, [_fails(429, "rate limited")])
    with pytest.raises(TransportError):
        transport.send_json(url=URL, payload={}, retry=transport.NO_RETRY, sleep=lambda s: None)
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# Backoff
# ---------------------------------------------------------------------------


def test_backoff_delay_grows_exponentially() -> None:
    policy = RetryPolicy(base_seconds=1.0, cap_seconds=100.0, jitter=0.0)
    delays = [transport.backoff_delay(attempt=a, policy=policy) for a in (1, 2, 3, 4)]
    assert delays == [1.0, 2.0, 4.0, 8.0]


def test_backoff_delay_is_capped() -> None:
    policy = RetryPolicy(base_seconds=1.0, cap_seconds=3.0, jitter=0.0)
    assert transport.backoff_delay(attempt=10, policy=policy) == 3.0


def test_backoff_delay_with_full_jitter_spans_0_to_window() -> None:
    policy = RetryPolicy(base_seconds=2.0, cap_seconds=100.0, jitter=1.0)
    window = 2.0 * (2 ** (3 - 1))  # attempt=3
    assert transport.backoff_delay(attempt=3, policy=policy, rand=lambda: 0.0) == 0.0
    assert transport.backoff_delay(attempt=3, policy=policy, rand=lambda: 1.0) == window


def test_backoff_delay_with_zero_jitter_is_exactly_the_window() -> None:
    policy = RetryPolicy(base_seconds=2.0, cap_seconds=100.0, jitter=0.0)
    window = 2.0 * (2 ** (3 - 1))
    assert transport.backoff_delay(attempt=3, policy=policy, rand=lambda: 0.37) == window


def test_send_json_sleeps_the_exact_backoff_sequence_across_retries(monkeypatch) -> None:
    _install(monkeypatch, [_fails(503)] * 3 + [_ok({"ok": True})])
    policy = RetryPolicy(max_attempts=4, base_seconds=1.0, cap_seconds=100.0, jitter=0.0)
    sleeps: list[float] = []
    result = transport.send_json(
        url=URL, payload={}, retry=policy, sleep=sleeps.append, rand=lambda: 0.0
    )
    assert result == {"ok": True}
    assert sleeps == [1.0, 2.0, 4.0]


# ---------------------------------------------------------------------------
# Retry-After
# ---------------------------------------------------------------------------


def test_parse_retry_after_delta_seconds() -> None:
    assert transport.parse_retry_after("5") == 5.0


def test_parse_retry_after_http_date() -> None:
    target = datetime.now(timezone.utc) + timedelta(seconds=30)
    header = format_datetime(target, usegmt=True)
    seconds = transport.parse_retry_after(header)
    assert seconds is not None
    assert 25.0 <= seconds <= 30.0


def test_parse_retry_after_past_date_yields_zero() -> None:
    target = datetime.now(timezone.utc) - timedelta(seconds=10)
    header = format_datetime(target, usegmt=True)
    assert transport.parse_retry_after(header) == 0.0


def test_parse_retry_after_clamped_to_max() -> None:
    assert transport.parse_retry_after(str(transport.MAX_RETRY_AFTER_SECONDS + 1000)) == (
        transport.MAX_RETRY_AFTER_SECONDS
    )


def test_retry_after_header_beats_the_computed_backoff(monkeypatch) -> None:
    """A service may say 'come back in 7 seconds'; the formula's own guess must not override it."""
    _install(monkeypatch, [_fails(429, headers={"Retry-After": "7"}), _ok({"ok": True})])
    sleeps: list[float] = []
    transport.send_json(
        url=URL, payload={}, retry=transport.CLOUD_RETRY, sleep=sleeps.append, rand=lambda: 0.9
    )
    assert sleeps == [7.0]


def test_retry_after_header_is_clamped_when_honoured_by_send_json(monkeypatch) -> None:
    """A service may say 'come back in an hour'; a grading run must not sit there for one."""
    _install(
        monkeypatch,
        [_fails(429, headers={"Retry-After": "999999"}), _ok({"ok": True})],
    )
    sleeps: list[float] = []
    transport.send_json(
        url=URL, payload={}, retry=transport.CLOUD_RETRY, sleep=sleeps.append, rand=lambda: 0.0
    )
    assert sleeps == [transport.MAX_RETRY_AFTER_SECONDS]


# ---------------------------------------------------------------------------
# Connection errors
# ---------------------------------------------------------------------------


def test_connection_error_retried_only_when_policy_allows(monkeypatch) -> None:
    fake = _install(monkeypatch, [_connection_fails(), _ok({"ok": True})])
    policy = RetryPolicy(max_attempts=2, base_seconds=0.0, retry_on_connection_error=True)
    result = transport.send_json(url=URL, payload={}, retry=policy, sleep=lambda s: None)
    assert result == {"ok": True}
    assert len(fake.calls) == 2


def test_connection_error_raises_through_error_factory_when_policy_disallows(monkeypatch) -> None:
    fake = _install(monkeypatch, [_connection_fails()])
    policy = RetryPolicy(max_attempts=4, retry_on_connection_error=False)

    class _ConnError(Exception):
        pass

    with pytest.raises(_ConnError):
        transport.send_json(url=URL, payload={}, retry=policy, error_factory=_ConnError)
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# Redaction — L8
# ---------------------------------------------------------------------------


def test_registered_secret_never_reaches_the_raised_message(monkeypatch, registered_secret) -> None:
    """Safety: a resolved API key must not leak through an error body, even echoed back by the service."""
    secret = registered_secret("sk-livekey-1234567890abcdefg")
    _install(monkeypatch, [_fails(401, json.dumps({"error": f"invalid key {secret}"}))])
    with pytest.raises(TransportError) as excinfo:
        transport.send_json(url=URL, payload={})
    assert secret not in str(excinfo.value)


def test_unregistered_bearer_token_is_scrubbed(monkeypatch) -> None:
    """A `Bearer <token>` in an error body is scrubbed even when never registered."""
    token = "abcdefghijklmnop1234567890"
    _install(monkeypatch, [_fails(401, json.dumps({"error": f"rejected: Bearer {token}"}))])
    with pytest.raises(TransportError) as excinfo:
        transport.send_json(url=URL, payload={})
    assert token not in str(excinfo.value)


def test_unregistered_x_api_key_header_value_is_scrubbed(monkeypatch) -> None:
    token = "abcdefghijklmnop1234567890"
    _install(
        monkeypatch,
        [_fails(401, json.dumps({"error": f"bad request, x-api-key: {token}"}))],
    )
    with pytest.raises(TransportError) as excinfo:
        transport.send_json(url=URL, payload={})
    assert token not in str(excinfo.value)


def test_unregistered_api_key_json_field_value_is_scrubbed(monkeypatch) -> None:
    token = "abcdefghijklmnop1234567890"
    # A raw response body, as a service would actually send it — not
    # double-JSON-encoded, which would escape the inner quotes and change
    # what is under test.
    body = f'{{"error": "bad request", "api_key": "{token}"}}'
    _install(monkeypatch, [_fails(401, body)])
    with pytest.raises(TransportError) as excinfo:
        transport.send_json(url=URL, payload={})
    assert token not in str(excinfo.value)


def test_unregistered_sk_shaped_token_is_scrubbed(monkeypatch) -> None:
    """A key-shaped token (`sk-...`) is caught by shape alone, never having been registered."""
    token = "sk-abcdefghijklmnopqrstuvwxyz123456"
    _install(monkeypatch, [_fails(400, json.dumps({"error": f"token {token} is malformed"}))])
    with pytest.raises(TransportError) as excinfo:
        transport.send_json(url=URL, payload={})
    assert token not in str(excinfo.value)


def test_long_base64_blob_is_elided_not_quoted(monkeypatch) -> None:
    """Safety: an echoed image must not reach a log — a student's handwriting is the failure this prevents."""
    blob = base64.b64encode(os.urandom(300)).decode("ascii")
    _install(monkeypatch, [_fails(400, json.dumps({"error": f"bad request, image={blob}"}))])
    with pytest.raises(TransportError) as excinfo:
        transport.send_json(url=URL, payload={})
    message = str(excinfo.value)
    assert blob not in message
    assert "bytes elided" in message


def test_authorization_header_value_never_reaches_a_401_message(monkeypatch) -> None:
    """The key itself must not appear in the message even when the service echoes the header back."""
    key = "sk-liveapikeyvalue1234567890"
    body = json.dumps({"error": {"message": f"Invalid Authorization: Bearer {key}"}})
    _install(monkeypatch, [_fails(401, body)])
    with pytest.raises(TransportError) as excinfo:
        transport.send_json(url=URL, payload={})
    assert key not in str(excinfo.value)


def test_redact_empty_string_returns_unchanged() -> None:
    assert transport.redact("") == ""


def test_redact_message_with_nothing_to_redact_is_unchanged() -> None:
    assert transport.redact("model not found") == "model not found"


# ---------------------------------------------------------------------------
# error_factory
# ---------------------------------------------------------------------------


def test_error_factory_receives_the_type_the_caller_asked_for(monkeypatch) -> None:
    class CustomError(Exception):
        pass

    _install(monkeypatch, [_fails(404, "not found")])
    with pytest.raises(CustomError):
        transport.send_json(url=URL, payload={}, error_factory=CustomError)


def test_default_error_factory_is_transport_error_and_llm_error(monkeypatch) -> None:
    _install(monkeypatch, [_fails(404, "not found")])
    with pytest.raises(TransportError) as excinfo:
        transport.send_json(url=URL, payload={})
    assert isinstance(excinfo.value, LLMError)


def test_ollama_error_still_comes_out_of_ollama_transport(monkeypatch) -> None:
    """The new hierarchy is what lets a consumer catch one LLMError type regardless of provider."""
    fake = _ScriptedTransport([_fails(404, "model not found")])
    monkeypatch.setattr(ollama_transport.request, "urlopen", fake)
    with pytest.raises(OllamaError) as excinfo:
        ollama_generate(prompt="hi", config=OllamaConfig())
    assert isinstance(excinfo.value, LLMError)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------


def test_non_json_body_raises_through_error_factory_naming_the_service(monkeypatch) -> None:
    _install(monkeypatch, [_raw_ok("not json at all")])
    with pytest.raises(TransportError, match="svc"):
        transport.send_json(url=URL, payload={}, service="svc")


def test_json_list_body_raises_through_error_factory_naming_the_service(monkeypatch) -> None:
    _install(monkeypatch, [_raw_ok(json.dumps([1, 2, 3]))])
    with pytest.raises(TransportError, match="svc"):
        transport.send_json(url=URL, payload={}, service="svc")


# ---------------------------------------------------------------------------
# service labelling
# ---------------------------------------------------------------------------


def test_service_label_is_included_in_the_message(monkeypatch) -> None:
    _install(monkeypatch, [_fails(404, "missing")])
    with pytest.raises(TransportError, match=r"^Ollama at http://svc\.example/x returned HTTP 404"):
        transport.send_json(url=URL, payload={}, service="Ollama")


def test_no_service_label_message_uses_the_bare_url(monkeypatch) -> None:
    _install(monkeypatch, [_fails(404, "missing")])
    with pytest.raises(TransportError, match=r"^http://svc\.example/x returned HTTP 404"):
        transport.send_json(url=URL, payload={})


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_concurrency_limit_caps_concurrent_slot_holders(_reset_concurrency_limit) -> None:
    transport.set_concurrency_limit(2)
    lock = threading.Lock()
    current = 0
    high_water = 0
    release = threading.Event()

    def worker() -> None:
        nonlocal current, high_water
        with transport.concurrency_slot():
            with lock:
                current += 1
                high_water = max(high_water, current)
            release.wait(timeout=2.0)
            with lock:
                current -= 1

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    # A brief real wait: not a backoff stand-in, just letting the two allowed
    # threads rendezvous inside the slot before the assertion below.
    time.sleep(0.05)
    assert high_water == 2
    release.set()
    for t in threads:
        t.join(timeout=2.0)
    assert high_water <= 2


def test_concurrency_limit_none_restores_unbounded(_reset_concurrency_limit) -> None:
    transport.set_concurrency_limit(3)
    assert transport.concurrency_limit() == 3
    transport.set_concurrency_limit(None)
    assert transport.concurrency_limit() is None

    lock = threading.Lock()
    current = 0
    high_water = 0
    release = threading.Event()

    def worker() -> None:
        nonlocal current, high_water
        with transport.concurrency_slot():
            with lock:
                current += 1
                high_water = max(high_water, current)
            release.wait(timeout=2.0)
            with lock:
                current -= 1

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    time.sleep(0.05)
    assert high_water == 5
    release.set()
    for t in threads:
        t.join(timeout=2.0)


# ---------------------------------------------------------------------------
# parse_json_object
# ---------------------------------------------------------------------------
