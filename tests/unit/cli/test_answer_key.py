from __future__ import annotations

from pathlib import Path

import docx
import fitz
import pytest

import opengrader.core.qa_file.pdf_answer_key as pdf_answer_key_module
from opengrader.cli.answer_key import run_cli
from opengrader.core import app_config
from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.qa_file.pdf_answer_key import _load_blank_template_layout


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
    assert doc.title == "Override"
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


def _as_scan(pdf_path: Path, scan_path: Path) -> Path:
    """The same pages as pictures only, the way a scanner hands them over."""
    scan = fitz.open()
    with fitz.open(pdf_path) as source:
        for page in source:
            picture = page.get_pixmap(dpi=200)
            scan.new_page(width=page.rect.width, height=page.rect.height).insert_image(
                page.rect, pixmap=picture
            )
    scan.save(scan_path)
    scan.close()
    return scan_path


def _pdf(path: Path, lines: list[str]) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    y = 90
    for line in lines:
        if line:
            page.insert_text((72, y), line, fontsize=14)
        y += 40
    doc.save(path)
    doc.close()
    return path


@pytest.fixture()
def scanned_answer_key_pdf(answer_key_pdf: Path, tmp_path: Path) -> Path:
    return _as_scan(answer_key_pdf, tmp_path / "answer-key-scan.pdf")


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
    run_cli(
        [
            "--input", str(answer_key_pdf),
            "--output", str(output_path),
            "--blank-template", str(answer_key_pdf),
        ]
    )
    _assert_valid_solutions_markdown(output_path)


def _stub_extract(monkeypatch) -> dict:
    captured: dict = {}

    def fake_extract(
        pdf_path, *, title, instructions, blank_template_path, vision_reader, emit, progress,
        default_points, on_default_points,
    ):
        captured["vision_reader"] = vision_reader
        return "assignment_title: Stub\n\n1. (1 points) x?\nSolution: y\n"

    monkeypatch.setattr(pdf_answer_key_module, "extract_qa_markdown_from_pdf", fake_extract)
    return captured


