from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import fitz


RegionType = Literal["anchor", "field", "question"]


@dataclass
class EditorRegion:
    page_number: int
    region_id: str
    region_type: RegionType
    bbox_norm: list[float]
    locked: bool
    source_index: int


def clamp_bbox_norm(
    bbox: list[float],
    min_width: float = 0.005,
    min_height: float = 0.005,
) -> list[float]:
    x, y, w, h = [float(v) for v in bbox]

    min_width = max(0.0, min(min_width, 1.0))
    min_height = max(0.0, min(min_height, 1.0))

    w = max(min_width, min(w, 1.0))
    h = max(min_height, min(h, 1.0))

    max_x = max(0.0, 1.0 - w)
    max_y = max(0.0, 1.0 - h)
    x = min(max(0.0, x), max_x)
    y = min(max(0.0, y), max_y)
    return [x, y, w, h]


def bbox_norm_to_pixels(
    bbox: list[float],
    page_width_px: int,
    page_height_px: int,
) -> tuple[int, int, int, int]:
    x, y, w, h = clamp_bbox_norm(bbox)
    x0 = int(round(x * page_width_px))
    y0 = int(round(y * page_height_px))
    x1 = int(round((x + w) * page_width_px))
    y1 = int(round((y + h) * page_height_px))
    x1 = max(x1, x0 + 1)
    y1 = max(y1, y0 + 1)
    return x0, y0, x1, y1


def bbox_pixels_to_norm(
    rect_px: tuple[int, int, int, int],
    page_width_px: int,
    page_height_px: int,
) -> list[float]:
    x0, y0, x1, y1 = rect_px
    left = min(x0, x1)
    top = min(y0, y1)
    right = max(x0, x1)
    bottom = max(y0, y1)

    x = left / float(page_width_px)
    y = top / float(page_height_px)
    w = (right - left) / float(page_width_px)
    h = (bottom - top) / float(page_height_px)
    return clamp_bbox_norm([x, y, w, h])


def spec_to_editor_regions(spec: dict) -> list[EditorRegion]:
    regions: list[EditorRegion] = []
    for page in spec.get("pages", []):
        page_number = int(page["page_number"])

        for idx, anchor in enumerate(page.get("anchors", [])):
            regions.append(
                EditorRegion(
                    page_number=page_number,
                    region_id=str(anchor.get("id", f"anchor_{idx + 1}")),
                    region_type="anchor",
                    bbox_norm=clamp_bbox_norm(anchor["bbox"]),
                    locked=True,
                    source_index=idx,
                )
            )

        for idx, field in enumerate(page.get("fields", [])):
            regions.append(
                EditorRegion(
                    page_number=page_number,
                    region_id=str(field["id"]),
                    region_type="field",
                    bbox_norm=clamp_bbox_norm(field["bbox"]),
                    locked=False,
                    source_index=idx,
                )
            )

        for idx, question in enumerate(page.get("questions", [])):
            regions.append(
                EditorRegion(
                    page_number=page_number,
                    region_id=str(question["id"]),
                    region_type="question",
                    bbox_norm=clamp_bbox_norm(question["bbox"]),
                    locked=False,
                    source_index=idx,
                )
            )

    return regions


def apply_editor_regions_to_spec(
    spec: dict,
    regions: list[EditorRegion],
    precision: int = 4,
) -> dict:
    updated = copy.deepcopy(spec)
    pages_by_number = {int(p["page_number"]): p for p in updated.get("pages", [])}

    for region in regions:
        if region.region_type == "anchor" or region.locked:
            continue

        page = pages_by_number.get(region.page_number)
        if page is None:
            continue

        collection_key = "fields" if region.region_type == "field" else "questions"
        collection = page.get(collection_key, [])
        if region.source_index < 0 or region.source_index >= len(collection):
            continue

        clamped = clamp_bbox_norm(region.bbox_norm)
        collection[region.source_index]["bbox"] = [
            round(clamped[0], precision),
            round(clamped[1], precision),
            round(clamped[2], precision),
            round(clamped[3], precision),
        ]

    return updated


def regions_by_page(regions: list[EditorRegion]) -> dict[int, list[EditorRegion]]:
    grouped: dict[int, list[EditorRegion]] = {}
    for region in regions:
        grouped.setdefault(region.page_number, []).append(region)

    for page_regions in grouped.values():
        page_regions.sort(key=lambda r: (r.locked, r.bbox_norm[1], r.bbox_norm[0]))
    return grouped


