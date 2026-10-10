"""An answer key's answers as a form: what can be answered, and writing the
answers back without touching anything else in the file."""

from __future__ import annotations

import pytest

from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.qa_file.answers import answer_fields, apply_answers
from opengrader.core.qa_file.draft_key import draft_from_text

DRAFT = draft_from_text(
    """1. (10 points) Explain recursion.
2. Pick one. (4 points)
a) Sodium
b) Chlorine
3. Parts.
3a) Name a noble gas.
3b) Name a halogen.
""",
    title="quiz",
).markdown


def _by_label(markdown: str) -> dict:
    return {field.label: field for field in answer_fields(markdown)}


def test_every_question_and_part_of_a_draft_can_be_answered() -> None:
    fields = answer_fields(DRAFT)

    assert [(f.label, f.prompt, f.points, f.answers) for f in fields] == [
        ("1", "Explain recursion.", 10, ()),
        ("2", "Pick one.", 4, ()),
        ("3a", "Name a noble gas.", 10, ()),
        ("3b", "Name a halogen.", 10, ()),
    ]
    # The options a draft kept as question text are offered as options again.
    assert fields[1].choices == (("A", "Sodium"), ("B", "Chlorine")) and fields[1].choices_in_prompt


def test_answers_are_written_and_several_lines_are_several_accepted_answers() -> None:
    fields = _by_label(DRAFT)

    updated = apply_answers(
        DRAFT,
        {
            fields["1"].source_line: ["a function that calls itself", "  self-reference  ", ""],
            fields["3b"].source_line: ["chlorine"],
        },
    )

    first, _second, third = parse_qa_markdown(updated).questions
    assert first.answers == ["a function that calls itself", "self-reference"]
    assert [part.answers for part in third.subquestions] == [[], ["chlorine"]]


def test_picking_an_option_makes_the_drafts_option_lines_real_choices() -> None:
    field = _by_label(DRAFT)["2"]

    updated = apply_answers(DRAFT, {field.source_line: ["b"]})

    question = parse_qa_markdown(updated).questions[1]
    assert question.prompt == "Pick one."
    assert [(choice.label, choice.text) for choice in question.choices] == [("A", "Sodium"), ("B", "Chlorine")]
    assert question.correct_choice_label == "B"
    again = _by_label(updated)["2"]
    assert again.answers == ("B",) and not again.choices_in_prompt
    # …and the correct one can then be changed like any other answer.
    assert parse_qa_markdown(apply_answers(updated, {again.source_line: ["A"]})).questions[1].correct_choice_label == "A"


def test_nothing_else_in_the_file_is_touched() -> None:
    field = _by_label(DRAFT)["1"]

    assert apply_answers(DRAFT, {}) == DRAFT
    assert apply_answers(DRAFT, {field.source_line: []}) == DRAFT
    updated = apply_answers(DRAFT, {field.source_line: ["x"]})
    # Only question 1 gained lines; every line that was there is still there, in order.
    remaining = iter(updated.splitlines())
    assert all(line in remaining for line in DRAFT.splitlines())
    assert len(updated.splitlines()) - len(DRAFT.splitlines()) == 2


def test_an_answer_can_be_changed_and_cleared() -> None:
    field = _by_label(DRAFT)["1"]
    answered = apply_answers(DRAFT, {field.source_line: ["first"]})
    field = _by_label(answered)["1"]

    changed = apply_answers(answered, {field.source_line: ["second"]})
    cleared = apply_answers(answered, {field.source_line: []})

    assert parse_qa_markdown(changed).questions[0].answers == ["second"]
    assert parse_qa_markdown(cleared).questions[0].answers == []


def test_a_letter_that_is_not_an_option_is_refused() -> None:
    field = _by_label(DRAFT)["2"]

    with pytest.raises(ValueError, match="no option Z"):
        apply_answers(DRAFT, {field.source_line: ["z"]})


def test_a_question_that_is_not_in_the_file_is_refused() -> None:
    with pytest.raises(ValueError):
        apply_answers(DRAFT, {9999: ["x"]})
