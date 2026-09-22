from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import opengrader.core.submissions.processor as processor
from opengrader.core.submissions.processor import (
    ExtractionProgress,
    _extract_blank_template_questions,
    _extract_questions_from_text,
    _fill_responses_with_vlm,
    _split_prompt_and_response,
    _TextLine,
)
from opengrader.core.submissions.student_file import (
    StudentInkFragment,
    StudentSubmissionQuestion,
)


def _line(text: str) -> _TextLine:
    return _TextLine(page=1, page_w=612, page_h=792, x0=0, y0=0, x1=10, y1=10, text=text)


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
    prompt, response = _split_prompt_and_response("Capital of France?\nParis")
    assert prompt.strip() == "Capital of France?\nParis"
    assert response == ""


def test_marker_inside_prompt_line_not_split_midline() -> None:
    prompt, response = _split_prompt_and_response("Explain your answer below.\n\nBecause reasons.")
    assert prompt.strip() == "Explain your answer below."
    assert response.strip() == "Because reasons."


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


def test_blank_template_expands_lettered_subquestions_without_print_leakage() -> None:
    lines = [
        _TextLine(1, 612, 792, 50, 100, 560, 120, "4. (10 pts) Plain question"),
        _TextLine(1, 612, 792, 50, 250, 560, 270, "5. (20 pts) Answer both parts"),
        _TextLine(1, 612, 792, 55, 300, 560, 320, "a) (8 pts) First part"),
        _TextLine(1, 612, 792, 55, 430, 560, 450, "b) (12 pts) Second part"),
    ]

    questions = _extract_blank_template_questions(lines)

    assert [question.question_id for question in questions] == ["q4", "q5a", "q5b"]
    assert [question.response_text for question in questions] == ["", "", ""]
    assert questions[1].prompt == "First part"
    assert questions[1].source_segments[0].bbox_norm[3] > 0.1


def _question(qid: str, image: str | None, text: str = "") -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=qid,
        prompt="",
        possible_points=None,
        response_image_path=image,
        response_text=text,
        source_segments=(),
    )


class _StubVisionReader:
    """A settable `VisionReader` stub for `_fill_responses_with_vlm` tests.

    Records every image path passed to `extract_question_prompt_and_answer`
    and `extract_handwriting_only` (in `answer_calls` / `handwriting_calls`),
    and each method's outcome is configured per-test via plain attributes
    instead of a bespoke fake function per test.

    `answer` / `handwriting_answer` may be a literal string or a
    `Callable[[Path], str]` computed from the crop path, for tests whose
    answer depends on which crop was read. `raise_on_answer` /
    `raise_on_handwriting`, when set to an exception instance, is raised
    instead of returning a value, for the VLM-failure and
    "must never be called" tests.
    """

    def __init__(self) -> None:
        self.prompt: str = "prompt ignored"
        self.answer: str | Callable[[Path], str] = ""
        self.handwriting_answer: str | Callable[[Path], str] = ""
        self.raise_on_answer: Exception | None = None
        self.raise_on_handwriting: Exception | None = None
        self.answer_calls: list[Path] = []
        self.handwriting_calls: list[Path] = []

    def extract_question_prompt_and_answer(self, *, image_path, math_latex=False):
        path = Path(image_path)
        self.answer_calls.append(path)
        if self.raise_on_answer is not None:
            raise self.raise_on_answer
        answer = self.answer(path) if callable(self.answer) else self.answer
        return self.prompt, answer

    def extract_handwriting_only(self, *, image_path, math_latex=False):
        path = Path(image_path)
        self.handwriting_calls.append(path)
        if self.raise_on_handwriting is not None:
            raise self.raise_on_handwriting
        if callable(self.handwriting_answer):
            return self.handwriting_answer(path)
        return self.handwriting_answer


def test_vlm_fill_transcribes_each_crop(tmp_path: Path) -> None:
    reader = _StubVisionReader()
    reader.answer = lambda path: f"answer for {path.stem}"

    questions = [_question("q1", "responses/q1.png"), _question("q2", "responses/q2.png")]
    ticks: list[str] = []
    progress = ExtractionProgress(on_response=lambda pdf, qid: ticks.append(qid))

    filled = _fill_responses_with_vlm(
        questions,
        tmp_path,
        reader,
        pdf_name="submission-0001.pdf",
        progress=progress,
        progress_callback=lambda _m: None,
    )

    assert [q.response_text for q in filled] == ["answer for q1", "answer for q2"]
    assert ticks == ["q1", "q2"]
    assert [p.name for p in reader.answer_calls] == ["q1.png", "q2.png"]


def test_vlm_fill_skips_already_filled_and_imageless(tmp_path: Path) -> None:
    reader = _StubVisionReader()
    reader.prompt = ""
    reader.answer = "SHOULD NOT BE USED"

    questions = [
        _question("q1", "responses/q1.png", text="already graded text"),
        _question("q2", None),
    ]
    ticks: list[str] = []
    filled = _fill_responses_with_vlm(
        questions,
        tmp_path,
        reader,
        pdf_name="s.pdf",
        progress=ExtractionProgress(on_response=lambda p, q: ticks.append(q)),
        progress_callback=lambda _m: None,
    )
    assert filled[0].response_text == "already graded text"
    assert filled[1].response_text == ""
    assert ticks == []


