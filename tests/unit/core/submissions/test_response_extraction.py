"""Tests for response-text extraction: text-layer typed capture, template
vision-model fill, and the ExtractionProgress reporter."""

from pathlib import Path

import opengrader.core.submissions.processor as processor
from opengrader.core.submissions.processor import (
    ExtractionProgress,
    _extract_questions_from_text,
    _fill_responses_with_vlm,
    _split_prompt_and_response,
    _TextLine,
)
from opengrader.core.submissions.student_file import StudentSubmissionQuestion


def _line(text: str) -> _TextLine:
    return _TextLine(page=1, page_w=612, page_h=792, x0=0, y0=0, x1=10, y1=10, text=text)


# --------------------------------------------------------------------------- #
# _split_prompt_and_response
# --------------------------------------------------------------------------- #


def test_split_on_answer_marker() -> None:
    prompt, response = _split_prompt_and_response("What is 2+2?\nAnswer: 4")
    assert prompt.strip() == "What is 2+2?"
    assert response.strip() == "4"


def test_split_on_various_markers() -> None:
    for marker in ("Answer:", "Ans.", "Response -", "Solution:"):
        prompt, response = _split_prompt_and_response(f"Prompt text\n{marker} the value")
        assert prompt.strip() == "Prompt text"
        assert response.strip() == "the value"


def test_split_on_blank_line_when_no_marker() -> None:
    prompt, response = _split_prompt_and_response("The prompt paragraph.\n\nThe typed answer.")
    assert prompt.strip() == "The prompt paragraph."
    assert response.strip() == "The typed answer."


def test_split_keeps_all_as_prompt_when_ambiguous() -> None:
    # No marker, no blank line -> conservative: never guess, response empty.
    prompt, response = _split_prompt_and_response("Capital of France?\nParis")
    assert prompt.strip() == "Capital of France?\nParis"
    assert response == ""


def test_marker_inside_prompt_line_not_split_midline() -> None:
    # The marker is anchored to a line start, so "answer" mid-sentence is safe.
    prompt, response = _split_prompt_and_response("Explain your answer below.\n\nBecause reasons.")
    assert prompt.strip() == "Explain your answer below."
    assert response.strip() == "Because reasons."


# --------------------------------------------------------------------------- #
# _extract_questions_from_text
# --------------------------------------------------------------------------- #


def test_text_layer_captures_typed_response() -> None:
    lines = [
        _line("1. (5 points) What is 2+2?"),
        _line("Answer: 4"),
        _line("2. (5 points) Name a prime.\n"),
        _line("Answer: 7"),
    ]
    questions = _extract_questions_from_text(lines)
    by_id = {q.question_id: q for q in questions}
    assert by_id["q1"].prompt == "What is 2+2?"
    assert by_id["q1"].response_text == "4"
    assert by_id["q1"].possible_points == 5.0
    assert by_id["q2"].response_text == "7"


# --------------------------------------------------------------------------- #
# _fill_responses_with_vlm (template mode)
# --------------------------------------------------------------------------- #


def _question(qid: str, image: str | None, text: str = "") -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=qid,
        prompt="",
        possible_points=None,
        response_image_path=image,
        response_text=text,
        source_segments=(),
    )


def test_vlm_fill_transcribes_each_crop(monkeypatch, tmp_path: Path) -> None:
    calls: list[Path] = []

    def fake_vlm(*, image_path, config, math_latex=False):
        calls.append(Path(image_path))
        return ("prompt ignored", f"answer for {Path(image_path).stem}")

    monkeypatch.setattr(processor, "extract_question_prompt_and_answer", fake_vlm)

    questions = [_question("q1", "responses/q1.png"), _question("q2", "responses/q2.png")]
    ticks: list[str] = []
    progress = ExtractionProgress(on_response=lambda pdf, qid: ticks.append(qid))

    filled = _fill_responses_with_vlm(
        questions,
        tmp_path,
        config := object(),  # noqa: F841 - config is passed through untouched
        pdf_name="submission-0001.pdf",
        progress=progress,
        progress_callback=lambda _m: None,
    )

    assert [q.response_text for q in filled] == ["answer for q1", "answer for q2"]
    assert ticks == ["q1", "q2"]
    assert [p.name for p in calls] == ["q1.png", "q2.png"]


