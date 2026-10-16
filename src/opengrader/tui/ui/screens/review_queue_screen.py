from __future__ import annotations

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from opengrader.core.submissions.student_file import IdentityCandidate
from opengrader.tui.data.models import ReviewItem
from opengrader.tui.formatting import format_review_row


class _ReviewListItem(ListItem):
    def __init__(self, item: ReviewItem, *, teacher_mode: bool) -> None:
        self.item = item
        super().__init__(
            Label(format_review_row(item, teacher_mode=teacher_mode)),
            classes="review-row",
        )


class ReviewQueueScreen(ModalScreen["ReviewItem | None"]):
    DEFAULT_CSS = """
    ReviewQueueScreen {
        align: center middle;
    }

    #review-dialog {
        width: 80%;
        max-width: 100;
        height: 80%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #review-summary {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #review-list {
        height: 1fr;
        margin-top: 1;
        background: transparent;
        border: round $surface-lighten-2;
        scrollbar-size: 1 1;
    }

    ReviewQueueScreen .review-empty {
        color: $text-muted;
        padding: 1 2;
    }

    #review-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("r", "close", "Close"),
    ]

    def __init__(self, items: list[ReviewItem], *, teacher_mode: bool) -> None:
        self._items = items
        self._teacher_mode = teacher_mode
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="review-dialog") as dialog:
            dialog.border_title = "▸ Needs Review"
            yield Static(self._summary_text(), id="review-summary")
            list_view = ListView(id="review-list")
            yield list_view
            with Horizontal(id="review-buttons"):
                yield Button("Close", id="review-close", compact=True)

    def on_mount(self) -> None:
        list_view = self.query_one("#review-list", ListView)
        if not self._items:
            list_view.append(
                ListItem(
                    Label("No submissions need review. ✓", classes="review-empty")
                )
            )
            list_view.can_focus = False
            self.query_one("#review-close", Button).focus()
            return
        for item in self._items:
            list_view.append(_ReviewListItem(item, teacher_mode=self._teacher_mode))
        list_view.index = 0
        list_view.focus()

    def _summary_text(self) -> str:
        if not self._items:
            return "Nothing flagged — every submission looks clean."
        counts = {"high": 0, "medium": 0, "low": 0}
        for item in self._items:
            counts[item.severity] += 1
        has_identity = any(item.is_identity_flag for item in self._items)
        opens = "Enter assigns id / opens" if has_identity else "Enter opens"
        return (
            f"{len(self._items)} item(s): {counts['high']} high, "
            f"{counts['medium']} medium, {counts['low']} low.  "
            f"{opens} · esc closes."
        )

    @on(ListView.Selected)
    def _on_row_selected(self, event: ListView.Selected) -> None:
        event.stop()
        if isinstance(event.item, _ReviewListItem):
            self.dismiss(event.item.item)

    @on(Button.Pressed, "#review-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class _CandidateListItem(ListItem):
    def __init__(self, candidate: IdentityCandidate) -> None:
        self.candidate = candidate
        label = Text()
        label.append(candidate.student_id or "(no id)", style="bold")
        if candidate.name:
            label.append("  ·  ", style="dim")
            label.append(candidate.name)
        super().__init__(Label(label), classes="candidate-row")


class _ManualEntryListItem(ListItem):
    def __init__(self) -> None:
        super().__init__(
            Label(Text("✎ Enter ID manually…", style="italic")),
            classes="candidate-row",
        )


class AssignIdentityScreen(ModalScreen["tuple[str, str] | None"]):
    DEFAULT_CSS = """
    AssignIdentityScreen {
        align: center middle;
    }

    #identity-dialog {
        width: 70%;
        max-width: 80;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #identity-summary {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #identity-list {
        height: auto;
        max-height: 14;
        margin-top: 1;
        background: transparent;
        border: round $surface-lighten-2;
        scrollbar-size: 1 1;
    }

    #identity-manual-row {
        height: auto;
        margin-top: 1;
    }

    #identity-manual-row.hidden {
        display: none;
    }

    #identity-manual-id {
        width: 1fr;
    }

    #identity-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, item: ReviewItem) -> None:
        self._item = item
        self._candidates = tuple(item.id_candidates)
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="identity-dialog") as dialog:
            dialog.border_title = "▸ Assign student ID"
            yield Static(self._summary_text(), id="identity-summary")
            list_view = ListView(id="identity-list")
            yield list_view
            manual_hidden = "hidden" if self._candidates else ""
            with Horizontal(id="identity-manual-row", classes=manual_hidden):
                yield Input(
                    placeholder="Type the student ID…",
                    id="identity-manual-id",
                )
                yield Button("Assign", id="identity-assign", variant="success", compact=True)
            with Horizontal(id="identity-buttons"):
                yield Button("Cancel", id="identity-cancel", compact=True)

    def on_mount(self) -> None:
        list_view = self.query_one("#identity-list", ListView)
        for candidate in self._candidates:
            list_view.append(_CandidateListItem(candidate))
        list_view.append(_ManualEntryListItem())
        if self._candidates:
            list_view.index = 0
            list_view.focus()
        else:
            self.query_one("#identity-manual-id", Input).focus()

    def _summary_text(self) -> str:
        name = self._item.student_name or self._item.student_display_id or "this submission"
        parts = [f"Who is “{name}”?"]
        if self._item.source_pdf_name:
            parts.append(f"from {self._item.source_pdf_name}")
        if self._candidates:
            parts.append(
                f"{len(self._candidates)} roster students share this name — pick one."
            )
        else:
            parts.append("No roster match — enter the id.")
        return "  ·  ".join(parts)

    @on(ListView.Selected, "#identity-list")
    def _on_candidate_selected(self, event: ListView.Selected) -> None:
        event.stop()
        if isinstance(event.item, _CandidateListItem):
            candidate = event.item.candidate
            self.dismiss((candidate.student_id, candidate.name))
        elif isinstance(event.item, _ManualEntryListItem):
            manual_row = self.query_one("#identity-manual-row", Horizontal)
            manual_row.remove_class("hidden")
            self.query_one("#identity-manual-id", Input).focus()

    @on(Input.Submitted, "#identity-manual-id")
    def _on_manual_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._assign_manual()

    @on(Button.Pressed, "#identity-assign")
    def _on_assign_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._assign_manual()

    def _assign_manual(self) -> None:
        student_id = self.query_one("#identity-manual-id", Input).value.strip()
        if not student_id:
            self.app.notify("Enter a student ID first.", severity="warning")
            return
        self.dismiss((student_id, self._item.student_name))

    @on(Button.Pressed, "#identity-cancel")
    def _on_cancel_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# What `AssignVersionScreen` returns for "show me the paper first".
