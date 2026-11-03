from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

ScanSeverity = str

PageNumberSource = str

PageOrderEvidence = str

_EXACT_PAGE_NUMBER_SOURCES = frozenset({"text_layer", "qr"})

ANALYSIS_DPI = 150

DEFAULT_DPI_FLOOR = 200.0

CRITICAL_DPI = 150.0

# OSD confidence is an arbitrary scale, not a probability.
DEFAULT_MIN_ORIENTATION_CONFIDENCE = 2.0

BLANK_INK_FRACTION = 0.003

INTERIOR_INSET = 0.04

EDGE_BAND = 0.012

EDGE_INK_MIN = 0.02

# Near-solid edge bands are scanner shadows, not clipped text.
EDGE_SATURATED = 0.60

DOMINANT_ROTATION_MAJORITY = 0.75
MIN_PAGES_FOR_DOMINANT_ROTATION = 2

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


class ScanIssueKind(str, Enum):
    ROTATED_PAGE = "rotated_page"
    PAGE_ORDER = "page_order"
    BLANK_PAGE = "blank_page"
    LOW_RESOLUTION = "low_resolution"
    MARGIN_CLIPPED = "margin_clipped"
    PAGE_COUNT_MISMATCH = "page_count_mismatch"


class OrientationUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScanIssue:
    kind: ScanIssueKind
    severity: ScanSeverity
    page: int | None
    detail: str
    fixable: bool = False


@dataclass(frozen=True)
class PageScanFeatures:
    page: int  # 1-based.
    # Clockwise correction in degrees; None means OSD had no usable reading.
    rotation: int | None = None
    orientation_confidence: float | None = None
    ink_fraction: float | None = None
    # Ink fractions for left, right, top, bottom edge bands.
    edge_ink: tuple[float, float, float, float] | None = None
    dpi: float | None = None
    page_number: int | None = None
    page_number_source: PageNumberSource | None = None


@dataclass(frozen=True)
class RotationPlan:
    angles: Mapping[int, int] = field(default_factory=dict)
    inferred_pages: tuple[int, ...] = ()
    unverified_pages: tuple[int, ...] = ()
    dominant: int | None = None

    def __bool__(self) -> bool:
        return bool(self.angles)


@dataclass(frozen=True)
class PageOrderWindow:
    first_page: int
    last_page: int
    numbers: tuple[int | None, ...] = ()
    break_page: int = 0


@dataclass(frozen=True)
class PageOrderPlan:
    submission_length: int | None = None
    detected: tuple[int | None, ...] = ()
    unread_pages: tuple[int, ...] = ()
    out_of_order_windows: tuple[PageOrderWindow, ...] = ()
    evidence: PageOrderEvidence = "none"
    checked: bool = False
    note: str = ""

    def __bool__(self) -> bool:
        return bool(self.out_of_order_windows)


@dataclass(frozen=True)
class ScanReport:
    source: str
    page_count: int
    features: tuple[PageScanFeatures, ...] = ()
    issues: tuple[ScanIssue, ...] = ()
    rotation_plan: RotationPlan = field(default_factory=RotationPlan)
    page_order_plan: PageOrderPlan = field(default_factory=PageOrderPlan)
    orientation_available: bool = True
    orientation_note: str = ""

    @property
    def high_severity(self) -> tuple[ScanIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "high")

    @property
    def fixable_issues(self) -> tuple[ScanIssue, ...]:
        return tuple(issue for issue in self.issues if issue.fixable)

    def counts_by_severity(self) -> dict[str, int]:
        counts = {"high": 0, "medium": 0, "low": 0}
        for issue in self.issues:
            counts[issue.severity] = counts.get(issue.severity, 0) + 1
        return counts


def dominant_rotation(
    features: Sequence[PageScanFeatures],
    *,
    min_confidence: float = DEFAULT_MIN_ORIENTATION_CONFIDENCE,
) -> int | None:
    readable = [
        _usable_rotation(page, min_confidence=min_confidence) for page in features
    ]
    determined = [angle for angle in readable if angle is not None]
    if len(determined) < MIN_PAGES_FOR_DOMINANT_ROTATION:
        return None
    angle, count = Counter(determined).most_common(1)[0]
    if angle == 0 or count / len(determined) < DOMINANT_ROTATION_MAJORITY:
        return None
    return angle


