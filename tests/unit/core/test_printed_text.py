from __future__ import annotations

from pathlib import Path

from opengrader.core.grading.questions import flatten_question_contexts
from opengrader.core.grading.solutions import flatten_solution_entries
from opengrader.core.qa_file import load_qa_markdown
from opengrader.core.submissions.printed_text import (
    printed_question_text,
    response_without_printed_text,
    strip_printed_text,
)

REPO = Path(__file__).resolve().parents[3]

Q1 = "Convert decimal 5 to binary [A]."
Q3 = "What is the most significant bit (MSB) of the binary number 1010 [A]?"


def test_strips_numbered_prompt_and_points_marker() -> None:
    text = "1. (10 pts) Convert decimal 5 to binary [A]. Ie as \\ Ww lA yr"
    assert strip_printed_text(text, [Q1]) == "Ie as \\ Ww lA yr"


def test_multiple_choice_with_only_printed_text_becomes_empty() -> None:
    text = f"3. (10 pts) {Q3} A) 0 B 1 C) 2 D) 10"
    printed = [Q3, "A) 0", "B) 1", "C) 2", "D) 10"]
    assert strip_printed_text(text, printed) == ""


def test_tolerates_ocr_noise_in_the_printed_prompt() -> None:
    text = "1. (10 pts) Convert decmal 5 t0 binary [A]. 101"
    assert strip_printed_text(text, [Q1]) == "101"


def test_multipart_keeps_part_labels_and_student_writing() -> None:
    text = (
        "5. (25 pts) Answer all parts about the list [A]. "
        "a) (8 pts) What algorithm would you use here? linear scan "
        "b) (9 pts) What is the result for [5, 2, 8]? 5"
    )
    printed = [
        "Answer all parts about the list [A].",
        "What algorithm would you use here?",
        "What is the result for [5, 2, 8]?",
    ]
    assert strip_printed_text(text, printed) == "a) linear scan b) 5"


def test_text_without_the_prompt_is_returned_unchanged() -> None:
    for text in ["101", "binary is 101 because 5 = 4+1", "  spaced  out  "]:
        assert strip_printed_text(text, [Q1]) == text


def test_short_choice_only_removed_on_exact_match() -> None:
    assert strip_printed_text("B 2", ["B) 1"]) == "B 2"


def test_only_tesseract_readings_are_cleaned() -> None:
    text = f"1. (10 pts) {Q1} 101"
    assert response_without_printed_text(text, source="tesseract", printed=[Q1]) == "101"
    for source in ("vision-model", "human", None):
        assert response_without_printed_text(text, source=source, printed=[Q1]) == text


def test_printed_text_comes_from_the_answer_key() -> None:
    qa = load_qa_markdown(REPO / "samples" / "cs146-quiz1" / "qa.md")
    questions = qa.version_questions["A"]

    q3 = next(question for question in questions if question.id == "q3")
    assert printed_question_text(q3) == (Q3, "A) 0", "B) 1", "C) 2", "D) 10")

    contexts = {context.question_id: context for context in flatten_question_contexts(questions)}
    entries = flatten_solution_entries(questions)
    q5 = next(question for question in questions if question.id == "q5")
    # One crop can cover several parts, so every part carries the whole question's print.
    assert contexts["q5b"].printed_text == printed_question_text(q5)
    assert entries["q5b"].printed_text == printed_question_text(q5)
    assert contexts["q1"].printed_text == (Q1,)
