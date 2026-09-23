import pytest

from opengrader.core.ocr.vision_prompts import parse_question_prompt_and_answer, parse_handwriting_only


def test_blank_and_repeated_student_answers_are_preserved() -> None:
    assert parse_question_prompt_and_answer('{"answer":"", "prompt":"Minimum score"}') == ("Minimum score", "")
    assert parse_question_prompt_and_answer('{"answer":"no no no", "prompt":"Explain"}') == ("Explain", "no no no")
    assert parse_handwriting_only('{"text":"5-4-3-2-1"}') == "5-4-3-2-1"


@pytest.mark.parametrize("raw", ['{"answer":"no\\nno', '{"answer":null,"prompt":"Question"}', 'Let me solve this'])
def test_malformed_output_cannot_be_saved_as_student_work(raw: str) -> None:
    with pytest.raises(ValueError, match="invalid or truncated"):
        parse_question_prompt_and_answer(raw)
    with pytest.raises(ValueError, match="invalid or truncated"):
        parse_handwriting_only(raw)