def plan_rotations(
    features: Sequence[PageScanFeatures],
    *,
    min_confidence: float = DEFAULT_MIN_ORIENTATION_CONFIDENCE,
) -> RotationPlan:
    dominant = dominant_rotation(features, min_confidence=min_confidence)
    angles: dict[int, int] = {}
    inferred: list[int] = []
    unverified: list[int] = []

    for page in features:
        angle = _usable_rotation(page, min_confidence=min_confidence)
        if angle is None:
            if dominant is not None:
                angles[page.page] = dominant
                inferred.append(page.page)
            else:
                unverified.append(page.page)
            continue
        if angle:
            angles[page.page] = angle

    return RotationPlan(
        angles=angles,
        inferred_pages=tuple(inferred),
        unverified_pages=tuple(unverified),
        dominant=dominant,
    )


def plan_page_order(
    features: Sequence[PageScanFeatures],
    *,
    submission_length: int | None = None,
) -> PageOrderPlan:
    """Printed numbers need only increase, allowing offsets and unnumbered pages. Without
    submission_length, ordering is unchecked because numbering restarts per student.
    """
    total = len(features)
    detected = tuple(page.page_number for page in features)
    unread = tuple(page.page for page in features if page.page_number is None)
    evidence = _page_order_evidence(features)

    if total == 0:
        return PageOrderPlan(
            detected=detected, evidence=evidence, note="no pages to check"
        )

    if submission_length is None or submission_length <= 0:
        return PageOrderPlan(
            detected=detected,
            unread_pages=unread,
            evidence=evidence,
            note=(
                "page order not checked — how many pages a submission has is "
                "unknown; pass --pages-per-submission or --blank-template"
            ),
        )

    if evidence == "none":
        return PageOrderPlan(
            submission_length=submission_length,
            detected=detected,
            unread_pages=unread,
            evidence=evidence,
            note=(
                "page order not checked — no page number could be read on any "
                "page (no QR codes, no PDF text layer, and none legible to OCR)"
            ),
        )

    if total % submission_length:
        # A missing sheet would make fixed-size order windows mix students.
        return PageOrderPlan(
            submission_length=submission_length,
            detected=detected,
            unread_pages=unread,
            evidence=evidence,
            note=(
                f"page order not checked — {total} pages is not a whole number of "
                f"{submission_length}-page submissions, so submission boundaries "
                "cannot be trusted; resolve the page count first"
            ),
        )

    windows: list[PageOrderWindow] = []
    for start in range(0, total, submission_length):
        chunk = features[start : start + submission_length]
        break_page = _first_backward_page(chunk)
        if break_page is not None:
            windows.append(
                PageOrderWindow(
                    first_page=chunk[0].page,
                    last_page=chunk[-1].page,
                    numbers=tuple(page.page_number for page in chunk),
                    break_page=break_page,
                )
            )

    return PageOrderPlan(
        submission_length=submission_length,
        detected=detected,
        unread_pages=unread,
        out_of_order_windows=tuple(windows),
        evidence=evidence,
        checked=True,
    )


def page_order_fix_is_safe(
    plan: PageOrderPlan, *, has_blank_template: bool = False
) -> bool:
    if not plan:
        return False
    return plan.evidence == "exact" or has_blank_template


def _first_backward_page(chunk: Sequence[PageScanFeatures]) -> int | None:
    previous: int | None = None
    for page in chunk:
        number = page.page_number
        if number is None:
            continue
        if previous is not None and number <= previous:
            return page.page
        previous = number
    return None


def _page_order_evidence(
    features: Sequence[PageScanFeatures],
) -> PageOrderEvidence:
    sources = [
        page.page_number_source
        for page in features
        if page.page_number is not None and page.page_number_source is not None
    ]
    if not sources:
        return "none"
    if all(source in _EXACT_PAGE_NUMBER_SOURCES for source in sources):
        return "exact"
    return "ocr"


def flag_bundle_page_count(
    total_pages: int, pages_per_submission: int
) -> ScanIssue | None:
    if pages_per_submission <= 0 or total_pages <= 0:
        return None
    remainder = total_pages % pages_per_submission
    if remainder == 0:
        return None
    whole = total_pages // pages_per_submission
    return ScanIssue(
        kind=ScanIssueKind.PAGE_COUNT_MISMATCH,
        severity="high",
        page=None,
        detail=(
            f"{total_pages} pages is not a whole number of "
            f"{pages_per_submission}-page submissions "
            f"({whole} submissions + {remainder} page(s) left over); "
            "a sheet is missing, doubled, or the count is wrong"
        ),
    )


