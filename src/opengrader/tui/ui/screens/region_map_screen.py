"""Draw the answer regions on your own assignment, from inside the TUI.

``region-map propose`` gets an instructor a draft and ``region-map preview``
shows them whether it is right. This screen is the part in between: the place
they *fix* it. It opens a blank copy of their assignment, draws the current
regions on it, and lets them move, resize, add, delete and describe boxes until
the picture matches the paper — then writes ``<assignment>.regions.json`` and
hands the path back.

**The keyboard is the primary interface, not a fallback.** Opengrader runs in
terminals with no pixel graphics at all (see ``tui/terminal_graphics.py``: Kitty,
iTerm2, Sixel, and a half-block renderer for everything else, plus the ``o``
hatch that opens a page in the desktop viewer). A drag-only box editor would be
unusable for a real share of users, so *every* operation has a key and mouse
drag is strictly additive. Nothing here may become mouse-only.

Layout of the controls, and why:

* **j / k** walk the selection forward and back through every region on the
  assignment, matching the repo-wide meaning of those keys. Selection is over
  *segments*, not questions, so a continuation is nudged exactly like a primary
  box and needs no separate mode.
* **arrows** move the selected box; **shift+arrows** resize it by its right and
  bottom edges. Two motions reach every rectangle, and both are single
  keystrokes that repeat when held.
* **page up / page down** turn the page (a page is not "forward" in the same
  sense a selection is, and stealing j/k for it would leave the selection —
  which is edited far more often — with the worse keys).
* The **metadata panel** on the right edits the selected question's number,
  points, type and prompt as plain widgets. A modal inside a modal to type a
  number would be worse than the problem it solves.

Redraw cost is deliberate: the rendered PDF page is cached per page number and
only the overlay is re-composited per edit (``render_region_map_page(base=…)``).
Re-rasterizing the page on every arrow key made the editor feel broken, which is
also why the page renders at ``PREVIEW_DPI`` rather than extraction DPI.

Saving is refused while any **error**-level finding stands, and allowed with
warnings — an instructor deliberately leaving question 7 unmapped because it is
extra credit is doing something ordinary, while a duplicate question id would
silently overwrite a crop. The findings themselves come from
``core/region_map.validate_region_map``, the same call ``region-map validate``
makes, so the editor and the CLI can never disagree about whether a map is
usable.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Select, Static

from opengrader.core.question_types import (
    FREE_RESPONSE,
    MULTIPLE_ANSWER,
    MULTIPLE_CHOICE,
    NUMERIC,
)
from opengrader.core.region_map import (
    NEW_REGION_SIZE,
    PREVIEW_DPI,
    MappedQuestion,
    RegionBox,
    RegionMap,
    RegionMapError,
    blank_region_map,
    default_region_map_path,
    grid_region_map,
    load_region_map,
    region_map_errors,
    render_region_map_page,
    save_region_map,
    validate_region_map,
    verify_region_map,
)
from opengrader.core.pdf_utils import clamp_bbox_norm, render_pdf_page
from opengrader.tui.ui.widgets.sharp_image import SharpImage

# One nudge, as a fraction of the page. 0.005 is ~4pt on US Letter — fine enough
# to line a box up with a printed rule, coarse enough that crossing the page
# takes a held key rather than a career.
NUDGE_STEP = 0.005

# The smallest box the editor will shrink to. A zero-area region is an
# error-level finding, so the editor stops just before creating one rather than
# letting a held key produce something it then refuses to save.
MIN_REGION_SIZE = 0.01

# How many findings the panel lists before summarizing. Enough to see the shape
# of the problem without pushing the page image off the screen.
_MAX_LISTED_FINDINGS = 6

_TYPE_OPTIONS = [
    ("Free response", FREE_RESPONSE),
    ("Multiple choice", MULTIPLE_CHOICE),
    ("Select all", MULTIPLE_ANSWER),
    ("Numeric", NUMERIC),
]

_SEVERITY_GLYPH = {"error": "●", "warning": "◐", "info": "○"}


class RegionCanvas(Widget):
    """The page image, with click-drag box drawing where the terminal allows it.

    A container that *composes* a :class:`SharpImage` rather than subclassing
    it. Two reasons, and both are load-bearing:

    * ``SharpImage`` derives from ``textual_image``'s widget, whose
      ``__init_subclass__`` demands a ``Renderable=`` class keyword — it is a
      leaf widget by construction, not a base to extend.
    * ``SharpImage`` already composes a child of its own for the Sixel/iTerm2
      protocols (``inline_graphics.InlineGraphicsPane``), so wrapping is the
      only way to add behaviour that works on *every* graphics backend rather
      than just the Rich-renderable ones.

    The drag itself is an *enhancement*: every box it can draw is also
    reachable with ``a`` plus the arrow keys, because a terminal with no
    graphics protocol has no meaningful pixels to drag over. The mapping from
    cells to normalized coordinates is approximate for the same reason — a cell
    is several pixels — which is fine, since a dragged box is a starting point
    the arrows then refine.
    """

    DEFAULT_CSS = """
    RegionCanvas {
        width: 100%;
        height: auto;
    }
    """

    # Focusable so it can *hold* focus: the metadata Inputs are the only other
    # focusable widgets here, and a screen that opens with a text field focused
    # would swallow every single-letter command into it.
    can_focus = True

    class BoxDrawn(Message):
        def __init__(self, bbox: tuple[float, float, float, float]) -> None:
            self.bbox = bbox
            super().__init__()

    class BoxDragging(Message):
        def __init__(self, bbox: tuple[float, float, float, float] | None) -> None:
            self.bbox = bbox
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._drag_origin: tuple[float, float] | None = None
        self._image_widget = SharpImage(
            None,
            id="region-page-image",
            fit_width_fraction=0.98,
            allow_upscale=True,
        )

    def compose(self) -> ComposeResult:
        yield self._image_widget

    @property
    def image(self):  # noqa: ANN201 - mirrors SharpImage's own loose typing
        return self._image_widget.image

    @image.setter
    def image(self, value) -> None:  # noqa: ANN001
        self._image_widget.image = value

    def _normalized(self, screen_x: int, screen_y: int) -> tuple[float, float]:
        """Screen coordinates to a fraction of the displayed page.

        Measured against the *image* child's region, not this container's, so a
        page that does not fill the pane (a portrait sheet in a wide pane) still
        maps a click to the right part of the paper.
        """
        try:
            region = self._image_widget.region
        except Exception:  # pragma: no cover - not laid out yet
            return (0.0, 0.0)
        width = max(1, region.width)
        height = max(1, region.height)
        return (
            min(1.0, max(0.0, (screen_x - region.x) / width)),
            min(1.0, max(0.0, (screen_y - region.y) / height)),
        )

    def _bbox_from(self, corner: tuple[float, float]) -> tuple[float, float, float, float]:
        origin = self._drag_origin or corner
        x0, y0 = origin
        x1, y1 = corner
        return clamp_bbox_norm(
            (min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        )

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if event.button != 1:
            return
        self._drag_origin = self._normalized(event.screen_x, event.screen_y)
        self.capture_mouse()
        event.stop()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self._drag_origin is None:
            return
        self.post_message(
            self.BoxDragging(
                self._bbox_from(self._normalized(event.screen_x, event.screen_y))
            )
        )
        event.stop()

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self._drag_origin is None:
            return
        bbox = self._bbox_from(self._normalized(event.screen_x, event.screen_y))
        self._drag_origin = None
        self.release_mouse()
        self.post_message(self.BoxDragging(None))
        # A click that did not travel is a click, not a box: creating a sliver
        # region every time someone taps the page would be maddening.
        if bbox[2] >= MIN_REGION_SIZE and bbox[3] >= MIN_REGION_SIZE:
            self.post_message(self.BoxDrawn(bbox))
        event.stop()


class RegionMapScreen(ModalScreen["Path | None"]):
    """Edit the answer regions of a blank assignment and save them as a region map.

    Dismisses with the path the map was written to, or ``None`` if cancelled.
    Self-contained: it takes a PDF path and an optional existing map path and
    needs nothing from the app around it, so it can be pushed from anywhere (and
    driven headless by Textual's pilot).
    """

    DEFAULT_CSS = """
    RegionMapScreen {
        align: center middle;
    }

    #region-dialog {
        width: 94%;
        height: 94%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #region-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #region-body {
        height: 1fr;
        margin-top: 1;
    }

    #region-page-pane {
        width: 2fr;
        height: 100%;
        scrollbar-size: 1 1;
    }

    #region-side {
        width: 44;
        height: 100%;
        margin-left: 2;
    }

    RegionMapScreen .region-label {
        width: 100%;
        height: auto;
        margin-top: 1;
        text-style: bold;
    }

    #region-status, #region-selection {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #region-findings {
        width: 100%;
        height: auto;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        padding: 0 1;
    }

    #region-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #region-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
        Binding("j", "select_next", "Next region"),
        Binding("k", "select_previous", "Prev region"),
        Binding("pagedown", "next_page", "Next page"),
        Binding("pageup", "previous_page", "Prev page"),
        Binding("up", "nudge('up')", "Move up", show=False),
        Binding("down", "nudge('down')", "Move down", show=False),
        Binding("left", "nudge('left')", "Move left", show=False),
        Binding("right", "nudge('right')", "Move right", show=False),
        Binding("shift+up", "resize('up')", "Shorter", show=False),
        Binding("shift+down", "resize('down')", "Taller", show=False),
        Binding("shift+left", "resize('left')", "Narrower", show=False),
        Binding("shift+right", "resize('right')", "Wider", show=False),
        Binding("a", "add_region", "Add"),
        Binding("d", "delete_region", "Delete"),
        Binding("c", "add_continuation", "Continuation"),
        Binding("f", "add_identity_field", "Identity field"),
        Binding("g", "fill_grid", "Grid"),
        Binding("u", "select_least_confident", "Least sure"),
        Binding("e", "edit_metadata", "Edit"),
        Binding("o", "open_externally", "Open page"),
    ]

    def __init__(
        self,
        pdf_path: str | Path,
        map_path: str | Path | None = None,
        *,
        output_path: str | Path | None = None,
        dpi: int = PREVIEW_DPI,
    ) -> None:
        self._pdf_path = Path(pdf_path)
        self._map_path = Path(map_path) if map_path else None
        self._output_path = Path(output_path) if output_path else (
            self._map_path or default_region_map_path(self._pdf_path)
        )
        self._dpi = dpi
        self._page = 1
        self._selection = 0
        self._status = ""
        self._draft: tuple[float, float, float, float] | None = None
        # Rendered pages, keyed by page number. The whole reason an arrow-key
        # nudge repaints in a few milliseconds instead of re-rasterizing a PDF.
        self._page_cache: dict[int, tuple[object, int, int]] = {}
        self._map = self._initial_map()
        super().__init__()

    # ── loading ────────────────────────────────────────────────────────────

    def _initial_map(self) -> RegionMap:
        """The map to start from: an existing one, or an empty one for this assignment.

        A map that does not belong to this PDF is *not* silently adopted — its
        boxes describe a different page. It is reported and dropped, leaving an
        empty map for this assignment, which is the only honest starting point.
        """
        if self._map_path is not None and self._map_path.exists():
            try:
                candidate = load_region_map(self._map_path)
            except RegionMapError as exc:
                self._status = str(exc)
            else:
                verification = verify_region_map(candidate, self._pdf_path)
                if verification:
                    return candidate
                self._status = verification.message
        try:
            return blank_region_map(self._pdf_path)
        except RegionMapError as exc:
            self._status = str(exc)
            return RegionMap(
                source_name=self._pdf_path.name,
                source_sha256="",
                source_pages=1,
                source_page_size=(612.0, 792.0),
            )

    # ── targets: the flat list the selection walks ─────────────────────────

    def _targets(self) -> list[tuple[str, int, int, RegionBox]]:
        """Every editable box: ``(kind, question index, segment index, box)``.

        ``kind`` is ``"name"``, ``"student_id"`` or ``"question"``. Flattening
        identity fields and question segments into one cursor means j/k, the
        arrows and delete all behave identically wherever the cursor is, which
        is one rule to learn instead of three.
        """
        targets: list[tuple[str, int, int, RegionBox]] = []
        if self._map.name_field is not None:
            targets.append(("name", -1, 0, self._map.name_field))
        if self._map.student_id_field is not None:
            targets.append(("student_id", -1, 0, self._map.student_id_field))
        for q_index, question in enumerate(self._map.questions):
            for s_index, segment in enumerate(question.segments):
                targets.append(("question", q_index, s_index, segment))
        return targets

    def _selected(self) -> tuple[str, int, int, RegionBox] | None:
        targets = self._targets()
        if not targets:
            return None
        self._selection = max(0, min(self._selection, len(targets) - 1))
        return targets[self._selection]

    def _selected_question(self) -> MappedQuestion | None:
        target = self._selected()
        if target is None or target[0] != "question":
            return None
        return self._map.questions[target[1]]

    def _describe_selection(self) -> str:
        target = self._selected()
        if target is None:
            return "Nothing selected — press a to add an answer region."
        kind, q_index, s_index, box = target
        x, y, w, h = box.bbox
        geometry = f"page {box.page}  x{x:.3f} y{y:.3f} w{w:.3f} h{h:.3f}"
        if kind == "name":
            return f"Name field — {geometry}"
        if kind == "student_id":
            return f"Student id field — {geometry}"
        question = self._map.questions[q_index]
        part = "" if s_index == 0 else f" continuation {s_index}"
        confidence = (
            "" if question.confidence is None else f"  detected {question.confidence:.0%}"
        )
        return f"{question.question_id}{part} — {geometry}{confidence}"

    # ── composition ────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="region-dialog") as dialog:
            dialog.border_title = f"▸ Region map — {self._pdf_path.name}"
            yield Static(
                "j/k select · u least sure · arrows move · shift+arrows resize · "
                "PgUp/PgDn page · a add · c continuation · d delete · "
                "f identity field · g grid · e edit · ctrl+s save · esc cancel",
                id="region-hint",
            )
            with Horizontal(id="region-body"):
                with VerticalScroll(id="region-page-pane"):
                    yield RegionCanvas(id="region-page")
                with Vertical(id="region-side"):
                    yield Static("", id="region-status")
                    yield Static("", id="region-selection")
                    yield Static("Question number", classes="region-label")
                    yield Input(placeholder="1", id="region-number")
                    yield Static("Points", classes="region-label")
                    yield Input(placeholder="10", id="region-points")
                    yield Static("Question type", classes="region-label")
                    yield Select(
                        _TYPE_OPTIONS,
                        value=FREE_RESPONSE,
                        allow_blank=False,
                        id="region-type",
                    )
                    yield Static("Prompt (optional)", classes="region-label")
                    yield Input(placeholder="short prompt", id="region-prompt")
                    yield Static("", id="region-findings")
            with Horizontal(id="region-buttons"):
                yield Button("Save", variant="primary", id="region-save", compact=True)
                yield Button("Cancel", id="region-cancel", compact=True)

    def on_mount(self) -> None:
        self.query_one("#region-findings", Static).border_title = "Findings"
        self.query_one("#region-page", RegionCanvas).focus()
        self._refresh()

    # ── redraw ─────────────────────────────────────────────────────────────

    def _base_page(self) -> tuple[object, int, int] | None:
        """The rendered page, from cache when possible.

        Rendering is the expensive part of a redraw by two orders of magnitude,
        and the page under the boxes never changes while the editor is open.
        """
        cached = self._page_cache.get(self._page)
        if cached is not None:
            return cached
        try:
            rendered = render_pdf_page(str(self._pdf_path), self._page, dpi=self._dpi)
        except Exception as exc:  # noqa: BLE001 - a bad page must not kill the editor
            self._status = f"Could not render page {self._page}: {exc}"
            return None
        self._page_cache[self._page] = rendered
        return rendered

    def _refresh(self) -> None:
        """Repaint the page, the selection line, the panel and the findings."""
        target = self._selected()
        emphasis = self._draft if self._draft is not None else (
            target[3].bbox if target is not None and target[3].page == self._page else None
        )

        canvas = self.query_one("#region-page", RegionCanvas)
        base = self._base_page()
        if base is not None:
            canvas.image = render_region_map_page(
                self._pdf_path,
                self._map,
                self._page,
                dpi=self._dpi,
                emphasis=emphasis,
                base=base,  # type: ignore[arg-type]
            )
            canvas.display = True
        else:
            canvas.display = False

        pages = max(1, self._map.source_pages)
        header = f"Page {self._page}/{pages} · {len(self._map.questions)} region(s)"
        if self._status:
            header = f"{header}\n{self._status}"
        self.query_one("#region-status", Static).update(header)
        self.query_one("#region-selection", Static).update(self._describe_selection())

        self._sync_metadata_panel()
        self._refresh_findings()

    def _sync_metadata_panel(self) -> None:
        """Show the selected question's metadata without echoing back an edit.

        Writing to an ``Input`` fires ``Input.Changed``, which is also how the
        panel *applies* an edit; without the guard, selecting a region would
        write its own values back over themselves in a loop.
        """
        question = self._selected_question()
        self._syncing = True
        try:
            number = self.query_one("#region-number", Input)
            points = self.query_one("#region-points", Input)
            prompt = self.query_one("#region-prompt", Input)
            kind = self.query_one("#region-type", Select)
            if question is None:
                number.value = ""
                points.value = ""
                prompt.value = ""
                return
            number.value = str(question.number)
            points.value = (
                "" if question.possible_points is None else f"{question.possible_points:g}"
            )
            prompt.value = question.prompt
            kind.value = question.question_type
        finally:
            self._syncing = False

    def _refresh_findings(self) -> None:
        findings = validate_region_map(self._map)
        panel = self.query_one("#region-findings", Static)
        if not findings:
            panel.update("No problems. This map is ready to save.")
            return
        lines = [
            f"{_SEVERITY_GLYPH.get(finding.severity, '·')} {finding.message}"
            for finding in findings[:_MAX_LISTED_FINDINGS]
        ]
        if len(findings) > _MAX_LISTED_FINDINGS:
            lines.append(f"…and {len(findings) - _MAX_LISTED_FINDINGS} more.")
        panel.update("\n".join(lines))

    # ── mutation helpers ───────────────────────────────────────────────────

    def _replace_selected_box(self, bbox: tuple[float, float, float, float]) -> None:
        target = self._selected()
        if target is None:
            return
        kind, q_index, s_index, box = target
        moved = RegionBox(box.page, clamp_bbox_norm(bbox))
        if kind == "name":
            self._map = replace(self._map, name_field=moved)
        elif kind == "student_id":
            self._map = replace(self._map, student_id_field=moved)
        else:
            question = self._map.questions[q_index]
            if s_index == 0:
                updated = replace(question, page=moved.page, bbox=moved.bbox)
            else:
                continuations = list(question.continuations)
                continuations[s_index - 1] = moved
                updated = replace(question, continuations=tuple(continuations))
            # A box that a human moved is no longer the detector's guess, so its
            # confidence stops being reported — leading the editor with "least
            # confident first" would otherwise keep offering corrected boxes.
            updated = replace(updated, confidence=None)
            questions = list(self._map.questions)
            questions[q_index] = updated
            self._map = replace(self._map, questions=tuple(questions))

    def _next_question_number(self) -> int:
        used = {question.number for question in self._map.questions}
        number = 1
        while number in used:
            number += 1
        return number

    # ── actions: navigation ────────────────────────────────────────────────

    def action_select_next(self) -> None:
        targets = self._targets()
        if not targets:
            return
        self._selection = (self._selection + 1) % len(targets)
        self._follow_selection()

    def action_select_previous(self) -> None:
        targets = self._targets()
        if not targets:
            return
        self._selection = (self._selection - 1) % len(targets)
        self._follow_selection()

    def _follow_selection(self) -> None:
        """Turn to the page the newly selected box is on, then repaint.

        Selecting something invisible would be a cursor that vanishes; the page
        follows the selection rather than the other way round.
        """
        target = self._selected()
        if target is not None:
            self._page = target[3].page
        self._status = ""
        self._refresh()

    def action_select_least_confident(self) -> None:
        """Jump to the detected box the detector was least sure of.

        Correcting a proposal is not a linear job: a run of confidently detected
        questions needs a glance, and the one the detector scored at 40% needs
        the work. This walks straight to it, and to the next-worst after that,
        so an instructor spends their attention where it changes the outcome.
        Boxes a human has already moved carry no confidence and are skipped —
        they have been reviewed by definition.
        """
        ranked = sorted(
            (
                (question.confidence, index)
                for index, question in enumerate(self._map.questions)
                if question.confidence is not None
            ),
            key=lambda entry: entry[0],
        )
        if not ranked:
            self._status = "Every region has been reviewed by hand."
            self._refresh()
            return
        current = self._selected()
        current_index = current[1] if current is not None and current[0] == "question" else -1
        # Advance past the current one so repeated presses walk up the list.
        for _confidence, index in ranked:
            if index != current_index:
                self._select_box_of(index, 0)
                break
        self._follow_selection()

    def action_next_page(self) -> None:
        if self._page < max(1, self._map.source_pages):
            self._page += 1
            self._status = ""
            self._refresh()

    def action_previous_page(self) -> None:
        if self._page > 1:
            self._page -= 1
            self._status = ""
            self._refresh()

    # ── actions: geometry ──────────────────────────────────────────────────

    def action_nudge(self, direction: str) -> None:
        target = self._selected()
        if target is None:
            return
        x, y, w, h = target[3].bbox
        dx = NUDGE_STEP * (1 if direction == "right" else -1 if direction == "left" else 0)
        dy = NUDGE_STEP * (1 if direction == "down" else -1 if direction == "up" else 0)
        # Clamp the *origin* against the box's own size, so pushing a box at the
        # edge slides it flush instead of quietly shrinking it.
        self._replace_selected_box(
            (
                max(0.0, min(1.0 - w, x + dx)),
                max(0.0, min(1.0 - h, y + dy)),
                w,
                h,
            )
        )
        self._refresh()

    def action_resize(self, direction: str) -> None:
        target = self._selected()
        if target is None:
            return
        x, y, w, h = target[3].bbox
        if direction in ("left", "right"):
            step = NUDGE_STEP if direction == "right" else -NUDGE_STEP
            w = max(MIN_REGION_SIZE, min(1.0 - x, w + step))
        else:
            step = NUDGE_STEP if direction == "down" else -NUDGE_STEP
            h = max(MIN_REGION_SIZE, min(1.0 - y, h + step))
        self._replace_selected_box((x, y, w, h))
        self._refresh()

    # ── actions: structure ─────────────────────────────────────────────────

    def action_add_region(self) -> None:
        """Add an answer region to the current page, centred and selected."""
        width, height = NEW_REGION_SIZE
        number = self._next_question_number()
        question = MappedQuestion(
            question_id=f"q{number}",
            number=number,
            page=self._page,
            bbox=clamp_bbox_norm(((1 - width) / 2, (1 - height) / 2, width, height)),
        )
        self._map = replace(self._map, questions=self._map.questions + (question,))
        self._select_box_of(len(self._map.questions) - 1, 0)
        self._status = f"Added {question.question_id} on page {self._page}."
        self._refresh()

    def action_add_continuation(self) -> None:
        """Continue the selected question's answer onto the current page."""
        target = self._selected()
        if target is None or target[0] != "question":
            self._status = "Select an answer region first, then press c."
            self._refresh()
            return
        q_index = target[1]
        question = self._map.questions[q_index]
        width, height = NEW_REGION_SIZE
        segment = RegionBox(
            self._page, clamp_bbox_norm(((1 - width) / 2, 0.08, width, height))
        )
        questions = list(self._map.questions)
        questions[q_index] = replace(
            question, continuations=question.continuations + (segment,)
        )
        self._map = replace(self._map, questions=tuple(questions))
        self._select_box_of(q_index, len(questions[q_index].segments) - 1)
        self._status = (
            f"{question.question_id} now continues onto page {self._page}; its crops "
            "are stacked into one answer image."
        )
        self._refresh()

    def action_delete_region(self) -> None:
        target = self._selected()
        if target is None:
            return
        kind, q_index, s_index, _box = target
        if kind == "name":
            self._map = replace(self._map, name_field=None)
            self._status = "Removed the name field."
        elif kind == "student_id":
            self._map = replace(self._map, student_id_field=None)
            self._status = "Removed the student-id field."
        else:
            questions = list(self._map.questions)
            question = questions[q_index]
            if s_index == 0:
                self._status = f"Deleted {question.question_id}."
                questions.pop(q_index)
            else:
                continuations = list(question.continuations)
                continuations.pop(s_index - 1)
                questions[q_index] = replace(
                    question, continuations=tuple(continuations)
                )
                self._status = f"Removed a continuation of {question.question_id}."
            self._map = replace(self._map, questions=tuple(questions))
        self._selection = max(0, self._selection - 1)
        self._refresh()

    def action_add_identity_field(self) -> None:
        """Add whichever identity field is still missing, on this page.

        One key rather than two, because there are only ever two of them and the
        order they are wanted in is always the same.
        """
        box = RegionBox(self._page, (0.10, 0.05, 0.45, 0.06))
        if self._map.name_field is None:
            self._map = replace(self._map, name_field=box)
            self._status = "Added the name field — move it over the name blank."
        elif self._map.student_id_field is None:
            self._map = replace(
                self._map, student_id_field=RegionBox(self._page, (0.58, 0.05, 0.32, 0.06))
            )
            self._status = "Added the student-id field."
        else:
            self._status = "Both identity fields are already mapped."
        self._refresh()

    def action_fill_grid(self) -> None:
        """Replace the map with an evenly spaced grid — the from-scratch path.

        First-class rather than a fallback: plenty of assignments *are* four equal
        answer boxes to a page, and on one the detector cannot read at all this
        is the fastest route to a usable map.
        """
        try:
            self._map = grid_region_map(
                self._pdf_path,
                per_page=4,
                assignment_title=self._map.assignment_title,
            )
        except RegionMapError as exc:
            self._status = str(exc)
        else:
            self._status = "Laid out a 4-per-page grid; nudge the boxes into place."
        self._selection = 0
        self._refresh()

    def _select_box_of(self, question_index: int, segment_index: int) -> None:
        for index, (kind, q_index, s_index, _box) in enumerate(self._targets()):
            if kind == "question" and q_index == question_index and s_index == segment_index:
                self._selection = index
                return

    def action_edit_metadata(self) -> None:
        self.query_one("#region-number", Input).focus()

    def action_open_externally(self) -> None:
        """Open the current page in the desktop viewer — the no-graphics hatch.

        The same escape valve the grade screen offers: in a terminal that cannot
        draw images, this is how an instructor actually sees where the boxes
        landed while editing them.
        """
        import tempfile

        from opengrader.tui.external_viewer import (
            ExternalViewerError,
            open_in_external_viewer,
        )

        base = self._base_page()
        if base is None:
            return
        target = self._selected()
        image = render_region_map_page(
            self._pdf_path,
            self._map,
            self._page,
            dpi=self._dpi,
            emphasis=target[3].bbox if target is not None else None,
            base=base,  # type: ignore[arg-type]
        )
        path = Path(tempfile.gettempdir()) / f"opengrader-regions-p{self._page}.png"
        image.save(path)
        try:
            self._status = open_in_external_viewer(path)
        except ExternalViewerError as exc:
            self._status = str(exc)
        self._refresh()

    # ── metadata panel ─────────────────────────────────────────────────────

    _syncing = False

    def _update_selected_question(self, **changes) -> None:
        target = self._selected()
        if target is None or target[0] != "question":
            return
        q_index = target[1]
        questions = list(self._map.questions)
        questions[q_index] = replace(questions[q_index], **changes)
        self._map = replace(self._map, questions=tuple(questions))
        self.query_one("#region-selection", Static).update(self._describe_selection())
        self._refresh_findings()

    @on(Input.Changed, "#region-number")
    def _on_number_changed(self, event: Input.Changed) -> None:
        event.stop()
        if self._syncing:
            return
        raw = event.value.strip()
        if not raw.isdigit():
            return
        number = int(raw)
        # The id follows the number for the default `q<N>` ids, because those
        # two disagreeing is how a crop ends up filed under the wrong rubric
        # entry. A hand-written id (`q3a`) is left alone.
        question = self._selected_question()
        changes: dict[str, object] = {"number": number}
        if question is not None and question.question_id == f"q{question.number}":
            changes["question_id"] = f"q{number}"
        self._update_selected_question(**changes)

    @on(Input.Changed, "#region-points")
    def _on_points_changed(self, event: Input.Changed) -> None:
        event.stop()
        if self._syncing:
            return
        raw = event.value.strip()
        if not raw:
            self._update_selected_question(possible_points=None)
            return
        try:
            self._update_selected_question(possible_points=float(raw))
        except ValueError:
            return

    @on(Input.Changed, "#region-prompt")
    def _on_prompt_changed(self, event: Input.Changed) -> None:
        event.stop()
        if self._syncing:
            return
        self._update_selected_question(prompt=event.value)

    @on(Select.Changed, "#region-type")
    def _on_type_changed(self, event: Select.Changed) -> None:
        event.stop()
        if self._syncing or event.value is None:
            return
        self._update_selected_question(question_type=event.value)

    # ── mouse ──────────────────────────────────────────────────────────────

    @on(RegionCanvas.BoxDragging)
    def _on_box_dragging(self, event: RegionCanvas.BoxDragging) -> None:
        event.stop()
        self._draft = event.bbox
        self._refresh()

    @on(RegionCanvas.BoxDrawn)
    def _on_box_drawn(self, event: RegionCanvas.BoxDrawn) -> None:
        event.stop()
        self._draft = None
        number = self._next_question_number()
        question = MappedQuestion(
            question_id=f"q{number}",
            number=number,
            page=self._page,
            bbox=event.bbox,
        )
        self._map = replace(self._map, questions=self._map.questions + (question,))
        self._select_box_of(len(self._map.questions) - 1, 0)
        self._status = f"Drew {question.question_id} on page {self._page}."
        self._refresh()

    # ── save / cancel ──────────────────────────────────────────────────────

    def action_save(self) -> None:
        """Write the map, unless a finding says it would crop the wrong thing.

        Warnings pass: an unmapped extra-credit question, or a box that runs to
        the paper's edge, are things an instructor may well mean. Errors do not:
        a duplicate id overwrites another question's crop and an empty box
        produces nothing at all, and neither is recoverable after 200 scans have
        been extracted against them.
        """
        errors = region_map_errors(validate_region_map(self._map))
        if errors:
            self._status = (
                f"Cannot save: {errors[0].message} "
                f"({len(errors)} problem(s) must be fixed first.)"
            )
            self._refresh()
            self.app.notify(errors[0].message, severity="error")
            return
        try:
            written = save_region_map(self._map, self._output_path)
        except OSError as exc:
            self._status = f"Could not write {self._output_path}: {exc}"
            self._refresh()
            return
        self.dismiss(written)

    def action_cancel(self) -> None:
        """Leave without saving — unless escape was meant for a text field.

        Textual's ``Input`` does not consume escape, so without this the key that
        gets a user out of a half-typed point value would throw away every box
        they had drawn.
        """
        focused = self.focused
        if isinstance(focused, Input):
            self.query_one("#region-page", RegionCanvas).focus()
            return
        self.dismiss(None)

    @on(Button.Pressed, "#region-save")
    def _on_save_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_save()

    @on(Button.Pressed, "#region-cancel")
    def _on_cancel_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)
