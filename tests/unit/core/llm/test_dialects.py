"""Golden request/response shapes for each wire dialect (§7.10 test map).

Three services, three shapes, one contract (`core/llm/dialects/base.Dialect`).
These tests pin the shape of each dialect's request body literally — a golden
body is only golden if a reviewer can read it in the diff — and check the
handful of properties the design document calls out by name: that `ollama`
never gets a credential, that a fourth dialect could not skip `build_body`
without the conformance loop noticing, and that no dialect module reaches for
HTTP or the grading domain itself (§7.2).

The `ollama` dialect additionally gets a *behaviour pin*: it is driven through
`ollama_transport.generate_raw`'s own fake-HTTP path, and the body that comes
out over the wire is asserted equal to what `OllamaDialect.build_body` builds
for the same arguments. §7.9 makes "the ollama dialect is a refactor, not a
rewrite" the exit criterion for this phase, and this is the test that says so.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import opengrader.core.llm.dialects as dialects_pkg
from opengrader.core.llm import ollama_transport
from opengrader.core.llm.dialects import DIALECT_NAMES, Dialect, DialectError, get_dialect
from opengrader.core.llm.dialects.anthropic_messages import ANTHROPIC_VERSION, DIALECT as ANTHROPIC
from opengrader.core.llm.dialects.base import media_type_for
from opengrader.core.llm.dialects.ollama import DIALECT as OLLAMA
from opengrader.core.llm.dialects.openai_chat import DIALECT as OPENAI_CHAT

# ---------------------------------------------------------------------------
# Shared fixtures for golden bodies
# ---------------------------------------------------------------------------

# Real magic-byte prefixes (§base.py's `_MAGIC_PREFIXES`), padded out so the
# 16-character sniff window `media_type_for` reads always has enough to look at.
IMG_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
IMG_JPEG = "/9j/4AAQSkZJRgABAQEAAAAAAAAA//4AL0FkbyMwPjs9"
IMG_UNKNOWN = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="  # base64 of plain ASCII, no known magic

SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
}


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------


class TestOllamaDialect:
    def test_plain_text_golden_body(self) -> None:
        body = OLLAMA.build_body(model="qwen2.5vl:7b", prompt="What is 2+2?")
        assert body == {
            "model": "qwen2.5vl:7b",
            "prompt": "What is 2+2?",
            "stream": False,
            "options": {"temperature": 0},
        }

    def test_vision_golden_body(self) -> None:
        body = OLLAMA.build_body(
            model="qwen2.5vl:7b",
            prompt="Transcribe the handwriting.",
            images=[IMG_PNG],
            response_schema=SCHEMA,
            num_predict=256,
            keep_alive="10m",
        )
        assert body == {
            "model": "qwen2.5vl:7b",
            "prompt": "Transcribe the handwriting.",
            "stream": False,
            "options": {"temperature": 0, "num_predict": 256},
            "images": [IMG_PNG],
            "format": SCHEMA,
            "keep_alive": "10m",
        }

    def test_generate_raw_body_is_exactly_what_build_body_would_produce(
        self, monkeypatch
    ) -> None:
        """Behaviour pin (§7.9): the ollama dialect is a refactor, not a rewrite.

        `generate_raw` must put exactly the body `OllamaDialect.build_body`
        would build onto the wire — this is the test that would fail if
        someone "improved" the request shape here instead of in a hosted
        dialect, where there is no behaviour to preserve.
        """
        captured: dict = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeOkResponse({"response": "ok"})

        monkeypatch.setattr(ollama_transport.request, "urlopen", fake_urlopen)

        kwargs = dict(
            model="qwen2.5vl:7b",
            prompt="Transcribe the handwriting.",
            images=[IMG_PNG],
            options=None,
            response_format=SCHEMA,
            keep_alive="10m",
            num_predict=256,
        )
        ollama_transport.generate_raw(
            host="http://127.0.0.1:11434",
            timeout_seconds=5.0,
            error_factory=RuntimeError,
            **kwargs,
        )

        expected = OLLAMA.build_body(
            model=kwargs["model"],
            prompt=kwargs["prompt"],
            images=kwargs["images"],
            response_schema=kwargs["response_format"],
            num_predict=kwargs["num_predict"],
            keep_alive=kwargs["keep_alive"],
            options=kwargs["options"],
        )
        assert captured["body"] == expected

    def test_reply_text_happy_path(self) -> None:
        assert OLLAMA.reply_text({"response": "  4  "}) == "4"

    def test_reply_text_malformed_body_returns_empty_string_never_raises(self) -> None:
        assert OLLAMA.reply_text({}) == ""
        assert OLLAMA.reply_text({"unexpected": "shape"}) == ""

    def test_error_text_reported_in_a_200_body(self) -> None:
        """Ollama's distinguishing case: an error arrives in a 200 body, not a status code."""
        assert OLLAMA.error_text({"error": "model 'x' not found"}) == "model 'x' not found"

    def test_error_text_absent(self) -> None:
        assert OLLAMA.error_text({}) is None
        assert OLLAMA.error_text({"error": ""}) is None
        assert OLLAMA.error_text({"error": None}) is None

    def test_model_names_happy_path_dedup_and_sort(self) -> None:
        body = {
            "models": [
                {"name": "llama3"},
                {"model": "qwen2.5vl:7b"},
                {"name": "llama3"},
                "not-a-dict",
                {"name": ""},
            ]
        }
        assert OLLAMA.model_names(body) == ["llama3", "qwen2.5vl:7b"]

    def test_model_names_raises_value_error_when_field_is_not_a_list(self) -> None:
        with pytest.raises(ValueError, match="models is not a list"):
            OLLAMA.model_names({"models": "not-a-list"})

    def test_auth_headers_are_empty_even_with_a_key(self) -> None:
        """Safety property, not a detail: a local server must never be sent a credential."""
        assert OLLAMA.auth_headers("some-api-key-value") == {}
        assert OLLAMA.auth_headers(None) == {}

    def test_generate_endpoint_trailing_slash_and_already_full_path(self) -> None:
        assert OLLAMA.generate_endpoint("http://127.0.0.1:11434") == (
            "http://127.0.0.1:11434/api/generate"
        )
        assert OLLAMA.generate_endpoint("http://127.0.0.1:11434/") == (
            "http://127.0.0.1:11434/api/generate"
        )
        assert OLLAMA.generate_endpoint("http://127.0.0.1:11434/api/generate") == (
            "http://127.0.0.1:11434/api/generate"
        )

    def test_models_endpoint_trailing_slash_and_already_full_path(self) -> None:
        assert OLLAMA.models_endpoint("http://127.0.0.1:11434") == (
            "http://127.0.0.1:11434/api/tags"
        )
        assert OLLAMA.models_endpoint("http://127.0.0.1:11434/") == (
            "http://127.0.0.1:11434/api/tags"
        )
        assert OLLAMA.models_endpoint("http://127.0.0.1:11434/api/tags") == (
            "http://127.0.0.1:11434/api/tags"
        )


