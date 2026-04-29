from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Input, Label, Static

from opengrader.tui.ui.widgets.shortcut_input import ShortcutInput

FieldValidator = Callable[[str], str | None]
DialogValidator = Callable[[dict[str, str]], str | None]
DialogResult = dict[str, str] | None
DialogCallback = Callable[[DialogResult], None]


class _DialogHost(Protocol):
    def push_screen(
        self, screen: object, callback: Callable[[object], None] | None = None
    ) -> None: ...


@dataclass(frozen=True)
class DialogField:
    key: str
    label: str
    placeholder: str = ""
    value: str = ""
    required: bool = True
    validator: FieldValidator | None = None


@dataclass
class _QueuedScreenRequest:
    screen: ModalScreen[Any]
    callback: Callable[[Any], None] | None


@dataclass
class _DialogQueueState:
    pending: deque[_QueuedScreenRequest] = field(default_factory=deque)
    active: bool = False


def _queue_state_for(app: object) -> _DialogQueueState:
    queue_state = getattr(app, "_opengrader_dialog_queue_state", None)
    if isinstance(queue_state, _DialogQueueState):
        return queue_state

    queue_state = _DialogQueueState()
    setattr(app, "_opengrader_dialog_queue_state", queue_state)
    return queue_state


def _drain_queue(app: object) -> None:
    queue_state = _queue_state_for(app)
    if queue_state.active or not queue_state.pending:
        return

    request = queue_state.pending.popleft()
    queue_state.active = True

    def _on_closed(result: Any) -> None:
        try:
            if request.callback is not None:
                request.callback(result)
        finally:
            queue_state.active = False
            _drain_queue(app)

    host = cast(_DialogHost, app)
    try:
        host.push_screen(request.screen, _on_closed)
    except Exception:
        queue_state.active = False
        raise


def _present_dialog(
    app: object,
    screen: ModalScreen[Any],
    callback: Callable[[Any], None] | None = None,
    *,
    stack: bool = False,
) -> None:
    if stack:
        host = cast(_DialogHost, app)
        host.push_screen(screen, callback)
        return

    queue_state = _queue_state_for(app)
    queue_state.pending.append(_QueuedScreenRequest(screen=screen, callback=callback))
    _drain_queue(app)


