from __future__ import annotations

import io
from pathlib import Path

import fitz
from PIL import Image
from textual import events
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Footer, Label, ListItem, ListView, Static

from opengrader.core.submissions import (
    SubmissionDistributionPlan,
    SubmissionDistributionRange,
)
from opengrader.tui.ui.widgets.sharp_image import SharpImage


class _SubmissionListItem(ListItem):
    def __init__(self, page_range: SubmissionDistributionRange) -> None:
        self.page_range = page_range
        status = "review" if page_range.needs_review else "clean"
        missing = (
            f" missing {','.join(str(page) for page in page_range.missing_pages)}"
            if page_range.missing_pages
            else ""
        )
        label = (
            f"{page_range.name} [{status}] "
            f"pages {','.join(str(page) for page in page_range.physical_pages)}"
            f"{missing}"
        )
        super().__init__(Label(label))

    def on_click(self, event: events.Click) -> None:
        self.post_message(ScanReviewApp.SubmissionSelected(self.page_range))
        event.stop()


class ScanReviewApp(App[None]):
    """Read-only review UI for inferred scanned-submission splits."""

    CSS_PATH = [
        Path(__file__).parent / "tui.tcss",
        Path(__file__).parent / "scan_review.tcss",
    ]
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
        ("j", "cursor_down", "Next"),
        ("down", "cursor_down", "Next"),
        ("k", "cursor_up", "Previous"),
        ("up", "cursor_up", "Previous"),
        ("d", "detail_scroll_down", "Scroll details down"),
        ("u", "detail_scroll_up", "Scroll details up"),
        ("ctrl+d", "detail_scroll_down", "Scroll details down"),
        ("ctrl+u", "detail_scroll_up", "Scroll details up"),
        ("ctrl+f", "detail_scroll_down", "Scroll details down"),
        ("ctrl+b", "detail_scroll_up", "Scroll details up"),
        ("g", "detail_scroll_home", "Details top"),
        ("G", "detail_scroll_end", "Details bottom"),
        ("shift+g", "detail_scroll_end", "Details bottom"),
        ("pagedown", "detail_scroll_down", "Scroll details down"),
        ("pageup", "detail_scroll_up", "Scroll details up"),
        ("home", "detail_scroll_home", "Details top"),
        ("end", "detail_scroll_end", "Details bottom"),
    ]

    class SubmissionSelected(Message):
        def __init__(self, page_range: SubmissionDistributionRange) -> None:
            self.page_range = page_range
            super().__init__()

    def __init__(
        self,
        *,
        input_pdf: str | Path,
        plan: SubmissionDistributionPlan,
        pages_per_submission: int,
    ) -> None:
        super().__init__()
        self.input_pdf = Path(input_pdf)
        self.plan = plan
        self.pages_per_submission = pages_per_submission
        self._thumbnail_cache: dict[int, Image.Image] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="scan-review-screen"):
            yield Label("Review Split Submissions", id="scan-review-title")
            yield Static(self._summary_text(), id="scan-review-summary")
            with Horizontal(id="scan-review-workspace"):
                with Vertical(id="scan-review-left-column"):
                    yield ListView(id="scan-review-list")
                with Vertical(id="scan-review-details"):
                    yield Static("Select a submission to inspect it.", id="scan-review-info")
                    yield VerticalScroll(id="scan-review-pages")
        yield Footer()

    def on_mount(self) -> None:
        review_list = self.query_one("#scan-review-list", ListView)
        review_list.border_title = "Submissions"
        for page_range in self.plan.ranges:
            review_list.append(_SubmissionListItem(page_range))
        if self.plan.ranges:
            review_list.index = 0
            self._show_range(self.plan.ranges[0])
            review_list.focus()

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if isinstance(item, _SubmissionListItem):
            self._show_range(item.page_range)

    def on_scan_review_app_submission_selected(
        self, event: SubmissionSelected
    ) -> None:
        self._show_range(event.page_range)

    def action_cursor_down(self) -> None:
        self.query_one("#scan-review-list", ListView).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#scan-review-list", ListView).action_cursor_up()

    def action_detail_scroll_down(self) -> None:
        self.query_one("#scan-review-pages", VerticalScroll).scroll_page_down(
            animate=False
        )

    def action_detail_scroll_up(self) -> None:
        self.query_one("#scan-review-pages", VerticalScroll).scroll_page_up(animate=False)

    def action_detail_scroll_home(self) -> None:
        self.query_one("#scan-review-pages", VerticalScroll).scroll_home(
            animate=False,
            x_axis=False,
        )

    def action_detail_scroll_end(self) -> None:
        self.query_one("#scan-review-pages", VerticalScroll).scroll_end(
            animate=False,
            x_axis=False,
        )

    def _summary_text(self) -> str:
        review_count = sum(
            1 for page_range in self.plan.ranges if page_range.needs_review
        )
        clean_count = len(self.plan.ranges) - review_count
        return (
            f"{self.input_pdf} | {len(self.plan.page_evidence)} scanned page(s) | "
            f"{len(self.plan.ranges)} inferred submission(s) | "
            f"{clean_count} clean | {review_count} need review | "
            f"{self.pages_per_submission} expected page(s) per submission"
        )

    def _show_range(self, page_range: SubmissionDistributionRange) -> None:
        self.query_one("#scan-review-info", Static).update(
            self._range_detail_text(page_range)
        )
        page_container = self.query_one("#scan-review-pages", VerticalScroll)
        page_container.remove_children()

        columns = self._page_preview_columns()
        pages = list(zip(page_range.physical_pages, page_range.logical_pages))
        for row_start in range(0, len(pages), columns):
            page_cards: list[Vertical] = []
            for physical_page, logical_page in pages[row_start : row_start + columns]:
                logical = "?" if logical_page is None else str(logical_page)
                page_cards.append(
                    Vertical(
                        Static(
                            f"Physical page {physical_page} | logical page {logical}",
                            classes="scan-review-page-label",
                        ),
                        SharpImage(
                            self._thumbnail_for_page(physical_page),
                            classes="scan-review-page-image",
                            fit_width_fraction=0.98,
                            allow_upscale=False,
                            max_cell_height=44,
                        ),
                        classes="scan-review-page-card",
                    )
                )
            page_container.mount(Horizontal(*page_cards, classes="scan-review-page-row"))

    def _page_preview_columns(self) -> int:
        details = self.query_one("#scan-review-details", Vertical)
        return 2 if details.size.width >= 88 else 1

    def _range_detail_text(self, page_range: SubmissionDistributionRange) -> str:
        missing = (
            ", ".join(str(page) for page in page_range.missing_pages)
            if page_range.missing_pages
            else "none"
        )
        reasons = (
            "\n".join(f"- {reason}" for reason in page_range.review_reasons)
            if page_range.review_reasons
            else "none"
        )
        return (
            f"{page_range.name}\n"
            f"Status: {'needs review' if page_range.needs_review else 'clean'}\n"
            f"Confidence: {page_range.confidence:.1f}\n"
            f"Physical pages: {', '.join(str(page) for page in page_range.physical_pages)}\n"
            f"Logical pages: {', '.join('?' if page is None else str(page) for page in page_range.logical_pages)}\n"
            f"Missing logical pages: {missing}\n\n"
            f"Review reasons:\n{reasons}"
        )

    def _thumbnail_for_page(self, physical_page: int) -> Image.Image:
        cached = self._thumbnail_cache.get(physical_page)
        if cached is not None:
            return cached

        with fitz.open(str(self.input_pdf)) as document:
            page = document[physical_page - 1]
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            image = Image.open(io.BytesIO(pix.tobytes("png"))).copy()

        self._thumbnail_cache[physical_page] = image
        return image