class _FakeOkResponse:
    """A minimal `urlopen` response used by the ollama behaviour-pin test."""

    def __init__(self, payload: dict) -> None:
        self._raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def read(self) -> bytes:
        return self._raw


# ---------------------------------------------------------------------------
# OpenAI-chat
# ---------------------------------------------------------------------------


class TestOpenAIChatDialect:
    def test_plain_text_golden_body(self) -> None:
        body = OPENAI_CHAT.build_body(model="gpt-4o-mini", prompt="What is 2+2?")
        assert body == {
            "model": "gpt-4o-mini",
            "messages": [{"role": "user", "content": "What is 2+2?"}],
            "stream": False,
            "temperature": 0,
        }

    def test_vision_golden_body(self) -> None:
        body = OPENAI_CHAT.build_body(
            model="gpt-4o-mini",
            prompt="Transcribe the handwriting.",
            images=[IMG_PNG],
            response_schema=SCHEMA,
            num_predict=256,
        )
        assert body == {
            "model": "gpt-4o-mini",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Transcribe the handwriting."},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{IMG_PNG}"},
                        },
                    ],
                }
            ],
            "stream": False,
            "temperature": 0,
            "max_tokens": 256,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "response", "strict": False, "schema": SCHEMA},
            },
        }

    def test_reply_text_happy_path_string_content(self) -> None:
        body = {"choices": [{"message": {"content": "  4  "}}]}
        assert OPENAI_CHAT.reply_text(body) == "4"

    def test_reply_text_happy_path_list_of_content_parts(self) -> None:
        body = {
            "choices": [
                {
                    "message": {
                        "content": [
                            {"type": "text", "text": "Hello "},
                            {"type": "text", "text": "world"},
                            {"type": "refusal", "text": "ignored"},
                        ]
                    }
                }
            ]
        }
        assert OPENAI_CHAT.reply_text(body) == "Hello world"

    def test_reply_text_malformed_body_returns_empty_string_never_raises(self) -> None:
        assert OPENAI_CHAT.reply_text({}) == ""
        assert OPENAI_CHAT.reply_text({"choices": []}) == ""
        assert OPENAI_CHAT.reply_text({"choices": ["not-a-dict"]}) == ""
        assert OPENAI_CHAT.reply_text({"choices": [{"message": "not-a-dict"}]}) == ""
        assert OPENAI_CHAT.reply_text({"choices": [{"message": {"content": None}}]}) == ""

    def test_error_text_dict_form(self) -> None:
        body = {"error": {"message": "invalid api key"}}
        assert OPENAI_CHAT.error_text(body) == "invalid api key"

    def test_error_text_bare_string_form(self) -> None:
        assert OPENAI_CHAT.error_text({"error": "boom"}) == "boom"

    def test_error_text_absent(self) -> None:
        assert OPENAI_CHAT.error_text({}) is None
        assert OPENAI_CHAT.error_text({"error": {}}) is None
        assert OPENAI_CHAT.error_text({"error": None}) is None

    def test_model_names_happy_path_dedup_and_sort(self) -> None:
        body = {
            "data": [
                {"id": "gpt-4o"},
                {"id": "gpt-4o-mini"},
                {"id": "gpt-4o"},
                "not-a-dict",
                {"id": ""},
            ]
        }
        assert OPENAI_CHAT.model_names(body) == ["gpt-4o", "gpt-4o-mini"]

    def test_model_names_raises_value_error_when_field_is_not_a_list(self) -> None:
        with pytest.raises(ValueError, match="data is not a list"):
            OPENAI_CHAT.model_names({"data": "not-a-list"})

    def test_auth_headers_send_bearer_token(self) -> None:
        assert OPENAI_CHAT.auth_headers("sk-abc123") == {"Authorization": "Bearer sk-abc123"}

    def test_auth_headers_empty_without_a_key(self) -> None:
        assert OPENAI_CHAT.auth_headers(None) == {}
        assert OPENAI_CHAT.auth_headers("") == {}

    def test_generate_endpoint_trailing_slash_and_already_full_path(self) -> None:
        assert OPENAI_CHAT.generate_endpoint("https://api.openai.com/v1") == (
            "https://api.openai.com/v1/chat/completions"
        )
        assert OPENAI_CHAT.generate_endpoint("https://api.openai.com/v1/") == (
            "https://api.openai.com/v1/chat/completions"
        )
        assert OPENAI_CHAT.generate_endpoint(
            "https://api.openai.com/v1/chat/completions"
        ) == "https://api.openai.com/v1/chat/completions"

    def test_models_endpoint_trailing_slash_and_already_full_path(self) -> None:
        assert OPENAI_CHAT.models_endpoint("https://api.openai.com/v1") == (
            "https://api.openai.com/v1/models"
        )
        assert OPENAI_CHAT.models_endpoint("https://api.openai.com/v1/") == (
            "https://api.openai.com/v1/models"
        )
        assert OPENAI_CHAT.models_endpoint("https://api.openai.com/v1/models") == (
            "https://api.openai.com/v1/models"
        )