class Dialog(ModalScreen[DialogResult]):
    CSS_PATH = "dialog.tcss"

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("enter", "submit", "Submit", show=False),
    ]

    # TOOD: maybe consider an Alert variant that requires explicit action to dismiss
    ESCAPE_TO_MINIMIZE = True

    _QUEUE_STATE_ATTR = "_opengrader_dialog_queue_state"

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

    @classmethod
    def present(
        cls,
        app: object,
        dialog: "Dialog",
        callback: DialogCallback | None = None,
        *,
        stack: bool = False,
    ) -> None:
        _present_dialog(
            app,
            dialog,
            cast(Callable[[Any], None] | None, callback),
            stack=stack,
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-panel"):
            for field in self._fields:
                with Horizontal(classes="dialog-field-row"):
                    yield Label(field.label, classes="dialog-field-label")
                    yield ShortcutInput(
                        value=field.value,
                        placeholder=field.placeholder,
                        id=self._input_id(field.key),
                        classes="dialog-field-input",
                    )
            yield Static("", id="dialog-error")
            with Horizontal(id="dialog-actions"):
                yield Label(
                    f"Esc · {self._cancel_label}",
                    id="dialog-cancel",
                    classes="dialog-action",
                )
                yield Label(
                    f"Enter · {self._submit_label}",
                    id="dialog-submit",
                    classes="dialog-action",
                )

    def on_mount(self) -> None:
        panel = self.query_one("#dialog-panel", Vertical)
        panel.border_title = self._title

        self.query_one("#dialog-cancel", Label).can_focus = True
        self.query_one("#dialog-submit", Label).can_focus = True

        if not self._fields:
            return
        self.query_one(f"#{self._input_id(self._fields[0].key)}", Input).focus()

    @on(events.Key)
    def _on_key(self, event: events.Key) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        if event.key in ("tab", "shift+tab"):
            event.stop()
            event.prevent_default()
            self._move_focus(reverse=event.key != "tab")
            return

        focused = self.focused
        if event.key == "enter" and isinstance(focused, Label):
            focused_id = focused.id
            if focused_id == "dialog-cancel":
                event.stop()
                event.prevent_default()
                self.action_cancel()
                return
            if focused_id == "dialog-submit":
                event.stop()
                event.prevent_default()
                self.action_submit()
                return

    def _move_focus(self, *, reverse: bool = False) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        focus_chain = self._focus_chain()
        if not focus_chain:
            return

        current_focus = self.focused
        if current_focus in focus_chain:
            current_index = focus_chain.index(current_focus)
            next_index = (current_index - 1 if reverse else current_index + 1) % len(
                focus_chain
            )
        else:
            next_index = len(focus_chain) - 1 if reverse else 0

        focus_chain[next_index].focus()

    def _focus_chain(self) -> list[Widget]:
        focus_chain: list[Widget] = [*self.query(Input)]
        focus_chain.append(self.query_one("#dialog-cancel", Label))
        focus_chain.append(self.query_one("#dialog-submit", Label))
        return [
            widget for widget in focus_chain if widget.can_focus and not widget.disabled
        ]

    @on(events.Click, "#dialog-cancel")
    def _on_cancel_clicked(self, event: events.Click) -> None:
        event.stop()
        self.action_cancel()

    @on(events.Click, "#dialog-submit")
    def _on_submit_clicked(self, event: events.Click) -> None:
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


class AlertDialog(ModalScreen[bool]):
    CSS_PATH = "dialog.tcss"

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=False),
        Binding("enter", "submit", "Submit", show=False),
        Binding("left", "focus_left", "Focus Left", show=False),
        Binding("right", "focus_right", "Focus Right", show=False),
    ]

    ESCAPE_TO_MINIMIZE = False

    def __init__(
        self,
        *,
        title: str,
        message: str,
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
    ) -> None:
        super().__init__()
        self._title = title
        self._message = message
        self._confirm_label = confirm_label
        self._cancel_label = cancel_label

    @classmethod
    def present(
        cls,
        app: object,
        dialog: "AlertDialog",
        callback: Callable[[bool], None] | None = None,
        *,
        stack: bool = False,
    ) -> None:
        _present_dialog(
            app,
            dialog,
            cast(Callable[[Any], None] | None, callback),
            stack=stack,
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-panel"):
            yield Label(self._message, classes="dialog-alert-message")
            with Horizontal(id="dialog-actions"):
                # TODO: re-add key labels?
                yield Label(
                    f"{self._cancel_label}",
                    id="dialog-cancel",
                    classes="dialog-action",
                )
                yield Label(
                    f"{self._confirm_label}",
                    id="dialog-submit",
                    classes="dialog-action",
                )

    def on_mount(self) -> None:
        panel = self.query_one("#dialog-panel", Vertical)
        panel.border_title = self._title

        self.query_one("#dialog-cancel", Label).can_focus = True
        self.query_one("#dialog-submit", Label).can_focus = True

        self.query_one("#dialog-cancel", Label).focus()

    def action_focus_left(self) -> None:
        self.focus_previous()

    def action_focus_right(self) -> None:
        self.focus_next()

    @on(events.Key)
    def _on_key(self, event: events.Key) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        if event.key in ("tab", "shift+tab", "left", "right"):
            event.stop()
            event.prevent_default()
            self._move_focus(reverse=event.key in ("shift+tab", "left"))
            return

        focused = self.focused
        if event.key == "enter" and isinstance(focused, Label):
            focused_id = focused.id
            if focused_id == "dialog-cancel":
                event.stop()
                event.prevent_default()
                self.action_cancel()
                return
            if focused_id == "dialog-submit":
                event.stop()
                event.prevent_default()
                self.action_submit()
                return
        elif event.key == "enter":
            self.action_submit()

    def _move_focus(self, *, reverse: bool = False) -> None:
        focus_chain: list[Widget] = [
            self.query_one("#dialog-cancel", Label),
            self.query_one("#dialog-submit", Label),
        ]
        if not focus_chain:
            return

        current_focus = self.focused
        if current_focus in focus_chain:
            current_index = focus_chain.index(current_focus)
            next_index = (current_index - 1 if reverse else current_index + 1) % len(
                focus_chain
            )
        else:
            next_index = len(focus_chain) - 1 if reverse else 0

        focus_chain[next_index].focus()

    @on(events.Click, "#dialog-cancel")
    def _on_cancel_clicked(self, event: events.Click) -> None:
        event.stop()
        self.action_cancel()

    @on(events.Click, "#dialog-submit")
    def _on_submit_clicked(self, event: events.Click) -> None:
        event.stop()
        self.action_submit()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_submit(self) -> None:
        self.dismiss(True)
