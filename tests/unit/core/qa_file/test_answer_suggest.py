"""Asking a model for the answers a key is missing: what it is told, and
what of its reply is trusted."""

from __future__ import annotations

import json

from opengrader.core.qa_file.answer_suggest import (
    MAX_SUGGESTED_ANSWERS,
    build_answer_prompt,
    parse_answer_suggestions,
    suggest_answers,
)
from opengrader.core.qa_file.answers import AnswerField

GOLD = AnswerField(6, "1", "What is the chemical symbol for gold?", 5, (), ())
HALOGEN = AnswerField(12, "2", "Which is a halogen?", 4, (("A", "Sodium"), ("B", "Chlorine")), ())
ESSAY = AnswerField(20, "3a", "Explain the figure above.", 10, (), ())
FIELDS = [GOLD, HALOGEN, ESSAY]


def _reply(*entries) -> str:  # noqa: ANN002
    return json.dumps({"answers": [{"question": q, "answers": a} for q, a in entries]})


def test_the_prompt_holds_the_questions_and_their_options_and_nothing_else() -> None:
    prompt = build_answer_prompt(FIELDS, title="Chemistry Quiz 2")

    assert "[1] What is the chemical symbol for gold?" in prompt
    assert "[2] Which is a halogen?\n    A) Sodium\n    B) Chlorine" in prompt
    assert "[3a] Explain the figure above." in prompt
    assert 'titled "Chemistry Quiz 2"' in prompt
    # It is told when not to answer.
    assert "Never guess" in prompt and "empty list" in prompt


def test_usable_suggestions_are_kept_by_question() -> None:
    found = parse_answer_suggestions(
        _reply(("1", ["Au", " Au ", "AU"]), ("[2]", ["b)"]), ("3a", [])), FIELDS
    )

    assert found == {GOLD.source_line: ("Au", "AU"), HALOGEN.source_line: ("B",)}


def test_what_cannot_be_used_is_dropped_not_guessed_at() -> None:
    found = parse_answer_suggestions(
        _reply(
            ("2", ["Chlorine", "Z"]),       # not letters of its options
            ("9", ["something"]),            # not a question that was asked
            ("1", ["x" * 500, ""]),          # not an answer anyone would write
        ),
        FIELDS,
    )

    assert found == {}
    assert parse_answer_suggestions("not json at all", FIELDS) == {}
    assert parse_answer_suggestions(json.dumps({"answers": "none"}), FIELDS) == {}


def test_a_long_list_of_phrasings_is_cut_to_a_few() -> None:
    found = parse_answer_suggestions(_reply(("1", [f"way {n}" for n in range(9)])), FIELDS)

    assert len(found[GOLD.source_line]) == MAX_SUGGESTED_ANSWERS


def test_one_call_is_made_for_all_the_questions_and_none_for_no_questions() -> None:
    prompts: list[str] = []

    def generate(prompt: str) -> str:
        prompts.append(prompt)
        return _reply(("1", ["Au"]))

    assert suggest_answers(FIELDS, generate) == {GOLD.source_line: ("Au",)}
    assert suggest_answers([], generate) == {}
    assert len(prompts) == 1
