from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path
from typing import Literal

import cv2
import fitz
import numpy as np

from opengrader.core.ocr.editor import (
    EditorRegion,
    apply_editor_regions_to_spec,
    bbox_norm_to_pixels,
    bbox_pixels_to_norm,
    regions_by_page,
    render_master_template_pdf,
    render_review_pdf,
    spec_to_editor_regions,
)
from opengrader.core.ocr.extractor import (
    detect_version_from_anchors,
    render_page_to_image,
)
from opengrader.core.ocr.spec import load_spec, version_anchor_map

EPILOG = """
examples:
  uv run ocr-preview-pdf --input-pdf template.pdf --input-spec draft_spec.json --output-spec final_spec.json --output-pdf review_overlay.pdf
  uv run ocr-preview-pdf --input-pdf template.pdf --input-spec draft_spec.json --output-spec final_spec.json --output-master-pdf master_template.pdf
"""


MOVE_SMALL = 1
MOVE_LARGE = 10
RESIZE_SMALL = 2
RESIZE_LARGE = 12
MIN_SIZE_PX = 12
HANDLE_RADIUS = 7
HandleType = Literal["tl", "tr", "bl", "br"]


def _color_for_region(region_type: str) -> tuple[int, int, int]:
    if region_type == "field":
        return (235, 140, 60)
    if region_type == "question":
        return (65, 185, 90)
    return (30, 130, 235)


def _point_in_rect(x: int, y: int, rect: tuple[int, int, int, int]) -> bool:
    x0, y0, x1, y1 = rect
    return x0 <= x <= x1 and y0 <= y <= y1


def _rect_corners(rect: tuple[int, int, int, int]) -> dict[HandleType, tuple[int, int]]:
    x0, y0, x1, y1 = rect
    return {
        "tl": (x0, y0),
        "tr": (x1, y0),
        "bl": (x0, y1),
        "br": (x1, y1),
    }


def _hit_resize_handle(
    x: int, y: int, rect: tuple[int, int, int, int]
) -> HandleType | None:
    for name, (cx, cy) in _rect_corners(rect).items():
        if abs(x - cx) <= HANDLE_RADIUS and abs(y - cy) <= HANDLE_RADIUS:
            return name
    return None


def _clamp_rect(
    rect: tuple[int, int, int, int],
    width: int,
    height: int,
    min_size: int = MIN_SIZE_PX,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = rect
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))

    if x1 - x0 < min_size:
        x1 = x0 + min_size
    if y1 - y0 < min_size:
        y1 = y0 + min_size

    if x0 < 0:
        dx = -x0
        x0 += dx
        x1 += dx
    if y0 < 0:
        dy = -y0
        y0 += dy
        y1 += dy
    if x1 > width:
        dx = x1 - width
        x0 -= dx
        x1 -= dx
    if y1 > height:
        dy = y1 - height
        y0 -= dy
        y1 -= dy

    x0 = max(0, x0)
    y0 = max(0, y0)
    x1 = min(width, x1)
    y1 = min(height, y1)

    if x1 - x0 < min_size:
        x1 = min(width, x0 + min_size)
        x0 = max(0, x1 - min_size)
    if y1 - y0 < min_size:
        y1 = min(height, y0 + min_size)
        y0 = max(0, y1 - min_size)
    return x0, y0, x1, y1


