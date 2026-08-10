from __future__ import annotations

import pytest

from opengrader.core.qa_file import (
    parse_qa_markdown,
    qa_question_type,
    qa_to_template_questions,
    render_qa_markdown,
)
from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE, NUMERIC

_HEADER = """\
---
title: Types Exam
questions_start_page: 1
---

"""


def _doc(body: str):
    return parse_qa_markdown(_HEADER + body)


class TestNumericInference:
    def test_numeric_answer_infers_numeric_type(self) -> None:
        doc = _doc(
            "- (5 points) How many mL in a liter?\n"
            "  - **Correct**\n"
            "    - 1000 ±10 units:mL\n"
        )
        question = doc.questions[0]
        assert qa_question_type(question) == NUMERIC
        # The numeric spec is kept verbatim as the answer key.
        assert question.answers == ["1000 ±10 units:mL"]
        assert question.choices == []

    def test_plain_number_is_numeric(self) -> None:
        doc = _doc("- (5 points) 2+2?\n  - **Correct**\n    - 4\n")
        assert qa_question_type(doc.questions[0]) == NUMERIC

    def test_prose_answer_stays_free_response(self) -> None:
        doc = _doc(
            "- (5 points) Define recursion.\n"
            "  - **Correct**\n"
            "    - a function that calls itself\n"
        )
        assert qa_question_type(doc.questions[0]) == FREE_RESPONSE

    def test_numeric_template_gets_answer_hint(self) -> None:
        doc = _doc(
            "- (5 points) Volume?\n  - **Correct**\n    - 1000 units:mL\n"
        )
        [rendered] = qa_to_template_questions(doc)
        assert isinstance(rendered, dict)
        assert rendered["answer_hint"] == "Answer in mL"


class TestTrueFalse:
    def test_inferred_from_boolean_answer(self) -> None:
        doc = _doc(
            "- (2 points) The Earth is flat.\n  - **Correct**\n    - False\n"
        )
        question = doc.questions[0]
        # Materialized into a constrained two-option multiple choice.
        assert qa_question_type(question) == MULTIPLE_CHOICE
        assert [(c.label, c.text) for c in question.choices] == [
            ("A", "True"),
            ("B", "False"),
        ]
        assert question.correct_choice_label == "B"

    def test_explicit_type_marker(self) -> None:
        doc = _doc(
            "- (2 points) Water boils at 100C at sea level.\n"
            "  - **Type**\n"
            "    - True/False\n"
            "  - **Correct**\n"
            "    - True\n"
        )
        question = doc.questions[0]
        assert question.correct_choice_label == "A"
        assert [c.text for c in question.choices] == ["True", "False"]

    def test_true_false_round_trips_idempotently(self) -> None:
        doc = _doc(
            "- (2 points) The sky is blue.\n  - **Correct**\n    - True\n"
        )
        rendered = render_qa_markdown(doc)
        assert render_qa_markdown(parse_qa_markdown(rendered)) == rendered

    def test_explicit_choices_are_not_treated_as_shortcut(self) -> None:
        doc = _doc(
            "- (2 points) Pick one.\n"
            "  - **Choices**\n"
            "    - A) True\n"
            "    - B) False\n"
            "    - C) Maybe\n"
            "  - **Correct**\n"
            "    - C\n"
        )
        assert len(doc.questions[0].choices) == 3

    def test_unknown_type_marker_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown question type"):
            _doc("- (2 points) Q.\n  - **Type**\n    - essay\n  - **Correct**\n    - x\n")

    def test_free_response_marker_opts_out_of_true_false(self) -> None:
        doc = _doc(
            "- (2 points) Type the word true.\n"
            "  - **Type**\n"
            "    - Free Response\n"
            "  - **Correct**\n"
            "    - True\n"
        )
        question = doc.questions[0]
        assert qa_question_type(question) == FREE_RESPONSE
        assert question.choices == []
        assert question.answers == ["True"]