# ---------------------------------------------------------------------------
# Anthropic-messages
# ---------------------------------------------------------------------------


class TestAnthropicMessagesDialect:
    def test_plain_text_golden_body(self) -> None:
        body = ANTHROPIC.build_body(model="claude-3-5-sonnet-20241022", prompt="What is 2+2?")
        assert body == {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "What is 2+2?"}],
            "temperature": 0,
        }

    def test_vision_golden_body(self) -> None:
        body = ANTHROPIC.build_body(
            model="claude-3-5-sonnet-20241022",
            prompt="Transcribe the handwriting.",
            images=[IMG_PNG],
            response_schema=SCHEMA,
            num_predict=256,
        )
        expected_text = (
            "Transcribe the handwriting.\n\nRespond with JSON matching this schema: "
            + json.dumps(SCHEMA)
        )
        assert body == {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 256,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": IMG_PNG,
                            },
                        },
                        {"type": "text", "text": expected_text},
                    ],
                }
            ],
            "temperature": 0,
        }

    def test_reply_text_happy_path(self) -> None:
        body = {"content": [{"type": "text", "text": "4"}, {"type": "other", "text": "x"}]}
        assert ANTHROPIC.reply_text(body) == "4"

    def test_reply_text_malformed_body_returns_empty_string_never_raises(self) -> None:
        assert ANTHROPIC.reply_text({}) == ""
        assert ANTHROPIC.reply_text({"content": "not-a-list"}) == ""
        assert ANTHROPIC.reply_text({"content": ["not-a-dict"]}) == ""

    def test_error_text_dict_form(self) -> None:
        assert ANTHROPIC.error_text({"error": {"message": "overloaded"}}) == "overloaded"

    def test_error_text_bare_string_form(self) -> None:
        assert ANTHROPIC.error_text({"error": "boom"}) == "boom"

    def test_error_text_absent(self) -> None:
        assert ANTHROPIC.error_text({}) is None
        assert ANTHROPIC.error_text({"error": {}}) is None
        assert ANTHROPIC.error_text({"error": None}) is None

    def test_model_names_happy_path_dedup_and_sort(self) -> None:
        body = {
            "data": [
                {"id": "claude-3-5-sonnet-20241022"},
                {"id": "claude-3-opus-20240229"},
                {"id": "claude-3-5-sonnet-20241022"},
                "not-a-dict",
                {"id": ""},
            ]
        }
        assert ANTHROPIC.model_names(body) == [
            "claude-3-5-sonnet-20241022",
            "claude-3-opus-20240229",
        ]

    def test_model_names_raises_value_error_when_field_is_not_a_list(self) -> None:
        with pytest.raises(ValueError, match="data is not a list"):
            ANTHROPIC.model_names({"data": "not-a-list"})

    def test_auth_headers_send_x_api_key_and_version(self) -> None:
        assert ANTHROPIC.auth_headers("my-key") == {
            "anthropic-version": ANTHROPIC_VERSION,
            "x-api-key": "my-key",
        }

    def test_auth_headers_still_sends_version_with_no_key(self) -> None:
        assert ANTHROPIC.auth_headers(None) == {"anthropic-version": ANTHROPIC_VERSION}

    def test_generate_endpoint_trailing_slash_and_already_full_path(self) -> None:
        assert ANTHROPIC.generate_endpoint("https://api.anthropic.com/v1") == (
            "https://api.anthropic.com/v1/messages"
        )
        assert ANTHROPIC.generate_endpoint("https://api.anthropic.com/v1/") == (
            "https://api.anthropic.com/v1/messages"
        )
        assert ANTHROPIC.generate_endpoint("https://api.anthropic.com/v1/messages") == (
            "https://api.anthropic.com/v1/messages"
        )

    def test_models_endpoint_trailing_slash_and_already_full_path(self) -> None:
        assert ANTHROPIC.models_endpoint("https://api.anthropic.com/v1") == (
            "https://api.anthropic.com/v1/models"
        )
        assert ANTHROPIC.models_endpoint("https://api.anthropic.com/v1/") == (
            "https://api.anthropic.com/v1/models"
        )
        assert ANTHROPIC.models_endpoint("https://api.anthropic.com/v1/models") == (
            "https://api.anthropic.com/v1/models"
        )


