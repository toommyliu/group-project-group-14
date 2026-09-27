"""`PydanticAIVisionReader` — every operation, driven by `FunctionModel`.

Mirrors `test_dialect_vision_reader.py`'s structure and its `CASES` table on
purpose: the whole point of this reader is that it shares `vision_prompts`'
requests and parsers with `DialectVisionReader`, so the same scripted replies
must parse to the same results through either backend. No test here opens a
socket — `pydantic_ai_client.build_model` is monkeypatched to a
`FunctionModel` — and the whole file is skipped if the optional `pydantic-ai`
package is not installed, since it is an optional extra.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import pytest
from PIL import Image

pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai import BinaryContent  # noqa: E402
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart  # noqa: E402
from pydantic_ai.models.function import AgentInfo, FunctionModel  # noqa: E402
from pydantic_ai.usage import RequestUsage  # noqa: E402

from opengrader.core.llm import pydantic_ai_client as pac  # noqa: E402
from opengrader.core.llm.errors import LLMError  # noqa: E402
from opengrader.core.ocr.pydantic_ai_reader import PydanticAIVisionReader  # noqa: E402
from opengrader.core.ocr.vision_reader import TitleMetadataResult  # noqa: E402

_KEY = "sk-pydantic-ai-conformance-key-0123456789"


def _reader() -> PydanticAIVisionReader:
    return PydanticAIVisionReader(
        config=pac.PydanticAIConfig(
            model="anthropic:claude-haiku-4-5",
            api_key=_KEY,
            label="Claude (direct)",
            max_attempts=1,
        )
    )


@pytest.fixture
def crop(tmp_path: Path) -> Path:
    path = tmp_path / "crop.png"
    Image.new("RGB", (24, 12), color="white").save(path)
    return path


def _install_reply(monkeypatch: pytest.MonkeyPatch, reply: str) -> dict:
    """Script one reply; return a box the test can inspect afterward."""
    seen: dict = {}

    def func(messages, info: AgentInfo) -> ModelResponse:
        seen["messages"] = messages
        seen["model_settings"] = info.model_settings
        return ModelResponse(
            parts=[TextPart(content=reply)],
            usage=RequestUsage(input_tokens=3, output_tokens=2),
        )

    monkeypatch.setattr(pac, "build_model", lambda config: FunctionModel(func))
    return seen


def _user_prompt_content(seen: dict) -> list:
    last = seen["messages"][-1]
    return next(part.content for part in last.parts if isinstance(part, UserPromptPart))


# (operation, kwargs, what the model says, what the reader must return) — the
# same table test_dialect_vision_reader.py drives, so both backends are held
# to the same parsing behaviour for the same reply.
CASES = [
    (
        "extract_field_text",
        {"field_kind": "name"},
        '{"value": "Name: Ada  Lovelace"}',
        "Ada Lovelace",
    ),
    (
        "extract_field_text",
        {"field_kind": "sid"},
        '{"value": "12-345-6789"}',
        "123456789",
    ),
    (
        "extract_title_metadata",
        {},
        json.dumps(
            {
                "course_code": "CMPE30",
                "assignment_title": "Quiz 2",
                "assignment_version": "Version a",
                "raw_title": "CMPE30 Quiz 2 (a)",
            }
        ),
        TitleMetadataResult(
            course_code="CMPE30",
            assignment_title="Quiz 2",
            assignment_version="A",
            raw_title="CMPE30 Quiz 2 (a)",
        ),
    ),
    (
        "extract_question_prompt_and_answer",
        {},
        '```json\n{"prompt": "What is  a stack?", "answer": "LIFO"}\n```',
        ("What is a stack?", "LIFO"),
    ),
    (
        "extract_handwriting_only",
        {},
        '{"text": "  x = 4  "}',
        "x = 4",
    ),
    ("extract_page_number", {}, '{"value": "3"}', 3),
    ("page_has_identity_field", {}, '{"first_page": true}', True),
]


@pytest.mark.parametrize(
    ("operation", "kwargs", "reply", "expected"),
    CASES,
    ids=[f"{case[0]}-{i}" for i, case in enumerate(CASES)],
)
def test_every_operation_parses_the_same_as_dialectvisionreader(
    monkeypatch, crop, operation, kwargs, reply, expected
) -> None:
    _install_reply(monkeypatch, reply)

    result = getattr(_reader(), operation)(image_path=crop, **kwargs)

    assert result == expected


def test_the_crop_is_sent_as_binary_content_with_the_sniffed_media_type(
    monkeypatch, crop
) -> None:
    seen = _install_reply(monkeypatch, '{"text": "x = 4"}')

    _reader().extract_handwriting_only(image_path=crop)

    content = _user_prompt_content(seen)
    assert isinstance(content[0], str)  # the prompt text
    image = next(item for item in content if isinstance(item, BinaryContent))
    assert image.data == crop.read_bytes()
    assert image.media_type == "image/png"


def test_temperature_is_zero_and_num_predict_becomes_max_tokens(monkeypatch, crop) -> None:
    seen = _install_reply(monkeypatch, '{"value": ""}')

    _reader().extract_field_text(image_path=crop, field_kind="name")

    settings = dict(seen["model_settings"] or {})
    assert settings["temperature"] == 0
    assert settings["max_tokens"] == 128  # vision_prompts.field_text_request's num_predict


def test_math_latex_changes_the_prompt_pydantic_ai_sends(monkeypatch, crop) -> None:
    reader = _reader()

    seen = _install_reply(monkeypatch, '{"prompt": "", "answer": "$x^2$"}')
    reader.extract_question_prompt_and_answer(image_path=crop, math_latex=False)
    plain = _user_prompt_content(seen)[0]

    seen = _install_reply(monkeypatch, '{"prompt": "", "answer": "$x^2$"}')
    reader.extract_question_prompt_and_answer(image_path=crop, math_latex=True)
    latex = _user_prompt_content(seen)[0]

    assert "LaTeX" not in plain
    assert "LaTeX" in latex


def test_a_call_failure_surfaces_as_an_llm_error_with_the_key_redacted(
    monkeypatch, crop
) -> None:
    """Callers above the seam catch `LLMError` and fall through to Tesseract, so a failed read never loses the answer."""
    from pydantic_ai import ModelHTTPError

    def func(messages, info: AgentInfo) -> ModelResponse:
        raise ModelHTTPError(status_code=401, model_name="m", body={"error": f"bad key {_KEY}"})

    monkeypatch.setattr(pac, "build_model", lambda config: FunctionModel(func))

    with pytest.raises(LLMError) as caught:
        _reader().extract_handwriting_only(image_path=crop)

    assert "401" in str(caught.value)
    assert _KEY not in str(caught.value)


def test_reader_is_picklable_and_keeps_its_key_out_of_repr() -> None:
    """`batch.ExtractionJob` carries a reader into a `spawn`ed worker."""
    reader = _reader()

    clone = pickle.loads(pickle.dumps(reader))

    assert clone.config.api_key == _KEY
    assert clone.config.model == "anthropic:claude-haiku-4-5"
    assert _KEY not in repr(reader)
