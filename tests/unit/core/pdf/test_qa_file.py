import pytest

from opengrader.core.pdf.templates.qa_file import (
    parse_qa_markdown,
    qa_to_template_questions,
    render_qa_markdown,
)


def test_parse_indentation_single_version_with_subquestions_and_correct_answers() -> (
    None
):
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
    assert q1.rubric == []
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
    assert q2a.rubric == []
    assert (q2b.id, q2b.label, q2b.points, q2b.prompt, q2b.answers) == (
        "q2b",
        "b",
        6,
        "What is 15 in hexadecimal?",
        ["0xF"],
    )
    assert q2b.rubric == []


def test_parse_rubric_items_with_positive_negative_and_partial_credit_notes() -> None:
    content = """
- (10 points) Solve for x.
  - **Correct**
    - x = 4
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-4)
      - Correct setup but arithmetic mistake
      - Close to final answer
    - Incorrect: -10

- (8 points) Multi-part scoring
  - (8 points) Show your work
    - **Rubric**
      - Correct (+2)
      - Partially Correct (-3)
      - Incorrect (-8)
"""

    document = parse_qa_markdown(content)

    q1 = document.questions[0]
    assert [(item.label, item.points_delta) for item in q1.rubric] == [
        ("Correct", 0.0),
        ("Partially Correct", -4.0),
        ("Incorrect", -10.0),
    ]
    assert q1.rubric[1].notes == [
        "Correct setup but arithmetic mistake",
        "Close to final answer",
    ]

    q2a = document.questions[1].subquestions[0]
    assert [(item.label, item.points_delta) for item in q2a.rubric] == [
        ("Correct", 2.0),
        ("Partially Correct", -3.0),
        ("Incorrect", -8.0),
    ]


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

    with pytest.raises(
        ValueError, match="content found before the first version header"
    ):
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


def test_parse_weighted_partial_credit_items_can_accumulate_to_full_deduction() -> None:
    content = """
- (4 points) Explain the process.
  - **Rubric**
    - Correct (+0)
    - Partially Correct
      - Major conceptual error (-3)
      - Minor notation issue (-1)
    - Incorrect (-4)
"""

    document = parse_qa_markdown(content)
    rubric = document.questions[0].rubric
    assert [(item.label, item.points_delta) for item in rubric] == [
        ("Correct", 0.0),
        ("Partially Correct", None),
        ("Incorrect", -4.0),
    ]
    assert [(sub.label, sub.points_delta) for sub in rubric[1].subitems] == [
        ("Major conceptual error", -3.0),
        ("Minor notation issue", -1.0),
    ]


def test_parse_rejects_rubric_deductions_that_do_not_reach_question_points() -> None:
    content = """
- (5 points) Parent
  - **Rubric**
    - Correct (+0)
    - Partially Correct
      - Major conceptual error (-3)
      - Minor notation issue (-1)
    - Incorrect (-6)
"""

    with pytest.raises(ValueError, match="must accumulate to -5 points"):
        parse_qa_markdown(content)


def test_parse_rejects_invalid_rubric_top_level_structure() -> None:
    content = """
- (5 points) Parent
  - **Rubric**
    - Correct (+0)
    - Partial Credit (-2)
    - Incorrect (-5)
"""

    with pytest.raises(ValueError, match="Top-level rubric categories"):
        parse_qa_markdown(content)


def test_parse_multiple_choice_question_and_template_payload() -> None:
    content = """
- (5 points) Which data structure is FIFO?
  - **Choices**
    - A) Stack
    - B. Queue
    - C: Tree
    - D) Graph
  - **Correct**
    - b
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-2)
    - Incorrect (-5)
"""

    document = parse_qa_markdown(content)
    question = document.questions[0]

    assert question.answers == []
    assert [(choice.label, choice.text) for choice in question.choices] == [
        ("A", "Stack"),
        ("B", "Queue"),
        ("C", "Tree"),
        ("D", "Graph"),
    ]
    assert question.correct_choice_label == "B"

    assert qa_to_template_questions(document) == [
        {
            "id": "q1",
            "points": 5,
            "prompt": "Which data structure is FIFO?",
            "choices": [
                {"label": "A", "text": "Stack"},
                {"label": "B", "text": "Queue"},
                {"label": "C", "text": "Tree"},
                {"label": "D", "text": "Graph"},
            ],
        }
    ]


def test_parse_multiple_choice_subquestion_and_template_payload() -> None:
    content = """
- (6 points) Answer all parts.
  - (3 points) Pick the even number.
    - **Choices**
      - A) 3
      - B) 4
      - C) 5
    - **Correct**
      - B
  - (3 points) Explain your choice.
    - **Correct**
      - 4 is divisible by 2
"""

    document = parse_qa_markdown(content)
    question = document.questions[0]
    subquestion = question.subquestions[0]

    assert [(choice.label, choice.text) for choice in subquestion.choices] == [
        ("A", "3"),
        ("B", "4"),
        ("C", "5"),
    ]
    assert subquestion.correct_choice_label == "B"

    assert qa_to_template_questions(document) == [
        {
            "id": "q1",
            "points": 6,
            "prompt": "Answer all parts.",
            "subquestions": [
                {
                    "id": "q1a",
                    "label": "a",
                    "points": 3,
                    "prompt": "Pick the even number.",
                    "choices": [
                        {"label": "A", "text": "3"},
                        {"label": "B", "text": "4"},
                        {"label": "C", "text": "5"},
                    ],
                },
                {
                    "id": "q1b",
                    "label": "b",
                    "points": 3,
                    "prompt": "Explain your choice.",
                },
            ],
        }
    ]