# ---------------------------------------------------------------------------
# Cross-cutting: media_type_for, get_dialect, conformance, structural pins
# ---------------------------------------------------------------------------


class TestMediaTypeFor:
    def test_png_magic_bytes(self) -> None:
        assert media_type_for(IMG_PNG) == "image/png"

    def test_jpeg_magic_bytes(self) -> None:
        assert media_type_for(IMG_JPEG) == "image/jpeg"

    def test_unrecognised_payload_falls_back_to_png(self) -> None:
        assert media_type_for(IMG_UNKNOWN) == "image/png"

    def test_sniffed_type_reaches_the_openai_chat_body(self) -> None:
        body = OPENAI_CHAT.build_body(
            model="gpt-4o-mini", prompt="describe", images=[IMG_JPEG]
        )
        image_url = body["messages"][0]["content"][1]["image_url"]["url"]
        assert image_url.startswith("data:image/jpeg;base64,")

    def test_sniffed_type_reaches_the_anthropic_body(self) -> None:
        body = ANTHROPIC.build_body(
            model="claude-3-5-sonnet-20241022", prompt="describe", images=[IMG_JPEG]
        )
        assert body["messages"][0]["content"][0]["source"]["media_type"] == "image/jpeg"


class TestGetDialect:
    def test_returns_a_singleton_satisfying_the_dialect_protocol_for_every_name(self) -> None:
        for name in DIALECT_NAMES:
            dialect = get_dialect(name)
            assert isinstance(dialect, Dialect)
            assert get_dialect(name) is dialect

    def test_unknown_name_raises_dialect_error_naming_the_known_dialects(self) -> None:
        with pytest.raises(DialectError) as excinfo:
            get_dialect("not-a-real-dialect")
        message = str(excinfo.value)
        for name in DIALECT_NAMES:
            assert name in message


