from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from opengrader.core.roster import RosterStudent
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
    def __init__(self, candidate: IdentityCandidate, *, suggested: bool = False) -> None:
        self.candidate = candidate
        label = Text()
        if suggested:
            label.append("★ ", style="bold yellow")
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


# What `AssignIdentityScreen` returns for "leave this one, show the next".
SKIP_PAPER = "\x00skip"
# Rows shown at once; typing narrows the roster further.
_ROSTER_ROWS = 60


def rank_roster(
    roster: Sequence[RosterStudent],
    *,
    read_name: str,
    read_id: str,
    query: str = "",
) -> list[RosterStudent]:
    """The roster, students most like what was read first, narrowed to those
    matching `query` (part of a name or an ID) when one is typed."""
    from difflib import SequenceMatcher

    from opengrader.core.submissions.name_match import name_similarity, name_tokens

    digits = "".join(ch for ch in read_id if ch.isdigit())

    def fit(student: RosterStudent) -> float:
        score = name_similarity(read_name, student.name).score if read_name else 0.0
        if digits:
            score = max(score, SequenceMatcher(None, digits, student.student_id).ratio())
        return score

    query = query.strip()
    if query:
        typed = name_tokens(query)
        typed_digits = "".join(ch for ch in query if ch.isdigit())

        def wanted(student: RosterStudent) -> bool:
            if typed_digits and typed_digits in student.student_id:
                return True
            words = name_tokens(student.name)
            return bool(typed) and all(
                any(word.startswith(part) for word in words) for part in typed
            )

        roster = [student for student in roster if wanted(student)]
    return sorted(roster, key=lambda student: (-fit(student), student.name.casefold()))


class AssignIdentityScreen(ModalScreen["tuple[str, str] | str | None"]):
    """Say who wrote a paper: pick a roster student, or type the ID.

    The name and ID boxes cut from the scan are shown beside what was read
    off them, and the class roster below, the students most like that
    reading first (`core/submissions/name_match`). Typing narrows the roster
    by name or ID. Without a roster it is the old dialog: the tied
    candidates, or an ID typed by hand.

    Returns `(student_id, student_name)`, `SKIP_PAPER` (when stepping
    through several papers) to leave this one for later, or None to stop.
    """

    DEFAULT_CSS = """
    AssignIdentityScreen {
        align: center middle;
    }

    #identity-dialog {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 90%;
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

    #identity-crops {
        height: auto;
        max-height: 8;
        margin-top: 1;
    }

    #identity-crops SharpImage {
        width: 1fr;
        height: auto;
    }

    #identity-filter {
        margin-top: 1;
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

    #identity-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        item: ReviewItem,
        *,
        roster: Sequence[RosterStudent] = (),
        crops: Sequence[Path] = (),
        read_id: str = "",
        position: tuple[int, int] | None = None,
    ) -> None:
        self._item = item
        self._candidates = tuple(item.id_candidates)
        self._roster = tuple(roster)
        self._crops = tuple(crops)
        self._read_id = read_id
        self._position = position
        super().__init__()

    def compose(self) -> ComposeResult:
        from opengrader.tui.ui.widgets.sharp_image import SharpImage

        with Vertical(id="identity-dialog") as dialog:
            dialog.border_title = (
                "▸ Match to roster" if self._roster else "▸ Assign student ID"
            )
            yield Static(self._summary_text(), id="identity-summary")
            if self._crops:
                with Horizontal(id="identity-crops"):
                    for crop in self._crops:
                        yield SharpImage(
                            str(crop), fit_width_fraction=1.0, max_cell_height=6
                        )
            if self._roster:
                yield Input(
                    placeholder="Type part of a name or ID to narrow the roster…",
                    id="identity-filter",
                )
            yield ListView(id="identity-list")
            manual_hidden = "hidden" if self._candidates or self._roster else ""
            with Horizontal(id="identity-manual-row", classes=manual_hidden):
                yield Input(
                    placeholder="Type the student ID…",
                    id="identity-manual-id",
                )
                yield Button("Assign", id="identity-assign", variant="success", compact=True)
            with Horizontal(id="identity-buttons"):
                if self._position is not None:
                    yield Button("Skip", id="identity-skip", compact=True)
                yield Button(
                    "Stop" if self._position is not None else "Cancel",
                    id="identity-cancel",
                    compact=True,
                )

    def on_mount(self) -> None:
        self._fill_list("")
        if self._roster:
            self.query_one("#identity-filter", Input).focus()
        elif self._candidates:
            self.query_one("#identity-list", ListView).focus()
        else:
            self.query_one("#identity-manual-id", Input).focus()

    def _fill_list(self, query: str) -> None:
        list_view = self.query_one("#identity-list", ListView)
        list_view.clear()
        shown: set[str] = set()
        if not query.strip():
            for candidate in self._candidates:
                shown.add(candidate.student_id)
                list_view.append(_CandidateListItem(candidate, suggested=bool(self._roster)))
        if self._roster:
            ranked = rank_roster(
                self._roster,
                read_name=self._item.student_name,
                read_id=self._read_id,
                query=query,
            )
            for student in ranked[:_ROSTER_ROWS]:
                if student.student_id in shown:
                    continue
                list_view.append(
                    _CandidateListItem(IdentityCandidate(student.student_id, student.name))
                )
        list_view.append(_ManualEntryListItem())
        list_view.index = 0

    def _summary_text(self) -> str:
        name = self._item.student_name or "(no name read)"
        parts = []
        if self._position is not None:
            parts.append(f"Paper {self._position[0]} of {self._position[1]}")
        read = f"Read: “{name}”"
        if self._read_id:
            read += f", ID {self._read_id}"
        parts.append(read)
        if self._item.source_pdf_name:
            parts.append(f"from {self._item.source_pdf_name}")
        if self._roster:
            parts.append("pick who wrote it — the closest matches are first.")
        elif self._candidates:
            parts.append(
                f"{len(self._candidates)} roster students share this name — pick one."
            )
        else:
            parts.append("No roster match — enter the id.")
        return "  ·  ".join(parts)

    @on(Input.Changed, "#identity-filter")
    def _on_filter_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._fill_list(event.value)

    @on(Input.Submitted, "#identity-filter")
    def _on_filter_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        list_view = self.query_one("#identity-list", ListView)
        rows = [row for row in list_view.children if isinstance(row, _CandidateListItem)]
        if event.value.strip() and len(rows) == 1:
            candidate = rows[0].candidate
            self.dismiss((candidate.student_id, candidate.name))
            return
        list_view.focus()

    def key_down(self) -> None:
        if self.focused is not None and self.focused.id == "identity-filter":
            self.query_one("#identity-list", ListView).focus()

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
        name = self._item.student_name
        for student in self._roster:
            if student.student_id == student_id:
                name = student.name  # a roster ID brings its name
                break
        self.dismiss((student_id, name))

    @on(Button.Pressed, "#identity-skip")
    def _on_skip_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(SKIP_PAPER)

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