def test_vlm_fill_skips_already_filled_and_imageless(monkeypatch, tmp_path: Path) -> None:
    def fake_vlm(*, image_path, config):
        return ("", "SHOULD NOT BE USED")

    monkeypatch.setattr(processor, "extract_question_prompt_and_answer", fake_vlm)

    questions = [
        _question("q1", "responses/q1.png", text="already graded text"),
        _question("q2", None),  # no crop image
    ]
    ticks: list[str] = []
    filled = _fill_responses_with_vlm(
        questions,
        tmp_path,
        object(),
        pdf_name="s.pdf",
        progress=ExtractionProgress(on_response=lambda p, q: ticks.append(q)),
        progress_callback=lambda _m: None,
    )
    assert filled[0].response_text == "already graded text"
    assert filled[1].response_text == ""
    assert ticks == []  # neither question triggered a transcription tick


def test_vlm_failure_falls_back_to_ocr(monkeypatch, tmp_path: Path) -> None:
    def boom(*, image_path, config):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(processor, "extract_question_prompt_and_answer", boom)
    monkeypatch.setattr(
        processor,
        "_ocr_image_with_confidence",
        lambda path, psm=6: ("ocr fallback text", 55.0),
    )

    warnings: list[str] = []
    filled = _fill_responses_with_vlm(
        [_question("q1", "responses/q1.png")],
        tmp_path,
        object(),
        pdf_name="s.pdf",
        progress=ExtractionProgress(),
        progress_callback=warnings.append,
    )
    assert filled[0].response_text == "ocr fallback text"
    # The Tesseract fallback also carries a real transcription confidence.
    assert filled[0].response_confidence == 55.0
    assert any("Warning" in w for w in warnings)


# --------------------------------------------------------------------------- #
# ExtractionProgress
# --------------------------------------------------------------------------- #


def test_extraction_progress_noop_by_default() -> None:
    # A bare reporter must be safely callable (core calls it unconditionally).
    progress = ExtractionProgress()
    progress.file_start("x.pdf", 3)
    progress.response("x.pdf", "q1")


def test_extraction_progress_invokes_callbacks() -> None:
    starts: list[tuple[str, int]] = []
    responses: list[tuple[str, str]] = []
    progress = ExtractionProgress(
        on_file_start=lambda pdf, n: starts.append((pdf, n)),
        on_response=lambda pdf, qid: responses.append((pdf, qid)),
    )
    progress.file_start("a.pdf", 2)
    progress.response("a.pdf", "q1")
    assert starts == [("a.pdf", 2)]
    assert responses == [("a.pdf", "q1")]


# --------------------------------------------------------------------------- #
# Escalation: cheap reader first, a model only where it read badly
# --------------------------------------------------------------------------- #


def _escalating_fill(
    questions,
    tmp_path: Path,
    *,
    ocr: tuple[str, float | None],
    vlm_text: str = "model reading",
    floor: float = 60.0,
    monkeypatch,
):
    """Run the template fill in escalated order with both readers stubbed."""
    vlm_calls: list[str] = []

    def fake_vlm(*, image_path, config, math_latex=False):
        vlm_calls.append(Path(image_path).name)
        return ("prompt ignored", vlm_text)

    monkeypatch.setattr(processor, "extract_question_prompt_and_answer", fake_vlm)
    monkeypatch.setattr(
        processor, "_ocr_image_with_confidence", lambda path, psm=6: ocr
    )

    filled = _fill_responses_with_vlm(
        questions,
        tmp_path,
        object(),
        pdf_name="s.pdf",
        progress=ExtractionProgress(),
        progress_callback=lambda _m: None,
        escalate_floor=floor,
    )
    return filled, vlm_calls


def test_escalation_keeps_a_confident_ocr_reading_and_never_calls_the_model(
    monkeypatch, tmp_path: Path
) -> None:
    filled, vlm_calls = _escalating_fill(
        [_question("q1", "responses/q1.png")],
        tmp_path,
        ocr=("clear handwriting", 91.0),
        monkeypatch=monkeypatch,
    )

    assert filled[0].response_text == "clear handwriting"
    assert filled[0].response_confidence == 91.0
    assert filled[0].response_source == "tesseract"
    # The whole point of the flag: no model call for an answer read well.
    assert vlm_calls == []


def test_escalation_re_reads_a_shaky_answer_with_the_model(
    monkeypatch, tmp_path: Path
) -> None:
    filled, vlm_calls = _escalating_fill(
        [_question("q1", "responses/q1.png")],
        tmp_path,
        ocr=("smudged nonsens", 41.0),
        monkeypatch=monkeypatch,
    )

    assert filled[0].response_text == "model reading"
    # A model reading carries no measured confidence, so the number the OCR
    # claimed must not survive next to words it never scored.
    assert filled[0].response_confidence is None
    assert filled[0].response_source == "vision-model"
    assert vlm_calls == ["q1.png"]


