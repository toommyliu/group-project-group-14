from __future__ import annotations

import re
import tempfile
from collections import defaultdict, deque
from pathlib import Path
import sys
from dataclasses import dataclass, field
from statistics import median
from typing import TYPE_CHECKING, Callable

from PIL import Image
from pypdf import PdfReader, PdfWriter

from opengrader.core.pdf_utils import norm_bbox, render_page_to_image, safe_image_to_data

if TYPE_CHECKING:  # pragma: no cover
    import fitz
    from opengrader.core.ocr.vision_reader import VisionReader


_ROMAN_RE = re.compile(r"^[IVXLCDM]+$", re.IGNORECASE)


def _roman_to_int(s: str) -> int:
    s = s.upper()
    roman = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    res = 0
    for i in range(len(s)):
        if i + 1 < len(s) and roman[s[i]] < roman[s[i + 1]]:
            res -= roman[s[i]]
        else:
            res += roman[s[i]]
    return res


def _word_to_int(s: str) -> int | None:
    words = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
        "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
        "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
        "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    }
    return words.get(s.lower())


def _parse_page_number(text: str, max_pages: int | None = None) -> int | None:
    if not text:
        return None

    clean = re.sub(r"[\[\]\(\)·•\-\.\|—,;]", " ", text).strip()

    def validate(num: int) -> int | None:
        if num <= 0:
            return None
        if max_pages is not None and num > max_pages + 5:
            return None
        return num

    page_match = re.search(r"page\s+([a-z\d]+)", clean, re.IGNORECASE)
    if page_match:
        val = page_match.group(1)
        if val.isdigit():
            return validate(int(val))
        word_val = _word_to_int(val)
        if word_val:
            return validate(word_val)
        if _ROMAN_RE.fullmatch(val):
            return validate(_roman_to_int(val))

    of_match = re.search(r"(\d+)\s*(?:of|/)\s*\d+", clean, re.IGNORECASE)
    if of_match:
        return validate(int(of_match.group(1)))

    tokens = clean.split()
    for token in reversed(tokens):
        if token.isdigit():
            return validate(int(token))
        if _ROMAN_RE.fullmatch(token):
            return validate(_roman_to_int(token))
        word_val = _word_to_int(token)
        if word_val:
            return validate(word_val)

    return None


def detect_page_number_tesseract(
    image: Image.Image, already_cropped: bool = False, max_pages: int | None = None
) -> int | None:
    import pytesseract

    if not already_cropped:
        w, h = image.size
        image = image.crop((0, int(h * 0.85), w, h))

    text = pytesseract.image_to_string(image, config="--psm 6").strip()
    return _parse_page_number(text, max_pages=max_pages)


def detect_page_number_text_layer(
    page: "fitz.Page", max_pages: int | None = None
) -> int | None:
    try:
        page_height = page.rect.height or 1.0
        footer_top = page_height * 0.85
        footer_words = [
            word[4]
            for word in page.get_text("words")
            # word = (x0, y0, x1, y1, text, block, line, word_no).
            if word[1] >= footer_top
        ]
        text = " ".join(footer_words).strip()
    except Exception:
        return None
    if not text:
        return None
    return _parse_page_number(text, max_pages=max_pages)


@dataclass(frozen=True)
class PageNumberLayoutInfo:
    # Normalized (x, y, w, h) page-number region.
    bbox_norm: tuple[float, float, float, float]
    page_w: int
    page_h: int
    total_pages: int
    # Printed number at each template position; None marks an intentionally unnumbered page.
    page_labels: tuple[int | None, ...] = field(default=())


def _crop_layout_region(
    image: Image.Image,
    bbox_norm: tuple[float, float, float, float],
) -> Image.Image | None:
    x_norm, y_norm, w_norm, h_norm = bbox_norm
    x0 = max(0, int(x_norm * image.width))
    y0 = max(0, int(y_norm * image.height))
    x1 = min(image.width, int((x_norm + w_norm) * image.width))
    y1 = min(image.height, int((y_norm + h_norm) * image.height))
    if x1 <= x0 or y1 <= y0:
        return None
    return image.crop((x0, y0, x1, y1))


def _detect_page_number_in_layout(
    image: Image.Image,
    bbox_norm: tuple[float, float, float, float],
    max_pages: int,
) -> int | None:
    cropped = _crop_layout_region(image, bbox_norm)
    if cropped is None:
        return None
    return detect_page_number_tesseract(
        cropped, already_cropped=True, max_pages=max_pages
    )


