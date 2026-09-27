"""The optional Pydantic AI backend — driven entirely by `FunctionModel`.

No test here opens a socket: `build_model` (the one seam `generate` uses to
reach the network) is monkeypatched to return a `pydantic_ai.models.function.
FunctionModel`, a scripted stand-in the package itself ships for exactly this
purpose. The whole module — including `pydantic_ai_client.py` — must still
import fine with the optional `pydantic-ai` package absent; this
file's own tests, which exercise `generate()`, cannot run without it, so the
whole file is skipped via `importorskip` rather than asserting anything that
requires the package.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai import ModelHTTPError  # noqa: E402
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart  # noqa: E402
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402
from pydantic_ai.usage import RequestUsage  # noqa: E402

from opengrader.core.llm import pydantic_ai_client as pac  # noqa: E402
from opengrader.core.llm import transport, usage as usage_module  # noqa: E402
from opengrader.core.llm.errors import LLMError  # noqa: E402


def _install_model(monkeypatch: pytest.MonkeyPatch, func) -> None:
    model = FunctionModel(func)
    monkeypatch.setattr(pac, "build_model", lambda config: model)


@pytest.fixture
def registered_secret():
    """Register a secret and guarantee it is forgotten afterward (see
    test_transport.py — this mutates the same process-wide set)."""
    registered: list[str] = []

    def _register(value: str) -> str:
        transport.register_secret(value)
        registered.append(value)
        return value

    yield _register
    for value in registered:
        transport.forget_secret(value)


def test_generate_returns_plain_text_when_no_schema_is_given(monkeypatch) -> None:
    def func(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content="ready")],
            usage=RequestUsage(input_tokens=2, output_tokens=1),
        )

    _install_model(monkeypatch, func)
    config = pac.PydanticAIConfig(model="anthropic:claude-haiku-4-5", max_attempts=1)

    result = pac.generate(config=config, prompt="say ready")

    assert result == "ready"


def test_generate_passes_the_schema_as_structured_output_and_reencodes_json(
    monkeypatch,
) -> None:
    def func(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content=json.dumps({"value": "Ada Lovelace"}))],
            usage=RequestUsage(input_tokens=5, output_tokens=4),
        )

    _install_model(monkeypatch, func)
    config = pac.PydanticAIConfig(model="anthropic:claude-haiku-4-5", max_attempts=1)
    schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }

    result = pac.generate(config=config, prompt="read the name", response_schema=schema)

    # A JSON *string*, exactly what vision_prompts.parse_* (built for the raw
    # HTTP dialects) already knows how to read.
    assert json.loads(result) == {"value": "Ada Lovelace"}


def test_generate_sends_images_as_binary_content_with_the_sniffed_media_type(
    monkeypatch,
) -> None:
    seen: dict = {}

    def func(messages, info: AgentInfo) -> ModelResponse:
        last = messages[-1]
        seen["content"] = next(
            part.content for part in last.parts if isinstance(part, UserPromptPart)
        )
        return ModelResponse(parts=[TextPart(content="ok")])

    _install_model(monkeypatch, func)
    config = pac.PydanticAIConfig(model="openai:gpt-5-mini", max_attempts=1)
    png_bytes = b"\x89PNG\r\n\x1a\nrest-of-a-fake-png"
    encoded = base64.b64encode(png_bytes).decode("ascii")

    pac.generate(config=config, prompt="describe this", images=[encoded])

    content = seen["content"]
    assert content[0] == "describe this"
    image = content[1]
    assert image.data == png_bytes
    assert image.media_type == "image/png"


def test_generate_sets_temperature_zero_and_num_predict_as_max_tokens(monkeypatch) -> None:
    seen: dict = {}

    def func(messages, info: AgentInfo) -> ModelResponse:
        seen["settings"] = dict(info.model_settings or {})
        return ModelResponse(parts=[TextPart(content="ok")])

    _install_model(monkeypatch, func)
    config = pac.PydanticAIConfig(model="groq:llama-4", max_attempts=1)

    pac.generate(config=config, prompt="hi", num_predict=64)

    assert seen["settings"]["temperature"] == 0
    assert seen["settings"]["max_tokens"] == 64


def test_generate_records_usage_from_the_result(monkeypatch) -> None:
    def func(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content="ok")],
            usage=RequestUsage(input_tokens=17, output_tokens=9),
        )

    _install_model(monkeypatch, func)
    config = pac.PydanticAIConfig(model="mistral:mistral-small-latest", max_attempts=1)
    usage_module.take_last()  # clear any leftover from another test on this thread

    pac.generate(config=config, prompt="hi")

    assert usage_module.take_last() == (17, 9)


def test_generate_retries_a_429_then_succeeds(monkeypatch) -> None:
    calls = {"n": 0}

    def func(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        if calls["n"] == 1:
            raise ModelHTTPError(status_code=429, model_name="m", body={"error": "slow down"})
        return ModelResponse(
            parts=[TextPart(content="ready")],
            usage=RequestUsage(input_tokens=1, output_tokens=1),
        )

    _install_model(monkeypatch, func)
    monkeypatch.setattr(pac.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(pac.transport, "backoff_delay", lambda **_kwargs: 0.0)
    config = pac.PydanticAIConfig(model="groq:llama-4", max_attempts=3)

    result = pac.generate(config=config, prompt="hi")

    assert result == "ready"
    assert calls["n"] == 2


def test_a_non_retryable_status_is_not_retried(monkeypatch) -> None:
    calls = {"n": 0}

    def func(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        raise ModelHTTPError(status_code=401, model_name="m", body={"error": "bad key"})

    _install_model(monkeypatch, func)
    config = pac.PydanticAIConfig(model="openai:gpt-5-mini", max_attempts=3)

    with pytest.raises(LLMError, match="401"):
        pac.generate(config=config, prompt="hi")

    assert calls["n"] == 1  # never retried


def test_a_connection_error_is_retried_then_raises_after_the_policy_is_exhausted(
    monkeypatch,
) -> None:
    class _FakeConnectTimeout(Exception):
        """Named like a real SDK's connection-timeout exception on purpose —
        `pydantic_ai_client._classify` matches by class name, not an
        exhaustive per-vendor import list."""

    calls = {"n": 0}

    def func(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        raise _FakeConnectTimeout("could not connect")

    _install_model(monkeypatch, func)
    monkeypatch.setattr(pac.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(pac.transport, "backoff_delay", lambda **_kwargs: 0.0)
    config = pac.PydanticAIConfig(model="bedrock:anthropic.claude-haiku-4-5-v1:0", max_attempts=3)

    with pytest.raises(LLMError):
        pac.generate(config=config, prompt="hi")

    assert calls["n"] == 3  # retried up to the policy, then gave up


def test_a_local_config_never_retries(monkeypatch) -> None:
    calls = {"n": 0}

    def func(messages, info: AgentInfo) -> ModelResponse:
        calls["n"] += 1
        raise ModelHTTPError(status_code=503, model_name="m", body={"error": "busy"})

    _install_model(monkeypatch, func)
    config = pac.PydanticAIConfig(model="openai:local-model", max_attempts=1)

    with pytest.raises(LLMError):
        pac.generate(config=config, prompt="hi")

    assert calls["n"] == 1


def test_a_registered_key_never_appears_in_a_raised_error(
    monkeypatch, registered_secret
) -> None:
    secret = registered_secret("sk-pydantic-ai-test-key-0123456789")

    def func(messages, info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(
            status_code=401, model_name="m", body={"error": f"bad key {secret}"}
        )

    _install_model(monkeypatch, func)
    config = pac.PydanticAIConfig(model="anthropic:claude-haiku-4-5", api_key=secret, max_attempts=1)

    with pytest.raises(LLMError) as caught:
        pac.generate(config=config, prompt="hi")

    assert secret not in str(caught.value)
    assert "401" in str(caught.value)


def test_generate_from_a_thread_with_a_running_event_loop_does_not_crash(
    monkeypatch,
) -> None:
    """The Textual TUI calls into this from a worker thread that already runs
    an asyncio loop; `Agent.run_sync` refuses that outright."""

    def func(messages, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[TextPart(content="ready")],
            usage=RequestUsage(input_tokens=1, output_tokens=1),
        )

    _install_model(monkeypatch, func)
    config = pac.PydanticAIConfig(model="anthropic:claude-haiku-4-5", max_attempts=1)

    async def _inside_a_running_loop() -> str:
        return pac.generate(config=config, prompt="hi")

    result = asyncio.run(_inside_a_running_loop())

    assert result == "ready"


def test_build_model_converts_a_missing_package_into_a_friendly_llm_error(monkeypatch) -> None:
    def _boom(prefix, config):
        raise ImportError("simulated: no module named openai")

    monkeypatch.setattr(pac, "_build_provider", _boom)
    config = pac.PydanticAIConfig(model="openai:gpt-5-mini")

    with pytest.raises(LLMError, match="uv sync --extra pydantic-ai"):
        pac._build_model_uncached(config)


def test_config_never_writes_its_key_to_the_environment(monkeypatch) -> None:
    import os

    before = dict(os.environ)
    config = pac.PydanticAIConfig(model="anthropic:claude-haiku-4-5", api_key="k" * 24)

    assert os.environ == before
    transport.forget_secret(config.api_key)


def test_pydantic_ai_config_is_picklable() -> None:
    import pickle

    config = pac.PydanticAIConfig(
        model="anthropic:claude-haiku-4-5",
        api_key="k" * 24,
        base_url="https://proxy.example.edu",
        max_attempts=3,
        label="Claude (direct)",
    )

    clone = pickle.loads(pickle.dumps(config))

    assert clone == config
    assert clone.api_key == "k" * 24
    transport.forget_secret(config.api_key)


def test_the_latency_probe_dispatches_a_pydantic_ai_config_to_this_client(monkeypatch) -> None:
    # AI Setup's Test connection and doctor's latency probe pick a generate
    # function from the config's type; a third config shape once fell through
    # to the Ollama client and failed on a missing `host`.
    from opengrader.core import app_config

    assert app_config._generate_for(pac.PydanticAIConfig(model="openai:x")) is pac.generate


def test_a_key_cut_in_half_by_the_length_limit_still_does_not_leak(
    monkeypatch, registered_secret
) -> None:
    """An SDK error that quotes the key right at the length limit: cutting
    the message first would leave half the key, which no longer matches the
    registered secret. The key here has no recognisable shape (like an AWS
    secret key), so only the registered-secret match can catch it."""
    secret = registered_secret("Zq8rT2vLm9XcA4bN7pQe1WsYd6HfJk3U")
    head = secret[:16]

    def func(messages, info: AgentInfo) -> ModelResponse:
        # The key starts 20 characters before the cut.
        padding = "x" * (pac._DETAIL_LIMIT - len(" signed ") - 20)
        raise RuntimeError(f"{padding} signed {secret} rejected")

    _install_model(monkeypatch, func)
    config = pac.PydanticAIConfig(model="bedrock:some-model", api_key=secret, max_attempts=1)

    with pytest.raises(LLMError) as caught:
        pac.generate(config=config, prompt="hi")

    assert head not in str(caught.value)


def test_a_traceback_of_the_raised_error_does_not_show_the_sdk_exception(
    monkeypatch, registered_secret
) -> None:
    """An uncaught LLMError prints its whole exception chain. The SDK's own
    exception is not redacted, so it must not be part of that chain."""
    import traceback

    secret = "unshaped-but-secret-" + "w" * 20

    def func(messages, info: AgentInfo) -> ModelResponse:
        raise RuntimeError(f"request headers were {{'x-goog-api-key': '{secret}'}}")

    _install_model(monkeypatch, func)
    config = pac.PydanticAIConfig(model="google:gemini-2.5-flash", max_attempts=1)

    with pytest.raises(LLMError) as caught:
        pac.generate(config=config, prompt="hi")

    printed = "".join(traceback.format_exception(caught.value))
    assert secret not in printed
