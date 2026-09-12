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

CHECKBOX_LABEL_LIMIT = 45

_CHECKBOX_RE = re.compile(
    r"Checkbox\(\s*((?:\"[^\"]*\"\s*)+),\s*(?:value=\w+,\s*)?id=\"([\w-]+)\""
)


def _checkbox_labels() -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for path in sorted(SCREENS_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in _CHECKBOX_RE.finditer(source):
            label = "".join(re.findall(r'"([^"]*)"', match.group(1)))
            found.append((path.name, match.group(2), label))
    return found


def test_the_scan_finds_the_checkboxes_it_is_meant_to_guard() -> None:
    labels = _checkbox_labels()
    assert len(labels) > 15, f"expected the screens to declare checkboxes, got {labels}"
    assert any(name == "setup_screen.py" for name, _id, _label in labels)
    assert any(name == "handoff_screen.py" for name, _id, _label in labels)


def test_no_checkbox_label_would_be_cut_off_on_a_narrow_terminal() -> None:
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