class TestConformance:
    """A fourth dialect cannot be added without meeting the bar this loop sets."""

    @pytest.mark.parametrize("name", DIALECT_NAMES)
    def test_build_body_is_json_serialisable_for_a_vision_call(self, name: str) -> None:
        dialect = get_dialect(name)
        body = dialect.build_body(
            model="some-model",
            prompt="Transcribe the handwriting.",
            images=[IMG_PNG],
            response_schema=SCHEMA,
            num_predict=64,
            keep_alive="10m",
            options={"temperature": 0.2},
        )
        assert isinstance(body, dict)
        json.dumps(body)  # must not raise


# ---------------------------------------------------------------------------
# Structural pin: a dialect performs no HTTP and knows no grading domain (§7.2)
# ---------------------------------------------------------------------------

_DIALECTS_DIR = Path(dialects_pkg.__file__).parent

# §7.2: "A dialect performs no HTTP."
_FORBIDDEN_HTTP_ROOTS = {"urllib", "http", "requests", "httpx"}

# §7.2: "A dialect knows nothing about grading, OCR, rubrics or submissions."
_FORBIDDEN_DOMAIN_PARTS = {"grading", "submissions", "qa_file"}


def _dialect_modules() -> list[Path]:
    return sorted(p for p in _DIALECTS_DIR.glob("*.py") if p.name != "__pycache__")


def _imported_module_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module)
    return names


@pytest.mark.parametrize("path", _dialect_modules(), ids=lambda p: p.name)
def test_no_dialect_module_performs_http_or_names_the_grading_domain(path: Path) -> None:
    """§7.2's two hard constraints, enforced as a test rather than a promise in prose."""
    for module_name in _imported_module_names(path):
        root = module_name.split(".")[0]
        assert root not in _FORBIDDEN_HTTP_ROOTS, (
            f"{path.name} imports {module_name!r} — a dialect performs no HTTP; "
            "that belongs in core/llm/transport.py (§7.2)."
        )
        parts = set(module_name.split("."))
        offending = parts & _FORBIDDEN_DOMAIN_PARTS
        assert not offending, (
            f"{path.name} imports {module_name!r}, naming {offending}; a dialect "
            "is the wire, not the domain (§7.2)."
        )


def test_the_structural_pin_can_actually_see_a_violation(tmp_path: Path) -> None:
    """Guard the guard: a scanner that always reports clean is not a test."""
    offender = tmp_path / "offender.py"
    offender.write_text(
        "from urllib import request\n"
        "from opengrader.core.grading import rubric\n",
        encoding="utf-8",
    )
    names = _imported_module_names(offender)
    assert any(n.split(".")[0] in _FORBIDDEN_HTTP_ROOTS for n in names)
    assert any("grading" in n.split(".") for n in names)