def bbox_norm_to_fitz_rect(bbox: list[float], page_rect: fitz.Rect) -> fitz.Rect:
    x, y, w, h = clamp_bbox_norm(bbox)
    x0 = page_rect.x0 + x * page_rect.width
    y0 = page_rect.y0 + y * page_rect.height
    x1 = page_rect.x0 + (x + w) * page_rect.width
    y1 = page_rect.y0 + (y + h) * page_rect.height
    return fitz.Rect(x0, y0, x1, y1)


def border_strip_rects(rect: fitz.Rect, thickness: float = 1.5) -> list[fitz.Rect]:
    t = max(0.1, float(thickness))
    top = fitz.Rect(rect.x0, rect.y0, rect.x1, min(rect.y1, rect.y0 + t))
    bottom = fitz.Rect(rect.x0, max(rect.y0, rect.y1 - t), rect.x1, rect.y1)
    left = fitz.Rect(rect.x0, rect.y0, min(rect.x1, rect.x0 + t), rect.y1)
    right = fitz.Rect(max(rect.x0, rect.x1 - t), rect.y0, rect.x1, rect.y1)
    return [top, bottom, left, right]


def _pdf_region_color(region_type: RegionType) -> tuple[float, float, float]:
    if region_type == "field":
        return (0.22, 0.55, 0.95)
    if region_type == "question":
        return (0.20, 0.65, 0.30)
    return (0.88, 0.52, 0.16)


def _draw_region_overlay(page: fitz.Page, region: EditorRegion) -> None:
    rect = bbox_norm_to_fitz_rect(region.bbox_norm, page.rect)
    color = _pdf_region_color(region.region_type)

    shape = page.new_shape()
    shape.draw_rect(rect)
    try:
        shape.finish(
            color=color,
            fill=color,
            width=0.8,
            stroke_opacity=0.9,
            fill_opacity=0.16 if not region.locked else 0.12,
        )
    except TypeError:
        fallback_fill = (
            0.82 + color[0] * 0.15,
            0.82 + color[1] * 0.15,
            0.82 + color[2] * 0.15,
        )
        shape.finish(color=color, fill=fallback_fill, width=0.8)
    shape.commit()

    label = f"{region.region_type}:{region.region_id}"
    if region.locked:
        label = f"{label} LOCK"
    label_rect = fitz.Rect(rect.x0 + 2, rect.y0 + 1, rect.x1 - 2, rect.y0 + 13)
    if label_rect.width > 20:
        page.insert_textbox(
            label_rect,
            label,
            fontsize=7,
            fontname="helv",
            color=(0.08, 0.08, 0.08),
            align=0,
        )


# TODO: reconsider how we strip borders
# 1. regenerate the spec and then re-generate the PDF while excluding the border from the spec
# 2. or just improve this
def _apply_outline_removal(
    page: fitz.Page,
    page_regions: list[EditorRegion],
    erase_border_width: float,
) -> None:
    has_redactions = False
    for region in page_regions:
        if region.region_type == "anchor":
            continue
        rect = bbox_norm_to_fitz_rect(region.bbox_norm, page.rect)
        for strip in border_strip_rects(rect, erase_border_width):
            page.add_redact_annot(strip, fill=(1.0, 1.0, 1.0))
            has_redactions = True

    if has_redactions:
        page.apply_redactions()


def _render_pdf_with_regions(
    input_pdf_path: str | Path,
    output_pdf_path: str | Path,
    regions: list[EditorRegion],
    erase_border_width: float,
    draw_overlays: bool,
) -> None:
    grouped = regions_by_page(regions)

    doc = fitz.open(str(input_pdf_path))
    try:
        for page_index in range(len(doc)):
            page_number = page_index + 1
            page = doc[page_index]
            page_regions = grouped.get(page_number, [])
            if not page_regions:
                continue

            _apply_outline_removal(page, page_regions, erase_border_width)

            if draw_overlays:
                for region in page_regions:
                    _draw_region_overlay(page, region)

        output_path = Path(output_pdf_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), garbage=4, deflate=True)
    finally:
        doc.close()


def render_review_pdf(
    input_pdf_path: str | Path,
    output_pdf_path: str | Path,
    regions: list[EditorRegion],
    erase_border_width: float = 1.2,
) -> None:
    _render_pdf_with_regions(
        input_pdf_path=input_pdf_path,
        output_pdf_path=output_pdf_path,
        regions=regions,
        erase_border_width=erase_border_width,
        draw_overlays=True,
    )


def render_master_template_pdf(
    input_pdf_path: str | Path,
    output_pdf_path: str | Path,
    regions: list[EditorRegion],
    erase_border_width: float = 1.2,
) -> None:
    _render_pdf_with_regions(
        input_pdf_path=input_pdf_path,
        output_pdf_path=output_pdf_path,
        regions=regions,
        erase_border_width=erase_border_width,
        draw_overlays=False,
    )
