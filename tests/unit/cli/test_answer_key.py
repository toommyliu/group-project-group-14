from __future__ import annotations

from pathlib import Path

import docx
import fitz
import pytest

import opengrader.core.qa_file.pdf_answer_key as pdf_answer_key_module
from opengrader.cli.answer_key import run_cli
from opengrader.core import ai_config
from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.qa_file.pdf_answer_key import _load_blank_template_layout

# --------------------------------------------------------------------------- #
# text2qti (.md/.txt) and .docx dispatch
# --------------------------------------------------------------------------- #

TEXT2QTI = """Quiz title: CS146 Quiz 1
Quiz description: A short quiz.

1. What is 2+2?
*a) 4
b) 5

2. Capital of France?
* Paris
"""


def test_md_input_produces_qa(tmp_path: Path) -> None:
    src = tmp_path / "q.md"
    src.write_text(TEXT2QTI)
    out = tmp_path / "qa.md"

    run_cli(["--input", str(src), "--output", str(out)])

    doc = parse_qa_markdown(out.read_text())
    assert len(doc.questions) == 2
    assert doc.title == "CS146 Quiz 1"


def test_docx_input_produces_qa(tmp_path: Path) -> None:
    document = docx.Document()
    for line in TEXT2QTI.splitlines():
        document.add_paragraph(line)
    src = tmp_path / "q.docx"
    document.save(src)
    out = tmp_path / "qa.md"

    run_cli(["--input", str(src), "--output", str(out), "--title", "Override"])

    doc = parse_qa_markdown(out.read_text())
    assert doc.title == "Override"  # title override wins over the "Quiz title:"
    assert len(doc.questions) == 2


def test_unsupported_extension_errors(tmp_path: Path) -> None:
    src = tmp_path / "q.rtf"
    src.write_text("whatever")
    with pytest.raises(SystemExit):
        run_cli(["--input", str(src), "--output", str(tmp_path / "qa.md")])


def test_missing_input_exits_with_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        run_cli(["--input", str(tmp_path / "missing.md"), "--output", str(tmp_path / "out.md")])


def test_text_input_with_no_questions_errors(tmp_path: Path) -> None:
    src = tmp_path / "empty.md"
    src.write_text("Just prose, no numbered questions.\n")
    with pytest.raises(SystemExit):
        run_cli(["--input", str(src), "--output", str(tmp_path / "out.md")])


# --------------------------------------------------------------------------- #
# PDF dispatch (text-layer + blank-template alignment)
# --------------------------------------------------------------------------- #

ANSWER_KEY_LINES = [
    "1. (5 points) What is 2 + 2?",
    "Solution: 4",
    "",
    "2. (10 points) What color is the sky?",
    "Solution: blue",
]


@pytest.fixture()
def answer_key_pdf(tmp_path: Path) -> Path:
    pdf_path = tmp_path / "answer-key.pdf"
    doc = fitz.open()
    page = doc.new_page()
    y = 90
    for line in ANSWER_KEY_LINES:
        if line:
            page.insert_text((72, y), line, fontsize=12)
        y += 40
    doc.save(pdf_path)
    doc.close()
    return pdf_path


def _assert_valid_solutions_markdown(output_path: Path) -> None:
    qa_doc = parse_qa_markdown(output_path.read_text())
    assert not qa_doc.version_questions
    assert len(qa_doc.questions) == 2

    first, second = qa_doc.questions
    assert first.points == 5.0
    assert "2 + 2" in first.prompt
    assert "4" in first.answers
    assert second.points == 10.0
    assert "blue" in second.answers


def test_pdf_text_layer_extraction_produces_valid_qa(answer_key_pdf: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "solutions.md"
    run_cli(["--input", str(answer_key_pdf), "--output", str(output_path)])
    _assert_valid_solutions_markdown(output_path)


def test_pdf_blank_template_extraction_produces_valid_qa(answer_key_pdf: Path, tmp_path: Path) -> None:
    output_path = tmp_path / "solutions.md"
    # Using the answer key as its own blank template exercises the full
    # template-alignment path with zero expected offsets.
    run_cli(
        [
            "--input", str(answer_key_pdf),
            "--output", str(output_path),
            "--blank-template", str(answer_key_pdf),
        ]
    )
    _assert_valid_solutions_markdown(output_path)


# --------------------------------------------------------------------------- #
# --llm-read-scans: whether a scanned/image PDF is also read with the vision
# model, and its AI Setup default.
# --------------------------------------------------------------------------- #


def _stub_extract(monkeypatch) -> dict:
    captured: dict = {}

    def fake_extract(
        pdf_path, *, title, instructions, blank_template_path, ollama_config, emit, progress
    ):
        captured["ollama_config"] = ollama_config
        return "assignment_title: Stub\n\n1. (1 points) x?\nSolution: y\n"

    monkeypatch.setattr(pdf_answer_key_module, "extract_qa_markdown_from_pdf", fake_extract)
    return captured


def test_no_llm_read_scans_leaves_ollama_config_none(
    answer_key_pdf: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    captured = _stub_extract(monkeypatch)

    run_cli(
        [
            "--input", str(answer_key_pdf),
            "--output", str(tmp_path / "solutions.md"),
            "--no-llm-read-scans",
        ]
    )

    assert captured["ollama_config"] is None
    out = capsys.readouterr().out
    assert "OCR alone" in out
    assert "Read handwriting on scans" in out


def test_llm_read_scans_defaults_on_from_ai_setup(
    answer_key_pdf: Path, tmp_path: Path, monkeypatch
) -> None:
    """With the setting on, the pre-existing "should I even try Ollama" check
    (auto-ollama, or a non-default model/host) still decides whether a config
    is actually built; --llm-read-scans only gates whether that check runs at
    all. A non-default model is enough to exercise it here.
    """
    ai_config.save_settings(ai_config.AISettings(read_handwriting=True))
    import opengrader.core.ocr.ollama_vlm as ollama_vlm_module

    monkeypatch.setattr(ollama_vlm_module, "ensure_model_available", lambda _c: None)
    captured = _stub_extract(monkeypatch)

    run_cli(
        [
            "--input", str(answer_key_pdf),
            "--output", str(tmp_path / "solutions.md"),
            "--ollama-model", "a-non-default-model",
        ]
    )

    assert captured["ollama_config"] is not None


def test_llm_read_scans_defaults_off_when_ai_setup_leaves_it_off(
    answer_key_pdf: Path, tmp_path: Path, monkeypatch
) -> None:
    ai_config.save_settings(ai_config.AISettings())
    captured = _stub_extract(monkeypatch)

    run_cli(
        [
            "--input", str(answer_key_pdf),
            "--output", str(tmp_path / "solutions.md"),
        ]
    )

    assert captured["ollama_config"] is None


def test_load_blank_template_layout_extracts_questions(answer_key_pdf: Path) -> None:
    doc = fitz.open(answer_key_pdf)
    try:
        layout = _load_blank_template_layout(doc)
    finally:
        doc.close()

    assert sorted(layout.questions) == [1, 2]
    assert layout.questions[1].points == 5.0
    assert "2 + 2" in layout.questions[1].prompt
    assert layout.questions[2].page == 1
    assert 0.0 <= layout.questions[1].y_top_norm < layout.questions[2].y_top_norm
