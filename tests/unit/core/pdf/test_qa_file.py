import pytest

from opengrader.core.pdf.templates.qa_file import (
    parse_qa_markdown,
    qa_to_template_questions,
    render_qa_markdown,
)


def test_parse_indentation_single_version_with_subquestions_and_correct_answers() -> None:
    content = """---
title: Quiz 1
instructions: Keep answers concise.
continued_page_header: Quiz 1 (continued)
questions_start_page: 2
---

- (5 points) What does OS stand for?
  - **Correct**
    - Operating System
    - operating system
  - **Expected**
    - Not used for template generation

- (10 points) Answer all parts.
  - (4 points) What is 2 in binary?
    - **Correct**
      - 10
      - 0b10
  - (6 points) What is 15 in hexadecimal?
    - **Correct**
      - 0xF
    - **Wrong**
      - 15
"""

    document = parse_qa_markdown(content)

    assert document.title == "Quiz 1"
    assert document.instructions == "Keep answers concise."
    assert document.continued_page_header == "Quiz 1 (continued)"
    assert document.questions_start_page == 2

    assert document.versions == []
    assert document.version_questions == {}

    assert len(document.questions) == 2

    q1 = document.questions[0]
    assert q1.id == "q1"
    assert q1.points == 5
    assert q1.prompt == "What does OS stand for?"
    assert q1.answers == ["Operating System", "operating system"]
    assert q1.subquestions == []

    q2 = document.questions[1]
    assert q2.id == "q2"
    assert q2.points == 10
    assert q2.prompt == "Answer all parts."
    assert q2.answers == []
    assert len(q2.subquestions) == 2

    q2a, q2b = q2.subquestions
    assert (q2a.id, q2a.label, q2a.points, q2a.prompt, q2a.answers) == (
        "q2a",
        "a",
        4,
        "What is 2 in binary?",
        ["10", "0b10"],
    )
    assert (q2b.id, q2b.label, q2b.points, q2b.prompt, q2b.answers) == (
        "q2b",
        "b",
        6,
        "What is 15 in hexadecimal?",
        ["0xF"],
    )


def test_parse_indentation_multiple_versions_and_template_selection() -> None:
    content = """---
title: Midterm
questions_start_page: 1
---

## Version A

- (2 points) A question
  - **Correct**
    - A

## B

- (3 points) B question
  - **Correct**
    - B
"""

    document = parse_qa_markdown(content)

    assert document.versions == ["A", "B"]
    assert set(document.version_questions.keys()) == {"A", "B"}

    assert [q.prompt for q in document.questions] == ["A question"]

    a_questions = qa_to_template_questions(document, version="A")
    b_questions = qa_to_template_questions(document, version="B")

    assert a_questions == [("q1", 2, "A question")]
    assert b_questions == [("q1", 3, "B question")]


def test_parse_single_version_without_header_is_allowed() -> None:
    content = """
- (1 points) Lone version question
  - **Correct**
    - yes
"""

    document = parse_qa_markdown(content)

    assert document.versions == []
    assert document.version_questions == {}
    assert len(document.questions) == 1
    assert document.questions[0].prompt == "Lone version question"


def test_parse_rejects_content_before_first_version_header() -> None:
    content = """
- (1 points) Should fail

## Version A
- (1 points) question
"""

    with pytest.raises(ValueError, match="content found before the first version header"):
        parse_qa_markdown(content)


def test_parse_rejects_mixing_question_answers_and_subquestions() -> None:
    content = """
- (5 points) Mixed structure
  - **Correct**
    - root answer
  - (2 points) part a
    - **Correct**
      - sub answer
"""

    with pytest.raises(ValueError, match="mixes direct answers and subquestions"):
        parse_qa_markdown(content)


def test_parse_rejects_nested_subquestions() -> None:
    content = """
- (5 points) Parent
  - (2 points) Child
    - (1 points) Grandchild
"""

    with pytest.raises(ValueError, match="nested subquestions are not supported"):
        parse_qa_markdown(content)


def test_render_round_trip_preserves_versions_and_question_structure() -> None:
    content = """---
title: Round Trip Quiz
instructions: Show all work.
questions_start_page: 1
---

## Version A

- (6 points) A root question
  - (3 points) A part one
    - **Correct**
      - alpha
  - (3 points) A part two
    - **Correct**
      - beta

## Version B

- (6 points) B root question
  - **Correct**
    - gamma
"""

    parsed = parse_qa_markdown(content)
    rendered = render_qa_markdown(parsed)
    reparsed = parse_qa_markdown(rendered)

    assert reparsed.title == "Round Trip Quiz"
    assert reparsed.instructions == "Show all work."
    assert reparsed.questions_start_page == 1
    assert reparsed.versions == ["A", "B"]

    assert [q.prompt for q in reparsed.version_questions["A"]] == ["A root question"]
    assert reparsed.version_questions["A"][0].subquestions[0].prompt == "A part one"
    assert reparsed.version_questions["A"][0].subquestions[1].prompt == "A part two"

    assert [q.prompt for q in reparsed.version_questions["B"]] == ["B root question"]
    assert reparsed.version_questions["B"][0].answers == ["gamma"]
