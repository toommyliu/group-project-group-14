from opengrader.core.ocr import ollama_vlm
from opengrader.core.ocr.ollama_vlm import (
    OllamaVisionConfig,
    extract_question_prompt_and_answer,
)


def _capture_prompt(monkeypatch) -> list[str]:
    prompts: list[str] = []

    def fake_generate(*, image_path, prompt, config, **_kwargs):
        prompts.append(prompt)
        return '{"prompt": "Q", "answer": "a"}'

    monkeypatch.setattr(ollama_vlm, "_generate_with_image", fake_generate)
    return prompts


def test_math_latex_off_by_default(monkeypatch):
    prompts = _capture_prompt(monkeypatch)
    extract_question_prompt_and_answer(
        image_path="x.png", config=OllamaVisionConfig()
    )
    assert "LaTeX" not in prompts[0]


def test_math_latex_prompt_instructs_latex(monkeypatch):
    prompts = _capture_prompt(monkeypatch)
    extract_question_prompt_and_answer(
        image_path="x.png", config=OllamaVisionConfig(), math_latex=True
    )
    assert "LaTeX" in prompts[0]
    assert "$...$" in prompts[0]


def test_math_flag_threads_through_fill(tmp_path):
    from opengrader.core.submissions import processor
    from opengrader.core.submissions.processor import _fill_responses_with_vlm
    from opengrader.core.submissions.student_file import StudentSubmissionQuestion

    seen: dict[str, bool] = {}

    class _StubReader:
        def extract_question_prompt_and_answer(self, *, image_path, math_latex=False):
            seen["math_latex"] = math_latex
            return "Q", r"$\frac{1}{2}$"

    (tmp_path / "responses").mkdir()
    (tmp_path / "responses" / "q1.png").write_bytes(b"x")
    question = StudentSubmissionQuestion(
        question_id="q1",
        prompt="",
        possible_points=10,
        response_image_path="responses/q1.png",
        response_text="",
    )
    filled = _fill_responses_with_vlm(
        [question],
        tmp_path,
        _StubReader(),
        pdf_name="s.pdf",
        progress=processor.ExtractionProgress(),
        progress_callback=lambda _m: None,
        math_latex=True,
    )
    assert seen["math_latex"] is True
    assert filled[0].response_text == r"$\frac{1}{2}$"
    assert filled[0].response_confidence is None
