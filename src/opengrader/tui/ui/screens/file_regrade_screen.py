from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static

_MANUAL_STUDENT = "\x00manual-student"

_WHOLE_ASSIGNMENT = ""


@dataclass(frozen=True)
class FileRegradeResult:
    student_id: str
    question_id: str
    reason: str


class FileRegradeScreen(ModalScreen["FileRegradeResult | None"]):
    DEFAULT_CSS = """
    FileRegradeScreen {
        align: center middle;
    }

    #file-regrade-dialog {
        width: 72%;
        max-width: 88;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #file-regrade-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    FileRegradeScreen .field-label {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    #file-regrade-manual-row {
        height: auto;
        margin-top: 1;
    }

    #file-regrade-manual-row.hidden {
        display: none;
    }

    FileRegradeScreen Select,
    FileRegradeScreen Input {
        width: 100%;
    }

    #file-regrade-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #file-regrade-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        *,
        student_choices: list[tuple[str, str]],
        question_choices: list[tuple[str, str]],
        initial_student_id: str = "",
        initial_question_id: str | None = None,
    ) -> None:
        super().__init__()
        self._student_choices = student_choices
        self._question_choices = question_choices
        self._initial_student_id = initial_student_id.strip()
        self._initial_question_id = initial_question_id

    @staticmethod
    def _field_label(text: str, detail: str = "") -> Static:
        markup = f"[b]{text}[/b]"
        if detail:
            markup = f"{markup}  [dim]{detail}[/dim]"
        return Static(markup, classes="field-label")


    def _student_select_options(self) -> list[tuple[str, str]]:
        options = [(label, sid) for sid, label in self._student_choices]
        options.append(("✎ Enter a student ID manually…", _MANUAL_STUDENT))
        return options

    def _initial_student_value(self) -> str:
        known = {sid for sid, _label in self._student_choices}
        if self._initial_student_id and self._initial_student_id in known:
            return self._initial_student_id
        if self._initial_student_id:
            return _MANUAL_STUDENT
        if self._student_choices:
            return self._student_choices[0][0]
        return _MANUAL_STUDENT

    def _question_select_options(self) -> list[tuple[str, str]]:
        options = [("Whole assignment (no specific question)", _WHOLE_ASSIGNMENT)]
        options.extend((label, qid) for qid, label in self._question_choices)
        return options

    def _initial_question_value(self) -> str:
        if self._initial_question_id is None:
            return _WHOLE_ASSIGNMENT
        known = {qid for qid, _label in self._question_choices}
        if self._initial_question_id in known:
            return self._initial_question_id
        return _WHOLE_ASSIGNMENT


    def compose(self) -> ComposeResult:
        with Vertical(id="file-regrade-dialog") as dialog:
            dialog.border_title = "▸ File a regrade request"
            yield Static(
                "Log a student's grade dispute. It never changes a grade — it "
                "surfaces in the review queue and the Regrades (Shift+G) modal, "
                "routing a grader to the question.",
                id="file-regrade-hint",
            )

            initial_student = self._initial_student_value()
            yield self._field_label("Student", "whose grade is disputed")
            yield Select(
                self._student_select_options(),
                value=initial_student,
                allow_blank=False,
                id="file-regrade-student",
            )
            manual_hidden = "" if initial_student == _MANUAL_STUDENT else "hidden"
            with Horizontal(id="file-regrade-manual-row", classes=manual_hidden):
                yield Input(
                    value=self._initial_student_id
                    if initial_student == _MANUAL_STUDENT
                    else "",
                    placeholder="Type the student ID…",
                    id="file-regrade-manual-id",
                )

            yield self._field_label("Question", "or the whole assignment")
            yield Select(
                self._question_select_options(),
                value=self._initial_question_value(),
                allow_blank=False,
                id="file-regrade-question",
            )

            yield self._field_label("Reason", "the student's stated argument")
            yield Input(
                placeholder="e.g. Rubric says partial credit for a valid method.",
                id="file-regrade-reason",
            )

            with Horizontal(id="file-regrade-buttons"):
                yield Button(
                    "File request",
                    variant="primary",
                    id="file-regrade-submit",
                    compact=True,
                )
                yield Button("Cancel", id="file-regrade-cancel", compact=True)

    def on_mount(self) -> None:
        if self._initial_student_id:
            self.query_one("#file-regrade-reason", Input).focus()
        else:
            self.query_one("#file-regrade-student", Select).focus()


    @on(Select.Changed, "#file-regrade-student")
    def _on_student_changed(self, event: Select.Changed) -> None:
        event.stop()
        manual_row = self.query_one("#file-regrade-manual-row", Horizontal)
        if event.value == _MANUAL_STUDENT:
            manual_row.remove_class("hidden")
            self.query_one("#file-regrade-manual-id", Input).focus()
        else:
            manual_row.add_class("hidden")

    def _resolve_student_id(self) -> str:
        value = self.query_one("#file-regrade-student", Select).value
        if value == _MANUAL_STUDENT:
            return self.query_one("#file-regrade-manual-id", Input).value.strip()
        return str(value).strip() if value is not None else ""

    @on(Button.Pressed, "#file-regrade-submit")
    def _on_submit(self, event: Button.Pressed) -> None:
        event.stop()
        student_id = self._resolve_student_id()
        if not student_id:
            self.app.notify("Choose or enter a student ID.", severity="error")
            return

        reason = self.query_one("#file-regrade-reason", Input).value.strip()
        if not reason:
            self.app.notify("Enter a reason for the dispute.", severity="error")
            return

        question_value = self.query_one("#file-regrade-question", Select).value
        question_id = "" if question_value == _WHOLE_ASSIGNMENT else str(question_value)

        self.dismiss(
            FileRegradeResult(
                student_id=student_id,
                question_id=question_id,
                reason=reason,
            )
        )

    @on(Button.Pressed, "#file-regrade-cancel")
    def _on_cancel(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
