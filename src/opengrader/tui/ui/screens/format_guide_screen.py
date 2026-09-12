from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Select, Static

from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen

_SYNTAX_ROWS: list[tuple[str, str]] = [
    ("1.  2.  3.", "Start each question with its number. Number them in order."),
    ("a)  *b)", "Multiple choice — list options a) b) c); mark the correct one with a *."),
    ("[ ]  [*]", "Select all that apply — check every correct option with a *."),
    ("=", "Numeric answer, e.g. = 42, or = 3.14 +- 0.01 to allow a tolerance."),
    ("*", "Short answer. Use several * lines to accept alternative answers."),
    ("____", "Essay / long answer — a line of underscores where the answer goes."),
    ("Points possible:", "Optional — points for the questions that follow it."),
    (
        "Outcome:",
        "Optional — a learning outcome this question measures, e.g. Outcome: "
        "CLO3. Repeat it, or comma-separate. Put it before question 1 to cover "
        "every question after it.",
    ),
    ("Quiz title:", 'Optional header (the keyword is literally "Quiz" for any assignment).'),
]

SIMPLE_TEMPLATE = """\
Quiz title: Chapter 3 Homework

1. What is the capital of France?
a) London
*b) Paris
c) Rome

2. Water freezes at what temperature in Celsius?
= 0

3. Name the process plants use to make food from sunlight.
* photosynthesis
"""

COMPLEX_TEMPLATE = """\
Quiz title: Biology Midterm
Quiz description: Answer every question. Show your work where asked.

Points possible: 2
1. Which planet is known as the Red Planet?
a) Venus
*b) Mars
c) Jupiter
d) Saturn

Points possible: 3
2. Select every prime number.
[*] 2
[ ] 4
[*] 5
[ ] 9

Points possible: 2
3. What is pi to two decimal places?
= 3.14 +- 0.01

Points possible: 1
4. What is the chemical symbol for gold?
* Au
* gold

Points possible: 5
5. In your own words, explain how a binary search works.
____
"""

OUTCOMES_TEMPLATE = """\
Quiz title: CS 146 Quiz 3 — Sorting

# Tip: when most of an exam measures the same outcome, put one Outcome line
# here, before question 1 — it then applies to every question after it, and a
# question can still add its own. Uncomment to try it:
#   Outcome: CLO1

Points possible: 2
1. Which sorting algorithm is stable?
Outcome: CLO1
a) Quicksort
*b) Merge sort
c) Heapsort

Points possible: 3
2. Merge sort runs in what average time?
Outcome: CLO1
* n log n
* O(n log n)

Points possible: 5
3. Explain why the merge step is linear in the size of the two halves.
Outcome: CLO2, CLO3
____

Points possible: 5
4. Write the recurrence for merge sort and solve it.
Outcome: CLO3
____

Points possible: 1
5. Write your student ID in the box.
____
"""

BUBBLE_TEMPLATE = """\
Quiz title: Chapter 5 Quiz
Quiz description: Fill in one circle per question.

Points possible: 2
1. Which data structure uses first-in, first-out ordering?
a) Stack
*b) Queue
c) Binary search tree
d) Hash map

Points possible: 2
2. What is the average time complexity of binary search on a sorted array?
a) O(1)
*b) O(log n)
c) O(n)
d) O(n log n)

Points possible: 2
3. Which keyword declares a constant in modern JavaScript?
a) var
b) let
*c) const
d) static

Points possible: 2
4. HTTP status 404 means:
a) The request succeeded
b) The server failed
*c) The resource was not found
d) The request was redirected

Points possible: 2
5. Which sorting algorithm has the worst-case time complexity O(n^2)?
a) Merge sort
b) Heap sort
*c) Quicksort
d) Radix sort
"""


class FormatGuideScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    FormatGuideScreen {
        align: center middle;
    }

    #format-dialog {
        width: 80%;
        max-width: 100;
        height: 85%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #format-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #format-body {
        height: 1fr;
        margin-top: 1;
        scrollbar-size: 1 1;
    }

    FormatGuideScreen .format-section {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }

    FormatGuideScreen .format-section.-first {
        margin-top: 0;
    }

    FormatGuideScreen .format-row {
        width: 100%;
        height: auto;
    }

    FormatGuideScreen .format-marker {
        width: 20;
        padding-right: 2;
        color: #EBCB8B;
        text-style: bold;
    }

    FormatGuideScreen .format-desc {
        width: 1fr;
        height: auto;
    }

    FormatGuideScreen .format-note {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-top: 1;
    }

    FormatGuideScreen .template-card {
        width: 100%;
        height: auto;
        border: round $surface-lighten-2;
        border-title-color: $text;
        border-title-style: bold;
        padding: 0 1;
        margin-top: 1;
    }

    FormatGuideScreen .template-text {
        width: 100%;
        height: auto;
        color: $text;
        padding: 1 0;
    }

    FormatGuideScreen .template-actions {
        width: 100%;
        height: auto;
        align-horizontal: right;
        padding-bottom: 1;
    }

    FormatGuideScreen #format-save-row {
        height: auto;
        align-vertical: middle;
        margin-top: 1;
        border-top: solid $surface-lighten-2;
        padding-top: 1;
    }

    FormatGuideScreen .format-save-label {
        width: auto;
        height: 3;
        content-align: left middle;
        margin-right: 1;
        color: $text-muted;
    }

    FormatGuideScreen #template-choice {
        width: 36;
        margin-right: 1;
    }

    #format-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close_guide", "Close"),
        Binding("t", "close_guide", "Close", key_display="t"),
    ]

    @staticmethod
    def _syntax_section() -> Iterable[Static | Horizontal]:
        yield Static("[b]HOW TO WRITE A QUESTION[/b]", classes="format-section -first")
        for marker, description in _SYNTAX_ROWS:
            yield Horizontal(
                Static(marker, classes="format-marker", markup=False),
                Static(description, classes="format-desc", markup=False),
                classes="format-row",
            )

    @staticmethod
    def _template_card(title: str, body: str, copy_button_id: str) -> Vertical:
        card = Vertical(
            Static(body, classes="template-text", markup=False),
            Horizontal(
                Button("Copy", id=copy_button_id, compact=True),
                classes="template-actions",
            ),
            classes="template-card",
        )
        card.border_title = title
        return card

    def compose(self) -> ComposeResult:
        with Vertical(id="format-dialog") as dialog:
            dialog.border_title = "▸ Assignment file format"
            yield Static(
                "Write your assignment as a plain-text file. Copy a template "
                "below (Copy button or select with the mouse), paste it into a "
                ".txt or .md file, edit it, then choose it as the Answer-key "
                "source. Press esc or t to close.",
                id="format-hint",
            )
            with VerticalScroll(id="format-body"):
                yield from self._syntax_section()
                yield Static(
                    "Every question gets a standard Correct / Partial / Incorrect "
                    "rubric — fine-tune it later with e while grading.",
                    classes="format-note",
                )
                yield Static("[b]TEMPLATES[/b]", classes="format-section")
                yield self._template_card(
                    "Simple — start here", SIMPLE_TEMPLATE, "copy-simple"
                )
                yield self._template_card(
                    "Full — every question type", COMPLEX_TEMPLATE, "copy-complex"
                )
                yield self._template_card(
                    "Multiple choice only — for a bubble answer sheet",
                    BUBBLE_TEMPLATE,
                    "copy-bubble",
                )
                yield self._template_card(
                    "Learning outcomes — tag what each question measures",
                    OUTCOMES_TEMPLATE,
                    "copy-outcomes",
                )
                yield Static(
                    "Outcome lines are optional and cost nothing when unused. "
                    "Tag them and you get per-outcome mastery in the shift+S "
                    "report (press o), a column each in the gradebook CSV, a "
                    "page in the analysis PDF, and mastery across the whole "
                    "semester in the course gradebook.",
                    classes="format-note",
                )
                yield Static(
                    "A bubble sheet can only carry multiple-choice questions, "
                    "so start from this one if you plan to use it — anything "
                    "else would be left off the printed sheet.",
                    classes="format-note",
                )
            with Horizontal(id="format-save-row"):
                yield Static("Save a starter file:", classes="format-save-label")
                yield Select(
                    [
                        ("Simple — start here", "simple"),
                        ("Full — every question type", "full"),
                        ("Learning outcomes", "outcomes"),
                        ("Multiple choice only (bubble sheet)", "bubble"),
                    ],
                    value="simple",
                    allow_blank=False,
                    id="template-choice",
                )
                yield Button(
                    "Save file…", id="save-template", variant="primary", compact=True
                )
            with Horizontal(id="format-buttons"):
                yield Button("Close", id="format-close", compact=True)

    def action_close_guide(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#copy-simple")
    def _copy_simple(self, event: Button.Pressed) -> None:
        event.stop()
        self._copy(SIMPLE_TEMPLATE, "Simple")

    @on(Button.Pressed, "#copy-complex")
    def _copy_complex(self, event: Button.Pressed) -> None:
        event.stop()
        self._copy(COMPLEX_TEMPLATE, "Full")

    @on(Button.Pressed, "#copy-outcomes")
    def _copy_outcomes(self, event: Button.Pressed) -> None:
        event.stop()
        self._copy(OUTCOMES_TEMPLATE, "Learning outcomes")

    @on(Button.Pressed, "#copy-bubble")
    def _copy_bubble(self, event: Button.Pressed) -> None:
        event.stop()
        self._copy(BUBBLE_TEMPLATE, "Multiple-choice")

    @on(Button.Pressed, "#save-template")
    def _on_save_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._save_template()

    @on(Button.Pressed, "#format-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def _save_template(self) -> None:
        choice = self.query_one("#template-choice", Select).value
        template = {
            "full": COMPLEX_TEMPLATE,
            "outcomes": OUTCOMES_TEMPLATE,
            "bubble": BUBBLE_TEMPLATE,
        }.get(str(choice), SIMPLE_TEMPLATE)

        def _write(dest: Path | None) -> None:
            if dest is None:
                return
            try:
                path = self._unique_path(Path(dest) / "assignment-template.md")
                path.write_text(template)
            except OSError as exc:
                self.app.notify(
                    f"Could not save the template: {exc}", severity="error"
                )
                return
            self.app.notify(
                f"Saved {path}. Edit it, then load it as the Answer-key source."
            )

        self.app.push_screen(
            PathPickerScreen(
                title="Choose where to save the template",
                mode="directory",
                start_path=Path.cwd(),
            ),
            _write,
        )

    @staticmethod
    def _unique_path(path: Path) -> Path:
        if not path.exists():
            return path
        counter = 1
        while True:
            candidate = path.with_name(f"{path.stem}-{counter}{path.suffix}")
            if not candidate.exists():
                return candidate
            counter += 1

    def _copy(self, text: str, label: str) -> None:
        self.app.copy_to_clipboard(text)
        self.app.notify(
            f"{label} template copied. Paste it into a .txt or .md file.",
        )