def _load_page_number_layout(blank_template_path: Path) -> PageNumberLayoutInfo | None:
    import fitz

    doc = fitz.open(blank_template_path)
    try:
        candidate_bboxes_px: list[tuple[int, int, int, int]] = []
        page_dims: tuple[int, int] | None = None

        for pg_idx in range(min(len(doc), 3)):
            page_image, current_page_w, current_page_h = render_page_to_image(doc, pg_idx, dpi=300)
            if page_dims is None:
                page_dims = (current_page_w, current_page_h)

            bottom_crop_px = (0, int(current_page_h * 0.80), current_page_w, current_page_h)
            cropped_img = page_image.crop(bottom_crop_px)

            config = "--psm 6"
            data = safe_image_to_data(cropped_img, config=config)

            for i, text in enumerate(data["text"]):
                text = text.strip()
                if not text:
                    continue

                token_clean = re.sub(r"[\[\]\(\)·•\-\.\|—,;]", "", text).strip()
                if not token_clean:
                    continue

                if token_clean.isdigit() or _word_to_int(token_clean) or _ROMAN_RE.fullmatch(token_clean):
                    x0_cropped = data["left"][i]
                    y0_cropped = data["top"][i]
                    x1_cropped = x0_cropped + data["width"][i]
                    y1_cropped = y0_cropped + data["height"][i]

                    x0_full = x0_cropped
                    y0_full = y0_cropped + bottom_crop_px[1]
                    x1_full = x1_cropped
                    y1_full = y1_cropped + bottom_crop_px[1]

                    candidate_bboxes_px.append((x0_full, y0_full, x1_full, y1_full))

        if not candidate_bboxes_px or page_dims is None:
            return None

        x_coords = [bbox[0] for bbox in candidate_bboxes_px]
        y_coords = [bbox[1] for bbox in candidate_bboxes_px]
        widths = [bbox[2] - bbox[0] for bbox in candidate_bboxes_px]
        heights = [bbox[3] - bbox[1] for bbox in candidate_bboxes_px]

        median_x = median(x_coords)
        median_y = median(y_coords)
        median_w = median(widths)
        median_h = median(heights)

        pad_x = median_w * 0.5
        pad_y = median_h * 0.5

        final_x0 = max(0, int(median_x - pad_x))
        final_y0 = max(0, int(median_y - pad_y))
        final_x1 = min(page_dims[0], int(median_x + median_w + pad_x))
        final_y1 = min(page_dims[1], int(median_y + median_h + pad_y))

        bbox_norm_result = norm_bbox(
            page_dims[0], page_dims[1],
            x0=final_x0, y0=final_y0, x1=final_x1, y1=final_y1
        )

        page_labels: list[int | None] = []
        for pg_idx in range(len(doc)):
            page_image, _, _ = render_page_to_image(doc, pg_idx, dpi=150)
            page_labels.append(
                _detect_page_number_in_layout(
                    page_image, bbox_norm_result, max_pages=len(doc)
                )
            )

        return PageNumberLayoutInfo(
            bbox_norm=bbox_norm_result,
            page_w=page_dims[0],
            page_h=page_dims[1],
            total_pages=len(doc),
            page_labels=tuple(page_labels),
        )

    finally:
        doc.close()

def _order_pages_by_template(
    detected: list[int | None],
    page_labels: tuple[int | None, ...],
    *,
    extra_work_pages: bool = False,
) -> tuple[list[int], list[str], set[int]]:
    warnings: list[str] = []
    printed_labels = {label for label in page_labels if label is not None}

    label_slots: dict[int, deque[int]] = defaultdict(deque)
    for position, label in enumerate(page_labels):
        if label is not None:
            label_slots[label].append(position)

    assigned: dict[int, int] = {}
    unassigned: list[int] = []
    for scan_index, num in enumerate(detected):
        slots = label_slots.get(num) if num is not None else None
        if slots:
            assigned[scan_index] = slots.popleft()
            continue
        if num is not None:
            if num in printed_labels:
                warnings.append(
                    f"Page number {num} was detected on more scanned pages than "
                    f"the template prints it; keeping the first occurrence."
                )
            else:
                warnings.append(
                    f"Detected page number {num} is not printed anywhere in the "
                    f"blank template; treating the page as unnumbered."
                )
        unassigned.append(scan_index)

    used_positions = set(assigned.values())
    if extra_work_pages:
        free_positions = deque(
            position
            for position, label in enumerate(page_labels)
            if label is None and position not in used_positions
        )
    else:
        free_positions = deque(
            position
            for position in range(len(page_labels))
            if position not in used_positions
        )
    overflow_position = len(page_labels)
    overflowed: set[int] = set()
    for scan_index in unassigned:
        if free_positions:
            assigned[scan_index] = free_positions.popleft()
        else:
            assigned[scan_index] = overflow_position
            overflow_position += 1
            overflowed.add(scan_index)

    if extra_work_pages and overflowed:
        warnings.append(
            f"Moved {len(overflowed)} extra page(s) to the end (not matched to a "
            f"template page)."
        )

    order = sorted(assigned, key=lambda scan_index: (assigned[scan_index], scan_index))
    return order, warnings, overflowed


