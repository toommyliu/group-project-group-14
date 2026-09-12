from __future__ import annotations

import asyncio

from textual.app import App
from textual.widgets import Static

from opengrader.core.analytics import GradedAnswer, build_grader_agreement_report
from opengrader.tui.formatting import (
    format_grader_agreement_overview,
    format_grader_pair,
    format_grader_stats,
)
from opengrader.tui.ui.screens import GraderAgreementScreen


def _answer(actor: str, student: str, points: float, *, possible: float | None = 10.0):
    return GradedAnswer(
        actor=actor,
        student_key=student,
        question_id="q1",
        points_awarded=points,
        possible_points=possible,
    )


def _report(answers, **kwargs):
    return build_grader_agreement_report(answers, **kwargs)


def test_overview_explains_a_headline_that_exceeds_the_counted_rows():
    report = _report(
        [_answer("alice", "s1", 9.0), _answer("bob", "s2", 0.0, possible=0.0)]
    )

    text = format_grader_agreement_overview(report)

    assert "2[/b] graded answer(s)" in text
    assert "1 excluded" in text
    assert "no point value" in text


def test_overview_says_when_two_graders_cannot_be_compared():
    report = _report([_answer("alice", "s1", 9.0), _answer("bob", "s2", 4.0)])

    text = format_grader_agreement_overview(report)

    assert report.has_overlap is False
    assert "cannot be compared" in text
    assert "calibrate pack" in text


def test_overview_names_the_basis_when_they_can_be_compared():
    report = _report([_answer("alice", "s1", 9.0), _answer("bob", "s1", 4.0)])

    assert "same students' answers" in format_grader_agreement_overview(report)


def test_overview_is_safe_with_nothing_graded():
    assert "No graded answers" in format_grader_agreement_overview(_report([]))


def test_an_unmeasurable_drift_renders_as_a_dash_not_a_zero():
    report = _report([_answer("alice", "s1", 9.0)])

    text = format_grader_stats(report.graders[0])

    assert report.graders[0].drift is None
    assert "drift —" in text


def test_a_grader_row_carries_the_residual_with_its_sign():
    report = _report([_answer("alice", "s1", 9.0), _answer("bob", "s1", 5.0)])
    rows = {grader.actor: format_grader_stats(grader) for grader in report.graders}

    assert "+20.0 pts/100" in rows["alice"]
    assert "-20.0 pts/100" in rows["bob"]


def test_a_pair_row_names_the_more_generous_grader():
    answers = []
    for index in range(4):
        answers.append(_answer("alice", f"s{index}", 9.0))
        answers.append(_answer("bob", f"s{index}", 5.0))

    text = format_grader_pair(_report(answers, min_pair_samples=4).same_work_pairs[0])

    assert "alice ↔ bob" in text
    assert "alice marks 40.0 pts/100 more generously" in text
    assert "provisional" not in text


def test_a_small_sample_pair_is_labelled_provisional():
    report = _report([_answer("alice", "s1", 10.0), _answer("bob", "s1", 0.0)])

    assert "provisional" in format_grader_pair(report.same_work_pairs[0])


def test_identical_grading_reports_no_systematic_difference():
    answers = [_answer("alice", "s1", 7.0), _answer("bob", "s1", 7.0)]

    assert "no systematic difference" in format_grader_pair(
        _report(answers).same_work_pairs[0]
    )


class _Host(App):
    def __init__(self, report) -> None:
        self._report = report
        super().__init__()

    def on_mount(self) -> None:
        self.push_screen(GraderAgreementScreen(self._report))


def _rendered(screen) -> str:
    parts = []
    for widget in screen.query(Static):
        rendered = widget.render()
        parts.append(rendered.plain if hasattr(rendered, "plain") else str(rendered))
    return "\n".join(parts)


def _shared(actor: str, student: str, points: float) -> GradedAnswer:
    return GradedAnswer(
        actor=actor,
        student_key=student,
        question_id="q1",
        points_awarded=points,
        possible_points=10.0,
        answer_key="shared answer",
    )


def test_the_modal_lists_every_grader_and_their_pair() -> None:
    answers = []
    for index in range(4):
        answers.append(_answer("alice", f"s{index}", 9.0))
        answers.append(_answer("bob", f"s{index}", 5.0))
    app = _Host(_report(answers, min_pair_samples=4))

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            text = _rendered(app.screen)

        assert "PER GRADER" in text
        assert "alice" in text and "bob" in text
        assert "AGREEMENT — SAME STUDENTS' ANSWERS" in text

    asyncio.run(run())


def test_the_modal_prefers_the_stronger_overlap_basis() -> None:
    answers = []
    for index in range(3):
        answers.append(_shared("alice", f"s{index}", 9.0))
        answers.append(_shared("bob", f"s{index}", 5.0))
    report = _report(answers, min_pair_samples=3)
    assert report.same_work_pairs and report.same_answer_pairs
    app = _Host(report)

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            text = _rendered(app.screen)

        assert "SAME STUDENTS' ANSWERS" in text
        assert "SAME ANSWER, DIFFERENT STUDENTS" not in text

    asyncio.run(run())


def test_the_modal_falls_back_to_same_answer_overlap() -> None:
    answers = []
    for index in range(3):
        answers.append(_shared("alice", f"a{index}", 9.0))
        answers.append(_shared("bob", f"b{index}", 5.0))
    app = _Host(_report(answers, min_pair_samples=3))

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            text = _rendered(app.screen)

        assert "SAME ANSWER, DIFFERENT STUDENTS" in text

    asyncio.run(run())


def test_the_modal_closes_on_escape() -> None:
    app = _Host(_report([_answer("alice", "s1", 9.0)]))

    async def run() -> None:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, GraderAgreementScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, GraderAgreementScreen)

    asyncio.run(run())