def test_no_llm_read_scans_leaves_vision_reader_none(
    scanned_answer_key_pdf: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    answer_key_pdf = scanned_answer_key_pdf
    captured = _stub_extract(monkeypatch)

    run_cli(
        [
            "--input", str(answer_key_pdf),
            "--output", str(tmp_path / "solutions.md"),
            "--no-llm-read-scans",
        ]
    )

    assert captured["vision_reader"] is None
    out = capsys.readouterr().out
    assert "OCR alone" in out
    assert "Read handwriting on scans" in out


def test_llm_read_scans_defaults_on_from_ai_setup(
    scanned_answer_key_pdf: Path, tmp_path: Path, monkeypatch
) -> None:
    answer_key_pdf = scanned_answer_key_pdf
    app_config.save_settings(app_config.AISettings(read_handwriting=True))

    monkeypatch.setattr(app_config, "ensure_ready", lambda *_a, **_k: None)
    captured = _stub_extract(monkeypatch)

    run_cli(
        [
            "--input", str(answer_key_pdf),
            "--output", str(tmp_path / "solutions.md"),
            "--ollama-model", "a-non-default-model",
        ]
    )

    assert captured["vision_reader"] is not None


def test_llm_read_scans_defaults_off_when_ai_setup_leaves_it_off(
    scanned_answer_key_pdf: Path, tmp_path: Path, monkeypatch
) -> None:
    answer_key_pdf = scanned_answer_key_pdf
    app_config.save_settings(app_config.AISettings())
    captured = _stub_extract(monkeypatch)

    run_cli(
        [
            "--input", str(answer_key_pdf),
            "--output", str(tmp_path / "solutions.md"),
        ]
    )

    assert captured["vision_reader"] is None


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


PLAIN_QUESTIONS = """1. What is the capital of France?
Answer: Paris

Points: 4
2. Name a primary colour.
Answer: red
Answer: blue
"""


def test_questions_without_points_get_the_standard_ten(tmp_path: Path) -> None:
    src = tmp_path / "questions.txt"
    src.write_text(PLAIN_QUESTIONS)
    out = tmp_path / "qa.md"
    said: list[str] = []
    counted: list[tuple[int, int]] = []

    run_cli(
        ["--input", str(src), "--output", str(out)],
        status_callback=said.append,
        default_points_callback=lambda given, total: counted.append((given, total)),
    )

    text = out.read_text()
    assert "- (10 points) What is the capital of France?" in text
    assert "- (4 points) Name a primary colour." in text
    assert len(parse_qa_markdown(text).questions) == 2
    assert counted == [(1, 2)]
    assert any("1 of 2 questions" in line and "standard 10 (5 for partial" in line for line in said)


def test_a_word_file_takes_the_same_plain_answers_and_default(tmp_path: Path) -> None:
    document = docx.Document()
    for line in PLAIN_QUESTIONS.splitlines():
        document.add_paragraph(line)
    src = tmp_path / "questions.docx"
    document.save(src)
    out = tmp_path / "qa.md"

    run_cli(["--input", str(src), "--output", str(out), "--default-points", "6"])

    text = out.read_text()
    assert "- (6 points) What is the capital of France?" in text
    assert "    - Partially Correct (-3)\n    - Incorrect (-6)" in text
    assert "    - red\n    - blue" in text


def test_nothing_is_said_about_points_when_every_question_states_them(tmp_path: Path) -> None:
    src = tmp_path / "q.md"
    src.write_text("Points: 2\n" + TEXT2QTI)
    said: list[str] = []

    run_cli(["--input", str(src), "--output", str(tmp_path / "qa.md")], status_callback=said.append)

    assert not any("No points were found" in line for line in said)


def test_default_points_must_be_above_zero(tmp_path: Path) -> None:
    src = tmp_path / "q.md"
    src.write_text(PLAIN_QUESTIONS)

    with pytest.raises(SystemExit):
        run_cli(["--input", str(src), "--output", str(tmp_path / "qa.md"), "--default-points", "0"])


FRIENDLY_LINES = [
    "1) What is 2 + 2? (5 points)",
    "Answer: 4",
    "",
    "2) What color is the sky?",
    "Answer: blue",
    "Answer: light blue",
    "",
    "3a) Name a noble gas.",
    "Answer: neon",
    "3b) Name a halogen.",
    "Answer: chlorine",
]


def test_a_pdf_with_its_own_text_is_read_like_a_word_file(tmp_path: Path, monkeypatch) -> None:
    import opengrader.cli.answer_key as answer_key_cli

    def never(*_args, **_kwargs):
        raise AssertionError("a PDF that carries its text was read as a scan")

    monkeypatch.setattr(answer_key_cli, "_qa_from_pdf", never)
    src = _pdf(tmp_path / "key.pdf", FRIENDLY_LINES)
    out = tmp_path / "qa.md"
    said: list[str] = []

    run_cli(["--input", str(src), "--output", str(out)], status_callback=said.append)

    doc = parse_qa_markdown(out.read_text())
    first, second, third = doc.questions
    assert (first.points, first.prompt, first.answers) == (5, "What is 2 + 2?", ["4"])
    assert (second.points, second.answers) == (10, ["blue", "light blue"])
    assert [(part.label, part.points, part.answers) for part in third.subquestions] == [
        ("a", 10, ["neon"]),
        ("b", 10, ["chlorine"]),
    ]
    assert third.points == 20
    assert any("no OCR needed" in line for line in said)
    assert any("Read 3 question(s), 1 of them in parts" in line for line in said)
    assert any("3 of 4 questions (or parts)" in line for line in said)


def test_an_answer_on_the_questions_own_line_is_still_found_in_a_pdf(tmp_path: Path) -> None:
    src = _pdf(tmp_path / "key.pdf", ["1. (5 points) What is 2 + 2? Solution: 4"])
    out = tmp_path / "qa.md"

    run_cli(["--input", str(src), "--output", str(out)])

    (question,) = parse_qa_markdown(out.read_text()).questions
    assert (question.points, question.prompt, question.answers) == (5, "What is 2 + 2?", ["4"])


def test_a_scan_numbered_with_brackets_and_no_points_is_found(tmp_path: Path) -> None:
    typed = _pdf(
        tmp_path / "typed.pdf",
        [
            "1) What is 2 + 2?",
            "Solution: 4",
            "",
            "2) What color is the sky? (4 points)",
            "Solution: blue",
        ],
    )
    src = _as_scan(typed, tmp_path / "scan.pdf")
    out = tmp_path / "qa.md"
    said: list[str] = []

    run_cli(
        ["--input", str(src), "--output", str(out), "--no-llm-read-scans"],
        status_callback=said.append,
    )

    first, second = parse_qa_markdown(out.read_text()).questions
    assert first.points == 10 and "2 + 2" in first.prompt and "4" in first.answers
    # Points at the end of the line are read, and taken out of the question.
    assert second.points == 4 and "blue" in second.answers
    assert "points" not in second.prompt.lower()
    assert any("1 of 2 questions" in line for line in said)


def test_a_scan_with_nothing_numbered_says_how_to_number_it(tmp_path: Path, capsys) -> None:
    typed = _pdf(tmp_path / "typed.pdf", ["Just some prose.", "And a little more of it."])
    src = _as_scan(typed, tmp_path / "scan.pdf")

    with pytest.raises(SystemExit):
        run_cli(["--input", str(src), "--output", str(tmp_path / "qa.md"), "--no-llm-read-scans"])

    assert "'1.' or '1)'" in capsys.readouterr().err


def test_a_template_line_without_points_counts_only_as_the_next_number() -> None:
    from opengrader.core.qa_file.pdf_answer_key import _template_question_line

    assert _template_question_line("1) What is 2 + 2?", None) == (1, None, "What is 2 + 2?")
    assert _template_question_line("2. Sky colour? (4 points)", 1) == (2, 4.0, "Sky colour?")
    # A numbered step inside question 2's text is not question 1 again, or 5.
    assert _template_question_line("1) first add the digits", 2) is None
    assert _template_question_line("5. then carry", 2) is None
    # Points beside the number are always a question, as before.
    assert _template_question_line("7. (3 points) Anything", 2) == (7, 3.0, "Anything")


def test_a_numbered_list_inside_an_answer_is_not_taken_for_questions() -> None:
    from opengrader.core.qa_file.pdf_answer_key import _numbers_in_order

    margin = 0.10
    found = [
        (1, 1, 0.10, margin, False),
        # Steps written inside question 1's answer.
        (1, 1, 0.16, margin, False),
        (2, 1, 0.20, margin, False),
        (2, 1, 0.40, margin, False),
        (3, 1, 0.70, margin, False),
        (4, 2, 0.10, margin, False),
    ]

    run = _numbers_in_order(found)

    assert [number for number, *_rest in run] == [1, 2, 3, 4]
    assert run[-1][1] == 2


def test_a_missed_number_does_not_break_the_run_and_labelled_ones_win() -> None:
    from opengrader.core.qa_file.pdf_answer_key import _numbers_in_order

    found = [
        (1, 1, 0.10, 0.1, True),
        # OCR never saw "2."; a stray unlabelled 3 comes before the real one.
        (3, 1, 0.30, 0.1, False),
        (3, 1, 0.50, 0.1, True),
        (4, 1, 0.80, 0.1, True),
    ]

    run = _numbers_in_order(found)

    assert [(number, y) for number, _page, y, _x, _strong in run] == [(1, 0.10), (3, 0.50), (4, 0.80)]


def test_a_scan_is_read_by_the_same_rules_as_a_typed_file(tmp_path: Path) -> None:
    typed = _pdf(tmp_path / "typed.pdf", FRIENDLY_LINES)
    from_text, from_scan = tmp_path / "text.md", tmp_path / "scan.md"

    run_cli(["--input", str(typed), "--output", str(from_text)])
    run_cli(
        [
            "--input", str(_as_scan(typed, tmp_path / "scan.pdf")),
            "--output", str(from_scan),
            "--no-llm-read-scans",
        ]
    )

    # Answer: lines, several answers, parts and points at the end of the line
    # all come out of the scan as they do out of the typed PDF.
    assert parse_qa_markdown(from_scan.read_text()).questions == parse_qa_markdown(
        from_text.read_text()
    ).questions


def test_an_answer_in_a_pdf_runs_on_across_the_spacing_between_lines(tmp_path: Path) -> None:
    src = _pdf(
        tmp_path / "key.pdf",
        [
            "1. Why does ice float?",
            "Answer: It is less dense",
            "",
            "than liquid water.",
            "2. Next?",
            "Answer: always",
        ],
    )
    out = tmp_path / "qa.md"

    run_cli(["--input", str(src), "--output", str(out)])

    first, second = parse_qa_markdown(out.read_text()).questions
    assert first.answers == ["It is less dense than liquid water."]
    assert second.answers == ["always"]


def test_a_blank_templates_points_are_shared_among_a_scanned_questions_parts() -> None:
    from opengrader.core.qa_file.pdf_answer_key import _block_from_reader

    block, defaulted = _block_from_reader(
        3,
        "3. The periodic table.\n3a) A noble gas? Answer: neon\n3b) A halogen?\nAnswer: chlorine",
        default_points=10,
        template_points=8.0,
    )

    assert block.startswith("- (8 points) The periodic table.\n  - (4 points) A noble gas?")
    assert defaulted is False
    # Text the reader has no answer for is left to the older splitting.
    assert _block_from_reader(3, "3. Just a question", default_points=10, template_points=None) is None
    assert _block_from_reader(3, "4. Wrong number\nAnswer: x", default_points=10, template_points=None) is None
