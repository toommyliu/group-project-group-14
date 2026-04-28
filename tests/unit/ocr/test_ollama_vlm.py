from pathlib import Path

from opengrader.core.ocr import ollama_vlm
from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig


def test_question_qa_prompt_ignores_repeated_student_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    image_path = tmp_path / "q4.png"
    image_path.write_bytes(b"not a real image")
    captured_prompt = ""

    def _fake_generate_with_image(
        *,
        image_path: str | Path,
        prompt: str,
        config: OllamaVisionConfig,
    ) -> str:
        nonlocal captured_prompt
        _ = image_path, config
        captured_prompt = prompt
        return '{"prompt": "Question text", "answer": "no"}'

    monkeypatch.setattr(
        ollama_vlm,
        "_generate_with_image",
        _fake_generate_with_image,
    )

    prompt, answer = ollama_vlm.extract_question_prompt_and_answer(
        image_path=image_path,
        config=OllamaVisionConfig(),
    )

    assert prompt == "Question text"
    assert answer == "no"
    assert "Ignore repeated student-identification" in captured_prompt
    assert "Name/ID fields" in captured_prompt
    assert "very short handwritten token such as no" in captured_prompt
    assert "do not fill in blanks, replace XXXX" in captured_prompt
    assert "excluding repeated student metadata" in captured_prompt
