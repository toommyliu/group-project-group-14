from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static

FieldValidator = Callable[[str], str | None]
DialogValidator = Callable[[dict[str, str]], str | None]


@dataclass(frozen=True)
class DialogField:
    key: str
    label: str
    placeholder: str = ""
    value: str = ""
    required: bool = True
    validator: FieldValidator | None = None


class Dialog(ModalScreen[dict[str, str] | None]):
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("enter", "submit", "Submit", show=False),
    ]

    def __init__(
        self,
        *,
        title: str,
        fields: list[DialogField],
        submit_label: str = "Submit",
        cancel_label: str = "Cancel",
        validator: DialogValidator | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._fields = fields
        self._submit_label = submit_label
        self._cancel_label = cancel_label
        self._validator = validator

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-panel"):
            yield Label(self._title, id="dialog-title")
            for field in self._fields:
                yield Label(field.label, classes="dialog-field-label")
                yield Input(
                    value=field.value,
                    placeholder=field.placeholder,
                    id=self._input_id(field.key),
                )
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Button(self._cancel_label, id="dialog-cancel")
                yield Button(self._submit_label, id="dialog-submit", variant="primary")

    def on_mount(self) -> None:
        if not self._fields:
            return
        self.query_one(f"#{self._input_id(self._fields[0].key)}", Input).focus()

    @on(Button.Pressed, "#dialog-cancel")
    def _on_cancel_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    @on(Button.Pressed, "#dialog-submit")
    def _on_submit_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_submit()

    @on(Input.Submitted)
    def _on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.action_submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        values = self._collect_values()
        field_error = self._validate_fields(values)
        if field_error is not None:
            self._set_error(field_error[1])
            self.query_one(f"#{self._input_id(field_error[0])}", Input).focus()
            return

        if self._validator is not None:
            dialog_error = self._validator(values)
            if dialog_error is not None:
                self._set_error(dialog_error)
                return

        self._set_error("")
        self.dismiss(values)

    def _collect_values(self) -> dict[str, str]:
        return {
            field.key: self.query_one(
                f"#{self._input_id(field.key)}", Input
            ).value.strip()
            for field in self._fields
        }

    def _validate_fields(self, values: dict[str, str]) -> tuple[str, str] | None:
        for field in self._fields:
            value = values.get(field.key, "")
            if field.required and not value:
                return field.key, f"{field.label} is required."
            if field.validator is None:
                continue
            error = field.validator(value)
            if error is not None:
                return field.key, error
        return None

    def _set_error(self, message: str) -> None:
        self.query_one("#dialog-error", Static).update(message)

    @staticmethod
    def _input_id(field_key: str) -> str:
        return f"dialog-field-{field_key}"
