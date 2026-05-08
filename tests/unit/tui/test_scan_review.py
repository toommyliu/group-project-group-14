from __future__ import annotations

import asyncio
from pathlib import Path

from pypdf import PdfWriter
from textual.containers import VerticalScroll
from textual.widgets import Label, ListView, Static

from opengrader.core.submissions.distribution import (
    PageEvidence,
    plan_submission_distribution,
)
from opengrader.tui.scan_review import ScanReviewApp


def _write_blank_pdf(path: Path, page_count: int) -> None:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as file:
        writer.write(file)


def _evidence(*logical_pages: int | None) -> list[PageEvidence]:
    pages: list[PageEvidence] = []
    for index, logical_page in enumerate(logical_pages, start=1):
        pages.append(
            PageEvidence(
                physical_page=index,
                logical_page=logical_page,
                logical_page_confidence=100.0 if logical_page is not None else 0.0,
                is_first_page=(logical_page == 1),
                first_page_confidence=100.0 if logical_page == 1 else 0.0,
                evidence_reasons=(),
            )
        )
    return pages


def test_scan_review_app_lists_ranges_and_shows_selected_details(tmp_path: Path) -> None:
    async def run() -> None:
        input_pdf = tmp_path / "submissions.pdf"
        _write_blank_pdf(input_pdf, 5)
        plan = plan_submission_distribution(
            page_evidence=_evidence(1, 2, 3, 1, 3),
            pages_per_submission=3,
        )
        app = ScanReviewApp(
            input_pdf=input_pdf,
            plan=plan,
            pages_per_submission=3,
        )

        async with app.run_test() as pilot:
            await pilot.pause()

            assert "Review Split Submissions" in str(
                app.query_one("#scan-review-title", Label).content
            )
            review_list = app.query_one("#scan-review-list", ListView)
            assert len(review_list.children) == 2
            assert "submission-0001" in str(
                review_list.children[0].query_one(Label).content
            )

            details = str(app.query_one("#scan-review-info", Static).content)
            assert "submission-0001" in details
            assert "Status: clean" in details

            review_list.index = 1
            await pilot.pause()

            details = str(app.query_one("#scan-review-info", Static).content)
            assert "submission-0002" in details
            assert "Missing logical pages: 2" in details
            assert "missing_logical_page_2" in details

    asyncio.run(run())


def test_scan_review_app_detail_scroll_actions_target_preview_pane(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        input_pdf = tmp_path / "submissions.pdf"
        _write_blank_pdf(input_pdf, 3)
        plan = plan_submission_distribution(
            page_evidence=_evidence(1, 2, 3),
            pages_per_submission=3,
        )
        app = ScanReviewApp(
            input_pdf=input_pdf,
            plan=plan,
            pages_per_submission=3,
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            pages = app.query_one("#scan-review-pages", VerticalScroll)
            calls: list[str] = []

            def scroll_page_down(*, animate: bool = True) -> None:
                assert animate is False
                calls.append("down")

            def scroll_page_up(*, animate: bool = True) -> None:
                assert animate is False
                calls.append("up")

            def scroll_home(
                *,
                animate: bool = True,
                x_axis: bool = True,
            ) -> None:
                assert animate is False
                assert x_axis is False
                calls.append("home")

            def scroll_end(
                *,
                animate: bool = True,
                x_axis: bool = True,
            ) -> None:
                assert animate is False
                assert x_axis is False
                calls.append("end")

            pages.scroll_page_down = scroll_page_down  # type: ignore[method-assign]
            pages.scroll_page_up = scroll_page_up  # type: ignore[method-assign]
            pages.scroll_home = scroll_home  # type: ignore[method-assign]
            pages.scroll_end = scroll_end  # type: ignore[method-assign]

            app.action_detail_scroll_down()
            app.action_detail_scroll_up()
            app.action_detail_scroll_home()
            app.action_detail_scroll_end()

            assert calls == ["down", "up", "home", "end"]

    asyncio.run(run())


def test_scan_review_app_exposes_compact_keyboard_detail_scroll_bindings() -> None:
    bindings = {binding[0]: binding[1] for binding in ScanReviewApp.BINDINGS}

    assert bindings["d"] == "detail_scroll_down"
    assert bindings["u"] == "detail_scroll_up"
    assert bindings["ctrl+d"] == "detail_scroll_down"
    assert bindings["ctrl+u"] == "detail_scroll_up"
    assert bindings["ctrl+f"] == "detail_scroll_down"
    assert bindings["ctrl+b"] == "detail_scroll_up"
    assert bindings["g"] == "detail_scroll_home"
    assert bindings["G"] == "detail_scroll_end"
    assert bindings["shift+g"] == "detail_scroll_end"


def test_scan_review_app_dispatches_compact_keyboard_detail_scroll_bindings(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        input_pdf = tmp_path / "submissions.pdf"
        _write_blank_pdf(input_pdf, 3)
        plan = plan_submission_distribution(
            page_evidence=_evidence(1, 2, 3),
            pages_per_submission=3,
        )
        app = ScanReviewApp(
            input_pdf=input_pdf,
            plan=plan,
            pages_per_submission=3,
        )

        async with app.run_test() as pilot:
            await pilot.pause()
            calls: list[str] = []
            app.action_detail_scroll_down = lambda: calls.append("down")
            app.action_detail_scroll_up = lambda: calls.append("up")
            app.action_detail_scroll_home = lambda: calls.append("home")
            app.action_detail_scroll_end = lambda: calls.append("end")

            for key, expected in [
                ("d", "down"),
                ("u", "up"),
                ("ctrl+d", "down"),
                ("ctrl+u", "up"),
                ("ctrl+f", "down"),
                ("ctrl+b", "up"),
                ("g", "home"),
                ("G", "end"),
                ("shift+g", "end"),
            ]:
                before = len(calls)
                await pilot.press(key)
                await pilot.pause()
                assert calls[before:] == [expected]

    asyncio.run(run())
