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
from opengrader.tui.ui.button_roles import OutlineButtons

NUDGE_STEP = 0.005

MIN_REGION_SIZE = 0.01

_MAX_LISTED_FINDINGS = 6

_TYPE_OPTIONS = [
    ("Free response", FREE_RESPONSE),
    ("Multiple choice", MULTIPLE_CHOICE),
    ("Select all", MULTIPLE_ANSWER),
    ("Numeric", NUMERIC),
]

_SEVERITY_GLYPH = {"error": "●", "warning": "◐", "info": "○"}


class RegionCanvas(Widget):
    """SharpImage cannot be subclassed without renderer-specific arguments."""

    DEFAULT_CSS = """
    RegionCanvas {
        width: 100%;
        height: auto;
    }
    """

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
    def image(self):  # noqa: ANN201
        return self._image_widget.image

    @image.setter
    def image(self, value) -> None:  # noqa: ANN001
        self._image_widget.image = value

    def _normalized(self, screen_x: int, screen_y: int) -> tuple[float, float]:
        try:
            region = self._image_widget.region
        except Exception:  # pragma: no cover
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
        if bbox[2] >= MIN_REGION_SIZE and bbox[3] >= MIN_REGION_SIZE:
            self.post_message(self.BoxDrawn(bbox))
        event.stop()


class RegionMapScreen(OutlineButtons, ModalScreen["Path | None"]):
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
        self._page_cache: dict[int, tuple[object, int, int]] = {}
        self._map = self._initial_map()
        super().__init__()


    def _initial_map(self) -> RegionMap:
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


    def _targets(self) -> list[tuple[str, int, int, RegionBox]]:
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


    def _base_page(self) -> tuple[object, int, int] | None:
        cached = self._page_cache.get(self._page)
        if cached is not None:
            return cached
        try:
            rendered = render_pdf_page(str(self._pdf_path), self._page, dpi=self._dpi)
        except Exception as exc:  # noqa: BLE001
            self._status = f"Could not render page {self._page}: {exc}"
            return None
        self._page_cache[self._page] = rendered
        return rendered

    def _refresh(self) -> None:
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
        target = self._selected()
        if target is not None:
            self._page = target[3].page
        self._status = ""
        self._refresh()

    def action_select_least_confident(self) -> None:
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


    def action_nudge(self, direction: str) -> None:
        target = self._selected()
        if target is None:
            return
        x, y, w, h = target[3].bbox
        dx = NUDGE_STEP * (1 if direction == "right" else -1 if direction == "left" else 0)
        dy = NUDGE_STEP * (1 if direction == "down" else -1 if direction == "up" else 0)
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


    def action_add_region(self) -> None:
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


    def action_save(self) -> None:
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
