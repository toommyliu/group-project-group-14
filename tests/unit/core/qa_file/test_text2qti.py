from opengrader.core.qa_file.text2qti import parse_text2qti, render_qa


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
