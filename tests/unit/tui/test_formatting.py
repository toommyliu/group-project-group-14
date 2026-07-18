from opengrader.tui.formatting import format_question_label, format_question_type


def test_format_question_label_humanizes_simple_ids() -> None:
    assert format_question_label("q1") == "Question 1"
    assert format_question_label("Q2") == "Question 2"


def test_format_question_label_keeps_subquestion_suffixes() -> None:
    assert format_question_label("q5a") == "Question 5a"
    assert format_question_label("q6d") == "Question 6d"


def test_format_question_label_passes_through_custom_ids() -> None:
    assert format_question_label("bonus-question") == "bonus-question"
    assert format_question_label("qx") == "qx"
    assert format_question_label("") == ""


def test_format_question_type_humanizes_literals() -> None:
    assert format_question_type("free_response") == "Free Response"
    assert format_question_type("multiple_choice") == "Multiple Choice"


def test_format_question_type_returns_empty_for_unknown_values() -> None:
    assert format_question_type("") == ""
    assert format_question_type("essay") == ""
