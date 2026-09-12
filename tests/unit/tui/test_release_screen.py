from __future__ import annotations

import asyncio

import pytest
from textual.app import App
from textual.widgets import Button, Checkbox, Input, Static

from opengrader.core.curve import Curve
from opengrader.core.release import (
    build_readiness_report,
    build_release,
    compare_release,
)
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)
from opengrader.tui.ui.screens.release_screen import (
    VIEW_CHANGES,
    VIEW_READINESS,
    ReleaseScreen,
)


def _question(
    question_id: str = "q1",
    *,
    points_awarded: float | None = None,
    possible_points: float | None = 10,
) -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=question_id,
        prompt="",
        possible_points=possible_points,
        response_image_path=None,
        response_text="an answer",
        points_awarded=points_awarded,
    )


def _doc(
    student_id: str = "012345",
    student_name: str = "Tommy Liu",
    questions: list[StudentSubmissionQuestion] | None = None,
) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version="A",
        source_pdf=f"{student_id}.pdf",
        student_score="",
        questions=questions if questions is not None else [_question(points_awarded=8)],
    )


class _Host(App):
    def __init__(self, screen: ReleaseScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: object = "unset"

    def on_mount(self) -> None:
        self.push_screen(self._screen, lambda value: setattr(self, "result", value))


def _body_text(screen: ReleaseScreen) -> str:
    return "\n".join(
        str(widget.render()) for widget in screen.query_one("#release-body").query(Static)
    )


def _run(coro) -> None:
    asyncio.run(coro)


def test_a_ready_cohort_can_be_released_immediately() -> None:
    async def run() -> None:
        screen = ReleaseScreen(build_readiness_report([_doc()]))
        async with _Host(screen).run_test(size=(130, 44)) as pilot:
            await pilot.pause()
            assert not screen.query_one("#release-confirm", Button).disabled
            assert screen.query_one("#release-force", Checkbox).has_class("hidden")
            assert "nothing needs attention" in _body_text(screen).lower()

    _run(run())


def test_blocking_issues_gate_the_button_until_the_override_is_ticked() -> None:
    async def run() -> None:
        report = build_readiness_report([_doc(questions=[_question("q1")])])
        assert not report.ready
        screen = ReleaseScreen(report)
        async with _Host(screen).run_test(size=(130, 44)) as pilot:
            await pilot.pause()
            assert screen.query_one("#release-confirm", Button).disabled
            assert not screen.query_one("#release-force", Checkbox).has_class("hidden")

            await pilot.click("#release-force")
            await pilot.pause()
            assert not screen.query_one("#release-confirm", Button).disabled

    _run(run())


def test_releasing_passes_the_label_and_dismisses_with_the_new_id() -> None:
    seen: list[str] = []

    def _record(label: str):
        seen.append(label)
        return build_release([_doc()], label=label)

    async def run() -> None:
        screen = ReleaseScreen(
            build_readiness_report([_doc()]), on_release=_record
        )
        app = _Host(screen)
        async with app.run_test(size=(130, 44)) as pilot:
            await pilot.pause()
            screen.query_one("#release-label", Input).value = "returned in class"
            await pilot.click("#release-confirm")
            await pilot.pause()

            assert seen == ["returned in class"]
            assert isinstance(app.result, str)
            assert app.result.startswith("rel-")

    _run(run())


def test_the_release_callback_is_not_shadowed_by_the_button_handler() -> None:
    screen = ReleaseScreen(build_readiness_report([_doc()]), on_release=lambda _: None)
    assert callable(screen._on_release_pressed)
    assert screen._on_release_pressed.__self__ is screen


def test_a_failing_release_is_surfaced_not_raised() -> None:
    def _boom(label: str):
        raise OSError("read-only folder")

    async def run() -> None:
        screen = ReleaseScreen(build_readiness_report([_doc()]), on_release=_boom)
        app = _Host(screen)
        async with app.run_test(size=(130, 44)) as pilot:
            await pilot.pause()
            await pilot.click("#release-confirm")
            await pilot.pause()
            assert app.screen is screen
            assert app.result == "unset"

    _run(run())


def test_the_changes_view_lists_what_moved_since_the_release() -> None:
    before = _doc(questions=[_question("q1", points_awarded=4)])
    release = build_release([before])
    after = _doc(questions=[_question("q1", points_awarded=9)])
    comparison = compare_release(release, [after])

    async def run() -> None:
        screen = ReleaseScreen(
            build_readiness_report([after], previous_release=release),
            previous=release,
            comparison=comparison,
        )
        async with _Host(screen).run_test(size=(130, 44)) as pilot:
            await pilot.pause()
            assert screen._view == VIEW_READINESS

            await pilot.press("d")
            await pilot.pause()
            assert screen._view == VIEW_CHANGES
            text = _body_text(screen)
            assert "Question 1" in text
            assert "raised" in text
            assert "4 → 9" in text

            await pilot.press("s")
            await pilot.pause()
            assert screen._view == VIEW_READINESS

    _run(run())


def test_the_changes_view_shows_ids_outside_teacher_mode() -> None:
    before = _doc(questions=[_question("q1", points_awarded=4)])
    release = build_release([before])
    after = _doc(questions=[_question("q1", points_awarded=9)])
    comparison = compare_release(release, [after])

    async def run(teacher_mode: bool) -> str:
        screen = ReleaseScreen(
            build_readiness_report([after]),
            previous=release,
            comparison=comparison,
            teacher_mode=teacher_mode,
        )
        async with _Host(screen).run_test(size=(130, 44)) as pilot:
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            return _body_text(screen)

    anonymous = asyncio.run(run(False))
    assert "012345" in anonymous
    assert "Tommy Liu" not in anonymous

    named = asyncio.run(run(True))
    assert "Tommy Liu" in named


def test_with_no_previous_release_the_changes_view_says_so() -> None:
    async def run() -> None:
        screen = ReleaseScreen(build_readiness_report([_doc()]))
        async with _Host(screen).run_test(size=(130, 44)) as pilot:
            await pilot.pause()
            await pilot.press("d")
            await pilot.pause()
            assert "nothing to compare against" in _body_text(screen)

    _run(run())


def test_notes_are_shown_but_do_not_gate_the_release() -> None:
    async def run() -> None:
        report = build_readiness_report([_doc()], curve=Curve("bonus", 5.0))
        screen = ReleaseScreen(report)
        async with _Host(screen).run_test(size=(130, 44)) as pilot:
            await pilot.pause()
            assert not screen.query_one("#release-confirm", Button).disabled
            assert "curve is active" in _body_text(screen)

    _run(run())


def test_escape_dismisses_without_releasing() -> None:
    async def run() -> None:
        screen = ReleaseScreen(
            build_readiness_report([_doc()]),
            on_release=lambda _: pytest.fail("must not release"),
        )
        app = _Host(screen)
        async with app.run_test(size=(130, 44)) as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert app.result is None

    _run(run())
