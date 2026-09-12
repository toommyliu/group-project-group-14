from __future__ import annotations

from collections.abc import Callable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

from opengrader.core.comments import CommentLibrary, CommentSnippet
from opengrader.tui.formatting import format_comment_snippet_row

_MAX_DIGIT_HOTKEYS = 9

SCOPE_GLOBAL = "global"
SCOPE_QUESTION = "question"


class _SnippetItem(ListItem):
    def __init__(
        self, snippet: CommentSnippet, *, hotkey: int | None, uses: int
    ) -> None:
        self.snippet = snippet
        super().__init__(
            Label(format_comment_snippet_row(snippet, hotkey=hotkey, uses=uses)),
            classes="snippet-row",
        )


class CommentLibraryScreen(ModalScreen["str | None"]):
    DEFAULT_CSS = """
    CommentLibraryScreen {
        align: center middle;
    }

    #comment-library-dialog {
        width: 72%;
        max-width: 96;
        height: 80%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #comment-library-summary {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #comment-library-scope-row {
        height: auto;
        margin-top: 1;
    }

    #comment-library-scope {
        min-width: 24;
    }

    #comment-library-list {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        scrollbar-size: 1 1;
    }

    #comment-library-empty {
        color: $text-muted;
        padding: 1 2;
    }

    #comment-library-add-row {
        height: auto;
        margin-top: 1;
    }

    #comment-library-new {
        width: 1fr;
    }

    #comment-library-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    CommentLibraryScreen .snippet-row {
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("l", "close", "Close"),
        Binding("d,delete", "delete_highlighted", "Delete", show=False),
        Binding("g", "toggle_scope", "Scope", show=False),
    ]

    def __init__(
        self,
        library: CommentLibrary,
        *,
        on_change: Callable[[CommentLibrary], None] | None = None,
        current_comment: str = "",
        question_id: str | None = None,
        question_label: str = "",
        scope: str = SCOPE_GLOBAL,
        on_scope_change: Callable[[str], None] | None = None,
    ) -> None:
        self._library = library
        self._on_change = on_change
        self._current_comment = current_comment.strip()
        self._question_id = (question_id or "").strip()
        self._question_label = question_label.strip()
        self._on_scope_change = on_scope_change
        self._scope = (
            SCOPE_QUESTION
            if scope == SCOPE_QUESTION and self._question_id
            else SCOPE_GLOBAL
        )
        super().__init__()

    @property
    def _can_scope_per_question(self) -> bool:
        return bool(self._question_id)

    def _effective_question_id(self) -> str | None:
        return self._question_id if self._scope == SCOPE_QUESTION else None

    def compose(self) -> ComposeResult:
        with Vertical(id="comment-library-dialog") as dialog:
            dialog.border_title = "▸ Comment Library"
            yield Static(self._summary_text(), id="comment-library-summary")
            if self._can_scope_per_question:
                with Horizontal(id="comment-library-scope-row"):
                    yield Button(
                        self._scope_button_label(),
                        id="comment-library-scope",
                        compact=True,
                    )
            yield ListView(id="comment-library-list")
            yield Label(
                "No saved snippets yet — type feedback below and Save it.",
                id="comment-library-empty",
            )
            with Horizontal(id="comment-library-add-row"):
                yield Input(
                    placeholder="New snippet…",
                    id="comment-library-new",
                )
                yield Button("Save", id="comment-library-save", compact=True)
            with Horizontal(id="comment-library-buttons"):
                if self._current_comment:
                    yield Button(
                        "Save current comment",
                        id="comment-library-save-current",
                        compact=True,
                    )
                yield Button("Close", id="comment-library-close", compact=True)

    def on_mount(self) -> None:
        self._refresh_list()


    def _ordered_snippets(self) -> tuple[CommentSnippet, ...]:
        return self._library.ordered(self._effective_question_id())

    def _refresh_list(self) -> None:
        list_view = self.query_one("#comment-library-list", ListView)
        list_view.clear()
        qid = self._effective_question_id()
        ordered = self._ordered_snippets()
        for index, snippet in enumerate(ordered):
            hotkey = index + 1 if index < _MAX_DIGIT_HOTKEYS else None
            uses = snippet.uses_on(qid) if qid else snippet.uses
            list_view.append(_SnippetItem(snippet, hotkey=hotkey, uses=uses))
        has_snippets = bool(ordered)
        list_view.display = has_snippets
        self.query_one("#comment-library-empty", Label).display = not has_snippets
        self.query_one("#comment-library-summary", Static).update(self._summary_text())
        if has_snippets:
            list_view.index = 0

    def _scope_button_label(self) -> str:
        if self._scope == SCOPE_QUESTION:
            where = self._question_label or "this question"
            return f"Order: {where} ▸ global"
        return "Order: Global ▸ this question"

    def _summary_text(self) -> str:
        count = len(self._library.snippets)
        if not count:
            return "No saved snippets yet.  Save one below, then insert it with a keystroke.  esc closes."
        if self._scope == SCOPE_QUESTION:
            where = self._question_label or "this question"
            order_note = f"most-used on {where} first"
        else:
            order_note = "most-used first"
        toggle_note = "  g reorders." if self._can_scope_per_question else ""
        return (
            f"{count} saved snippet(s), {order_note}.  Press its number to "
            f"insert, d to delete the highlighted one.{toggle_note}  esc closes."
        )

    def action_toggle_scope(self) -> None:
        # Input consumes printable g; a focus guard here would also block button clicks.
        if not self._can_scope_per_question:
            return
        self._scope = (
            SCOPE_GLOBAL if self._scope == SCOPE_QUESTION else SCOPE_QUESTION
        )
        if self._on_scope_change is not None:
            self._on_scope_change(self._scope)
        self.query_one("#comment-library-scope", Button).label = (
            self._scope_button_label()
        )
        self._refresh_list()

    @on(Button.Pressed, "#comment-library-scope")
    def _on_scope_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_toggle_scope()


    def _persist(self, library: CommentLibrary) -> None:
        self._library = library
        if self._on_change is not None:
            self._on_change(library)

    def _add_snippet(self, text: str) -> None:
        updated = self._library.with_snippet(text)
        if updated is self._library:
            return
        self._persist(updated)
        self._refresh_list()

    def action_delete_highlighted(self) -> None:
        if isinstance(self.focused, Input):
            return
        item = self._highlighted_item()
        if item is None:
            return
        self._persist(self._library.without(item.snippet.text))
        self._refresh_list()

    def _highlighted_item(self) -> _SnippetItem | None:
        list_view = self.query_one("#comment-library-list", ListView)
        item = list_view.highlighted_child
        return item if isinstance(item, _SnippetItem) else None


    def _insert(self, snippet: CommentSnippet) -> None:
        self._persist(
            self._library.record_use(snippet.text, self._question_id or None)
        )
        self.dismiss(snippet.text)

    def _insert_by_position(self, position: int) -> bool:
        ordered = self._ordered_snippets()
        if 0 <= position < len(ordered):
            self._insert(ordered[position])
            return True
        return False


    def on_key(self, event) -> None:
        if isinstance(self.focused, Input):
            return
        if len(event.key) == 1 and event.key.isdigit() and event.key != "0":
            if self._insert_by_position(int(event.key) - 1):
                event.stop()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, _SnippetItem):
            event.stop()
            self._insert(event.item.snippet)

    @on(Input.Submitted, "#comment-library-new")
    def _on_new_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._add_snippet(event.value)
        event.input.value = ""

    @on(Button.Pressed, "#comment-library-save")
    def _on_save_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        new_input = self.query_one("#comment-library-new", Input)
        self._add_snippet(new_input.value)
        new_input.value = ""
        new_input.focus()

    @on(Button.Pressed, "#comment-library-save-current")
    def _on_save_current_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._add_snippet(self._current_comment)

    @on(Button.Pressed, "#comment-library-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)