def flag_submission_page_count(
    page_count: int, expected_pages: int
) -> ScanIssue | None:
    if expected_pages <= 0 or page_count == expected_pages:
        return None
    difference = page_count - expected_pages
    direction = "more" if difference > 0 else "fewer"
    return ScanIssue(
        kind=ScanIssueKind.PAGE_COUNT_MISMATCH,
        severity="high",
        page=None,
        detail=(
            f"{page_count} pages, but a submission should have {expected_pages} "
            f"({abs(difference)} {direction}); pages may be missing, duplicated, "
            "or belong to another student"
        ),
    )


def build_scan_issues(
    features: Sequence[PageScanFeatures],
    *,
    rotation_plan: RotationPlan | None = None,
    page_order_plan: PageOrderPlan | None = None,
    pages_per_submission: int | None = None,
    expected_page_count: int | None = None,
    dpi_floor: float = DEFAULT_DPI_FLOOR,
    blank_ink_fraction: float = BLANK_INK_FRACTION,
    min_orientation_confidence: float = DEFAULT_MIN_ORIENTATION_CONFIDENCE,
) -> tuple[ScanIssue, ...]:
    """pages_per_submission checks bundle divisibility; expected_page_count checks one
    submission's exact length.
    """
    plan = rotation_plan
    if plan is None:
        plan = plan_rotations(features, min_confidence=min_orientation_confidence)

    order_plan = page_order_plan
    if order_plan is None:
        order_plan = plan_page_order(
            features,
            submission_length=pages_per_submission or expected_page_count,
        )

    issues: list[ScanIssue] = []

    for page, angle in sorted(plan.angles.items()):
        inferred = page in plan.inferred_pages
        basis = (
            f"orientation unreadable on this page; following the {angle}° "
            "the rest of the stack was scanned at"
            if inferred
            else f"rotated {angle}° from upright"
        )
        issues.append(
            ScanIssue(
                kind=ScanIssueKind.ROTATED_PAGE,
                severity="high",
                page=page,
                detail=f"{basis} — answers here will not transcribe until it is turned",
                fixable=True,
            )
        )

    issues.extend(_page_order_issues(order_plan))

    for page_features in features:
        issues.extend(
            _page_issues(
                page_features,
                dpi_floor=dpi_floor,
                blank_ink_fraction=blank_ink_fraction,
            )
        )

    if pages_per_submission is not None:
        count_issue = flag_bundle_page_count(len(features), pages_per_submission)
        if count_issue is not None:
            issues.append(count_issue)
    if expected_page_count is not None:
        count_issue = flag_submission_page_count(len(features), expected_page_count)
        if count_issue is not None:
            issues.append(count_issue)

    return tuple(
        sorted(
            issues,
            key=lambda issue: (
                _SEVERITY_RANK.get(issue.severity, 9),
                issue.page if issue.page is not None else -1,
                issue.kind.value,
            ),
        )
    )


def build_scan_report(
    features: Sequence[PageScanFeatures],
    *,
    source: str,
    orientation_available: bool = True,
    orientation_note: str = "",
    pages_per_submission: int | None = None,
    expected_page_count: int | None = None,
    submission_length: int | None = None,
    check_page_order: bool = True,
    dpi_floor: float = DEFAULT_DPI_FLOOR,
    blank_ink_fraction: float = BLANK_INK_FRACTION,
    min_orientation_confidence: float = DEFAULT_MIN_ORIENTATION_CONFIDENCE,
) -> ScanReport:
    plan = plan_rotations(features, min_confidence=min_orientation_confidence)
    if check_page_order:
        order_plan = plan_page_order(
            features,
            submission_length=(
                submission_length or pages_per_submission or expected_page_count
            ),
        )
    else:
        order_plan = PageOrderPlan(note="page order check skipped")
    issues = build_scan_issues(
        features,
        rotation_plan=plan,
        page_order_plan=order_plan,
        pages_per_submission=pages_per_submission,
        expected_page_count=expected_page_count,
        dpi_floor=dpi_floor,
        blank_ink_fraction=blank_ink_fraction,
        min_orientation_confidence=min_orientation_confidence,
    )
    return ScanReport(
        source=source,
        page_count=len(features),
        features=tuple(features),
        issues=issues,
        rotation_plan=plan,
        page_order_plan=order_plan,
        orientation_available=orientation_available,
        orientation_note=orientation_note,
    )


