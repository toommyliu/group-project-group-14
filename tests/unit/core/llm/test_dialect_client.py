"""Tests for dialect_client usage recording."""

from __future__ import annotations

import json
from unittest import mock

import pytest

from opengrader.core.llm import dialect_client, usage
from opengrader.core.llm.dialects import DIALECT_NAMES


class FakeResponse:
    """Minimal response object for faking HTTP."""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class TestUsageRecording:
    """Test that dialect_client.generate records usage from the response."""

    def test_generate_records_usage_from_ollama_response(self, monkeypatch) -> None:
        """Ollama uses prompt_eval_count and eval_count."""

        def fake_send_json(*args, **kwargs):
            return {
                "response": "4",
                "prompt_eval_count": 100,
                "eval_count": 25,
            }

        monkeypatch.setattr("opengrader.core.llm.dialect_client.transport.send_json", fake_send_json)

        config = dialect_client.DialectConfig(
            dialect="ollama",
            host="http://127.0.0.1:11434",
            model="qwen2.5:7b",
        )

        result = dialect_client.generate(config=config, prompt="What is 2+2?")

        assert result == "4"
        input_tokens, output_tokens = usage.take_last()
        assert input_tokens == 100
        assert output_tokens == 25

    def test_generate_records_usage_from_openai_response(self, monkeypatch) -> None:
        """OpenAI uses prompt_tokens and completion_tokens."""

        def fake_send_json(*args, **kwargs):
            return {
                "choices": [{"message": {"content": "The answer is 4."}}],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                },
            }

        monkeypatch.setattr("opengrader.core.llm.dialect_client.transport.send_json", fake_send_json)

        config = dialect_client.DialectConfig(
            dialect="openai-chat",
            host="https://api.openai.com/v1",
            model="gpt-4o-mini",
        )

        result = dialect_client.generate(config=config, prompt="What is 2+2?")

        assert result == "The answer is 4."
        input_tokens, output_tokens = usage.take_last()
        assert input_tokens == 10
        assert output_tokens == 5

    def test_generate_records_usage_from_anthropic_response(self, monkeypatch) -> None:
        """Anthropic uses input_tokens and output_tokens."""

        def fake_send_json(*args, **kwargs):
            return {
                "content": [{"type": "text", "text": "The answer is 4."}],
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 8,
                },
            }

        monkeypatch.setattr("opengrader.core.llm.dialect_client.transport.send_json", fake_send_json)

        config = dialect_client.DialectConfig(
            dialect="anthropic-messages",
            host="https://api.anthropic.com/v1",
            model="claude-3-5-sonnet-20241022",
        )

        result = dialect_client.generate(config=config, prompt="What is 2+2?")

        assert result == "The answer is 4."
        input_tokens, output_tokens = usage.take_last()
        assert input_tokens == 20
        assert output_tokens == 8

    def test_generate_records_none_for_missing_tokens(self, monkeypatch) -> None:
        """If tokens are missing, they are recorded as None."""

        def fake_send_json(*args, **kwargs):
            return {
                "response": "4",
                # No token counts provided
            }

        monkeypatch.setattr("opengrader.core.llm.dialect_client.transport.send_json", fake_send_json)

        config = dialect_client.DialectConfig(
            dialect="ollama",
            host="http://127.0.0.1:11434",
            model="qwen2.5:7b",
        )

        result = dialect_client.generate(config=config, prompt="What is 2+2?")

        assert result == "4"
        input_tokens, output_tokens = usage.take_last()
        assert input_tokens is None
        assert output_tokens is None

    def test_generate_clears_usage_on_next_call(self, monkeypatch) -> None:
        """Each call's usage is cleared after take_last()."""

        def fake_send_json(*args, **kwargs):
            return {
                "response": "4",
                "prompt_eval_count": 100,
                "eval_count": 25,
            }

        monkeypatch.setattr("opengrader.core.llm.dialect_client.transport.send_json", fake_send_json)

        config = dialect_client.DialectConfig(
            dialect="ollama",
            host="http://127.0.0.1:11434",
            model="qwen2.5:7b",
        )

        # First call
        dialect_client.generate(config=config, prompt="What is 2+2?")
        input1, output1 = usage.take_last()
        assert input1 == 100

        # Second call with no tokens recorded
        def fake_send_json_no_tokens(*args, **kwargs):
            return {"response": "5"}

        monkeypatch.setattr("opengrader.core.llm.dialect_client.transport.send_json", fake_send_json_no_tokens)
        dialect_client.generate(config=config, prompt="What is 3+2?")
        input2, output2 = usage.take_last()
        assert input2 is None
        assert output2 is None

    def test_usage_not_recorded_on_error(self, monkeypatch) -> None:
        """If generate raises an error, usage is not recorded."""

        call_count = [0]

        def fake_send_json(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("Network error")
            return {"response": "4", "prompt_eval_count": 100, "eval_count": 25}

        monkeypatch.setattr("opengrader.core.llm.dialect_client.transport.send_json", fake_send_json)

        config = dialect_client.DialectConfig(
            dialect="ollama",
            host="http://127.0.0.1:11434",
            model="qwen2.5:7b",
        )

        # First call fails
        with pytest.raises(RuntimeError):
            dialect_client.generate(config=config, prompt="What is 2+2?")

        # No usage was recorded
        input_tokens, output_tokens = usage.take_last()
        assert input_tokens is None
        assert output_tokens is None


class TestUsageThreadLocal:
    """Test that usage.record_last and usage.take_last are thread-safe."""

    def test_take_last_clears_recorded_value(self) -> None:
        """take_last() clears the value so the next call returns (None, None)."""
        usage.record_last(100, 50)
        result1 = usage.take_last()
        assert result1 == (100, 50)

        result2 = usage.take_last()
        assert result2 == (None, None)

    def test_record_last_overwrites_previous(self) -> None:
        """Calling record_last() again overwrites the previous value."""
        usage.record_last(100, 50)
        usage.record_last(200, 75)
        result = usage.take_last()
        assert result == (200, 75)

    def test_record_last_with_none(self) -> None:
        """record_last can store (None, None)."""
        usage.record_last(None, None)
        result = usage.take_last()
        assert result == (None, None)

    def test_record_last_partial_none(self) -> None:
        """record_last can store partial None values."""
        usage.record_last(100, None)
        result = usage.take_last()
        assert result == (100, None)

        usage.record_last(None, 50)
        result = usage.take_last()
        assert result == (None, 50)
