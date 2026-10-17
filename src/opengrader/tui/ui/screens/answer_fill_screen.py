"""Add the answers to an answer key without opening the file.

Shown after a key is drafted from the blank template (every question, no
answers), and from the Start grading card's "Add answers…" for any key. Each
question gets a box: one accepted answer per line, or — for a multiple-choice
question — its options to tick the correct one (or ones) of. Save writes only the
questions that changed into the file (`core.qa_file.answers`).

"Open as text file" is the way out for anything a box cannot do: it opens the
key in the computer's plain-text editor, which needs no Markdown program.

"Suggest answers with AI" is there only when an AI can be asked right now
(`ai_suggest_ready`). It fills the questions that are still empty, each
marked as the AI's, and saves nothing: the teacher reads them and presses
Save. Only the questions' text is sent, never student work.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Static, TextArea

from opengrader.core import app_config
from opengrader.core.qa_file.answer_suggest import ANSWER_SUGGEST_SCHEMA, suggest_answers
from opengrader.core.qa_file.answers import AnswerField, answer_fields, apply_answers
from opengrader.tui.external_viewer import ExternalViewerError, open_in_text_editor
from opengrader.tui.ui import button_roles
from opengrader.tui.ui.button_roles import OutlineButtons


# Checkbox labels cannot wrap.
_OPTION_LABEL_WIDTH = 90


# A local server that is up answers its model list at once.
_LOCAL_CHECK_SECONDS = 3.0


def ai_suggest_ready() -> bool:
    """Can the configured AI be asked from here, with nothing to set up?

    A local AI: its server is answering and has the grading model. Opengrader
    never starts one; the teacher runs Ollama (`opengrader ai-readme --local`).
    A cloud provider: it is the one chosen and agreed to in AI setup — and it
    is not contacted to find out, because any request there carries its key.
    Called off the main thread (the local check talks to the server).
    """
    try:
        resolved = app_config.resolve()
        if resolved.kind == "cloud":
            return resolved.cloud_allowed and not resolved.cloud_consent_required
        app_config.ensure_ready(
            app_config.with_overrides(resolved, timeout_seconds=_LOCAL_CHECK_SECONDS),
            purpose="text",
        )
        return True
    except Exception:  # noqa: BLE001 - not ready is the answer, whatever went wrong
        return False


def ai_generate(resolved: "app_config.ResolvedAI") -> Callable[[str], str]:
    """The guarded call to the configured text model (worker thread).

    A local AI that is not running, or lacks the model, is reported by
    `ensure_ready`; nothing is started or downloaded from a form.
    """
    app_config.ensure_ready(resolved, purpose="text")
    return app_config.text_generate(resolved, response_format=ANSWER_SUGGEST_SCHEMA)


class AnswerFillScreen(OutlineButtons, ModalScreen[bool]):
    """Dismisses True when answers were saved, False otherwise."""

    BINDINGS = [Binding("escape", "cancel", "Not now")]

    DEFAULT_CSS = """
    AnswerFillScreen {
        align: center middle;
    }

    #answers-dialog {
        width: 88%;
        max-width: 104;
        height: 90%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #answers-intro, #answers-problem {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    #answers-problem {
        color: $error;
    }

    #answers-ai-status {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }

    .answers-ai-note {
        width: 100%;
        height: auto;
        color: $warning;
    }

    #answers-fields {
        height: 1fr;
    }

    .answers-question {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    .answers-note {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    AnswerFillScreen TextArea {
        height: 4;
    }

    AnswerFillScreen Checkbox {
        width: 100%;
        background: transparent;
        border: none;
        padding: 0 0 0 2;
    }

    AnswerFillScreen Checkbox:focus {
        background: $boost;
    }

    #answers-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #answers-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, qa_file: str | Path, *, note: str = "") -> None:
        """`note` is shown above the questions (what happens after saving)."""
        super().__init__()
        self._path = Path(qa_file)
        self._note = note
        self._fields: list[AnswerField] = []
        self._problem = ""
        self._read_at: float | None = None
        try:
            self._text = self._path.read_text(encoding="utf-8")
            self._read_at = os.stat(self._path).st_mtime
            self._fields = answer_fields(self._text)
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self._text = ""
            self._problem = (
                f"{self._path.name} could not be read as an answer key ({exc}). "
                "Open it as a text file to fix it."
            )

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="answers-dialog") as dialog:
            dialog.border_title = f"▸ Add the answers — {self._path.name}"
            intro = (
                "Type what you accept as correct, one answer per line (several "
                "lines = several accepted answers). For a multiple-choice "
                "question, tick the correct option — or every correct one, if "
                "there are several. Leave a question empty to grade it by hand."
            )
            yield Static(f"{intro} {self._note}".strip(), id="answers-intro", markup=False)
            yield Static(self._problem, id="answers-problem", markup=False)
            with VerticalScroll(id="answers-fields"):
                for index, field in enumerate(self._fields):
                    yield Static(
                        f"{field.label}. ({field.points} points) {field.prompt}",
                        classes="answers-question",
                        markup=False,
                    )
                    if field.choices:
                        for letter, text in field.choices:
                            # A checkbox label is one line: a long option is
                            # cut here, never in the file.
                            yield Checkbox(
                                f"{letter}) {text}"[:_OPTION_LABEL_WIDTH],
                                value=letter in field.answers,
                                id=f"answers-option-{index}-{letter}",
                            )
                    else:
                        yield TextArea("\n".join(field.answers), id=f"answers-text-{index}")
                    note = Static(
                        "↑ Suggested by the AI. Check it before you save.",
                        id=f"answers-ai-note-{index}",
                        classes="answers-ai-note",
                        markup=False,
                    )
                    note.display = False
                    yield note
            yield Static("", id="answers-ai-status", markup=False)
            with Horizontal(id="answers-buttons"):
                # Hidden until `ai_suggest_ready` says an AI can be asked.
                suggest = Button("Suggest answers with AI", id="answers-suggest")
                suggest.display = False
                yield suggest
                yield Button("Open as text file", id="answers-open-text", classes=button_roles.OPEN)
                yield Button("Not now", id="answers-cancel")
                yield Button(
                    "Save answers", id="answers-save", variant="primary",
                    disabled=not self._fields,
                )

    def on_mount(self) -> None:
        self.query_one("#answers-problem").display = bool(self._problem)
        first = self.query("TextArea, Checkbox")
        (first.first() if first else self.query_one("#answers-open-text")).focus()
        self.query_one("#answers-ai-status").display = False
        if self._fields:
            self._check_ai()

    @work(thread=True, exclusive=True, group="answers-ai-check")
    def _check_ai(self) -> None:
        if ai_suggest_ready():
            self.app.call_from_thread(self._show_suggest_button)

    def _show_suggest_button(self) -> None:
        self.query_one("#answers-suggest", Button).display = True

    def _is_empty(self, index: int, field: AnswerField) -> bool:
        if field.choices:
            return not any(
                self.query_one(f"#answers-option-{index}-{letter}", Checkbox).value
                for letter, _text in field.choices
            )
        return not self.query_one(f"#answers-text-{index}", TextArea).text.strip()

    def _ai_status(self, message: str) -> None:
        status = self.query_one("#answers-ai-status", Static)
        status.update(message)
        status.display = bool(message)

    @on(Button.Pressed, "#answers-suggest")
    def _on_suggest(self, event: Button.Pressed) -> None:
        event.stop()
        wanted = [
            field for index, field in enumerate(self._fields) if self._is_empty(index, field)
        ]
        if not wanted:
            self._ai_status("Every question already has an answer; there is nothing to suggest.")
            return
        try:
            resolved = app_config.resolve()
        except Exception as exc:  # noqa: BLE001 - a broken config is reported, not raised
            self._ai_status(f"AI is not set up: {exc}. Nothing was sent.")
            return

        def proceed() -> None:
            self.query_one("#answers-suggest", Button).disabled = True
            self._ai_status(f"Asking the AI about {len(wanted)} question(s)…")
            self._suggest_worker(resolved, wanted)

        if resolved.kind != "cloud":
            proceed()
            return

        # A cloud provider: say exactly what leaves this computer, and ask.
        from opengrader.core.cloud_preflight import CloudPreflight
        from opengrader.tui.ui.screens.cloud_consent_screen import CloudPreflightConfirmScreen

        preflight = CloudPreflight(
            provider_label=resolved.provider.label,
            host=resolved.host,
            counts={"graded-text": 1},
            cost_basis="one short request",
        )
        statement = "\n\n".join(
            part
            for part in (
                f"The AI is asked for the answers to {len(wanted)} question(s). "
                "Only those questions' text and options are sent — no "
                "student's work, name or ID.",
                preflight.statement(),
                app_config.consent.egress_statement(resolved),
            )
            if part
        )

        def answered(confirmed: bool | None) -> None:
            if confirmed:
                proceed()
            else:
                self._ai_status("Not sent. Nothing left this computer.")

        self.app.push_screen(CloudPreflightConfirmScreen(statement, resolved.host), answered)

    @work(thread=True, exclusive=True, group="answers-ai")
    def _suggest_worker(self, resolved, wanted: list[AnswerField]) -> None:  # noqa: ANN001
        from opengrader.core import egress

        try:
            # Recorded beside the answer key, like Create answer key's own calls.
            with egress.recording(self._path.parent):
                generate = ai_generate(resolved)
                self.app.call_from_thread(
                    self._ai_status, f"Asking the AI about {len(wanted)} question(s)…"
                )
                suggestions = suggest_answers(wanted, generate)
        except Exception as exc:  # noqa: BLE001 - shown in the form; nothing was changed
            self.app.call_from_thread(self._suggest_failed, str(exc))
            return
        self.app.call_from_thread(self._apply_suggestions, suggestions, len(wanted))

    def _suggest_failed(self, reason: str) -> None:
        self.query_one("#answers-suggest", Button).disabled = False
        self._ai_status(f"The AI could not be asked: {reason} No answer was changed.")

    def _apply_suggestions(self, suggestions: dict[int, tuple[str, ...]], asked: int) -> None:
        self.query_one("#answers-suggest", Button).disabled = False
        filled = 0
        for index, field in enumerate(self._fields):
            answers = suggestions.get(field.source_line)
            # Only a box that is still empty: what was typed meanwhile stays.
            if not answers or not self._is_empty(index, field):
                continue
            if field.choices:
                for letter in answers:
                    self.query_one(f"#answers-option-{index}-{letter}", Checkbox).value = True
            else:
                self.query_one(f"#answers-text-{index}", TextArea).text = "\n".join(answers)
            self.query_one(f"#answers-ai-note-{index}").display = True
            filled += 1
        if not filled:
            self._ai_status(
                "The AI had no answer to suggest for these questions. Nothing was changed."
            )
            return
        left = asked - filled
        self._ai_status(
            f"The AI suggested answers for {filled} of {asked} question(s)"
            + (f" and left {left} for you" if left else "")
            + ". They are marked below. Check each one: nothing is saved until "
            "you press Save answers, and you can change or clear any of them."
        )

    def _collected(self) -> dict[int, list[str]]:
        answers: dict[int, list[str]] = {}
        for index, field in enumerate(self._fields):
            if field.choices:
                answers[field.source_line] = [
                    letter
                    for letter, _text in field.choices
                    if self.query_one(f"#answers-option-{index}-{letter}", Checkbox).value
                ]
            else:
                text = self.query_one(f"#answers-text-{index}", TextArea).text
                answers[field.source_line] = [line for line in text.splitlines() if line.strip()]
        return answers

    def _fail(self, message: str) -> None:
        problem = self.query_one("#answers-problem", Static)
        problem.update(message)
        problem.display = True

    @on(Button.Pressed, "#answers-save")
    def _on_save(self, event: Button.Pressed) -> None:
        event.stop()
        try:
            if os.stat(self._path).st_mtime != self._read_at:
                # The boxes are addressed to the file as it was read.
                self._fail(
                    f"{self._path.name} was changed by something else since this "
                    "opened, so nothing was saved. Close this and open it again."
                )
                return
            updated = apply_answers(self._text, self._collected())
            if updated != self._text:
                # Beside the file, then swapped in: never half an answer key.
                temporary = self._path.with_name(f".{self._path.name}.{os.getpid()}.tmp")
                temporary.write_text(updated, encoding="utf-8")
                temporary.replace(self._path)
        except (OSError, ValueError) as exc:
            self._fail(f"Not saved: {exc}")
            return
        self.dismiss(True)

    @on(Button.Pressed, "#answers-open-text")
    def _on_open_text(self, event: Button.Pressed) -> None:
        event.stop()
        try:
            open_in_text_editor(self._path)
        except ExternalViewerError as exc:
            self._fail(f"{exc} The file is {self._path}.")
            return
        # Two editors on one file would overwrite each other.
        self.app.notify(
            f"{self._path.name} is open in your text editor. Save it there; "
            "this form was closed so the two do not overwrite each other."
        )
        self.dismiss(False)

    @on(Button.Pressed, "#answers-cancel")
    def _on_cancel(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
