from __future__ import annotations

import asyncio

from textual.app import App
from textual.widgets import Static

from opengrader.core.submissions.answer_patterns import PatternKind
from opengrader.tui.data.models import (
    AnswerPatternMember,
    AnswerPatternReportItem,
    AnswerPatternReportView,
    SharedWrongAnswerRow,
    SimilarityReportItem,
    SimilarityReportMember,
)
from opengrader.tui.ui.screens.similarity_screen import (
    SimilarityScreen,
    _SimilarityMemberRow,
)
from opengrader.core.submissions.similarity import SimilarityKind


def _member(item_id: str, name: str) -> AnswerPatternMember:
    return AnswerPatternMember(
        student_item_id=item_id,
        student_id=name.lower().replace(" ", ""),
        student_name=name,
        source_pdf_name=f"{name}.pdf",
    )


def _pattern_item(
    *,
    severity: str = "medium",
    z_score: float = 5.6,
    shared: tuple[SharedWrongAnswerRow, ...] | None = None,
    difference_count: int = 4,
    harpp_hogan_index: float | None = 1.5,
) -> AnswerPatternReportItem:
    shared = shared or (
        SharedWrongAnswerRow("q7", "D", "A", 0.08),
        SharedWrongAnswerRow("q2", "C", "B", 0.31),
    )
    return AnswerPatternReportItem(
        assignment_version="A",
        kind=PatternKind.SHARED_WRONG_ANSWERS,
        severity=severity,
        a=_member("s1", "Ann Lee"),
        b=_member("s2", "Bob Chan"),
        shared_wrong=shared,
        expected_shared=1.8,
        z_score=z_score,
        both_wrong_count=len(shared),
        difference_count=difference_count,
        harpp_hogan_index=harpp_hogan_index,
    )


def _view(*items: AnswerPatternReportItem, analyzed: int = 20) -> AnswerPatternReportView:
    return AnswerPatternReportView(
        items=items,
        analyzed_question_count=analyzed,
        excluded_question_count=4,
        pairs_compared=1770,
        threshold=4.0,
        expected_by_chance=0.056,
    )


def _similarity_item() -> SimilarityReportItem:
    member = SimilarityReportMember(
        student_item_id="s3",
        student_id="333",
        student_name="Cat Diaz",
        source_pdf_name="cat.pdf",
        response_text="a long written answer that two students happened to share",
    )
    return SimilarityReportItem(
        question_id="q1",
        assignment_version="A",
        kind=SimilarityKind.NEAR_DUPLICATE,
        severity="high",
        max_score=0.97,
        members=(member,),
        pairs=(),
    )


class _Host(App):
    def __init__(self, screen: SimilarityScreen) -> None:
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
    return str(screen.query_one("#similarity-summary", Static).render())


def _body_text(screen) -> str:  # noqa: ANN001
    parts = [
        str(widget.render())
        for widget in screen.query("#similarity-body Label")
    ]
    parts += [
        str(widget.render()) for widget in screen.query("#similarity-body Static")
    ]
    return "\n".join(parts)


def test_without_a_pattern_report_the_toggle_is_absent_and_inert() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        before = _summary(screen)
        await pilot.press("o")
        await pilot.pause()
        return before, screen._view, _summary(screen)

    before, view, after = _run(
        lambda: SimilarityScreen([_similarity_item()], teacher_mode=True), body
    )
    assert "objective" not in before.lower()
    assert view == SimilarityScreen.VIEW_RESPONSES
    assert after == before


def test_the_toggle_is_advertised_when_the_report_exists() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        return _summary(screen)

    summary = _run(
        lambda: SimilarityScreen(
            [_similarity_item()], teacher_mode=True, patterns=_view(_pattern_item())
        ),
        body,
    )
    assert "o shows objective answers" in summary