def _usable_rotation(
    page: PageScanFeatures, *, min_confidence: float
) -> int | None:
    if page.rotation is None:
        return None
    confidence = page.orientation_confidence
    if confidence is not None and confidence < min_confidence:
        return None
    return page.rotation % 360


def _page_order_issues(plan: PageOrderPlan) -> list[ScanIssue]:
    """Order issues are never fixable here; this tool corrects rotation only."""
    if not plan.out_of_order_windows:
        return []

    if plan.evidence == "exact":
        remedy = (
            "these page numbers are exact (QR code or PDF text layer), so "
            "splitting with --split every-n puts them back in order by itself"
        )
    else:
        remedy = (
            "these page numbers were read by OCR and one may simply have been "
            "misread — check the pages by eye, then split with --reorder"
        )

    issues: list[ScanIssue] = []
    for window in plan.out_of_order_windows:
        span = (
            f"page {window.first_page}"
            if window.first_page == window.last_page
            else f"pages {window.first_page}-{window.last_page}"
        )
        issues.append(
            ScanIssue(
                kind=ScanIssueKind.PAGE_ORDER,
                severity="high",
                page=window.first_page,
                detail=(
                    f"{span} read as {_format_page_numbers(window.numbers)} — "
                    f"page {window.break_page} does not follow the one before it, "
                    "so these sheets are out of order; every later stage addresses "
                    "a page by its position, so answers here would be cropped, "
                    "transcribed and graded against the wrong questions. "
                    f"{remedy}"
                ),
            )
        )
    return issues


def _format_page_numbers(numbers: Sequence[int | None]) -> str:
    return ", ".join("?" if number is None else str(number) for number in numbers)


def _page_issues(
    page: PageScanFeatures,
    *,
    dpi_floor: float,
    blank_ink_fraction: float,
) -> list[ScanIssue]:
    issues: list[ScanIssue] = []

    if page.dpi is not None and page.dpi < dpi_floor:
        issues.append(
            ScanIssue(
                kind=ScanIssueKind.LOW_RESOLUTION,
                severity="high" if page.dpi < CRITICAL_DPI else "medium",
                page=page.page,
                detail=(
                    f"scanned at about {page.dpi:.0f} DPI, below the {dpi_floor:.0f} "
                    "DPI handwriting OCR needs; re-scan this page at 300 DPI"
                ),
            )
        )

    if page.ink_fraction is not None and page.ink_fraction < blank_ink_fraction:
        issues.append(
            ScanIssue(
                kind=ScanIssueKind.BLANK_PAGE,
                severity="low",
                page=page.page,
                detail=(
                    "blank — if this is a duplex back side or a separator sheet it "
                    "shifts every page after it, and a fixed-count split will give "
                    "this student's work to the next one"
                ),
            )
        )

    clipped = _clipped_edges(page.edge_ink)
    if clipped:
        issues.append(
            ScanIssue(
                kind=ScanIssueKind.MARGIN_CLIPPED,
                severity="medium",
                page=page.page,
                detail=(
                    f"content runs off the {_join_words(clipped)} edge; part of an "
                    "answer may not be in the file at all"
                ),
            )
        )

    return issues


def _clipped_edges(
    edge_ink: tuple[float, float, float, float] | None,
) -> tuple[str, ...]:
    if edge_ink is None:
        return ()
    names = ("left", "right", "top", "bottom")
    return tuple(
        name
        for name, fraction in zip(names, edge_ink)
        if EDGE_INK_MIN <= fraction < EDGE_SATURATED
    )


def _join_words(words: Sequence[str]) -> str:
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-1]) + f" and {words[-1]}"


def measure_page(
    image, *, raster_dpi: float | None = None, page: int = 1
) -> PageScanFeatures:
    return PageScanFeatures(
        page=page,
        ink_fraction=_interior_ink_fraction(image),
        edge_ink=_edge_ink_fractions(image),
        dpi=raster_dpi,
    )


