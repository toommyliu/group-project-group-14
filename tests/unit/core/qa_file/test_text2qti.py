from opengrader.core.qa_file.text2qti import (
    PointsSummary,
    parse_text2qti,
    render_qa,
    summarize_points,
)


def test_parses_title_description_and_all_question_types() -> None:
    text = """Quiz title: Sample
Quiz description: Desc line one.

1. Multiple choice?
*a) right
b) wrong

2. Multiple answer?
[*] yes
[ ] no
[x] also

3. Numeric?
= 42

4. Short answer?
* photosynthesis

5. Essay?
____
"""
    parsed = parse_text2qti(text)
    assert parsed.title == "Sample"
    assert parsed.description == "Desc line one."
    assert [q.number for q in parsed.questions] == [1, 2, 3, 4, 5]

    q1, q2, q3, q4, q5 = parsed.questions
    assert q1.correct_lines() == ["A"]
    assert q2.correct_lines() == ["A", "C"]
    assert q3.correct_lines() == ["42"]
    assert q4.correct_lines() == ["photosynthesis"]
    assert q5.has_free_response is True


def test_code_fence_lines_are_not_parsed_as_answers() -> None:
    text = """Quiz title: Code

1. What does this print?
```
* not an answer
= not numeric
```
*a) correct
b) wrong
"""
    parsed = parse_text2qti(text)
    q = parsed.questions[0]
    assert q.correct_lines() == ["A"]
    assert any("not an answer" in line for line in q.prompt_lines)


def test_render_qa_emits_frontmatter_and_rubric() -> None:
    parsed = parse_text2qti("Quiz title: T\n\n1. Q?\n= 5\n")
    md = render_qa(parsed, questions_start_page=2)
    assert "title: T" in md
    assert "questions_start_page: 2" in md
    assert "- **Correct**" in md
    assert "- **Rubric**" in md


def _qa(source: str):  # noqa: ANN202
    from opengrader.core.qa_file import parse_qa_markdown
    from opengrader.core.qa_file.text2qti import parse_text2qti, render_qa

    return parse_qa_markdown(render_qa(parse_text2qti(source)))


def test_an_outcome_line_tags_the_question_it_sits_under() -> None:
    doc = _qa("1. What is X?\nOutcome: CLO3\n* x\n")
    assert doc.questions[0].outcomes == ("CLO3",)


def test_outcomes_repeat_and_comma_separate() -> None:
    doc = _qa("1. What is X?\nOutcome: CLO1\nOutcome: CLO2, CLO3\n* x\n")
    assert doc.questions[0].outcomes == ("CLO1", "CLO2", "CLO3")


def test_an_outcome_before_the_first_question_applies_to_all_of_them() -> None:
    doc = _qa("Outcome: CLO2\n\n1. A?\n* a\n\n2. B?\n* b\n")
    assert [q.outcomes for q in doc.questions] == [("CLO2",), ("CLO2",)]


def test_a_question_adds_to_the_running_default_rather_than_replacing_it() -> None:
    doc = _qa("Outcome: CLO1\n\n1. A?\n* a\n\n2. B?\nOutcome: CLO9\n* b\n")
    assert doc.questions[0].outcomes == ("CLO1",)
    assert doc.questions[1].outcomes == ("CLO1", "CLO9")


def test_an_outcome_line_is_not_mistaken_for_prompt_text() -> None:
    doc = _qa("1. What is X?\nOutcome: CLO3\n____\n")
    assert "Outcome" not in doc.questions[0].prompt


def test_a_source_file_with_no_outcomes_generates_none() -> None:
    from opengrader.core.qa_file.text2qti import parse_text2qti, render_qa

    markdown = render_qa(parse_text2qti("1. What is X?\n* x\n"))
    assert "Outcome" not in markdown


def test_outcome_names_are_kept_verbatim() -> None:
    doc = _qa("1. X?\nOutcome: ABET-2, Applies Bayes' rule\n* x\n")
    assert doc.questions[0].outcomes == ("ABET-2", "Applies Bayes' rule")


def _answer_key(text: str, **options):  # noqa: ANN003, ANN202
    from opengrader.core.qa_file import parse_qa_markdown

    return parse_qa_markdown(render_qa(parse_text2qti(text), **options))


def test_a_file_of_just_questions_and_answers_is_a_complete_answer_key() -> None:
    text = """1. What is the capital of France?
Answer: Paris

2. What is 6 x 7?
Answer: 42
"""
    rendered = render_qa(parse_text2qti(text))

    # Full credit 10, partial credit 5, no credit 0 — written as deductions.
    assert rendered.count("- (10 points) ") == 2
    assert rendered.count("    - Correct (+0)\n    - Partially Correct (-5)\n    - Incorrect (-10)") == 2
    assert "  - **Correct**\n    - Paris" in rendered
    assert len(_answer_key(text).questions) == 2


