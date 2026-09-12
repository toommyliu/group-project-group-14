from __future__ import annotations

import asyncio

from textual.app import App
from textual.widgets import Label, Static

from opengrader.core.analytics import (
    ItemAnalysisReport,
    OutcomeMastery,
    OutcomeReport,
    StudentOutcomeMastery,
)
from opengrader.tui.ui.screens.item_analysis_screen import ItemAnalysisScreen

EMPTY_ITEMS = ItemAnalysisReport(items=(), class_size=3)


def _student(key: str, earned: float, possible: float) -> StudentOutcomeMastery:
    return StudentOutcomeMastery(
        student_key=key,
        student_id=key,
        student_name=f"Student {key}",
        outcome="CLO1",
        earned_points=earned,
        possible_points=possible,
        questions_graded=1,
    )


def _outcome(
    name: str = "CLO1",
    *,
    mastered: int = 2,
    measured: int = 3,
    mean: float | None = 0.72,
    possible: float = 20.0,
    assignment_possible: float = 25.0,
    question_ids: tuple[str, ...] = ("q1", "q2"),
) -> OutcomeMastery:
    return OutcomeMastery(
        outcome=name,
        question_ids=question_ids,
        possible_points=possible,
        assignment_possible_points=assignment_possible,
        students_measured=measured,
        students_mastered=mastered,
        mean_mastery=mean,
        students=(_student("s1", 18, 20),),
    )


def _outcome_report(*outcomes: OutcomeMastery, untagged: float = 5.0) -> OutcomeReport:
    return OutcomeReport(
        outcomes=outcomes,
        class_size=3,
        mastery_threshold=0.7,
        untagged_points=untagged,
        assignment_possible_points=25.0,
    )


class _Host(App):
    def __init__(self, screen: ItemAnalysisScreen) -> None:
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _run(screen_factory, body):  # noqa: ANN001, ANN202
    result: list = []

    async def go() -> None:
        app = _Host(screen_factory())
        async with app.run_test(size=(140, 46)) as pilot:
            await pilot.pause()
            result.append(await body(pilot, app.screen))

    asyncio.run(go())
    return result[0]


def _summary(screen) -> str:  # noqa: ANN001
    return str(screen.query_one("#item-analysis-summary", Static).render())


def test_without_outcomes_the_toggle_is_absent_and_inert() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        before = _summary(screen)
        await pilot.press("o")
        await pilot.pause()
        return before, screen._view, _summary(screen)

    before, view, after = _run(lambda: ItemAnalysisScreen(EMPTY_ITEMS), body)
    assert "outcomes" not in before.lower()
    assert view == ItemAnalysisScreen.VIEW_QUESTIONS
    assert after == before


def test_with_outcomes_the_hint_advertises_the_toggle() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        return _summary(screen)

    summary = _run(
        lambda: ItemAnalysisScreen(
            EMPTY_ITEMS, outcome_report=_outcome_report(_outcome())
        ),
        body,
    )
    assert "o shows outcomes" in summary


def test_the_questions_view_is_still_the_default() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        return screen._view, str(screen.query_one("#item-analysis-dialog").border_title)

    view, title = _run(
        lambda: ItemAnalysisScreen(
            EMPTY_ITEMS, outcome_report=_outcome_report(_outcome())
        ),
        body,
    )
    assert view == ItemAnalysisScreen.VIEW_QUESTIONS
    assert title == "▸ Item Analysis"


def test_o_swaps_to_outcomes_and_back() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        first = (screen._view, str(screen.query_one("#item-analysis-dialog").border_title))
        await pilot.press("o")
        await pilot.pause()
        return first, screen._view

    first, back = _run(
        lambda: ItemAnalysisScreen(
            EMPTY_ITEMS, outcome_report=_outcome_report(_outcome())
        ),
        body,
    )
    assert first == (ItemAnalysisScreen.VIEW_OUTCOMES, "▸ Item Analysis — Learning Outcomes")
    assert back == ItemAnalysisScreen.VIEW_QUESTIONS


def test_the_outcome_view_shows_one_card_per_outcome() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        cards = screen.query_one("#item-analysis-cards").children
        return [str(card.border_title) for card in cards]

    titles = _run(
        lambda: ItemAnalysisScreen(
            EMPTY_ITEMS,
            outcome_report=_outcome_report(_outcome("CLO1"), _outcome("CLO2")),
        ),
        body,
    )
    assert len(titles) == 2
    assert "CLO1" in titles[0] and "CLO2" in titles[1]


