"""Check where the scan was cut into submissions before extraction reads it.

Shown when the pipeline pauses because the split looks wrong somewhere: a
submission of the wrong length (a six-page assignment split 5 + 1), or a
printed page missing or repeated. Where a fix could be worked out its box
starts ticked; Continue applies the ticked fixes and keeps everything else
as split. Each place has its own Open button so the PDFs can be looked at
first, and Stop leaves everything as it is for the user to mend by hand
before running again.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Sequence

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Static

from opengrader.tui.external_viewer import ExternalViewerError, open_in_external_viewer
from opengrader.tui.ui import button_roles
from opengrader.tui.ui.button_roles import OutlineButtons

if TYPE_CHECKING:  # pragma: no cover - importing it at startup would load OpenCV
    from opengrader.core.pages.split_review import SplitFinding

# Checkbox labels cannot wrap.
_LABEL_WIDTH = 44


class SplitReviewScreen(OutlineButtons, ModalScreen[tuple[str, ...] | None]):
    """Dismisses with the keys of the fixes to apply (the rest are kept as
    split), or None to stop."""

    BINDINGS = [Binding("escape", "stop", "Stop")]

    DEFAULT_CSS = """
    SplitReviewScreen {
        align: center middle;
    }

    #split-review-dialog {
        width: 84%;
        max-width: 104;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: round $warning;
        border-title-align: left;
        border-title-color: $warning;
        border-title-style: bold;
        padding: 1 2;
    }

    #split-review-intro, .split-review-read {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    .split-review-read {
        color: $text-muted;
        padding-left: 4;
    }

    .split-review-place {
        height: auto;
        align-vertical: middle;
    }

    .split-review-place Checkbox, .split-review-place .split-review-title {
        width: 1fr;
    }

    .split-review-place .split-review-title {
        padding-left: 1;
    }

    #split-review-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #split-review-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, pending: Sequence["SplitFinding"], split_dir: Path) -> None:
        super().__init__()
        self._pending = tuple(pending)
        self._split_dir = split_dir

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="split-review-dialog") as dialog:
            dialog.border_title = "▸ Check the split before extraction"
            yield Static(
                f"The scan was cut into one PDF per student, and in "
                f"{len(self._pending)} place(s) the result looks wrong. Answers "
                "are read by their position in each PDF, so this has to be "
                "right before extraction. Open the PDFs to look; tick the fixes "
                "to apply, and anything left unticked is kept as split. To mend "
                "the PDFs yourself, stop, edit them in the folder, and run "
                "again: what you mend is not asked about.",
                id="split-review-intro",
                markup=False,
            )
            for index, finding in enumerate(self._pending):
                with Horizontal(classes="split-review-place"):
                    if finding.has_fix:
                        yield Checkbox(
                            f"Fix at {finding.key}"[:_LABEL_WIDTH],
                            value=True,
                            id=f"split-review-fix-{index}",
                        )
                    else:
                        yield Static(
                            f"At {finding.key}", classes="split-review-title", markup=False
                        )
                    yield Button("Open", id=f"split-review-open-{index}", classes=button_roles.OPEN)
                yield Static(_explain(finding), classes="split-review-read", markup=False)
            with Horizontal(id="split-review-buttons"):
                yield Button("Stop and fix them myself", id="split-review-stop")
                yield Button("Open folder", id="split-review-folder", classes=button_roles.OPEN)
                yield Button("Continue", id="split-review-continue", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#split-review-continue", Button).focus()

    @on(Button.Pressed, "#split-review-continue")
    def _on_continue(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(
            tuple(
                finding.key
                for index, finding in enumerate(self._pending)
                if finding.has_fix
                and self.query_one(f"#split-review-fix-{index}", Checkbox).value
            )
        )

    @on(Button.Pressed, ".split-review-place Button")
    def _on_open(self, event: Button.Pressed) -> None:
        event.stop()
        index = int((event.button.id or "").rsplit("-", 1)[-1])
        for name in self._pending[index].names:
            self._open(self._split_dir / f"{name}.pdf")

    @on(Button.Pressed, "#split-review-folder")
    def _on_open_folder(self, event: Button.Pressed) -> None:
        event.stop()
        self._open(self._split_dir)

    def _open(self, path: Path) -> None:
        try:
            open_in_external_viewer(path)
        except ExternalViewerError as exc:
            self.app.notify(f"{exc} The PDFs are in {self._split_dir}.", severity="warning")

    @on(Button.Pressed, "#split-review-stop")
    def _on_stop(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_stop(self) -> None:
        self.dismiss(None)


def _explain(finding: "SplitFinding") -> str:
    problems = "; ".join(finding.problems)
    if finding.has_fix:
        return f"{problems}. Proposed: {finding.describe_fix()}"
    return f"{problems}. {finding.describe_fix()} Continuing keeps it as split."