def test_o_swaps_the_view_and_swaps_back() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        objective = (screen._view, _summary(screen), _body_text(screen))
        await pilot.press("o")
        await pilot.pause()
        return objective, (screen._view, _summary(screen))

    (view, summary, body_text), (back_view, back_summary) = _run(
        lambda: SimilarityScreen(
            [_similarity_item()], teacher_mode=True, patterns=_view(_pattern_item())
        ),
        body,
    )
    assert view == SimilarityScreen.VIEW_OBJECTIVE
    assert "objective answer-pattern flag" in summary
    assert "Ann Lee ↔ Bob Chan" in body_text
    assert back_view == SimilarityScreen.VIEW_RESPONSES
    assert "similar-answer cluster" in back_summary


def test_the_pair_carries_its_supporting_numbers() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        return _body_text(screen)

    text = _run(
        lambda: SimilarityScreen([], teacher_mode=True, patterns=_view(_pattern_item())),
        body,
    )
    assert "2 shared (1.8 expected)" in text
    assert "z = 5.6" in text
    assert "Harpp-Hogan index 1.50" in text
    assert "Question 7" in text
    assert "8% of wrong pairs agree here" in text


def test_scope_is_stated_and_survives_finding_nothing() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        return _body_text(screen)

    text = _run(lambda: SimilarityScreen([], teacher_mode=True, patterns=_view()), body)
    assert "20 of 24 rule-graded question(s) analyzed" in text
    assert "1770 student pair(s) compared" in text
    assert "No implausible objective answer patterns" in text


def test_nothing_analyzable_never_reads_as_a_clean_result() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        return _body_text(screen)

    text = _run(
        lambda: SimilarityScreen(
            [],
            teacher_mode=True,
            patterns=AnswerPatternReportView(
                items=(), analyzed_question_count=0, excluded_question_count=6
            ),
        ),
        body,
    )
    assert "No objective question could be analyzed" in text
    assert "Nothing to compare" in text
    assert "✓" not in text


def test_the_view_is_anonymity_aware() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        return _body_text(screen)

    text = _run(
        lambda: SimilarityScreen([], teacher_mode=False, patterns=_view(_pattern_item())),
        body,
    )
    assert "Ann Lee" not in text
    assert "annlee" in text


def test_selecting_a_student_routes_to_the_rarest_shared_answer() -> None:
    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        rows = list(screen.query(_SimilarityMemberRow))
        rows[0].focus()
        await pilot.pause()
        return rows[0]._question_id

    question_id = _run(
        lambda: SimilarityScreen([], teacher_mode=True, patterns=_view(_pattern_item())),
        body,
    )
    assert question_id == "q7"


def test_a_long_shared_list_is_truncated_with_a_count() -> None:
    shared = tuple(
        SharedWrongAnswerRow(f"q{index}", "B", "A", 0.2) for index in range(1, 11)
    )

    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        return _body_text(screen)

    text = _run(
        lambda: SimilarityScreen(
            [], teacher_mode=True, patterns=_view(_pattern_item(shared=shared))
        ),
        body,
    )
    assert "… and 4 more identical wrong answer(s)" in text


def test_an_unmappable_student_renders_inert() -> None:
    item = _pattern_item()
    orphan = AnswerPatternReportItem(
        assignment_version=item.assignment_version,
        kind=item.kind,
        severity=item.severity,
        a=AnswerPatternMember("", "999", "Ghost", "ghost.pdf"),
        b=item.b,
        shared_wrong=item.shared_wrong,
        expected_shared=item.expected_shared,
        z_score=item.z_score,
        both_wrong_count=item.both_wrong_count,
        difference_count=item.difference_count,
        harpp_hogan_index=item.harpp_hogan_index,
    )

    async def body(pilot, screen):  # noqa: ANN001, ANN202
        await pilot.press("o")
        await pilot.pause()
        return len(list(screen.query(_SimilarityMemberRow))), _body_text(screen)

    rows, text = _run(
        lambda: SimilarityScreen([], teacher_mode=True, patterns=_view(orphan)), body
    )
    assert rows == 1
    assert "can't open" in text
