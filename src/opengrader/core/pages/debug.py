from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .layout import question_regions

if TYPE_CHECKING:
    from .extract import AlignedPage

_LONG_SIDE = 1600
_QUESTION_COLORS = ((0, 114, 178), (213, 94, 0), (0, 158, 115), (204, 121, 167), (86, 180, 233), (230, 159, 0))
_FIELD_COLORS = {"name": (220, 20, 60), "id": (148, 0, 211), "title": (105, 105, 105)}


def write_layout_debug(pages: list[AlignedPage], next_layouts: list, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    continued: dict[int, list[tuple[str, tuple[float, float, float, float]]]] = {}
    summary = []
    for index, page in enumerate(pages):
        entry: dict = {"page": page.number, "group": page.group.id if page.group else None}
        if page.group is not None:
            layout = page.group.layout
            entry.update(
                printed_page=page.group.page_number,
                rotation_degrees=round(page.alignment.rotation_degrees, 1),
                matched_features=page.alignment.inliers,
                questions={}, fields={key: list(box) for key, box in layout.fields.items()},
            )
            for region, continuation in question_regions(layout, next_layouts[index]):
                entry["questions"][region.key] = {"box": list(region.box), "points": region.points}
                if continuation is not None:
                    entry["questions"][region.key]["continues_on_page"] = page.number + 1
                    continued.setdefault(index + 1, []).append((region.key, continuation))
        summary.append(entry)

    for index, page in enumerate(pages):
        image = page.image.convert("RGB")
        scale = _LONG_SIDE / max(image.size)
        image = image.resize((round(image.width * scale), round(image.height * scale)))
        pen = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=max(14, image.width // 60))
        if page.group is None:
            caption = f"page {page.number}: did not match any printed page (no crops)"
            color = (200, 0, 0)
        else:
            layout = page.group.layout
            for number, (region, _) in enumerate(question_regions(layout, next_layouts[index])):
                points = f" ({region.points:g} pts)" if region.points is not None else ""
                _outline(pen, page, region.box, image.size, _QUESTION_COLORS[number % len(_QUESTION_COLORS)], region.key + points, font)
            for number, (key, box) in enumerate(continued.get(index, [])):
                _outline(pen, page, box, image.size, (120, 120, 120), f"{key} (continued)", font)
            for kind, box in layout.fields.items():
                _outline(pen, page, box, image.size, _FIELD_COLORS.get(kind, (0, 0, 0)), kind, font)
            printed = f"printed page {page.group.page_number}" if page.group.page_number else "unnumbered printed page"
            caption = (f"page {page.number} = {printed} ({page.group.id}), turned {page.alignment.rotation_degrees:.0f} deg, "
                       f"{page.alignment.inliers} matching features")
            color = (0, 0, 0)
        box = pen.textbbox((8, 6), caption, font=font)
        pen.rectangle((box[0] - 4, box[1] - 3, box[2] + 4, box[3] + 3), fill=(255, 255, 210))
        pen.text((8, 6), caption, fill=color, font=font)
        image.save(directory / f"page-{page.number}.png")

    (directory / "layout.json").write_text(json.dumps({"pages": summary}, indent=1))


def _outline(pen: ImageDraw.ImageDraw, page: AlignedPage, box, size: tuple[int, int], color, label: str, font) -> None:
    x0, y0, x1, y1 = box
    corners = page.alignment.to_page(np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], dtype=np.float64))
    points = [(float(x * size[0]), float(y * size[1])) for x, y in corners]
    pen.polygon(points, outline=color, width=3)
    left, top = min(p[0] for p in points), min(p[1] for p in points)
    height = pen.textbbox((0, 0), label, font=font)[3]
    at = (left + 2, top - height - 4) if top - height - 4 > 0 else (left + 4, top + 3)
    pen.rectangle(pen.textbbox(at, label, font=font), fill=(255, 255, 255))
    pen.text(at, label, fill=color, font=font)
