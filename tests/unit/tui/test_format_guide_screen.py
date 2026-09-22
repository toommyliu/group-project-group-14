import pytest

from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.qa_file.text2qti import parse_text2qti, render_qa
from opengrader.tui.ui.screens.format_guide_screen import (
    COMPLEX_TEMPLATE,
    SIMPLE_TEMPLATE,
)


@pytest.mark.parametrize(
    "template, expected_questions",
    [(SIMPLE_TEMPLATE, 3), (COMPLEX_TEMPLATE, 5)],
)
def test_template_parses_and_renders_a_valid_answer_key(
    template: str, expected_questions: int
) -> None:
    parsed = parse_text2qti(template)
    assert len(parsed.questions) == expected_questions
    assert all(q.has_answer_block() for q in parsed.questions)

    qa_markdown = render_qa(parsed)
    document = parse_qa_markdown(qa_markdown)
    assert len(document.questions) == expected_questions


def test_complex_template_exercises_every_supported_question_type() -> None:
    parsed = parse_text2qti(COMPLEX_TEMPLATE)
    q1, q2, q3, q4, q5 = parsed.questions

    assert [c.correct for c in q1.choices] == [False, True, False, False]
    assert sum(c.correct for c in q2.choices) == 2
    assert q3.numeric_answer is not None and "3.14" in q3.numeric_answer
    assert q4.short_answers == ["Au", "gold"]
    assert q5.has_free_response


def test_complex_template_select_all_renders_a_valid_multiple_answer_key() -> None:
    from opengrader.core.qa_file import qa_question_type
    from opengrader.core.question_types import MULTIPLE_ANSWER

    document = parse_qa_markdown(render_qa(parse_text2qti(COMPLEX_TEMPLATE)))
    select_all = document.questions[1]
    assert qa_question_type(select_all) == MULTIPLE_ANSWER
    assert select_all.correct_choice_labels == ("A", "C")


def test_bubble_template_parses_and_is_entirely_multiple_choice() -> None:
    from opengrader.core.qa_file import qa_question_type
    from opengrader.core.question_types import MULTIPLE_CHOICE
    from opengrader.tui.ui.screens.format_guide_screen import BUBBLE_TEMPLATE

    parsed = parse_text2qti(BUBBLE_TEMPLATE)
    assert len(parsed.questions) == 5
    assert all(q.has_answer_block() for q in parsed.questions)

    document = parse_qa_markdown(render_qa(parsed))
    assert len(document.questions) == 5
    assert all(qa_question_type(q) == MULTIPLE_CHOICE for q in document.questions)


def test_bubble_template_survives_the_bubble_sheet_bridge() -> None:
    from opengrader.core.qa_file.markdown import qa_to_bubble_questions
    from opengrader.tui.ui.screens.format_guide_screen import BUBBLE_TEMPLATE

    document = parse_qa_markdown(render_qa(parse_text2qti(BUBBLE_TEMPLATE)))
    bubbles = qa_to_bubble_questions(document)
    assert len(bubbles) == len(document.questions)
    assert all(len(b.choice_labels) == 4 for b in bubbles)


def test_the_outcomes_template_is_offered_and_copyable() -> None:
    import asyncio

    from textual.widgets import Button, Select

    from opengrader.tui.tui import TuiApp
    from opengrader.tui.ui.screens import FormatGuideScreen

    async def go() -> tuple[bool, list[str]]:
        app = TuiApp()
        async with app.run_test(size=(150, 50)) as pilot:
            await pilot.pause()
            await pilot.press("t")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, FormatGuideScreen), type(screen)
            has_button = bool(screen.query("#copy-outcomes"))
            options = [
                str(value)
                for _, value in screen.query_one(Select)._options  # type: ignore[attr-defined]
            ]
            _ = Button
            return has_button, options

    has_button, options = asyncio.run(go())
    assert has_button
    assert "outcomes" in options


def test_the_outcomes_template_actually_produces_tagged_questions() -> None:
    from opengrader.core.qa_file import (
        outcomes_by_question,
        parse_qa_markdown,
        possible_points_by_question,
    )
    from opengrader.core.qa_file.text2qti import parse_text2qti, render_qa
    from opengrader.tui.ui.screens.format_guide_screen import OUTCOMES_TEMPLATE

    doc = parse_qa_markdown(render_qa(parse_text2qti(OUTCOMES_TEMPLATE)))
    tags = outcomes_by_question(doc)
    assert tags == {
        "q1": ("CLO1",),
        "q2": ("CLO1",),
        "q3": ("CLO2", "CLO3"),
        "q4": ("CLO3",),
    }
    points = possible_points_by_question(doc)
    assert "q5" in points and "q5" not in tags
