from __future__ import annotations

from typing import ClassVar

from textual.binding import Binding, BindingType
from textual.widgets import Input


class ShortcutInput(Input):
    """Single-line input with readline-style keyboard aliases."""

    BINDINGS: ClassVar[list[BindingType]] = [
        # https://textual.textualize.io/widgets/input/#textual.widgets._input.Input.BINDINGS
        # TODO: ask Carlos if he likes these native textual Input bindings
        *Input.BINDINGS,
        # Textual owns the editing actions; this widget only adds aliases.
        Binding("alt+b", "cursor_left_word", "Move cursor left a word", show=False),
        Binding("alt+f", "cursor_right_word", "Move cursor right a word", show=False),
        Binding("alt+d", "delete_right_word", "Delete right to word", show=False),
    ]
