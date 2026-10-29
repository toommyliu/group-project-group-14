"""Write a workspace assignment's grades back into its class folder.

The UI half of `core/workspace/writeback.py`, which is `grade-merge` pointed
at a workspace. It opens on a preview; nothing is written until *Write to
files*. The layout and conflict rows are the grader-copy merge screen's
(`MergeScreen`), relabelled: "ours" is the files and "theirs" is the
workspace.

A plain `ModalScreen`, so it works inside `WorkspaceHome` (its own `App`) as
well as inside the grading app.
"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from opengrader.core.workspace.writeback import (
    WriteBackError,
    WriteBackPlan,
    apply_write_back,
    plan_write_back,
)
from opengrader.tui.ui.screens.handoff_screen import ConflictRow

__all__ = ["WriteBackScreen"]


class _WriteBackConflictRow(ConflictRow):
    def _refresh_label(self) -> None:
        marker = "→ workspace" if self.decision == "theirs" else "  keep file"
        self.update(f"  [{marker:<11}] {self.conflict.describe()}")
        self.set_class(self.decision == "theirs", "takes-theirs")


class WriteBackScreen(ModalScreen[bool]):
    """Dismisses with True when anything was written."""

    DEFAULT_CSS = """
    WriteBackScreen {
        align: center middle;
    }

    #wb-dialog {
        width: 88%;
        max-width: 130;
        height: 86%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #wb-hint, #wb-summary {
        width: 100%;
        height: auto;
    }

    #wb-hint {
        color: $text-muted;
    }

    #wb-summary {
        margin-top: 1;
    }

    #wb-body {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        padding: 0 1;
    }

    WriteBackScreen .merge-section {
        width: 100%;
        height: auto;
        color: $text-muted;
        text-style: bold;
        margin-top: 1;
    }

    WriteBackScreen .merge-change-row, WriteBackScreen .merge-note {
        width: 100%;
        height: auto;
    }

    WriteBackScreen .merge-note {
        color: $text-muted;
    }

    WriteBackScreen .merge-conflict-row {
        width: 100%;
        height: auto;
        color: $warning;
    }

    WriteBackScreen .merge-conflict-row.takes-theirs {
        color: $accent;
    }

    WriteBackScreen .merge-conflict-row:focus {
        background: $accent 20%;
        text-style: bold;
    }

    #wb-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #wb-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, *, workspace_root: str | Path, assignment_id: str, title: str = "") -> None:
        super().__init__()
        self._root = Path(workspace_root)
        self._assignment_id = assignment_id
        self._title = title
        self._plan: WriteBackPlan | None = None
        self._written = False

    def compose(self) -> ComposeResult:
        with Vertical(id="wb-dialog") as dialog:
            dialog.border_title = f"▸ Write grades to files{': ' + self._title if self._title else ''}"
            yield Static(
                "Copies grades and comments from this workspace into the "
                "assignment's .submission.md files, so exports, backups and "
                "grade history see them. A grade changed only in the workspace "
                "is copied; one changed only in the files is kept; one changed "
                "in both is listed for you to decide. Nothing is written until "
                "you press Write to files.",
                id="wb-hint",
            )
            yield Static("Comparing…", id="wb-summary")
            body = VerticalScroll(id="wb-body")
            body.border_title = "Plan"
            yield body
            with Horizontal(id="wb-buttons"):
                yield Button("Keep files for all", id="wb-all-files", compact=True)
                yield Button("Take workspace for all", id="wb-all-workspace", compact=True)
                yield Button(
                    "Write to files", id="wb-apply", variant="success", compact=True, disabled=True
                )
                yield Button("Close", id="wb-close", compact=True)

    def on_mount(self) -> None:
        self.run_worker(self._compare, thread=True, exclusive=True)

    # --- preview -------------------------------------------------------------

    def _compare(self) -> None:
        try:
            plan = plan_write_back(self._root, self._assignment_id)
        except (WriteBackError, OSError, ValueError) as exc:
            self.app.call_from_thread(self._show_error, str(exc))
            return
        self.app.call_from_thread(self._show_plan, plan)

    def _show_error(self, message: str) -> None:
        if self.is_attached:
            self.query_one("#wb-summary", Static).update(f"[b]Cannot write back:[/b] {message}")

    def _show_plan(self, write_back: WriteBackPlan) -> None:
        if not self.is_attached:
            return
        self._plan = write_back
        plan = write_back.plan
        self.query_one("#wb-summary", Static).update(
            " · ".join(
                [
                    f"[b]{len(plan.changes)}[/b] to copy",
                    f"[b]{len(plan.conflicts)}[/b] to decide",
                    f"{plan.kept_ours} kept from the files",
                    f"{plan.already_agree} already agree",
                ]
            )
            + f"\nInto {write_back.submissions_dir}"
        )
        self.query_one("#wb-apply", Button).disabled = write_back.nothing_to_do
        body = self.query_one("#wb-body", VerticalScroll)
        body.remove_children()
        if write_back.nothing_to_do:
            body.mount(Label("The files already have every workspace grade. Nothing to write.", classes="merge-note"))
        if plan.changes:
            body.mount(Static(f"COPYING FROM THE WORKSPACE ({len(plan.changes)})", classes="merge-section"))
            for change in plan.changes:
                body.mount(Static(f"  {change.describe()}", classes="merge-change-row", markup=False))
        if plan.conflicts:
            body.mount(
                Static(
                    f"CHANGED IN BOTH ({len(plan.conflicts)}): ours = the file, theirs = "
                    "the workspace · o keeps the file, t takes the workspace",
                    classes="merge-section",
                )
            )
            for conflict in plan.conflicts:
                body.mount(_WriteBackConflictRow(conflict))
        for note in self._notes(write_back):
            body.mount(Label(note, classes="merge-note"))
        rows = list(self.query(_WriteBackConflictRow))
        if rows:
            rows[0].focus()

    @staticmethod
    def _notes(write_back: WriteBackPlan) -> list[str]:
        notes = []
        if write_back.missing_files:
            notes.append(
                f"{len(write_back.missing_files)} submission file(s) are no longer in the "
                "folder and were skipped."
            )
        if write_back.unreadable:
            notes.append(f"{len(write_back.unreadable)} file(s) could not be read and were skipped.")
        if write_back.plan.unknown_questions:
            notes.append(
                f"{len(write_back.plan.unknown_questions)} answer(s) have no matching "
                "question in the files and were skipped."
            )
        if write_back.base_from == "last write-back":
            notes.append("Compared against the last write-back, so earlier decisions are not asked again.")
        return notes

    # --- conflict decisions --------------------------------------------------

    def focus_relative_conflict(self, current: ConflictRow, delta: int) -> None:
        rows = list(self.query(_WriteBackConflictRow))
        if current in rows:
            target = rows.index(current) + delta
            if 0 <= target < len(rows):
                rows[target].focus()
                rows[target].scroll_visible()

    @on(Button.Pressed, "#wb-all-files")
    def _on_all_files(self, event: Button.Pressed) -> None:
        event.stop()
        for row in self.query(_WriteBackConflictRow):
            row.set_decision("ours")

    @on(Button.Pressed, "#wb-all-workspace")
    def _on_all_workspace(self, event: Button.Pressed) -> None:
        event.stop()
        for row in self.query(_WriteBackConflictRow):
            row.set_decision("theirs")

    # --- apply -----------------------------------------------------------------

    @on(Button.Pressed, "#wb-apply")
    def _on_apply(self, event: Button.Pressed) -> None:
        event.stop()
        if self._plan is None:
            return
        decisions = {row.conflict.conflict_id: row.decision for row in self.query(_WriteBackConflictRow)}
        self.query_one("#wb-apply", Button).disabled = True
        plan = self._plan
        self.run_worker(lambda: self._apply(plan, decisions), thread=True, exclusive=True)

    def _apply(self, plan: WriteBackPlan, decisions: dict[str, str]) -> None:
        try:
            outcome = apply_write_back(self._root, plan, decisions=decisions)
        except OSError as exc:
            self.app.call_from_thread(self._show_error, f"could not write: {exc}")
            return
        lines = [
            f"Wrote {len(outcome.written)} change(s) into {outcome.files_touched} file(s).",
        ]
        if outcome.failed:
            lines.append(f"{len(outcome.failed)} change(s) could not be written and will be offered again:")
            lines += [f"  {change.describe()}: {error}" for change, error in outcome.failed]
        lines += [
            "",
            "Undo with:  opengrader grade-restore apply --submissions "
            f"{plan.submissions_dir} --without-actor merge:workspace --yes",
        ]
        self.app.call_from_thread(self._show_applied, "\n".join(lines), bool(outcome.written))

    def _show_applied(self, text: str, wrote: bool) -> None:
        self._written = self._written or wrote
        if not self.is_attached:
            return
        self.query_one("#wb-summary", Static).update("Done.")
        body = self.query_one("#wb-body", VerticalScroll)
        body.remove_children()
        body.mount(Static(text, classes="merge-note", markup=False))
        self.query_one("#wb-close", Button).focus()

    @on(Button.Pressed, "#wb-close")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_close()

    def action_close(self) -> None:
        self.dismiss(self._written)