def test_parse_rejects_multiple_choice_question_with_one_choice() -> None:
    content = """
- (1 points) Bad multiple choice
  - **Choices**
    - A) only option
  - **Correct**
    - A
"""

    with pytest.raises(ValueError, match="at least two choices"):
        parse_qa_markdown(content)


def test_parse_rejects_multiple_choice_question_with_duplicate_labels() -> None:
    content = """
- (1 points) Bad multiple choice
  - **Choices**
    - A) one
    - A) two
  - **Correct**
    - A
"""

    with pytest.raises(ValueError, match="unique labels"):
        parse_qa_markdown(content)


def test_parse_rejects_multiple_choice_question_with_non_contiguous_labels() -> None:
    content = """
- (1 points) Bad multiple choice
  - **Choices**
    - A) one
    - C) two
  - **Correct**
    - A
"""

    with pytest.raises(ValueError, match="contiguous labels starting at A"):
        parse_qa_markdown(content)


def test_parse_rejects_multiple_choice_question_without_single_correct_label() -> None:
    missing_correct = """
- (1 points) Bad multiple choice
  - **Choices**
    - A) one
    - B) two
"""
    multiple_correct = """
- (1 points) Bad multiple choice
  - **Choices**
    - A) one
    - B) two
  - **Correct**
    - A
    - B
"""

    with pytest.raises(ValueError, match="exactly one correct option label"):
        parse_qa_markdown(missing_correct)
    with pytest.raises(ValueError, match="exactly one correct option label"):
        parse_qa_markdown(multiple_correct)


def test_parse_rejects_multiple_choice_question_with_invalid_correct_label() -> None:
    wrong_label = """
- (1 points) Bad multiple choice
  - **Choices**
    - A) one
    - B) two
  - **Correct**
    - C
"""
    answer_text = """
- (1 points) Bad multiple choice
  - **Choices**
    - A) one
    - B) two
  - **Correct**
    - B) two
"""

    with pytest.raises(ValueError, match="is not one of the defined choices"):
        parse_qa_markdown(wrong_label)
    with pytest.raises(ValueError, match="must use only the option label"):
        parse_qa_markdown(answer_text)


def test_parse_rejects_mixing_multiple_choice_and_subquestions() -> None:
    content = """
- (5 points) Mixed structure
  - **Choices**
    - A) one
    - B) two
  - **Correct**
    - A
  - (2 points) part a
    - **Correct**
      - sub answer
"""

    with pytest.raises(
        ValueError, match="mixes multiple-choice options and subquestions"
    ):
        parse_qa_markdown(content)


def test_render_round_trip_preserves_versions_and_question_structure() -> None:
    content = """---
title: Round Trip Quiz
instructions: Show all work.
questions_start_page: 1
---

## Version A

- (6 points) A root question
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-2)
    - Incorrect (-6)
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
    assert [
        (item.label, item.points_delta)
        for item in reparsed.version_questions["A"][0].rubric
    ] == [
        ("Correct", 0.0),
        ("Partially Correct", -2.0),
        ("Incorrect", -6.0),
    ]

    assert [q.prompt for q in reparsed.version_questions["B"]] == ["B root question"]
    assert reparsed.version_questions["B"][0].answers == ["gamma"]


def test_render_round_trip_preserves_multiple_choice_structure() -> None:
    content = """---
title: Multiple Choice Quiz
questions_start_page: 1
---

## Version A

- (4 points) Which one is a queue operation?
  - **Choices**
    - A) push
    - B) enqueue
    - C) pop
  - **Correct**
    - B

- (4 points) Answer both parts.
  - (2 points) Pick the prime number.
    - **Choices**
      - A) 4
      - B) 5
      - C) 6
    - **Correct**
      - B
  - (2 points) Explain why.
    - **Correct**
      - 5 has exactly two positive divisors
"""

    parsed = parse_qa_markdown(content)
    rendered = render_qa_markdown(parsed)
    reparsed = parse_qa_markdown(rendered)

    version_a = reparsed.version_questions["A"]
    assert [(choice.label, choice.text) for choice in version_a[0].choices] == [
        ("A", "push"),
        ("B", "enqueue"),
        ("C", "pop"),
    ]
    assert version_a[0].correct_choice_label == "B"
    assert [
        (choice.label, choice.text) for choice in version_a[1].subquestions[0].choices
    ] == [
        ("A", "4"),
        ("B", "5"),
        ("C", "6"),
    ]
    assert version_a[1].subquestions[0].correct_choice_label == "B"
