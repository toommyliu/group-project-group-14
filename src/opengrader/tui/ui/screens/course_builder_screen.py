from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

from opengrader.core.course import (
    COURSE_FILENAME,
    CourseAssignment,
    DiscoveredAssignment,
    discover_course_assignments,
    find_solutions_near,
    humanize_assignment_name,
    weight_shares,
    write_course_toml,
)
from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen
from opengrader.tui.ui import button_roles
from opengrader.tui.ui.button_roles import OutlineButtons

DEFAULT_WEIGHT = "1"


class _AssignmentRow(OutlineButtons, Vertical):
    def __init__(self, assignment: DiscoveredAssignment | None = None) -> None:
        super().__init__(classes="assignment-row")
        self.name_input = Input(
            value=assignment.name if assignment else "",
            placeholder="Assignment name, e.g. Quiz 1",
            classes="assignment-name",
        )
        self.weight_input = Input(
            value=DEFAULT_WEIGHT,
            placeholder="1",
            classes="assignment-weight",
        )
        self.share_label = Label("", classes="assignment-share")
        self.remove_button = Button(
            "✕", classes="assignment-remove", variant="error", compact=True
        )
        self.submissions_input = Input(
            value=str(assignment.path) if assignment else "",
            placeholder="Submissions folder (.submission.md files)…",
            classes="assignment-path",
        )
        self.submissions_browse = Button(
            "Browse", classes=f"assignment-browse-subs {button_roles.OPEN}", compact=True
        )
        self.solutions_input = Input(
            value=str(assignment.solutions)
            if assignment and assignment.solutions
            else "",
            placeholder="Answer key (optional — adds learning outcomes)…",
            classes="assignment-path",
        )
        self.solutions_browse = Button(
            "Browse", classes=f"assignment-browse-key {button_roles.OPEN}", compact=True
        )

    def compose(self) -> ComposeResult:
        with Horizontal(classes="assignment-head"):
            yield self.name_input
            yield self.weight_input
            yield self.share_label
            yield self.remove_button
        with Horizontal(classes="assignment-paths"):
            yield self.submissions_input
            yield self.submissions_browse
        with Horizontal(classes="assignment-paths"):
            yield self.solutions_input
            yield self.solutions_browse

    @property
    def weight(self) -> float:
        try:
            value = float(self.weight_input.value.strip())
        except ValueError:
            return 0.0
        return max(0.0, value)

    def set_share(self, share: float) -> None:
        self.share_label.update(f"→ {share:.0%}" if share > 0 else "→ —")

    def to_assignment(self) -> CourseAssignment | None:
        name = self.name_input.value.strip()
        submissions = self.submissions_input.value.strip().strip("'\"")
        if not name or not submissions:
            return None
        solutions = self.solutions_input.value.strip().strip("'\"")
        return CourseAssignment(
            name=name,
            path=Path(submissions).expanduser(),
            weight=self.weight or 1.0,
            solutions=Path(solutions).expanduser() if solutions else None,
        )


