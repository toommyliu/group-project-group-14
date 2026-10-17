from __future__ import annotations

import pytest

from opengrader.core.qa_file.answer_list import apply_answer_list, parse_answer_list
from opengrader.core.qa_file.answers import answer_fields, unanswered_count
from opengrader.core.qa_file.draft_key import draft_from_text

DRAFT = draft_from_text(
    "1. What is water? (5 points)\n\n2. Pick the even number. (5 points)\na) 3\nb) 4\n\n3. Name primes. (4 points)\n",
    title="Quiz",
).markdown


def test_lines_are_read_the_ways_teachers_write_them() -> None:
    entries, problems = parse_answer_list(
        "1: H2O; Water\n2) b\nQ3. 1,000\nquestion 4 - x | y\n5a: yes\nB 6: C\n# a note\n\nno number here\n"
    )

    assert [(label, answers) for _line, label, answers in entries] == [
        ("1", ["H2O", "Water"]),
        ("2", ["b"]),
        ("3", ["1,000"]),
        ("4", ["x", "y"]),
        ("5a", ["yes"]),
        ("B · 6", ["C"]),
    ]
    assert problems == ["line 9: 'no number here' is not 'question number: answer'"]


def test_a_comma_and_space_separates_answers_only_without_semicolons() -> None:
    [(_, _, first), (_, _, second)] = parse_answer_list("1: H2O, Water\n2: 3, 5; 7\n")[0]

    assert first == ["H2O", "Water"] and second == ["3, 5", "7"]


def test_a_draft_is_filled_and_its_choices_become_real() -> None:
    result = apply_answer_list(DRAFT, "1: H2O, Water\n2) b\n3: 2; 3; 5\n9: z\n")

    assert result.filled == ["1", "2", "3"]
    assert result.problems == ["line 4: the answer key has no question 9"]
    assert unanswered_count(result.markdown) == 0
    fields = {field.label: field for field in answer_fields(result.markdown)}
    assert fields["2"].choices and fields["2"].answers == ("B",)


def test_answers_already_there_are_left_alone_and_a_bad_letter_refuses_the_whole_list() -> None:
    once = apply_answer_list(DRAFT, "1: H2O\n").markdown
    again = apply_answer_list(once, "1: H2O\n")
    assert again.filled == [] and again.unchanged == ["1"] and again.markdown == once

    with pytest.raises(ValueError):
        apply_answer_list(DRAFT, "1: H2O\n2: Z\n")


def test_answers_can_go_on_their_own_lines_under_the_question() -> None:
    entries, problems = parse_answer_list("1. H2O\nWater\n2:\nB\n3: 2\n3\n5\n\nafter a blank line\n")

    assert [(label, answers) for _line, label, answers in entries] == [
        ("1", ["H2O", "Water"]),
        ("2", ["B"]),
        # A number on its own line is an answer: only "3:" starts a question.
        ("3", ["2", "3", "5"]),
    ]
    assert problems == ["line 9: 'after a blank line' is not 'question number: answer'"]


def test_a_question_with_no_answer_at_all_is_reported() -> None:
    entries, problems = parse_answer_list("1:\n\n2: B\n")

    assert [label for _line, label, _answers in entries] == ["2"]
    assert problems == ["question 1 has a number but no answer"]