def test_every_answer_line_is_another_accepted_answer() -> None:
    parsed = parse_text2qti(
        """1. Name a primary colour.
Answer: red
Answer: blue
answers: yellow
Answer: red
"""
    )

    # Each line is kept, in order; a repeated one is not listed twice.
    assert parsed.questions[0].correct_lines() == ["red", "blue", "yellow"]
    assert "    - red\n    - blue\n    - yellow" in render_qa(parsed)


def test_an_answer_line_names_the_correct_choices_by_letter() -> None:
    parsed = parse_text2qti(
        """1. Which planet is the Red Planet?
a) Venus
b) Mars
c) Jupiter
Answer: b

2. Which are even?
a) 2
b) 3
c) 4
Answer: a and c
"""
    )

    assert parsed.questions[0].correct_lines() == ["B"]
    assert parsed.questions[1].correct_lines() == ["A", "C"]
    assert parsed.questions[0].short_answers == []


def test_an_empty_answer_line_is_a_blank_to_grade_by_hand() -> None:
    parsed = parse_text2qti("1. Explain recursion.\nAnswer:\n")

    question = parsed.questions[0]
    assert question.prompt_lines == ["Explain recursion."]
    assert question.has_free_response and question.correct_lines() == []


def test_stated_points_win_and_only_the_gaps_get_the_default() -> None:
    text = """1. No points stated here?
Answer: yes

Points: 4
2. Four points?
Answer: yes

Points: 0
3. A survey question worth nothing?
Answer: yes
"""
    parsed = parse_text2qti(text)
    from opengrader.core.qa_file.text2qti import questions_without_points

    assert questions_without_points(parsed) == 1
    rendered = render_qa(parsed)
    assert "- (10 points) No points stated here?" in rendered
    assert "- (4 points) Four points?" in rendered
    assert "    - Partially Correct (-2)\n    - Incorrect (-4)" in rendered
    # Zero that was asked for stays zero.
    assert "- (0 points) A survey question worth nothing?" in rendered


def test_the_default_can_be_changed_and_partial_credit_stays_half() -> None:
    rendered = render_qa(parse_text2qti("1. Q?\nAnswer: a\n"), default_points=5)

    assert "- (5 points) Q?" in rendered
    assert "    - Partially Correct (-2.5)\n    - Incorrect (-5)" in rendered


def test_a_question_can_be_numbered_several_ways() -> None:
    parsed = parse_text2qti(
        """1. One?
Answer: a
2) Two?
Answer: b
Q3. Three?
Answer: c
Q4) Four?
Answer: d
Question 5: Five?
Answer: e
Problem 6. Six?
Answer: f
"""
    )

    assert [(q.number, q.prompt_lines[0]) for q in parsed.questions] == [
        (1, "One?"),
        (2, "Two?"),
        (3, "Three?"),
        (4, "Four?"),
        (5, "Five?"),
        (6, "Six?"),
    ]
    assert [q.short_answers for q in parsed.questions] == [[c] for c in "abcdef"]


def test_what_only_looks_like_a_number_is_left_in_the_question() -> None:
    parsed = parse_text2qti(
        """1. a quick question about ratios
The mix is
3: 1 by volume.
Answer: yes
"""
    )

    (question,) = parsed.questions
    assert question.parts == []
    assert question.prompt_lines == ["a quick question about ratios", "The mix is", "3: 1 by volume."]


def test_points_are_read_anywhere_on_the_questions_line_and_taken_out() -> None:
    parsed = parse_text2qti(
        """1. (10 points) At the start?
Answer: a
2. At the end? (4 points)
Answer: b
3) In the [3 pts] middle?
Answer: c
4. Marks too? (2 marks)
Answer: d
"""
    )

    assert [(q.points, q.prompt_lines[0]) for q in parsed.questions] == [
        (10.0, "At the start?"),
        (4.0, "At the end?"),
        (3.0, "In the middle?"),
        (2.0, "Marks too?"),
    ]


def test_points_on_the_line_beat_the_points_line_above_for_that_question_only() -> None:
    parsed = parse_text2qti(
        """Points: 2
1. Worth five? (5 points)
Answer: a
2. Back to two?
Answer: b
"""
    )

    assert [q.points for q in parsed.questions] == [5.0, 2.0]


def test_numbered_letters_are_parts_and_bare_letters_stay_choices() -> None:
    parsed = parse_text2qti(
        """1. Answer both parts.
1a) Name a noble gas.
Answer: neon
Answer: argon
1b) Which is a halogen?
a) Sodium
b) Chlorine
Answer: b

2(a) First?
Answer: x
2.b) Second?
Answer: y
2c. Third?
Answer: z
"""
    )

    first, second = parsed.questions
    assert first.prompt_lines == ["Answer both parts."]
    assert [(part.label, part.prompt_lines[0]) for part in first.parts] == [
        ("a", "Name a noble gas."),
        ("b", "Which is a halogen?"),
    ]
    assert first.parts[0].correct_lines() == ["neon", "argon"]
    assert first.parts[1].correct_lines() == ["B"]
    assert [choice.text for choice in first.parts[1].choices] == ["Sodium", "Chlorine"]
    # No "2." line of its own: a question that is only its parts.
    assert second.prompt_lines == [""]
    assert [part.label for part in second.parts] == ["a", "b", "c"]