def test_verified_blank_ink_never_calls_a_model(tmp_path: Path) -> None:
    reader = _StubVisionReader()
    reader.raise_on_handwriting = AssertionError(
        "a blank ink layer must not reach a model"
    )

    question = _question("q1", "responses/q1.png")
    question = replace(
        question,
        ink_image_path="responses/q1.ink.png",
        ink_fragments=(),
    )

    filled = _fill_responses_with_vlm(
        [question],
        tmp_path,
        reader,
        pdf_name="s.pdf",
        progress=ExtractionProgress(),
        progress_callback=lambda _message: None,
    )

    assert filled[0].response_text == ""
    assert filled[0].response_source is None


def test_ink_layer_uses_the_handwriting_only_prompt(tmp_path: Path) -> None:
    reader = _StubVisionReader()
    reader.handwriting_answer = "isolated answer"

    question = replace(
        _question("q1", "responses/q1.png"),
        ink_image_path="responses/q1.ink.png",
        ink_fragments=(
            StudentInkFragment(page_number=1, bbox_norm=(0.2, 0.3, 0.1, 0.1)),
        ),
    )

    filled = _fill_responses_with_vlm(
        [question],
        tmp_path,
        reader,
        pdf_name="s.pdf",
        progress=ExtractionProgress(),
        progress_callback=lambda _message: None,
    )

    assert reader.handwriting_calls == [tmp_path / "responses/q1.ink.png"]
    assert filled[0].response_text == "isolated answer"
    assert filled[0].ink_fragments[0].text == "isolated answer"


def test_vlm_failure_falls_back_to_ocr(monkeypatch, tmp_path: Path) -> None:
    reader = _StubVisionReader()
    reader.raise_on_answer = RuntimeError("ollama down")

    monkeypatch.setattr(
        processor,
        "_ocr_image_with_confidence",
        lambda path, psm=6: ("ocr fallback text", 55.0),
    )

    warnings: list[str] = []
    filled = _fill_responses_with_vlm(
        [_question("q1", "responses/q1.png")],
        tmp_path,
        reader,
        pdf_name="s.pdf",
        progress=ExtractionProgress(),
        progress_callback=warnings.append,
    )
    assert filled[0].response_text == "ocr fallback text"
    assert filled[0].response_confidence == 55.0
    assert any("Warning" in w for w in warnings)


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


def _escalating_fill(
    questions,
    tmp_path: Path,
    *,
    ocr: tuple[str, float | None],
    vlm_text: str = "model reading",
    floor: float = 60.0,
    monkeypatch,
):
    reader = _StubVisionReader()
    reader.answer = vlm_text

    monkeypatch.setattr(
        processor, "_ocr_image_with_confidence", lambda path, psm=6: ocr
    )

    filled = _fill_responses_with_vlm(
        questions,
        tmp_path,
        reader,
        pdf_name="s.pdf",
        progress=ExtractionProgress(),
        progress_callback=lambda _m: None,
        escalate_floor=floor,
    )
    vlm_calls = [p.name for p in reader.answer_calls]
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
    reader = _StubVisionReader()
    reader.raise_on_answer = RuntimeError("ollama down")

    monkeypatch.setattr(
        processor, "_ocr_image_with_confidence", lambda path, psm=6: ("partial", 41.0)
    )

    filled = _fill_responses_with_vlm(
        [_question("q1", "responses/q1.png")],
        tmp_path,
        reader,
        pdf_name="s.pdf",
        progress=ExtractionProgress(),
        progress_callback=lambda _m: None,
        escalate_floor=60.0,
    )

    assert filled[0].response_text == "partial"
    assert filled[0].response_source == "tesseract"


def test_the_default_order_is_still_model_first(monkeypatch, tmp_path: Path) -> None:
    ocr_calls: list[str] = []

    reader = _StubVisionReader()
    reader.answer = "model reading"

    def fake_ocr(path, psm=6):
        ocr_calls.append(Path(path).name)
        return ("ocr text", 95.0)

    monkeypatch.setattr(processor, "_ocr_image_with_confidence", fake_ocr)

    filled = _fill_responses_with_vlm(
        [_question("q1", "responses/q1.png")],
        tmp_path,
        reader,
        pdf_name="s.pdf",
        progress=ExtractionProgress(),
        progress_callback=lambda _m: None,
    )

    assert filled[0].response_text == "model reading"
    assert filled[0].response_source == "vision-model"
    assert ocr_calls == []


def test_the_maths_backend_still_wins_when_escalating(monkeypatch, tmp_path: Path) -> None:
    reader = _StubVisionReader()
    reader.answer = "vision reading"

    monkeypatch.setattr(
        processor, "_ocr_image_with_confidence", lambda path, psm=6: ("smudge", 30.0)
    )

    filled = _fill_responses_with_vlm(
        [_question("q1", "responses/q1.png")],
        tmp_path,
        reader,
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
    reader = _StubVisionReader()
    reader.answer = "model reading"

    monkeypatch.setattr(
        processor, "_ocr_image_with_confidence", lambda path, psm=6: ("x", 10.0)
    )

    _fill_responses_with_vlm(
        [_question("q1", "responses/q1.png"), _question("q2", "responses/q2.png")],
        tmp_path,
        reader,
        pdf_name="s.pdf",
        progress=ExtractionProgress(on_response=lambda _p, q: ticks.append(q)),
        progress_callback=lambda _m: None,
        escalate_floor=60.0,
    )

    assert ticks == ["q1", "q2"]
