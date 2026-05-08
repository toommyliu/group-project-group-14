from __future__ import annotations

import io
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import fitz
import pytesseract
from PIL import Image, ImageOps
from pypdf import PdfReader, PdfWriter


@dataclass(frozen=True)
class PageEvidence:
    physical_page: int
    logical_page: int | None
    logical_page_confidence: float
    is_first_page: bool
    first_page_confidence: float
    evidence_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class MissingPage:
    student_index: int | None
    logical_page: int
    reason: str


@dataclass(frozen=True)
class SubmissionDistributionRange:
    name: str
    start_page: int
    end_page: int
    physical_pages: tuple[int, ...]
    logical_pages: tuple[int | None, ...]
    missing_pages: tuple[int, ...]
    needs_review: bool
    review_reasons: tuple[str, ...]
    confidence: float


@dataclass(frozen=True)
class SubmissionDistributionPlan:
    ranges: list[SubmissionDistributionRange]
    page_evidence: list[PageEvidence]
    needs_review: bool
    review_reasons: list[str]


def _sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip("-._")
    return cleaned or "submission"


def _clamp_confidence(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _trusted_logical_pages(group: list[PageEvidence]) -> list[int]:
    return [page.logical_page for page in group if page.logical_page is not None]


def _last_trusted_logical_page(group: list[PageEvidence]) -> int | None:
    for page in reversed(group):
        if page.logical_page is not None:
            return page.logical_page
    return None


def _observed_complete_submission(group: list[PageEvidence], pages_per_submission: int) -> bool:
    observed = set(_trusted_logical_pages(group))
    return all(page in observed for page in range(1, pages_per_submission + 1))


def _starts_new_submission(
    group: list[PageEvidence],
    page: PageEvidence,
    pages_per_submission: int,
) -> bool:
    """Return whether ``page`` should begin a new split range."""

    if not group:
        return True

    # pges_per_submission is both the expected logical page count and a
    # safety cap for emitted PDFs.  Once a range already has that many
    # physical pages, the next scanned page must start a new range even when
    # OCR cannot confidently read its logical page number.
    if len(group) >= pages_per_submission:
        return True

    if page.is_first_page or page.logical_page == 1:
        return True

    previous = _last_trusted_logical_page(group)
    current = page.logical_page
    if previous is None or current is None or current >= previous:
        return False

    observed = set(_trusted_logical_pages(group))
    if _observed_complete_submission(group, pages_per_submission):
        return True

    # When a review range already lacks page 1, seeing its first trusted page
    # repeat is the strongest available signal that another anonymous
    # submission has begun.
    if 1 not in observed and current in observed:
        return True

    # Otherwise a decrease such as 1,3,2 is more likely a scan ordering/OCR
    # problem within the same submission than a clean boundary.
    return False


def _infer_unknown_logical_pages(
    raw_pages: list[int | None],
    pages_per_submission: int,
) -> list[int | None]:
    inferred = list(raw_pages)
    known_indices = [i for i, page in enumerate(inferred) if page is not None]
    if not known_indices:
        return inferred

    first_index = known_indices[0]
    first_value = inferred[first_index]
    if first_value is not None:
        for index in range(first_index - 1, -1, -1):
            candidate = first_value - (first_index - index)
            if 1 <= candidate <= pages_per_submission:
                inferred[index] = candidate

    for left, right in zip(known_indices, known_indices[1:]):
        left_value = inferred[left]
        right_value = inferred[right]
        if left_value is None or right_value is None:
            continue
        unknown_slots = right - left - 1
        if unknown_slots <= 0:
            continue
        if right_value <= left_value:
            continue
        if right_value - left_value - 1 != unknown_slots:
            continue
        for offset in range(1, unknown_slots + 1):
            inferred[left + offset] = left_value + offset

    last_index = known_indices[-1]
    last_value = inferred[last_index]
    if last_value is not None:
        for index in range(last_index + 1, len(inferred)):
            candidate = last_value + (index - last_index)
            if 1 <= candidate <= pages_per_submission:
                inferred[index] = candidate

    return inferred


def _duplicate_logical_pages(pages: list[int | None]) -> list[int]:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for page in pages:
        if page is None:
            continue
        if page in seen:
            duplicates.add(page)
        seen.add(page)
    return sorted(duplicates)


def _has_non_monotonic_logical_pages(pages: list[int | None]) -> bool:
    previous: int | None = None
    for page in pages:
        if page is None:
            continue
        if previous is not None and page < previous:
            return True
        previous = page
    return False


def _range_confidence(
    group: list[PageEvidence],
    *,
    missing_pages: tuple[int, ...],
    review_reasons: list[str],
    inferred_pages: list[int | None],
) -> float:
    confidences = [
        page.logical_page_confidence for page in group if page.logical_page is not None
    ]
    confidence = sum(confidences) / len(confidences) if confidences else 45.0

    original_unknown_count = sum(1 for page in group if page.logical_page is None)
    unresolved_unknown_count = sum(1 for page in inferred_pages if page is None)
    if original_unknown_count:
        confidence = min(confidence, 78.0)
    confidence -= unresolved_unknown_count * 18.0
    confidence -= len(missing_pages) * 8.0

    severe_reasons = {
        "no_trusted_page_numbers",
        "non_monotonic_logical_pages",
    }
    if any(reason in severe_reasons for reason in review_reasons):
        confidence -= 28.0
    if any(reason.startswith("duplicate_logical_page_") for reason in review_reasons):
        confidence -= 24.0
    if "missing_first_page_unknown_owner" in review_reasons:
        confidence -= 30.0

    return _clamp_confidence(confidence)


def _build_range(
    group: list[PageEvidence],
    *,
    pages_per_submission: int,
    name: str,
) -> SubmissionDistributionRange:
    raw_logical_pages = [page.logical_page for page in group]
    inferred_pages = _infer_unknown_logical_pages(raw_logical_pages, pages_per_submission)
    observed_pages = {page for page in inferred_pages if page is not None}
    missing_pages = tuple(
        page for page in range(1, pages_per_submission + 1) if page not in observed_pages
    )

    review_reasons: list[str] = []
    if not any(page is not None for page in raw_logical_pages):
        review_reasons.append("no_trusted_page_numbers")
    if any(page is None for page in inferred_pages):
        review_reasons.append("ambiguous_unknown_logical_page")
    for duplicate in _duplicate_logical_pages(raw_logical_pages):
        review_reasons.append(f"duplicate_logical_page_{duplicate}")
    if _has_non_monotonic_logical_pages(raw_logical_pages):
        review_reasons.append("non_monotonic_logical_pages")
    for missing_page in missing_pages:
        review_reasons.append(f"missing_logical_page_{missing_page}")
    if 1 in missing_pages:
        review_reasons.append("missing_first_page_unknown_owner")
    if len(group) > pages_per_submission:
        review_reasons.append("too_many_physical_pages")

    needs_review = bool(review_reasons)
    confidence = _range_confidence(
        group,
        missing_pages=missing_pages,
        review_reasons=review_reasons,
        inferred_pages=inferred_pages,
    )

    physical_pages = tuple(page.physical_page for page in group)
    return SubmissionDistributionRange(
        name=name,
        start_page=min(physical_pages),
        end_page=max(physical_pages),
        physical_pages=physical_pages,
        logical_pages=tuple(inferred_pages),
        missing_pages=missing_pages,
        needs_review=needs_review,
        review_reasons=tuple(dict.fromkeys(review_reasons)),
        confidence=confidence,
    )


def plan_submission_distribution(
    *,
    page_evidence: list[PageEvidence],
    pages_per_submission: int,
    filename_prefix: str = "submission",
    review_prefix: str = "needs-review",
    zero_pad_width: int = 4,
) -> SubmissionDistributionPlan:
    """Infer per-submission page groups from scanned page evidence."""

    if pages_per_submission <= 0:
        raise ValueError("pages_per_submission must be a positive integer")
    if not page_evidence:
        raise ValueError("page_evidence must contain at least one page")

    ordered = sorted(page_evidence, key=lambda page: page.physical_page)
    physical_pages = [page.physical_page for page in ordered]
    if any(page < 1 for page in physical_pages):
        raise ValueError("physical_page values must be >= 1")
    if len(set(physical_pages)) != len(physical_pages):
        raise ValueError("physical_page values must be unique")

    groups: list[list[PageEvidence]] = []
    current: list[PageEvidence] = []
    for page in ordered:
        if _starts_new_submission(current, page, pages_per_submission):
            if current:
                groups.append(current)
            current = [page]
        else:
            current.append(page)
    if current:
        groups.append(current)

    normal_prefix = _sanitize_filename(filename_prefix)
    review_prefix = _sanitize_filename(review_prefix)
    normal_index = 1
    review_index = 1
    ranges: list[SubmissionDistributionRange] = []

    for group in groups:
        preview = _build_range(
            group,
            pages_per_submission=pages_per_submission,
            name="pending",
        )
        if "missing_first_page_unknown_owner" in preview.review_reasons:
            name = f"{review_prefix}-{review_index:0{zero_pad_width}d}"
            review_index += 1
        else:
            name = f"{normal_prefix}-{normal_index:0{zero_pad_width}d}"
            normal_index += 1
        ranges.append(
            _build_range(
                group,
                pages_per_submission=pages_per_submission,
                name=name,
            )
        )

    review_reasons: list[str] = []
    for page_range in ranges:
        for reason in page_range.review_reasons:
            review_reasons.append(f"{page_range.name}: {reason}")

    return SubmissionDistributionPlan(
        ranges=ranges,
        page_evidence=ordered,
        needs_review=any(page_range.needs_review for page_range in ranges),
        review_reasons=list(dict.fromkeys(review_reasons)),
    )


def distribution_plan_to_dict(plan: SubmissionDistributionPlan) -> dict:
    return asdict(plan)


def distribution_plan_to_json(plan: SubmissionDistributionPlan) -> str:
    return json.dumps(distribution_plan_to_dict(plan), indent=2)


def write_distribution_report(
    plan: SubmissionDistributionPlan,
    output_path: str | Path,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(distribution_plan_to_json(plan) + "\n", encoding="utf-8")
    return path


def _ocr_text_and_confidence(image: Image.Image, *, config: str) -> tuple[str, float]:
    prepared = ImageOps.autocontrast(ImageOps.grayscale(image))
    try:
        data = pytesseract.image_to_data(
            prepared,
            config=config,
            output_type=pytesseract.Output.DICT,
        )
    except Exception:
        return "", 0.0

    words: list[str] = []
    confidences: list[float] = []
    for text, raw_confidence in zip(data.get("text", []), data.get("conf", [])):
        cleaned = str(text).strip()
        if not cleaned:
            continue
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            confidence = -1.0
        words.append(cleaned)
        if confidence >= 0:
            confidences.append(confidence)

    if words:
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return " ".join(words), _clamp_confidence(confidence)
    return "", 0.0


def _extract_footer_logical_page(
    image: Image.Image,
    *,
    pages_per_submission: int,
    min_page_number_confidence: float,
) -> tuple[int | None, float, list[str]]:
    width, height = image.size
    crop = image.crop(
        (
            int(width * 0.34),
            int(height * 0.84),
            int(width * 0.66),
            int(height * 0.99),
        )
    )
    text, confidence = _ocr_text_and_confidence(
        crop,
        config="--psm 6 -c tessedit_char_whitelist=0123456789/",
    )
    reasons: list[str] = []
    numbers = [int(match) for match in re.findall(r"\d{1,3}", text)]
    candidates = [page for page in numbers if 1 <= page <= pages_per_submission]
    if not candidates:
        if text:
            reasons.append(f"footer_page_number_unusable:{text}")
        else:
            reasons.append("footer_page_number_missing")
        return None, confidence, reasons

    logical_page = candidates[0]
    if confidence < min_page_number_confidence:
        reasons.append(f"footer_page_number_low_confidence:{logical_page}")
        return None, confidence, reasons

    reasons.append(f"footer_page_number:{logical_page}")
    return logical_page, confidence, reasons


def _header_has_first_page_labels(image: Image.Image) -> tuple[bool, float, list[str]]:
    width, height = image.size
    crop = image.crop((0, 0, width, int(height * 0.32)))
    text, confidence = _ocr_text_and_confidence(crop, config="--psm 6")
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    normalized = "".join(tokens)
    token_pairs = {
        f"{left}{right}" for left, right in zip(tokens, tokens[1:])
    }
    has_name = any(token in {"name", "nane", "naine", "narne"} for token in tokens)
    has_student_id = (
        "studentid" in normalized
        or "studentid" in token_pairs
        or "student1d" in normalized
        or "studentld" in normalized
        or any(token in {"sid", "id", "1d", "ld"} for token in tokens)
    )
    if has_name and has_student_id:
        return True, max(confidence, 80.0), ["header_student_field_labels"]
    if has_name:
        return True, max(confidence, 68.0), ["header_name_label"]
    return False, confidence, []


def _render_page_to_image(doc: fitz.Document, page_index: int, dpi: int) -> Image.Image:
    zoom = dpi / 72.0
    pix = doc[page_index].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return Image.open(io.BytesIO(pix.tobytes("png")))


def collect_page_evidence_from_pdf(
    input_pdf: str | Path,
    *,
    pages_per_submission: int,
    start_page: int = 1,
    end_page: int | None = None,
    dpi: int = 200,
    min_page_number_confidence: float = 60.0,
) -> list[PageEvidence]:
    """Render and OCR selected pages from a merged scanned PDF."""

    path = Path(input_pdf)
    if not path.exists():
        raise FileNotFoundError(f"input PDF not found: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"input must be a PDF: {path}")
    if pages_per_submission <= 0:
        raise ValueError("pages_per_submission must be a positive integer")
    if start_page < 1:
        raise ValueError("start_page must be >= 1")
    if dpi <= 0:
        raise ValueError("dpi must be a positive integer")

    evidence: list[PageEvidence] = []
    doc = fitz.open(str(path))
    try:
        total_pages = len(doc)
        if total_pages <= 0:
            raise ValueError(f"input PDF has no pages: {path}")
        last_page = total_pages if end_page is None else end_page
        if last_page < start_page:
            raise ValueError("end_page must be >= start_page")
        if last_page > total_pages:
            raise ValueError(
                f"end_page ({last_page}) exceeds total pages in input PDF ({total_pages})"
            )

        for physical_page in range(start_page, last_page + 1):
            image = _render_page_to_image(doc, physical_page - 1, dpi)
            logical_page, logical_confidence, reasons = _extract_footer_logical_page(
                image,
                pages_per_submission=pages_per_submission,
                min_page_number_confidence=min_page_number_confidence,
            )
            header_first_page, header_confidence, header_reasons = (
                _header_has_first_page_labels(image)
            )
            reasons.extend(header_reasons)

            if logical_page == 1:
                is_first_page = True
                first_confidence = max(logical_confidence, 95.0)
                reasons.append("first_page_from_footer")
            elif logical_page is None and header_first_page:
                is_first_page = True
                first_confidence = header_confidence
                reasons.append("first_page_from_header")
            else:
                is_first_page = False
                first_confidence = 0.0

            evidence.append(
                PageEvidence(
                    physical_page=physical_page,
                    logical_page=logical_page,
                    logical_page_confidence=logical_confidence,
                    is_first_page=is_first_page,
                    first_page_confidence=_clamp_confidence(first_confidence),
                    evidence_reasons=tuple(dict.fromkeys(reasons)),
                )
            )
    finally:
        doc.close()

    return evidence


def split_distributed_submission_pdf(
    input_pdf: str | Path,
    output_dir: str | Path,
    ranges: list[SubmissionDistributionRange],
) -> list[Path]:
    """Write one PDF per inferred range using each range's physical pages."""

    input_path = Path(input_pdf)
    if not input_path.exists():
        raise FileNotFoundError(f"input PDF not found: {input_path}")
    if not ranges:
        return []

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    reader = PdfReader(str(input_path))
    total_pages = len(reader.pages)
    if total_pages <= 0:
        raise ValueError(f"input PDF has no pages: {input_path}")

    created_files: list[Path] = []
    for page_range in ranges:
        if not page_range.physical_pages:
            raise ValueError(f"range {page_range.name} contains no physical pages")

        writer = PdfWriter()
        for physical_page in page_range.physical_pages:
            if physical_page < 1 or physical_page > total_pages:
                raise ValueError(
                    f"invalid page {physical_page} for input PDF with {total_pages} pages"
                )
            writer.add_page(reader.pages[physical_page - 1])

        destination = output_path / f"{page_range.name}.pdf"
        with destination.open("wb") as file:
            writer.write(file)
        created_files.append(destination)

    return created_files
