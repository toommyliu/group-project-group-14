from __future__ import annotations

import pickle
from pathlib import Path

import pytest

from opengrader.core.ocr import ollama_vlm
from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig
from opengrader.core.ocr.vision_reader import (
    OllamaVisionReader,
    TitleMetadataResult,
)

_SENTINEL_TITLE = TitleMetadataResult(
    course_code="CMPE30",
    assignment_title="Quiz 2",
    assignment_version="A",
    raw_title="CMPE 30 Quiz 2 (A)",
)

_DELEGATION_CASES = [
    pytest.param(
        "extract_field_text",
        "extract_field_text",
        {"field_kind": "name"},
        "Ada Lovelace",
        id="extract_field_text",
    ),
    pytest.param(
        "extract_title_metadata",
        "extract_title_metadata",
        {},
        _SENTINEL_TITLE,
        id="extract_title_metadata",
    ),
    pytest.param(
        "extract_question_prompt_and_answer",
        "extract_question_prompt_and_answer",
        {"math_latex": True},
        ("What is 2+2?", "4"),
        id="extract_question_prompt_and_answer",
    ),
    pytest.param(
        "extract_handwriting_only",
        "extract_handwriting_only",
        {"math_latex": True},
        "[unclear]",
        id="extract_handwriting_only",
    ),
    pytest.param(
        "extract_page_number",
        "extract_page_number",
        {},
        3,
        id="extract_page_number",
    ),
    pytest.param(
        "page_has_identity_field",
        "page_has_identity_field",
        {},
        True,
        id="page_has_identity_field",
    ),
]


@pytest.mark.parametrize(
    "method_name, ollama_vlm_attr, extra_kwargs, sentinel", _DELEGATION_CASES
)
def test_reader_forwards_the_call_to_ollama_vlm_with_its_own_config_and_returns_unchanged(
    monkeypatch, method_name, ollama_vlm_attr, extra_kwargs, sentinel
) -> None:
    """`extract_student_field` is deliberately not in this table: it is an
    alias of `extract_field_text` with no caller anywhere in the tree (see the
    module docstring of vision_reader.py), so the protocol does not carry it."""

    config = OllamaVisionConfig(model="delegation-test-model")
    reader = OllamaVisionReader(config=config)
    image_path = Path("/crops/does-not-need-to-exist.png")

    captured: dict = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setattr(ollama_vlm, ollama_vlm_attr, fake)

    method = getattr(reader, method_name)
    result = method(image_path=image_path, **extra_kwargs)

    assert captured["image_path"] == image_path
    assert captured["config"] is config
    for key, value in extra_kwargs.items():
        assert captured[key] == value
    assert result == sentinel


def test_extract_field_text_forwards_sid_field_kind_too(monkeypatch) -> None:
    """The table above only exercises `field_kind="name"`; sid is the other
    branch and deserves its own assertion since the two produce different
    prompts inside ollama_vlm (not under test here, but the forwarding is)."""

    reader = OllamaVisionReader(config=OllamaVisionConfig())
    captured: dict = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return "123456789"

    monkeypatch.setattr(ollama_vlm, "extract_field_text", fake)

    result = reader.extract_field_text(image_path=Path("crop.png"), field_kind="sid")

    assert captured["field_kind"] == "sid"
    assert result == "123456789"


def test_math_latex_default_of_false_is_forwarded_when_the_caller_omits_it(
    monkeypatch,
) -> None:
    """`extract_question_prompt_and_answer` and `extract_handwriting_only` both
    default `math_latex` to False; a caller that omits it must still see that
    default land on the ollama_vlm call, not just whatever ollama_vlm itself
    would default to if the kwarg were dropped."""

    reader = OllamaVisionReader(config=OllamaVisionConfig())
    captured: dict = {}

    def fake(**kwargs):
        captured.update(kwargs)
        return ("prompt", "answer")

    monkeypatch.setattr(ollama_vlm, "extract_question_prompt_and_answer", fake)

    reader.extract_question_prompt_and_answer(image_path=Path("crop.png"))

    assert captured["math_latex"] is False


def test_monkeypatching_ollama_vlm_after_the_reader_is_constructed_is_still_observed(
    monkeypatch,
) -> None:
    """Late binding is the point of `OllamaVisionReader`. Every method does
    `from . import ollama_vlm` *inside the method body* and calls
    `ollama_vlm.extract_field_text(...)` rather than binding the function at
    class-definition time (e.g. `extract_field_text = ollama_vlm.extract_field_text`
    as a class attribute, or importing the bare name at module load).

    If a name were bound at class-definition time, it would already hold the
    real `ollama_vlm.extract_field_text` object by the time any test's
    `monkeypatch.setattr(ollama_vlm, "extract_field_text", fake)` ran — the
    reader's own reference would not change just because the module's
    attribute did. Every extraction test in the suite that monkeypatches
    `ollama_vlm` (test_ollama_vlm_hygiene.py, test_math_ocr.py, the
    student_file/batch extraction tests, and the stub-reader exit-criterion
    test in tests/unit/core/submissions/) relies on the module-at-call-time
    behaviour this test pins. Without it, none of those patches would take
    effect and the whole extraction test-suite would silently become a
    no-op that always talks to the real ollama_vlm functions.
    """

    reader = OllamaVisionReader(config=OllamaVisionConfig())  # constructed first

    def fake_extract_field_text(*, image_path, field_kind, config):
        return "late-bound-value"

    # Patched only *after* the reader already exists.
    monkeypatch.setattr(ollama_vlm, "extract_field_text", fake_extract_field_text)

    result = reader.extract_field_text(image_path=Path("crop.png"), field_kind="name")

    assert result == "late-bound-value"


def test_ollama_vision_reader_is_picklable() -> None:
    """`ExtractionJob` (core/submissions/batch.py) carries its `vision_reader`
    across a `spawn`ed worker process boundary whenever a batch resolves more
    than one worker. A reader implementation that closed over a socket, a
    lambda, or anything else unpicklable would import fine, pass every
    single-process test, and then fail only under the default multi-worker
    path — exactly the failure mode that is expensive to debug and easy to
    miss in a single-process test run. Round-tripping through pickle here is
    the cheap check that catches it up front.
    """

    reader = OllamaVisionReader(config=OllamaVisionConfig(model="pickle-test-model"))

    restored = pickle.loads(pickle.dumps(reader))

    assert restored == reader
    assert restored.config == reader.config