def _order_pages_by_detected_number(
    detected: list[int | None],
) -> tuple[list[int], list[str]]:
    warnings: list[str] = []
    page_map = [
        (num if num is not None else float("inf"), i)
        for i, num in enumerate(detected)
    ]
    sorted_map = sorted(page_map, key=lambda x: x[0])

    detected_sequence = [m[0] for m in sorted_map if m[0] != float("inf")]
    for i in range(len(detected_sequence) - 1):
        if abs(detected_sequence[i + 1] - detected_sequence[i]) > 2:
            warnings.append(
                f"Large page number jump detected between detected pages: "
                f"{detected_sequence[i]} followed by {detected_sequence[i + 1]}."
            )
            break

    return [m[1] for m in sorted_map], warnings


@dataclass(frozen=True)
class ReorderResult:
    changed: bool
    extra_work_pages: tuple[int, ...] = ()

    def __bool__(self) -> bool:
        return self.changed


def reorder_pdf_pages(
    pdf_path: str | Path,
    *,
    vision_reader: VisionReader | None = None,
    blank_template_layout: PageNumberLayoutInfo | None = None,
    extra_work_pages: bool = False,
    progress_callback: Callable[[str], None] | None = None,
) -> ReorderResult:
    import fitz

    path = Path(pdf_path)
    reader = PdfReader(path)
    if len(reader.pages) <= 1:
        return ReorderResult(changed=False)

    doc = fitz.open(path)
    num_pages = len(doc)

    expected_total = num_pages
    if blank_template_layout:
        expected_total = blank_template_layout.total_pages
        if num_pages != expected_total:
            print(f"Warning: PDF {path.name} has {num_pages} pages, but the template expects {expected_total}.", file=sys.stderr)

    detected: list[int | None] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        for i in range(num_pages):
            num = detect_page_number_text_layer(doc[i], max_pages=expected_total)

            if num is None:
                img, _, _ = render_page_to_image(doc, i, dpi=150)

                from opengrader.core.ocr.qr_reader import read_code_from_image

                code = read_code_from_image(img)
                if code is not None and code.page is not None:
                    num = code.page

                if num is None and blank_template_layout:
                    num = _detect_page_number_in_layout(
                        img, blank_template_layout.bbox_norm, max_pages=expected_total
                    )
                if num is None:
                    num = detect_page_number_tesseract(img, max_pages=expected_total)

                if num is None and vision_reader:
                    img_file = tmp_path / f"p{i}.png"
                    img.save(img_file)
                    try:
                        num = vision_reader.extract_page_number(image_path=img_file)
                    except Exception:  # noqa: BLE001 - blocked or failed: no number
                        num = None

            detected.append(num)

    doc.close()

    reorder_warnings: list[str] = []

    template_labels = blank_template_layout.page_labels if blank_template_layout else ()
    expected_unnumbered = 0
    overflowed: set[int] = set()
    if any(label is not None for label in template_labels):
        new_order, order_warnings, overflowed = _order_pages_by_template(
            detected, template_labels, extra_work_pages=extra_work_pages
        )
        expected_unnumbered = sum(1 for label in template_labels if label is None)
    else:
        new_order, order_warnings = _order_pages_by_detected_number(detected)
    reorder_warnings.extend(order_warnings)

    extra_positions = tuple(
        position + 1
        for position, scan_index in enumerate(new_order)
        if scan_index in overflowed
    )

    num_undetected_pages = sum(1 for num in detected if num is None)
    if num_undetected_pages > expected_unnumbered:
        reorder_warnings.append(f"Page numbers could not be detected for {num_undetected_pages} out of {num_pages} pages.")

    num_pages_moved = sum(
        1 for position, scan_index in enumerate(new_order) if position != scan_index
    )
    if num_pages_moved > 0 and num_pages_moved / num_pages > 0.15:
        reorder_warnings.append(f"Significant reordering detected: {num_pages_moved} out of {num_pages} pages were moved.")

    if reorder_warnings:
        print(f"Warning: Reordering of {path.name} completed with concerns:", file=sys.stderr)
        for warning in reorder_warnings:
            print(f"  - {warning}", file=sys.stderr)

    if new_order == list(range(num_pages)):
        return ReorderResult(changed=False, extra_work_pages=extra_positions)

    writer = PdfWriter()
    for original_index in new_order:
        writer.add_page(reader.pages[original_index])

    with open(path, "wb") as f:
        writer.write(f)
    return ReorderResult(changed=True, extra_work_pages=extra_positions)