def test_escalation_re_reads_an_empty_ocr_result(monkeypatch, tmp_path: Path) -> None:
    filled, vlm_calls = _escalating_fill(
        [_question("q1", "responses/q1.png")],
        tmp_path,
        ocr=("", None),
        monkeypatch=monkeypatch,
    )

    assert filled[0].response_text == "model reading"
    assert vlm_calls == ["q1.png"]


def test_a_model_that_reads_nothing_cannot_cost_the_ocr_answer(
    monkeypatch, tmp_path: Path
) -> None:
    """The rule that makes escalating safe: the worst case is wasted time."""
    filled, vlm_calls = _escalating_fill(
        [_question("q1", "responses/q1.png")],
        tmp_path,
        ocr=("partial readin", 41.0),
        vlm_text="",
        monkeypatch=monkeypatch,
    )

    assert filled[0].response_text == "partial readin"
    assert filled[0].response_confidence == 41.0
    assert filled[0].response_source == "tesseract"
    assert vlm_calls == ["q1.png"]


def test_a_failing_model_cannot_cost_the_ocr_answer(monkeypatch, tmp_path: Path) -> None:
    def boom(*, image_path, config, math_latex=False):
        raise RuntimeError("ollama down")

    monkeypatch.setattr(processor, "extract_question_prompt_and_answer", boom)
    monkeypatch.setattr(
        processor, "_ocr_image_with_confidence", lambda path, psm=6: ("partial", 41.0)
    )

    filled = _fill_responses_with_vlm(
        [_question("q1", "responses/q1.png")],
        tmp_path,
        object(),
        pdf_name="s.pdf",
        progress=ExtractionProgress(),
        progress_callback=lambda _m: None,
        escalate_floor=60.0,
    )

    assert filled[0].response_text == "partial"
    assert filled[0].response_source == "tesseract"


def test_the_default_order_is_still_model_first(monkeypatch, tmp_path: Path) -> None:
    """Without the flag nothing changes: every crop goes to the model."""
    ocr_calls: list[str] = []

    def fake_vlm(*, image_path, config, math_latex=False):
        return ("prompt ignored", "model reading")

    def fake_ocr(path, psm=6):
        ocr_calls.append(Path(path).name)
        return ("ocr text", 95.0)

    monkeypatch.setattr(processor, "extract_question_prompt_and_answer", fake_vlm)
    monkeypatch.setattr(processor, "_ocr_image_with_confidence", fake_ocr)

    filled = _fill_responses_with_vlm(
        [_question("q1", "responses/q1.png")],
        tmp_path,
        object(),
        pdf_name="s.pdf",
        progress=ExtractionProgress(),
        progress_callback=lambda _m: None,
    )

    assert filled[0].response_text == "model reading"
    assert filled[0].response_source == "vision-model"
    assert ocr_calls == []


def test_the_maths_backend_still_wins_when_escalating(monkeypatch, tmp_path: Path) -> None:
    """A specialist reader stays the specialist — it is just asked less often."""
    monkeypatch.setattr(
        processor,
        "extract_question_prompt_and_answer",
        lambda **_kwargs: ("prompt", "vision reading"),
    )
    monkeypatch.setattr(
        processor, "_ocr_image_with_confidence", lambda path, psm=6: ("smudge", 30.0)
    )

    filled = _fill_responses_with_vlm(
        [_question("q1", "responses/q1.png")],
        tmp_path,
        object(),
        pdf_name="s.pdf",
        progress=ExtractionProgress(),
        progress_callback=lambda _m: None,
        math_transcriber=lambda _path: "$\\frac{1}{2}$",
        escalate_floor=60.0,
    )

    assert filled[0].response_text == "$\\frac{1}{2}$"
    assert filled[0].response_source == "math-model"


def test_escalation_ticks_progress_once_per_crop(monkeypatch, tmp_path: Path) -> None:
    ticks: list[str] = []
    monkeypatch.setattr(
        processor,
        "extract_question_prompt_and_answer",
        lambda **_kwargs: ("prompt", "model reading"),
    )
    monkeypatch.setattr(
        processor, "_ocr_image_with_confidence", lambda path, psm=6: ("x", 10.0)
    )

    _fill_responses_with_vlm(
        [_question("q1", "responses/q1.png"), _question("q2", "responses/q2.png")],
        tmp_path,
        object(),
        pdf_name="s.pdf",
        progress=ExtractionProgress(on_response=lambda _p, q: ticks.append(q)),
        progress_callback=lambda _m: None,
        escalate_floor=60.0,
    )

    assert ticks == ["q1", "q2"]