def _part_points(text: str, **options) -> tuple[int, list[int]]:  # noqa: ANN003
    (question,) = _answer_key(text, **options).questions
    return question.points, [part.points for part in question.subquestions]


def test_a_questions_points_are_shared_among_parts_that_state_none() -> None:
    text = """1. Three parts. (10 points)
1a) One?
Answer: x
1b) Two?
Answer: y
1c) Three?
Answer: z
"""
    # Whole points; the first part takes what does not divide.
    assert _part_points(text) == (10, [4, 3, 3])


def test_a_part_keeps_its_own_points_and_the_rest_share_what_is_left() -> None:
    text = """1. Three parts. (10 points)
1a) One? (4 points)
Answer: x
1b) Two?
Answer: y
1c) Three?
Answer: z
"""
    assert _part_points(text) == (10, [4, 3, 3])


def test_parts_with_no_points_anywhere_each_get_the_default() -> None:
    text = """1a) One?
Answer: x
1b) Two?
Answer: y
"""
    assert _part_points(text) == (20, [10, 10])
    assert _part_points(text, default_points=5) == (10, [5, 5])
    assert summarize_points(parse_text2qti(text)) == PointsSummary(
        graded=2, defaulted=2, questions=1, with_parts=1
    )


def test_a_points_line_above_is_for_each_part_not_the_whole_question() -> None:
    text = """Points: 3
1a) One?
Answer: x
1b) Two?
Answer: y
"""
    assert _part_points(text) == (6, [3, 3])
    assert summarize_points(parse_text2qti(text)).defaulted == 0


def test_a_question_worth_less_than_its_parts_claim_does_not_go_negative() -> None:
    text = """1. Two parts. (3 points)
1a) One? (5 points)
Answer: x
1b) Two?
Answer: y
"""
    # Nothing is left to share, so the part without points gets the default.
    assert _part_points(text) == (15, [5, 10])


def test_parts_are_written_as_a_valid_answer_key() -> None:
    text = """1. Answer both parts.
1a) Name a noble gas.
Answer: neon
1b) Which is a halogen?
a) Sodium
b) Chlorine
Answer: b
"""
    rendered = render_qa(parse_text2qti(text))

    assert "- (20 points) Answer both parts.\n  - (10 points) Name a noble gas.\n    - **Correct**\n      - neon" in rendered
    (question,) = _answer_key(text).questions
    assert question.answers == [] and question.rubric == []
    halogen = question.subquestions[1]
    assert halogen.correct_choice_label == "B"
    assert [item.label for item in halogen.rubric] == ["Correct", "Partially Correct", "Incorrect"]


def test_an_answer_runs_on_until_a_blank_line() -> None:
    parsed = parse_text2qti(
        """1. Define osmosis.
Answer: the movement of water
across a membrane

This line is not part of anything.
2. Explain recursion.
Answer:
A function that
calls itself.
3. Left blank.
Answer:
"""
    )

    first, second, third = parsed.questions
    assert first.short_answers == ["the movement of water across a membrane"]
    assert second.short_answers == ["A function that calls itself."]
    assert not second.has_free_response
    assert third.has_free_response and third.short_answers == []


def test_a_draft_leaves_every_answer_blank_and_still_opens_for_grading() -> None:
    from opengrader.core.qa_file.draft_key import draft_from_text

    draft = draft_from_text(
        """Name: ________   ID: ______
1. (10 points) Explain recursion.
____
2. Pick one. (4 points)
a) x
b) y
Page 1 of 2
3. Parts.
3a) one
3b) two
""",
        title="quiz 2",
    )

    assert (draft.questions, draft.graded, draft.defaulted) == (3, 4, 2)
    assert "**Correct**" not in draft.markdown and "manual grading" not in draft.markdown
    # Page furniture is not question text.
    assert "Page 1 of 2" not in draft.markdown and "Name:" not in draft.markdown
    from opengrader.core.qa_file import parse_qa_markdown

    first, second, third = parse_qa_markdown(draft.markdown).questions
    assert (first.points, first.prompt, first.answers) == (10, "Explain recursion.", [])
    # Choices with none marked are kept as the question's text, not as an invalid question.
    assert second.choices == [] and "A) x" in second.prompt and "B) y" in second.prompt
    assert [(part.points, part.answers) for part in third.subquestions] == [(10, []), (10, [])]


def test_text_with_no_questions_has_no_draft() -> None:
    from opengrader.core.qa_file.draft_key import draft_from_text

    assert draft_from_text("Name: ____\nRead every question carefully.\n") is None
