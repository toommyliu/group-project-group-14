"""Confirm the page order of split submissions before extraction reads them.

Shown when the pipeline pauses because some submissions look out of order.
Nothing is moved unless its box is ticked: a ticked submission is sorted, an
unticked one is kept as scanned, and Stop leaves everything for the user to
check or rearrange by hand before running again. A box starts ticked only
when a blank template supplied the order — page numbers read off a scan can
be misread, so those start unticked.
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

if TYPE_CHECKING:  # pragma: no cover - importing it at startup would load OpenCV
    from opengrader.core.pages.order_review import OrderFinding

# Checkbox labels cannot wrap.
_LABEL_WIDTH = 44


class PageOrderScreen(ModalScreen[tuple[str, ...] | None]):
    """Dismisses with the names to sort (the rest are kept), or None to stop."""

    BINDINGS = [Binding("escape", "stop", "Stop")]

    DEFAULT_CSS = """
    PageOrderScreen {
        align: center middle;
    }

    #page-order-dialog {
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

    #page-order-intro, .page-order-read {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    .page-order-read {
        color: $text-muted;
        padding-left: 4;
    }

    #page-order-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #page-order-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, pending: Sequence["OrderFinding"], split_dir: Path) -> None:
        super().__init__()
        self._pending = tuple(pending)
        self._split_dir = split_dir

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="page-order-dialog") as dialog:
            dialog.border_title = "▸ Confirm page order before extraction"
            yield Static(
                f"{len(self._pending)} submission(s) look out of order. Tick the ones to "
                "sort; the rest are kept as scanned. Answers are found by what is printed "
                "on the page, so a submission left as scanned is still graded against the "
                "right questions — only its returned PDF keeps the scanned order. To "
                "rearrange pages yourself, stop, edit the PDFs in the folder, and run "
                "again: pages you put in order are not asked about.",
                id="page-order-intro",
                markup=False,
            )
            for index, finding in enumerate(self._pending):
                yield Checkbox(
                    f"Sort {finding.name}"[:_LABEL_WIDTH],
                    value=finding.recommended,
                    id=f"page-order-sort-{index}",
                )
                yield Static(_explain(finding), classes="page-order-read", markup=False)
            with Horizontal(id="page-order-buttons"):
                yield Button("Stop and check them myself", id="page-order-stop")
                yield Button("Open folder", id="page-order-open")
                yield Button("Continue", id="page-order-continue", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#page-order-continue", Button).focus()

    @on(Button.Pressed, "#page-order-continue")
    def _on_continue(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(
            tuple(
                finding.name
                for index, finding in enumerate(self._pending)
                if self.query_one(f"#page-order-sort-{index}", Checkbox).value
            )
        )

    @on(Button.Pressed, "#page-order-open")
    def _on_open(self, event: Button.Pressed) -> None:
        event.stop()
        try:
            open_in_external_viewer(self._split_dir)
        except ExternalViewerError as exc:
            self.app.notify(f"{exc} The PDFs are in {self._split_dir}.", severity="warning")

    @on(Button.Pressed, "#page-order-stop")
    def _on_stop(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_stop(self) -> None:
        self.dismiss(None)


def _explain(finding: "OrderFinding") -> str:
    read = finding.read[0].upper() + finding.read[1:]
    if finding.recommended:
        why = "The blank template supplies this order, so sorting is recommended."
    elif finding.shared:
        why = (
            "Most of the class shows the same break, so a page number was probably "
            "misread and the scan is fine."
        )
    else:
        why = "Read off the scan, so one number may simply be misread — check before sorting."
    return f"{read}; sorted it would be {finding.sorted_as}. {why}"