def _move_rect(
    rect: tuple[int, int, int, int],
    dx: int,
    dy: int,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = rect
    moved = (x0 + dx, y0 + dy, x1 + dx, y1 + dy)
    return _clamp_rect(moved, width, height)


def _resize_rect(
    rect: tuple[int, int, int, int],
    handle: HandleType,
    dx: int,
    dy: int,
    width: int,
    height: int,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = rect
    if handle == "tl":
        x0 += dx
        y0 += dy
    elif handle == "tr":
        x1 += dx
        y0 += dy
    elif handle == "bl":
        x0 += dx
        y1 += dy
    elif handle == "br":
        x1 += dx
        y1 += dy
    return _clamp_rect((x0, y0, x1, y1), width, height)


class LiveRegionEditor:
    def __init__(
        self,
        doc: fitz.Document,
        regions: list[EditorRegion],
        dpi: int = 150,
        max_width: int = 1400,
        detected_version: str | None = None,
        expected_versions: list[str] | None = None,
    ) -> None:
        self.doc = doc
        self.regions = regions
        self.dpi = dpi
        self.max_width = max_width
        self.window_name = "OCR Region Editor"
        self.detected_version = detected_version
        self.expected_versions = expected_versions or []

        grouped = regions_by_page(regions)
        self.page_to_regions = grouped
        self.page_numbers = sorted(grouped.keys())
        if not self.page_numbers:
            raise ValueError("Spec has no editable regions")

        self.current_page_pos = 0
        self.selected_index_by_page = {pn: 0 for pn in self.page_numbers}

        self.base_images: dict[int, np.ndarray] = {}
        self.drag_mode: Literal["none", "move", "resize"] = "none"
        self.resize_handle: HandleType | None = None
        self.drag_start: tuple[int, int] | None = None
        self.drag_origin_rect: tuple[int, int, int, int] | None = None

    def _current_page_number(self) -> int:
        return self.page_numbers[self.current_page_pos]

    def _current_regions(self) -> list[EditorRegion]:
        return self.page_to_regions[self._current_page_number()]

    def _ensure_selected_index(self) -> None:
        pn = self._current_page_number()
        page_regions = self._current_regions()
        current = self.selected_index_by_page.get(pn, 0)
        if not page_regions:
            self.selected_index_by_page[pn] = 0
            return
        if current >= len(page_regions) or current < 0:
            self.selected_index_by_page[pn] = 0

    def _selected_region(self) -> EditorRegion:
        self._ensure_selected_index()
        pn = self._current_page_number()
        return self._current_regions()[self.selected_index_by_page[pn]]

    def _set_selected_region(self, region: EditorRegion) -> None:
        pn = self._current_page_number()
        page_regions = self._current_regions()
        for i, candidate in enumerate(page_regions):
            if candidate is region:
                self.selected_index_by_page[pn] = i
                return

    def _version_status_text(self) -> str:
        detected = (
            self.detected_version
            if self.detected_version is not None
            else "(none detected)"
        )

        if self.detected_version:
            expected = self.detected_version
        elif len(self.expected_versions) == 1:
            expected = self.expected_versions[0]
        elif self.expected_versions:
            expected = ",".join(self.expected_versions)
        else:
            expected = "(none)"

        return f"version={detected} expected={expected}"

    def _render_page(self, page_number: int) -> np.ndarray:
        if page_number in self.base_images:
            return self.base_images[page_number]

        page = self.doc[page_number - 1]
        zoom = self.dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n
        )
        if pix.n == 4:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
        else:
            arr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

        if self.max_width > 0 and arr.shape[1] > self.max_width:
            scale = self.max_width / float(arr.shape[1])
            new_w = int(round(arr.shape[1] * scale))
            new_h = int(round(arr.shape[0] * scale))
            arr = cv2.resize(arr, (new_w, new_h), interpolation=cv2.INTER_AREA)

        self.base_images[page_number] = arr
        return arr

    def _region_rect_px(
        self, region: EditorRegion, image: np.ndarray
    ) -> tuple[int, int, int, int]:
        h, w = image.shape[:2]
        return bbox_norm_to_pixels(region.bbox_norm, w, h)

    def _update_region_from_rect(
        self,
        region: EditorRegion,
        rect: tuple[int, int, int, int],
        image: np.ndarray,
    ) -> None:
        h, w = image.shape[:2]
        region.bbox_norm = bbox_pixels_to_norm(rect, w, h)

    def _cycle_region(self, direction: int) -> None:
        pn = self._current_page_number()
        page_regions = self._current_regions()
        if not page_regions:
            return
        current = self.selected_index_by_page[pn]
        self.selected_index_by_page[pn] = (current + direction) % len(page_regions)

    def _next_page(self) -> None:
        self.current_page_pos = (self.current_page_pos + 1) % len(self.page_numbers)
        self._ensure_selected_index()

    def _prev_page(self) -> None:
        self.current_page_pos = (self.current_page_pos - 1) % len(self.page_numbers)
        self._ensure_selected_index()

    def _draw_ui(self, image: np.ndarray) -> np.ndarray:
        canvas = image.copy()
        overlay = canvas.copy()
        page_regions = self._current_regions()
        selected = self._selected_region()

        for region in page_regions:
            x0, y0, x1, y1 = self._region_rect_px(region, image)
            color = _color_for_region(region.region_type)
            cv2.rectangle(overlay, (x0, y0), (x1, y1), color, thickness=-1)

        cv2.addWeighted(overlay, 0.18, canvas, 0.82, 0.0, canvas)

        for region in page_regions:
            x0, y0, x1, y1 = self._region_rect_px(region, image)
            color = _color_for_region(region.region_type)
            thickness = 2 if region is selected else 1
            cv2.rectangle(canvas, (x0, y0), (x1, y1), color, thickness=thickness)
            label = f"{region.region_type}:{region.region_id}"
            if region.locked:
                label = f"{label} [LOCK]"
            cv2.putText(
                canvas,
                label,
                (x0 + 2, max(12, y0 - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.43,
                color,
                1,
                cv2.LINE_AA,
            )

            if region is selected:
                for cx, cy in _rect_corners((x0, y0, x1, y1)).values():
                    cv2.circle(canvas, (cx, cy), 4, color, thickness=-1)

        page_num = self._current_page_number()
        status = (
            f"Page {page_num}/{self.page_numbers[-1]}  "
            f"selected={selected.region_type}:{selected.region_id}  "
            f"locked={'yes' if selected.locked else 'no'}  "
            f"{self._version_status_text()}"
        )
        keymap = (
            "Mouse: click/drag move, drag corners resize | "
            "n/p next-prev region | [] prev-next page | arrows move | "
            "WASD fast move | -/= resize | s save+exit | q quit"
        )
        cv2.rectangle(
            canvas, (0, 0), (canvas.shape[1], 44), (245, 245, 245), thickness=-1
        )
        cv2.putText(
            canvas,
            status,
            (8, 16),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (20, 20, 20),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            canvas,
            keymap,
            (8, 34),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (30, 30, 30),
            1,
            cv2.LINE_AA,
        )

        return canvas

    def _on_mouse(
        self, event: int, x: int, y: int, _flags: int, _param: object
    ) -> None:
        image = self._render_page(self._current_page_number())
        page_regions = self._current_regions()

        if event == cv2.EVENT_LBUTTONDOWN:
            hit_region: EditorRegion | None = None
            hit_rect: tuple[int, int, int, int] | None = None
            for region in sorted(
                page_regions,
                key=lambda r: (r.bbox_norm[2] * r.bbox_norm[3], r.locked),
            ):
                rect = self._region_rect_px(region, image)
                if _point_in_rect(x, y, rect):
                    hit_region = region
                    hit_rect = rect
                    break

            if hit_region is None or hit_rect is None:
                return

            self._set_selected_region(hit_region)

            if hit_region.locked:
                self.drag_mode = "none"
                self.resize_handle = None
                return

            handle = _hit_resize_handle(x, y, hit_rect)
            self.drag_mode = "resize" if handle else "move"
            self.resize_handle = handle if handle else None
            self.drag_start = (x, y)
            self.drag_origin_rect = hit_rect
            return

        if event == cv2.EVENT_MOUSEMOVE:
            if (
                self.drag_mode == "none"
                or self.drag_start is None
                or self.drag_origin_rect is None
            ):
                return

            selected = self._selected_region()
            if selected.locked:
                return

            dx = x - self.drag_start[0]
            dy = y - self.drag_start[1]
            h, w = image.shape[:2]

            if self.drag_mode == "move":
                rect = _move_rect(self.drag_origin_rect, dx, dy, w, h)
            else:
                if self.resize_handle is None:
                    return
                rect = _resize_rect(
                    self.drag_origin_rect, self.resize_handle, dx, dy, w, h
                )
            self._update_region_from_rect(selected, rect, image)
            return

        if event == cv2.EVENT_LBUTTONUP:
            self.drag_mode = "none"
            self.resize_handle = None
            self.drag_start = None
            self.drag_origin_rect = None

    def _apply_keyboard_move(self, dx: int, dy: int) -> None:
        selected = self._selected_region()
        if selected.locked:
            return
        image = self._render_page(self._current_page_number())
        h, w = image.shape[:2]
        rect = self._region_rect_px(selected, image)
        moved = _move_rect(rect, dx, dy, w, h)
        self._update_region_from_rect(selected, moved, image)

    def _apply_keyboard_resize(self, dw: int, dh: int) -> None:
        selected = self._selected_region()
        if selected.locked:
            return
        image = self._render_page(self._current_page_number())
        h, w = image.shape[:2]
        x0, y0, x1, y1 = self._region_rect_px(selected, image)
        resized = _clamp_rect((x0, y0, x1 + dw, y1 + dh), w, h)
        self._update_region_from_rect(selected, resized, image)

    def run(self) -> bool:
        cv2.namedWindow(self.window_name, cv2.WINDOW_NORMAL)
        cv2.setMouseCallback(self.window_name, self._on_mouse)

        key_left = {81, 2424832, 65361}
        key_up = {82, 2490368, 65362}
        key_right = {83, 2555904, 65363}
        key_down = {84, 2621440, 65364}

        # TODO: this is a little jank
        try:
            while True:
                image = self._render_page(self._current_page_number())
                frame = self._draw_ui(image)
                cv2.imshow(self.window_name, frame)
                key = cv2.waitKeyEx(16)
                if key == -1:
                    continue

                if key in (27, ord("q"), ord("Q")):
                    return False
                if key in (ord("s"), ord("S")):
                    return True

                if key in (ord("n"), ord("N"), 9):
                    self._cycle_region(1)
                    continue
                if key in (ord("p"), ord("P"), 353):
                    self._cycle_region(-1)
                    continue

                if key in (ord("["), ord("{")):
                    self._prev_page()
                    continue
                if key in (ord("]"), ord("}")):
                    self._next_page()
                    continue

                if key in key_left:
                    self._apply_keyboard_move(-MOVE_SMALL, 0)
                    continue
                if key in key_right:
                    self._apply_keyboard_move(MOVE_SMALL, 0)
                    continue
                if key in key_up:
                    self._apply_keyboard_move(0, -MOVE_SMALL)
                    continue
                if key in key_down:
                    self._apply_keyboard_move(0, MOVE_SMALL)
                    continue

                if key in (ord("a"), ord("A")):
                    self._apply_keyboard_move(-MOVE_LARGE, 0)
                    continue
                if key in (ord("d"), ord("D")):
                    self._apply_keyboard_move(MOVE_LARGE, 0)
                    continue
                if key in (ord("w"), ord("W")):
                    self._apply_keyboard_move(0, -MOVE_LARGE)
                    continue
                if key in (ord("x"), ord("X")):
                    self._apply_keyboard_move(0, MOVE_LARGE)
                    continue

                if key in (ord("-"), ord("_")):
                    self._apply_keyboard_resize(-RESIZE_SMALL, -RESIZE_SMALL)
                    continue
                if key in (ord("="), ord("+")):
                    self._apply_keyboard_resize(RESIZE_SMALL, RESIZE_SMALL)
                    continue
                if key in (ord(","), ord("<")):
                    self._apply_keyboard_resize(-RESIZE_LARGE, 0)
                    continue
                if key in (ord("."), ord(">")):
                    self._apply_keyboard_resize(RESIZE_LARGE, 0)
                    continue
                if key in (ord(";"), ord(":")):
                    self._apply_keyboard_resize(0, -RESIZE_LARGE)
                    continue
                if key in (ord("'"), ord('"')):
                    self._apply_keyboard_resize(0, RESIZE_LARGE)
                    continue
        finally:
            cv2.destroyWindow(self.window_name)


def run_cli() -> None:
    parser = ArgumentParser(
        description=(
            "Interactive OCR region editor for manual second-pass review. "
            "Anchors are locked and preserved for version detection."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input-pdf", required=True, help="Path to the source template PDF."
    )
    parser.add_argument(
        "--input-spec", required=True, help="Path to the input spec JSON."
    )
    parser.add_argument(
        "--output-spec",
        required=True,
        help="Path to write the updated spec JSON after manual edits.",
    )
    parser.add_argument(
        "--output-pdf",
        required=False,
        help="Path to write the review PDF with overlays and outline removal.",
    )
    parser.add_argument(
        "--output-master-pdf",
        required=False,
        help="Path to write the final clean template PDF (no overlays, no region outlines).",
    )
    parser.add_argument(
        "--dpi", type=int, default=150, help="Editor rendering DPI (default: 150)."
    )
    parser.add_argument(
        "--max-width",
        type=int,
        default=1400,
        help="Max preview width in pixels for the editor window (default: 1400).",
    )
    parser.add_argument(
        "--erase-border-width",
        type=float,
        default=1.2,
        help="PDF border erase width in points for non-anchor regions (default: 1.2).",
    )
    args = parser.parse_args()

    input_pdf = Path(args.input_pdf)
    input_spec = Path(args.input_spec)
    output_spec = Path(args.output_spec)
    output_pdf = Path(args.output_pdf) if args.output_pdf else None
    output_master_pdf = Path(args.output_master_pdf) if args.output_master_pdf else None

    if not input_pdf.exists() or input_pdf.suffix.lower() != ".pdf":
        print(f"Error: input PDF not found or invalid: {input_pdf}", file=sys.stderr)
        sys.exit(1)
    if not input_spec.exists():
        print(f"Error: input spec not found: {input_spec}", file=sys.stderr)
        sys.exit(1)
    if output_pdf is None and output_master_pdf is None:
        print(
            "Error: provide at least one of --output-pdf or --output-master-pdf.",
            file=sys.stderr,
        )
        sys.exit(1)

    spec = load_spec(input_spec)
    regions = spec_to_editor_regions(spec)
    if not regions:
        print("Error: no regions found in spec.", file=sys.stderr)
        sys.exit(1)

    print("Opening live editor window...")
    print("Controls: mouse or keyboard. Press 's' to save, 'q' to quit without saving.")

    expected_versions = sorted(version_anchor_map(spec).keys())

    detected_version = None
    doc = fitz.open(str(input_pdf))
    try:
        if expected_versions and len(doc) > 0:
            page_image, _, _ = render_page_to_image(doc, 0, dpi=args.dpi)
            first_page_anchors = (
                spec.get("pages", [])[0].get("anchors", []) if spec.get("pages") else []
            )
            detected_version = detect_version_from_anchors(
                page_image, first_page_anchors, spec
            )

        editor = LiveRegionEditor(
            doc,
            regions,
            dpi=args.dpi,
            max_width=args.max_width,
            detected_version=detected_version,
            expected_versions=expected_versions,
        )
        should_save = editor.run()
    finally:
        doc.close()

    if not should_save:
        print("No changes saved.")
        sys.exit(0)

    updated_spec = apply_editor_regions_to_spec(spec, regions)
    output_spec.parent.mkdir(parents=True, exist_ok=True)
    with open(output_spec, "w") as f:
        json.dump(updated_spec, f, indent=2)

    if output_pdf is not None:
        render_review_pdf(
            input_pdf_path=input_pdf,
            output_pdf_path=output_pdf,
            regions=regions,
            erase_border_width=args.erase_border_width,
        )

    if output_master_pdf is not None:
        render_master_template_pdf(
            input_pdf_path=input_pdf,
            output_pdf_path=output_master_pdf,
            regions=regions,
            erase_border_width=args.erase_border_width,
        )

    print(f"Wrote updated spec: {output_spec}")
    if output_pdf is not None:
        print(f"Wrote review overlay PDF: {output_pdf}")
    if output_master_pdf is not None:
        print(f"Wrote final master template PDF: {output_master_pdf}")


if __name__ == "__main__":
    run_cli()