def test_coverage_sits_beside_mastery_on_the_card() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        card = screen.query_one("#item-analysis-cards").children[0]
        return str(card.border_title), [str(label.render()) for label in card.query(Label)]

    headline, lines = _run(
        lambda: ItemAnalysisScreen(
            EMPTY_ITEMS, outcome_report=_outcome_report(_outcome())
        ),
        body,
    )
    assert "2/3 mastered" in headline
    body_text = " ".join(lines)
    assert "Q1, Q2" in body_text
    assert "20 pts" in body_text
    assert "80% of the assignment" in body_text


def test_the_summary_leads_with_the_untagged_share() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        return _summary(screen)

    summary = _run(
        lambda: ItemAnalysisScreen(
            EMPTY_ITEMS, outcome_report=_outcome_report(_outcome())
        ),
        body,
    )
    assert "Mastery bar 70%" in summary
    assert "80% of points carry a tag" in summary
    assert "5 untagged" in summary


def test_a_tagged_but_ungraded_assignment_still_offers_the_view() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        labels = screen.query_one("#item-analysis-cards").query(Label)
        return screen._view, " ".join(str(label.render()) for label in labels)

    view, text = _run(
        lambda: ItemAnalysisScreen(EMPTY_ITEMS, outcome_report=_outcome_report()),
        body,
    )
    assert view == ItemAnalysisScreen.VIEW_OUTCOMES
    assert "No learning outcomes measured in this view yet" in text


def test_a_weak_outcome_is_flagged_like_a_weak_item() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        return [
            card.has_class("flagged")
            for card in screen.query_one("#item-analysis-cards").children
        ]

    flagged = _run(
        lambda: ItemAnalysisScreen(
            EMPTY_ITEMS,
            outcome_report=_outcome_report(
                _outcome("CLO1", mastered=1, measured=4),
                _outcome("CLO2", mastered=4, measured=4),
            ),
        ),
        body,
    )
    assert flagged == [True, False]


def test_switching_section_rebuilds_the_outcome_report() -> None:
    calls: list[str | None] = []

    def outcome_builder(section: str | None) -> OutcomeReport:
        calls.append(section)
        return _outcome_report(_outcome("CLO1"))

    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        await pilot.press("f")
        await pilot.pause()
        return list(calls)

    seen = _run(
        lambda: ItemAnalysisScreen(
            EMPTY_ITEMS,
            report_builder=lambda _section: EMPTY_ITEMS,
            sections=("MW",),
            outcome_report=_outcome_report(_outcome("CLO1")),
            outcome_builder=outcome_builder,
        ),
        body,
    )
    assert seen == ["MW"]


def _item(question_id: str = "q1"):  # noqa: ANN202
    from opengrader.core.analytics import DifficultyBand, ItemAnalysis, SeparationBand

    return ItemAnalysis(
        question_id=question_id,
        assignment_version=None,
        question_type="free_response",
        possible_points=10.0,
        responses_count=3,
        mean_points=7.0,
        difficulty=0.7,
        difficulty_band=DifficultyBand.MODERATE,
        separation=0.4,
        separation_band=SeparationBand.GOOD,
        distractors=(),
    )


GRADED_ITEMS = ItemAnalysisReport(items=(_item(),), class_size=3)


def _hint(screen) -> tuple[bool, str]:  # noqa: ANN001
    widget = screen.query_one("#item-analysis-outcome-hint", Static)
    return widget.display, str(widget.render())


def test_an_untagged_answer_key_is_offered_outcome_tagging() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        return _hint(screen)

    shown, text = _run(lambda: ItemAnalysisScreen(GRADED_ITEMS), body)
    assert shown is True
    assert text == (
        "Tag questions with Outcome: in your answer key to see per-outcome "
        "mastery here."
    )


def test_a_tagged_answer_key_gets_no_hint() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        return _hint(screen)[0]

    shown = _run(
        lambda: ItemAnalysisScreen(
            GRADED_ITEMS, outcome_report=_outcome_report(_outcome())
        ),
        body,
    )
    assert shown is False


def test_no_hint_before_anything_is_graded() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        return _hint(screen)[0]

    assert _run(lambda: ItemAnalysisScreen(EMPTY_ITEMS), body) is False


def test_the_hint_disappears_once_switched_into_the_outcome_view() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        return _hint(screen)[0]

    assert (
        _run(
            lambda: ItemAnalysisScreen(
                GRADED_ITEMS, outcome_report=_outcome_report(_outcome())
            ),
            body,
        )
        is False
    )
