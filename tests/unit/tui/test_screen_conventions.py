"""UI conventions that hold across every screen, not just one.

These are style guards rather than behaviour tests: they read the screen sources
and assert a rule that is easy to break by writing perfectly reasonable-looking
code. Each one exists because the breakage is *invisible* — the app renders, the
tests pass, and the only symptom is a user seeing less than was written.
"""

from __future__ import annotations

import re
from pathlib import Path

SCREENS_DIR = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "opengrader"
    / "tui"
    / "ui"
    / "screens"
)

# The longest a Checkbox label may be. Textual's ``ToggleButton`` hard-codes
# ``get_content_height`` to 1, so a Checkbox is one line tall no matter how it is
# styled — `text-wrap: wrap` does nothing, and the tail past the card's width is
# simply gone, with the card clipping before even the default ellipsis shows.
# 45 is what still fits inside a dialog or card on an 80-column terminal.
CHECKBOX_LABEL_LIMIT = 45

# Checkbox("...literal...", [value=...,] id="...") — labels are literals
# everywhere today, and a dynamic one is a style question this cannot answer.
_CHECKBOX_RE = re.compile(
    r"Checkbox\(\s*((?:\"[^\"]*\"\s*)+),\s*(?:value=\w+,\s*)?id=\"([\w-]+)\""
)


def _checkbox_labels() -> list[tuple[str, str, str]]:
    """Every (screen file, id, label) declared under ``ui/screens``."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(SCREENS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in _CHECKBOX_RE.finditer(source):
            label = "".join(re.findall(r'"([^"]*)"', match.group(1)))
            found.append((path.name, match.group(2), label))
    return found


def test_the_scan_finds_the_checkboxes_it_is_meant_to_guard() -> None:
    """Guard the guard: a regex that silently matches nothing passes forever."""
    labels = _checkbox_labels()
    assert len(labels) > 15, f"expected the screens to declare checkboxes, got {labels}"
    assert any(name == "setup_screen.py" for name, _id, _label in labels)
    assert any(name == "handoff_screen.py" for name, _id, _label in labels)


def test_no_checkbox_label_would_be_cut_off_on_a_narrow_terminal() -> None:
    """A checkbox label must fit on one line, because it only ever gets one.

    This shipped once already: "Check the scan before splitting (turn upside-down
    pages upright, …)" rendered as "…(turn upside-down pages upright, put o" and
    stopped there, with nothing on screen to say more had been written.

    The fix is never CSS — it cannot be. Shorten the label and put the detail in
    a Static underneath it (``card-hint`` on the setup screen, the dialog's own
    muted-hint id in a modal); a Static wraps as far as it likes.
    """
    too_long = [
        (name, box_id, len(label))
        for name, box_id, label in _checkbox_labels()
        if len(label) > CHECKBOX_LABEL_LIMIT
    ]
    assert not too_long, (
        f"checkbox labels over {CHECKBOX_LABEL_LIMIT} chars are cut off on a "
        "narrow terminal; shorten the label and move the detail into a hint "
        "Static below it: "
        + "; ".join(f"{name}:{box_id} ({length})" for name, box_id, length in too_long)
    )
