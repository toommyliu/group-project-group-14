"""Home: the first screen of `tui`, where you choose what to grade.

One path box instead of an answer-key field and a submissions field. The
folder is classified by `core/open_target.detect_target` as you type. **Open**
(green, and always labelled Open, so the main action never changes its name
under you) does whatever that folder is for, and a line under the box says
which:

* a class folder with an answer key and submissions is graded in place, with
  no service;
* a workspace starts the local workspace service and opens its assignment
  list;
* scans not processed yet open Tools with the pipeline card filled in from
  that folder.

**Open as workspace** is the one explicit way to make a class folder a
workspace: it imports the folder and starts the local service, which only the
web app needs. It is enabled only for a class folder that is not a workspace
yet. Grades made in a workspace go back into the files with *Write grades to
files* on its assignment list (`core/workspace/writeback.py`).

Recently opened folders and workspaces are listed above the box
(`core/recents`). A workspace appears there only after you have opened one:
the terminal app leads with folders, and a workspace is an addition.

Home is its own `App`, like `WorkspaceHome`. It returns a `HomeChoice` to the
launcher in `tui/main.py`, which runs the grading app and brings you back here
on F2. Nothing on this screen writes to an assignment folder.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

from opengrader.core.open_target import OpenTarget, detect_target
from opengrader.core.recents import RecentItem, forget_recent, load_recents

__all__ = ["HomeChoice", "Home"]


@dataclass(frozen=True)
class HomeChoice:
    # "new_workspace": make this class folder a workspace (import it), then
    # open it. Only ever chosen explicitly, with "Open as workspace".
    kind: Literal["folder", "workspace", "new_workspace", "tools"]
    folder: Path | None = None
    qa_file: Path | None = None
    submissions: Path | None = None
    # Tools opened on a folder of unprocessed scans: the pipeline card starts
    # filled in from it.
    pipeline_folder: Path | None = None


class _RecentRow(ListItem):
    def __init__(self, item: RecentItem) -> None:
        self.item = item
        present = item.exists()
        badge = "workspace" if item.kind == "workspace" else "folder"
        where = item.submissions if item.kind == "folder" and item.submissions else item.path
        text = f"{item.title}  ·  {badge}  ·  {_short(where)}"
        if not present:
            text += "  (missing)"
        super().__init__(Label(text, markup=False), classes="" if present else "missing")


class Home(App[HomeChoice | None]):
    TITLE = "Opengrader"
    SUB_TITLE = "Home"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("f3", "tools", "Tools & settings"),
        Binding("x", "forget", "Forget recent", show=False),
    ]
    DEFAULT_CSS = """
    #home {
        padding: 1 2;
    }

    .home-heading {
        height: 1;
        text-style: bold;
        margin-top: 1;
    }

    .home-hint, #home-detect, #home-message {
        height: auto;
        color: $text-muted;
    }

    #home-message {
        color: $error;
        margin-top: 1;
    }

    #home-recents {
        height: auto;
        max-height: 12;
        margin: 1 0;
        border: round $surface-lighten-2;
        background: transparent;
    }

    #home-recents .missing {
        color: $text-disabled;
    }

    .home-row {
        height: auto;
        margin-top: 1;
    }

    .home-row Input {
        width: 1fr;
    }

    .home-row Button {
        margin-left: 1;
    }

    #home-detect {
        margin-top: 1;
    }

    #home-actions {
        height: auto;
        margin-top: 2;
    }

    #home-actions Button {
        margin-right: 1;
    }
    """

    def __init__(self, *, start_path: str = "", message: str = "") -> None:
        super().__init__()
        self._start_path = start_path
        self._message = message
        self._target: OpenTarget | None = None
        self._recents: list[RecentItem] = []

    # --- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="home"):
            yield Label("OPEN FOR GRADING", classes="home-heading")
            yield Static(
                "A class folder holding an answer key and extracted "
                "submissions is graded right where it is. A folder of new "
                "scans opens in Tools, where the pipeline turns it into "
                "submissions. A workspace opens with its assignment list.",
                classes="home-hint",
            )
            yield Label("Recent", id="home-recent-title", classes="home-heading")
            yield ListView(id="home-recents")
            yield Static(
                "Nothing opened yet. Folders you open appear here.",
                id="home-no-recents",
                classes="home-hint",
            )
            with Horizontal(classes="home-row"):
                yield Input(
                    value=self._start_path,
                    placeholder="Path to a class folder, a submissions folder or a workspace…",
                    id="home-path",
                )
                yield Button("Browse", id="home-browse", compact=True)
                # One label, always: what Open does is stated underneath.
                yield Button("Open", id="home-open", variant="success", compact=True)
                yield Button(
                    "Open as workspace",
                    id="home-workspace",
                    compact=True,
                    tooltip=(
                        "Only needed to grade alongside the web app: imports this "
                        "folder into a workspace and starts a local service."
                    ),
                )
            yield Static("", id="home-detect", markup=False)
            yield Static("", id="home-actions-hint", classes="home-hint", markup=False)
            with Horizontal(classes="home-row", id="home-qa-row"):
                yield Input(
                    placeholder="Answer key (QA markdown), only if the folder's own is missing or ambiguous…",
                    id="home-qa",
                )
                yield Button("Browse", id="home-browse-qa", compact=True)
            yield Static(self._message, id="home-message", markup=False)
            with Horizontal(id="home-actions"):
                yield Button("Tools & settings (F3)", id="home-tools")
                yield Button("Quit (Q)", id="home-quit")
        yield Footer()

    def on_mount(self) -> None:
        self._load_recents()
        self._detect()
        self.query_one("#home-message").display = bool(self._message)
        if self._recents:
            self.query_one("#home-recents", ListView).focus()
        else:
            self.query_one("#home-path", Input).focus()

    # --- recents -----------------------------------------------------------

    def _load_recents(self) -> None:
        self._recents = load_recents()
        listing = self.query_one("#home-recents", ListView)
        listing.clear()
        listing.extend(_RecentRow(item) for item in self._recents)
        has_any = bool(self._recents)
        listing.display = has_any
        self.query_one("#home-no-recents").display = not has_any
        if has_any:
            listing.index = 0

    @on(ListView.Selected, "#home-recents")
    def _on_recent(self, event: ListView.Selected) -> None:
        event.stop()
        row = event.item
        if not isinstance(row, _RecentRow):
            return
        item = row.item
        if not item.exists():
            self._say(f"{item.path} is no longer there. Press X to forget it.")
            return
        if item.kind == "workspace":
            self.exit(HomeChoice("workspace", folder=Path(item.path)))
            return
        self.exit(
            HomeChoice(
                "folder",
                folder=Path(item.path),
                qa_file=Path(item.qa_file) if item.qa_file else None,
                submissions=Path(item.submissions) if item.submissions else Path(item.path),
            )
        )

    def action_forget(self) -> None:
        listing = self.query_one("#home-recents", ListView)
        if not listing.has_focus or listing.index is None:
            return
        row = listing.highlighted_child
        if isinstance(row, _RecentRow):
            forget_recent(row.item)
            self._load_recents()

    # --- the path box ------------------------------------------------------

    @on(Input.Changed, "#home-path")
    @on(Input.Changed, "#home-qa")
    def _on_path_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._detect()

    @on(Input.Submitted, "#home-path")
    @on(Input.Submitted, "#home-qa")
    def _on_path_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._open()

    def _detect(self) -> None:
        path = self.query_one("#home-path", Input).value
        qa = self.query_one("#home-qa", Input).value
        self._target = detect_target(path, qa_file=qa) if path.strip() else None
        detect = self.query_one("#home-detect", Static)
        hint = self.query_one("#home-actions-hint", Static)
        qa_row = self.query_one("#home-qa-row")
        opener = self.query_one("#home-open", Button)
        as_workspace = self.query_one("#home-workspace", Button)
        target = self._target
        if target is None:
            detect.update("")
            hint.update("")
            qa_row.display = bool(qa.strip())
            opener.disabled = as_workspace.disabled = True
            return
        detect.update("\n".join(target.lines))
        # The override only shows when it can help, or once someone typed one.
        qa_row.display = target.needs_answer_key or bool(qa.strip())
        opener.disabled = not target.openable
        as_workspace.disabled = not (target.kind == "folder" and target.openable)
        hint.update(_actions_hint(target))

    def _open_as_workspace(self) -> None:
        target = self._target
        if target is None or target.kind != "folder" or not target.openable:
            return
        self.exit(
            HomeChoice(
                "new_workspace",
                folder=target.folder,
                qa_file=target.qa_file,
                submissions=target.submissions,
            )
        )

    def _open(self) -> None:
        target = self._target
        if target is None or not target.openable:
            if target is not None:
                self._say(target.lines[0])
            return
        if target.kind == "workspace":
            self.exit(HomeChoice("workspace", folder=target.folder))
        elif target.kind == "scans":
            self.exit(HomeChoice("tools", pipeline_folder=target.folder))
        else:
            self.exit(
                HomeChoice(
                    "folder",
                    folder=target.folder,
                    qa_file=target.qa_file,
                    submissions=target.submissions,
                )
            )

    def _say(self, message: str) -> None:
        widget = self.query_one("#home-message", Static)
        widget.update(message)
        widget.display = bool(message)

    # --- buttons -----------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button.id
        if button == "home-open":
            self._open()
        elif button == "home-workspace":
            self._open_as_workspace()
        elif button == "home-tools":
            self.action_tools()
        elif button == "home-quit":
            self.exit(None)
        elif button == "home-browse":
            self._browse("home-path", "Choose a class folder or workspace", "any")
        elif button == "home-browse-qa":
            self._browse("home-qa", "Choose the answer key (QA markdown)", "file")

    def _browse(self, input_id: str, title: str, mode: str) -> None:
        from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen

        field = self.query_one(f"#{input_id}", Input)
        current = Path(field.value.strip()).expanduser() if field.value.strip() else None
        start = None
        if current is not None:
            start = current if current.is_dir() else current.parent

        def apply(chosen: Path | None) -> None:
            if chosen is not None:
                field.value = str(chosen)

        self.push_screen(PathPickerScreen(title=title, mode=mode, start_path=start), apply)

    def action_tools(self) -> None:
        self.exit(HomeChoice("tools"))


def _actions_hint(target: OpenTarget) -> str:
    if not target.openable:
        return ""
    if target.kind == "folder":
        return (
            "Open grades these files right here: each grade is saved straight "
            "into the submission files. Open as workspace is only for grading "
            "alongside the web app: it copies the folder into a database and "
            "starts a local service."
        )
    if target.kind == "scans":
        return "Open takes these scans to Tools, where the pipeline turns them into submissions."
    return (
        "Open: Shows this workspace's assignment list. Grades made here are "
        "saved in the workspace's database, not in the submission files; to "
        "copy them into the files, use Write grades to files on that list."
    )


def _short(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path