class CourseBuilderScreen(OutlineButtons, ModalScreen[Path | None]):
    DEFAULT_CSS = """
    CourseBuilderScreen {
        align: center middle;
    }

    #course-builder-dialog {
        width: 90%;
        max-width: 124;
        height: 90%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #course-builder-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    CourseBuilderScreen .field-label {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    CourseBuilderScreen .path-row {
        height: auto;
    }

    CourseBuilderScreen .path-row Input {
        width: 1fr;
    }

    CourseBuilderScreen .path-row Button {
        min-width: 10;
        width: auto;
        margin-left: 1;
    }

    #course-builder-rows {
        height: 1fr;
        margin-top: 1;
        scrollbar-size: 1 1;
    }
    CourseBuilderScreen .assignment-row {
        height: auto;
        border: round $surface-lighten-2;
        padding: 0 1;
        margin-bottom: 1;
    }

    CourseBuilderScreen .assignment-head,
    CourseBuilderScreen .assignment-paths {
        height: auto;
    }

    CourseBuilderScreen .assignment-name {
        width: 1fr;
    }

    CourseBuilderScreen .assignment-weight {
        width: 12;
        margin-left: 1;
    }

    CourseBuilderScreen .assignment-share {
        width: 10;
        height: 3;
        content-align: left middle;
        color: $accent;
        text-style: bold;
        margin-left: 1;
    }

    CourseBuilderScreen .assignment-remove {
        min-width: 5;
        width: auto;
        margin-left: 1;
    }

    CourseBuilderScreen .assignment-path {
        width: 1fr;
    }

    CourseBuilderScreen .assignment-browse-subs,
    CourseBuilderScreen .assignment-browse-key {
        min-width: 10;
        width: auto;
        margin-left: 1;
    }

    #course-builder-empty {
        color: $text-muted;
        padding: 1 2;
    }

    #course-builder-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #course-builder-buttons Button {
        margin-left: 1;
    }

    #course-builder-status {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Close")]

    def __init__(self, *, start_dir: str = "") -> None:
        super().__init__()
        self._start_dir = start_dir

    @staticmethod
    def _field_label(text: str, detail: str = "") -> Static:
        markup = f"[b]{text}[/b]"
        if detail:
            markup = f"{markup}  [dim]{detail}[/dim]"
        return Static(markup, classes="field-label")

    def compose(self) -> ComposeResult:
        with Vertical(id="course-builder-dialog") as dialog:
            dialog.border_title = "▸ New course file"
            yield Static(
                "List the assignments that make up this course and how much "
                "each one counts. [b]Weight[/b] is relative — 1/1/2 and 25/25/50 "
                "describe the same course — so just watch the [b]%[/b] beside "
                "each row until it says what you mean.",
                id="course-builder-hint",
            )
            yield self._field_label("Course name")
            yield Input(
                placeholder="e.g. CS 146 — Spring 2026", id="course-builder-name"
            )
            yield self._field_label(
                "Save the course file to", f"a folder, or a path ending in {COURSE_FILENAME}"
            )
            with Horizontal(classes="path-row"):
                yield Input(
                    value=self._start_dir,
                    placeholder=f"Where to write {COURSE_FILENAME}…",
                    id="course-builder-output",
                )
                yield Button("Browse", id="course-builder-output-browse", classes=button_roles.OPEN,  compact=True)
            yield self._field_label(
                "Assignments", "name · weight · share of the final grade"
            )
            with VerticalScroll(id="course-builder-rows"):
                yield Label(
                    "No assignments yet — Scan a folder finds them for you, or "
                    "Add assignment starts a blank row.",
                    id="course-builder-empty",
                )
            yield Static("", id="course-builder-status")
            with Horizontal(id="course-builder-buttons"):
                yield Button("Scan a folder…", id="course-builder-scan", classes=button_roles.OPEN,  compact=True)
                yield Button("Add assignment", id="course-builder-add", compact=True)
                yield Button(
                    "Save course file",
                    variant="primary",
                    id="course-builder-save",
                    compact=True,
                )
                yield Button("Cancel", id="course-builder-cancel", compact=True)

    def on_mount(self) -> None:
        self.query_one("#course-builder-name", Input).focus()


    def _rows(self) -> list[_AssignmentRow]:
        return list(self.query(_AssignmentRow))

    def _add_rows(self, assignments: list[DiscoveredAssignment | None]) -> None:
        container = self.query_one("#course-builder-rows", VerticalScroll)
        empty = self.query("#course-builder-empty")
        if empty:
            empty.remove()
        container.mount(*[_AssignmentRow(a) for a in assignments])
        self.call_after_refresh(self._refresh_shares)

    def _refresh_shares(self) -> None:
        rows = self._rows()
        for row, share in zip(rows, weight_shares([row.weight for row in rows])):
            row.set_share(share)
        self._refresh_status(rows)

    def _refresh_status(self, rows: list[_AssignmentRow]) -> None:
        status = self.query_one("#course-builder-status", Static)
        ready = [row for row in rows if row.to_assignment() is not None]
        if not rows:
            status.update("")
            return
        incomplete = len(rows) - len(ready)
        note = (
            f"  {incomplete} row(s) still need a name and a submissions folder."
            if incomplete
            else ""
        )
        status.update(f"{len(ready)} assignment(s) ready.{note}")

    @on(Input.Changed, ".assignment-weight")
    def _on_weight_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._refresh_shares()

    @on(Input.Changed, ".assignment-name")
    @on(Input.Changed, ".assignment-path")
    def _on_row_field_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._refresh_status(self._rows())

    @on(Button.Pressed, ".assignment-remove")
    async def _on_remove_row(self, event: Button.Pressed) -> None:
        event.stop()
        for row in self._rows():
            if event.button in row.walk_children(Button):
                await row.remove()
                break
        self._refresh_shares()

    @on(Button.Pressed, "#course-builder-add")
    def _on_add_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._add_rows([None])


    @on(Button.Pressed, "#course-builder-scan")
    def _on_scan_pressed(self, event: Button.Pressed) -> None:
        event.stop()

        def _apply(path: Path | None) -> None:
            if path is None:
                return
            found = discover_course_assignments(path)
            if not found:
                self.app.notify(
                    "No assignment folders found there — pick the folder that "
                    "holds your assignments.",
                    severity="warning",
                )
                return
            self._add_rows(found)
            output = self.query_one("#course-builder-output", Input)
            if not output.value.strip():
                output.value = str(path)
            self.app.notify(f"Found {len(found)} assignment(s).")

        self.app.push_screen(
            PathPickerScreen(
                title="Choose the folder holding your assignments",
                mode="directory",
                start_path=self._start_path(),
            ),
            _apply,
        )

    def _start_path(self) -> Path | None:
        raw = self.query_one("#course-builder-output", Input).value.strip().strip("'\"")
        if not raw:
            return None
        candidate = Path(raw).expanduser()
        if candidate.is_dir():
            return candidate
        return candidate.parent if candidate.parent.is_dir() else None

    @on(Button.Pressed, "#course-builder-output-browse")
    def _on_output_browse(self, event: Button.Pressed) -> None:
        event.stop()
        target = self.query_one("#course-builder-output", Input)

        def _apply(path: Path | None) -> None:
            if path is None:
                return
            target.value = str(path)
            target.cursor_position = len(target.value)

        self.app.push_screen(
            PathPickerScreen(
                title=f"Where should {COURSE_FILENAME} go?",
                mode="directory",
                start_path=self._start_path(),
            ),
            _apply,
        )

    @on(Button.Pressed, ".assignment-browse-subs")
    def _on_browse_submissions(self, event: Button.Pressed) -> None:
        event.stop()
        self._browse_row(event.button, want_solutions=False)

    @on(Button.Pressed, ".assignment-browse-key")
    def _on_browse_solutions(self, event: Button.Pressed) -> None:
        event.stop()
        self._browse_row(event.button, want_solutions=True)

    def _browse_row(self, button: Button, *, want_solutions: bool) -> None:
        row = next(
            (r for r in self._rows() if button in r.walk_children(Button)), None
        )
        if row is None:
            return
        target = row.solutions_input if want_solutions else row.submissions_input

        def _apply(path: Path | None) -> None:
            if path is None:
                return
            target.value = str(path)
            target.cursor_position = len(target.value)
            if not want_solutions:
                if not row.name_input.value.strip():
                    row.name_input.value = humanize_assignment_name(path)
                if not row.solutions_input.value.strip():
                    guess = find_solutions_near(path)
                    if guess is not None:
                        row.solutions_input.value = str(guess)
            self._refresh_status(self._rows())

        self.app.push_screen(
            PathPickerScreen(
                title="Choose the answer key" if want_solutions else "Choose the submissions folder",
                mode="any" if want_solutions else "directory",
                start_path=self._start_path(),
            ),
            _apply,
        )


    @on(Button.Pressed, "#course-builder-save")
    def _on_save_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        output = self.query_one("#course-builder-output", Input).value.strip().strip("'\"")
        if not output:
            self.app.notify(
                f"Choose where to save {COURSE_FILENAME}.", severity="error"
            )
            return

        assignments = [
            assignment
            for assignment in (row.to_assignment() for row in self._rows())
            if assignment is not None
        ]
        if not assignments:
            self.app.notify(
                "Add at least one assignment with a name and a submissions "
                "folder.",
                severity="error",
            )
            return

        name = self.query_one("#course-builder-name", Input).value.strip()
        try:
            written = write_course_toml(
                output, name=name or "Course", assignments=assignments
            )
        except OSError as exc:
            self.app.notify(f"Could not write the course file: {exc}", severity="error")
            return
        self.app.notify(f"Wrote {written}")
        self.dismiss(written)

    @on(Button.Pressed, "#course-builder-cancel")
    def _on_cancel_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