LOOK_AT_PAPER = "\x00look"


class _VersionListItem(ListItem):
    def __init__(self, version: str) -> None:
        self.version = version
        super().__init__(Label(Text(f"Version {version}", style="bold")), classes="candidate-row")


class AssignVersionScreen(ModalScreen["str | None"]):
    """Pick which answer-key version a paper is, when it could not be read.

    Returns the chosen version, `LOOK_AT_PAPER` to open the paper first, or
    None to leave it for now. Nothing is graded on the paper until a version
    is picked (`core/grading/versions.py`).
    """

    DEFAULT_CSS = """
    AssignVersionScreen {
        align: center middle;
    }

    #version-dialog {
        width: 70%;
        max-width: 80;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #version-summary {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #version-list {
        height: auto;
        max-height: 14;
        margin-top: 1;
        background: transparent;
        border: round $surface-lighten-2;
    }

    #version-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #version-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, item: ReviewItem, versions: tuple[str, ...]) -> None:
        self._item = item
        self._versions = versions
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="version-dialog") as dialog:
            dialog.border_title = "▸ Which version is this paper?"
            yield Static(self._summary_text(), id="version-summary", markup=False)
            yield ListView(
                *(_VersionListItem(version) for version in self._versions),
                id="version-list",
            )
            with Horizontal(id="version-buttons"):
                yield Button("Look at the paper", id="version-look", compact=True)
                yield Button("Cancel", id="version-cancel", compact=True)

    def on_mount(self) -> None:
        list_view = self.query_one("#version-list", ListView)
        list_view.index = 0
        list_view.focus()

    def _summary_text(self) -> str:
        name = self._item.student_name or self._item.student_display_id or "this paper"
        where = f" ({self._item.source_pdf_name})" if self._item.source_pdf_name else ""
        return (
            f"{name}{where}: {self._item.detail}\n"
            "Pick the version printed on the paper. Its questions and rubric "
            "then follow that version, and it can be graded."
        )

    @on(ListView.Selected, "#version-list")
    def _on_version_selected(self, event: ListView.Selected) -> None:
        event.stop()
        if isinstance(event.item, _VersionListItem):
            self.dismiss(event.item.version)

    @on(Button.Pressed, "#version-look")
    def _on_look_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(LOOK_AT_PAPER)

    @on(Button.Pressed, "#version-cancel")
    def _on_cancel_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