def detect_page_orientation(image) -> tuple[int, float]:
    import pytesseract
    from pytesseract import Output

    try:
        data = pytesseract.image_to_osd(image, output_type=Output.DICT)
    except Exception as exc:
        message = str(exc).lower()
        if isinstance(exc, pytesseract.TesseractNotFoundError) or any(
            marker in message for marker in _OSD_MISSING_MARKERS
        ):
            raise OrientationUnavailableError(str(exc) or exc.__class__.__name__)
        raise ValueError(str(exc) or exc.__class__.__name__) from exc

    try:
        rotation = int(data.get("rotate", 0)) % 360
        confidence = float(data.get("orientation_conf", 0.0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unreadable orientation result: {data!r}") from exc
    return rotation, confidence


_OSD_MISSING_MARKERS = (
    "osd.traineddata",
    "failed loading language 'osd'",
    "tesseract couldn't load any languages",
    "tessdata",
)


def detect_page_number(
    page, image, *, max_pages: int | None = None, rotation: int = 0
) -> tuple[int | None, PageNumberSource | None]:
    from opengrader.core.ocr.qr_reader import read_code_from_image
    from opengrader.core.submissions.reorder import (
        detect_page_number_tesseract,
        detect_page_number_text_layer,
    )

    number = detect_page_number_text_layer(page, max_pages=max_pages)
    if number is not None:
        return number, "text_layer"

    try:
        code = read_code_from_image(image)
    except Exception:
        code = None
    if code is not None and code.page is not None:
        return code.page, "qr"

    try:
        number = detect_page_number_tesseract(
            _upright_for_ocr(image, rotation), max_pages=max_pages
        )
    except Exception:
        return None, None
    if number is not None:
        return number, "ocr"
    return None, None


def _upright_for_ocr(image, rotation: int):
    angle = rotation % 360
    if not angle:
        return image
    try:
        return image.rotate(-angle, expand=True)
    except Exception:
        return image


def analyze_scan_pdf(
    pdf_path: str | Path,
    *,
    dpi: int = ANALYSIS_DPI,
    check_orientation: bool = True,
    check_page_order: bool = True,
    pages_per_submission: int | None = None,
    expected_page_count: int | None = None,
    single_submission: bool = False,
    dpi_floor: float = DEFAULT_DPI_FLOOR,
    progress_callback: Callable[[str], None] | None = None,
) -> ScanReport:
    import fitz

    from opengrader.core.pdf_utils import render_page_to_image

    path = Path(pdf_path)
    doc = fitz.open(path)
    features: list[PageScanFeatures] = []
    orientation_available = check_orientation
    orientation_note = "" if check_orientation else "orientation check skipped"

    try:
        total = len(doc)

        submission_length = pages_per_submission or expected_page_count
        if submission_length is None and single_submission and total:
            submission_length = total
        read_page_numbers = check_page_order and submission_length is not None

        for index in range(total):
            if progress_callback is not None:
                progress_callback(f"Checking page {index + 1}/{total}…")

            image, _width, _height = render_page_to_image(doc, index, dpi=dpi)
            measured = measure_page(
                image, raster_dpi=_page_raster_dpi(doc[index]), page=index + 1
            )

            if orientation_available:
                try:
                    rotation, confidence = detect_page_orientation(image)
                except OrientationUnavailableError as exc:
                    orientation_available = False
                    orientation_note = (
                        f"orientation could not be checked ({exc}); install "
                        "Tesseract's osd.traineddata to enable it"
                    )
                except ValueError:
                    pass
                else:
                    measured = replace(
                        measured,
                        rotation=rotation,
                        orientation_confidence=confidence,
                    )

            if read_page_numbers:
                upright_angle = (
                    _usable_rotation(
                        measured, min_confidence=DEFAULT_MIN_ORIENTATION_CONFIDENCE
                    )
                    or 0
                )
                number, source = detect_page_number(
                    doc[index],
                    image,
                    max_pages=submission_length,
                    rotation=upright_angle,
                )
                measured = replace(
                    measured, page_number=number, page_number_source=source
                )

            features.append(measured)
    finally:
        doc.close()

    return build_scan_report(
        features,
        source=str(path),
        orientation_available=orientation_available,
        orientation_note=orientation_note,
        pages_per_submission=pages_per_submission,
        expected_page_count=expected_page_count,
        submission_length=submission_length,
        check_page_order=check_page_order,
        dpi_floor=dpi_floor,
    )


def _interior_ink_fraction(image) -> float | None:
    from opengrader.core.pdf_utils import SCANNED_INK_LEVEL, ink_fraction

    width, height = getattr(image, "size", (0, 0))
    if width <= 0 or height <= 0:
        return None
    inset_x = int(width * INTERIOR_INSET)
    inset_y = int(height * INTERIOR_INSET)
    if width - 2 * inset_x <= 0 or height - 2 * inset_y <= 0:
        return ink_fraction(image, level=SCANNED_INK_LEVEL)
    return ink_fraction(
        image.crop((inset_x, inset_y, width - inset_x, height - inset_y)),
        level=SCANNED_INK_LEVEL,
    )


def _edge_ink_fractions(image) -> tuple[float, float, float, float] | None:
    from opengrader.core.pdf_utils import SCANNED_INK_LEVEL, ink_fraction

    width, height = getattr(image, "size", (0, 0))
    if width <= 0 or height <= 0:
        return None
    band_w = max(1, int(width * EDGE_BAND))
    band_h = max(1, int(height * EDGE_BAND))
    bands = (
        image.crop((0, 0, band_w, height)),
        image.crop((width - band_w, 0, width, height)),
        image.crop((0, 0, width, band_h)),
        image.crop((0, height - band_h, width, height)),
    )
    measured = [ink_fraction(band, level=SCANNED_INK_LEVEL) for band in bands]
    if any(fraction is None for fraction in measured):
        return None
    return tuple(measured)  # type: ignore[return-value]


def _page_raster_dpi(page) -> float | None:
    try:
        infos = page.get_image_info()
        page_area = page.rect.width * page.rect.height
    except Exception:
        return None
    if not infos or page_area <= 0:
        return None

    best: float | None = None
    for info in infos:
        try:
            bbox = info["bbox"]
            box_width = float(bbox[2]) - float(bbox[0])
            box_height = float(bbox[3]) - float(bbox[1])
            pixel_width = float(info["width"])
            pixel_height = float(info["height"])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if box_width <= 0 or box_height <= 0:
            continue
        if (box_width * box_height) / page_area < 0.5:
            continue
        dpi = min(pixel_width / (box_width / 72.0), pixel_height / (box_height / 72.0))
        best = dpi if best is None else max(best, dpi)
    return best


def apply_rotation_fixes(
    source: str | Path,
    destination: str | Path,
    angles: Mapping[int, int] | RotationPlan,
) -> int:
    """angles maps 1-based page numbers to clockwise degrees in quarter turns."""
    from pypdf import PdfReader, PdfWriter

    plan = angles.angles if isinstance(angles, RotationPlan) else angles
    source_path = Path(source)
    destination_path = Path(destination)

    reader = PdfReader(str(source_path))
    page_count = len(reader.pages)
    for page_number, angle in plan.items():
        if not 1 <= page_number <= page_count:
            raise ValueError(
                f"page {page_number} is outside {source_path} (1-{page_count})"
            )
        if angle % 90:
            raise ValueError(f"rotation must be a multiple of 90°, got {angle}°")

    writer = PdfWriter()
    turned = 0
    for index, page in enumerate(reader.pages, start=1):
        angle = plan.get(index, 0) % 360
        if angle:
            page.rotate(angle)
            page.rotation = page.rotation % 360
            turned += 1
        writer.add_page(page)

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with open(destination_path, "wb") as handle:
        writer.write(handle)
    return turned


def default_upright_path(source: str | Path) -> Path:
    path = Path(source)
    return path.with_name(f"{path.stem}-upright{path.suffix}")


def discover_scan_pdfs(input_path: str | Path) -> list[Path]:
    path = Path(input_path)
    if path.is_file():
        return [path]
    return sorted(
        candidate
        for candidate in path.glob("*.pdf")
        if candidate.is_file()
    )


def summarize_issues(issues: Iterable[ScanIssue]) -> str:
    counts = Counter(issue.severity for issue in issues)
    if not counts:
        return "no problems found"
    parts = [
        f"{counts[severity]} {severity}"
        for severity in ("high", "medium", "low")
        if counts.get(severity)
    ]
    return ", ".join(parts)
