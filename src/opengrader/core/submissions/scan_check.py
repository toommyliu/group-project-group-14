"""Scan pre-flight — the "scan doctor" that runs before anything is graded.

Every later stage of the pipeline assumes the scan is readable: the splitter
looks for a Name/ID header, the reorder pass reads a footer, the extractor
crops answer boxes by geometry and hands them to OCR. When that assumption
breaks the failure is **silent, not loud**. A stack fed into the scanner upside
down still splits, still extracts, still opens in the TUI — it just produces
empty transcriptions and a cohort of zeros, and nothing anywhere says why.

This module is the cheap check that runs *first* and says so out loud. Six
signals, chosen because each one silently poisons a different downstream stage:

* **Rotation** (:func:`plan_rotations`) — the expensive one. A 180° page still
  has an answer box exactly where the extractor expects one, so the crop
  succeeds and the OCR of upside-down handwriting comes back empty. Tesseract's
  orientation-and-script detection (OSD) answers this per page for the cost of
  one OCR pass.
* **Page order** (:func:`plan_page_order`) — the *worst* of the six, because it
  is the only one that produces confident, wrong grades rather than obviously
  missing ones. Everything downstream addresses a page by its position: a
  swapped pair means question 3's box is cropped off question 5's sheet,
  transcribed cleanly, and marked against the wrong rubric. Nothing raises.
* **Blank pages** — a duplex back side or a stray separator sheet shifts every
  page after it, so a fixed pages-per-submission split silently hands student
  N's work to student N+1.
* **Resolution** — below roughly 200 DPI, Tesseract's accuracy on handwriting
  falls off a cliff. The scan is fine to look at and useless to read.
* **Margin clipping** — content running off the edge means the answer the
  grader is about to mark is physically not in the file.
* **Page count** — a bundle whose page count is not a whole number of
  submissions is missing (or has gained) a sheet.

**Rotation is the only issue this module fixes**, and it is fixed the lossless
way: :func:`apply_rotation_fixes` sets the PDF page's ``/Rotate`` attribute
rather than re-rendering pixels, so no generation of quality is lost and every
downstream reader (PyMuPDF's renderer, pypdf's page copier, Tesseract through
both) sees an upright page. It always writes a **new file** — a scan is often
the only copy of a student's work, so nothing here overwrites an original.

Page order is deliberately **detected here and corrected elsewhere**. The two
halves of that split each have a reason. Detection belongs here because this is
the one pass that already renders every page, so reading the footer costs a crop
and an OCR of a thin strip rather than a second sweep of the document — and
because a check nobody asked for is the only kind that catches the scan whose
owner had no idea anything was wrong. Correction does *not* belong here because
reordering is a permutation of content, not a lossless attribute, and because it
can only be done per submission **after** the split: page numbers restart at 1
for every student, so a bundle has no single order to sort into. The fix lives
in ``core/submissions/reorder.py``, driven by ``submission-splitter``.

That split is also what makes the fix safe to automate, via
:func:`page_order_fix_is_safe`. A page number read from a QR code or a
born-digital text layer is *exact* — it decodes or it does not. One read by OCR
off a scanned footer is a *reading*, and a misread "6" for "8" would have the
reorder pass move a page that was in the right place all along. So an ordering
problem is corrected without asking only where the evidence is exact (or where a
blank template is supplying the true page sequence, which rejects impossible
readings); on OCR evidence alone it is **reported for a human**, on the same
"only act on evidence" principle that governs rotation below.

Two design rules make the report trustworthy:

* **Fail honestly, never quietly.** If OSD cannot run at all (no Tesseract, no
  ``osd.traineddata``), the report says ``orientation_available=False`` instead
  of reporting "no rotation problems" — a clean bill of health we never
  actually checked for is worse than no check at all. Per-page, a sheet with
  too little text to orient is listed as *unverified* rather than *fine*. The
  page-order check answers the same way: with no submission length to measure
  against it reports ``checked=False`` and says why, and a page whose number
  could not be read is listed as unread rather than counted as in sequence.
* **Only rotate on evidence.** A low-confidence OSD reading is treated as no
  reading. A page OSD cannot orient adopts the *stack's* rotation only when a
  strong majority of its readable neighbours agree on one — a scanner feeds a
  whole stack the same way round, which is exactly why the near-blank pages OSD
  can't read are upside down too.

The decision layer (everything from :func:`plan_rotations` to
:func:`build_scan_issues`) is pure and takes per-page measurements, so it is
fully unit-testable with no PDF, no Tesseract and no scanner; only
:func:`analyze_scan_pdf` and :func:`apply_rotation_fixes` touch files.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

ScanSeverity = str  # "high" | "medium" | "low", as elsewhere in core/submissions

# Where a page's own page number came from. The distinction is not trivia: the
# first two *decode*, the third *reads*. A QR symbol carries error correction and
# a born-digital text layer is the characters themselves, so either is right or
# absent — there is no third outcome. Tesseract on a scanned footer can return a
# confident 6 for an 8, which is the difference between a fix that can be applied
# unattended and one that has to be shown to a human first.
PageNumberSource = str  # "text_layer" | "qr" | "ocr"

# How much the page-order finding can be trusted, derived from the sources above
# by :func:`plan_page_order`. "exact" = every page number that was read came from
# a QR or a text layer; "ocr" = at least one was read off the pixels; "none" =
# nothing was read at all, so there is no finding either way.
PageOrderEvidence = str  # "exact" | "ocr" | "none"

_EXACT_PAGE_NUMBER_SOURCES = frozenset({"text_layer", "qr"})

# DPI the scan pre-flight renders at. High enough for Tesseract's orientation
# detection to be reliable, low enough that checking a 200-page bundle is a
# coffee break rather than an afternoon. This is *not* the extractor's DPI —
# nothing here is cropped or transcribed, only measured.
ANALYSIS_DPI = 150

# Effective raster resolution below which handwriting OCR degrades badly.
# 300 DPI is the classic Tesseract recommendation for small print; handwriting
# in a generous answer box survives lower, so 200 is the point where the scan
# is worth re-running rather than merely worth knowing about.
DEFAULT_DPI_FLOOR = 200.0

# Below this the scan is not "a bit soft", it is unreadable — flagged high so it
# outranks everything except a rotation, because no amount of grading care
# recovers detail the scanner never captured.
CRITICAL_DPI = 150.0

# Tesseract's OSD confidence is its own arbitrary scale (roughly 0-30, higher is
# better), not a probability. Readings below this are treated as no reading at
# all: rotation is the one auto-fixable issue, so a guess here would turn a
# correct page upside down rather than leave a wrong one alone.
DEFAULT_MIN_ORIENTATION_CONFIDENCE = 2.0

# A page whose interior carries less ink than this is blank paper — measured at
# ``pdf_utils.SCANNED_INK_LEVEL``, since at the strict cut a real scan is
# indistinguishable from an empty sheet. Measured, not guessed: on the sample
# scans a written page reads 0.0115-0.0140, an empty sheet with ordinary paper
# grain reads 0.000000, and a deliberately dirty one (dusty glass, heavy noise)
# still only reaches 0.0007. 0.003 therefore sits roughly 4x clear of both sides.
# A page carrying nothing but a printed footer can also land here — which is
# precisely why a blank page is *reported* (low severity) and never removed
# automatically.
BLANK_INK_FRACTION = 0.003

# Fraction of each side trimmed off before measuring "is this page blank".
# Scanner lids and misfed sheets leave dark borders and shadows around the edge
# that have nothing to do with what the student wrote; measuring the interior
# keeps a black frame from making an empty page look full.
INTERIOR_INSET = 0.04

# Thickness of the strip along each edge searched for clipped content, as a
# fraction of the page. A page trimmed by the feeder loses content *at* the cut,
# so the evidence is in a thin band right at the paper's edge.
EDGE_BAND = 0.012

# Ink in an edge band above this is real content running off the page rather
# than dust or a scanner rule. Generous by measurement: the margins of the
# sample scans read 0.0000 flat, so there is a wide gap to sit in.
EDGE_INK_MIN = 0.02

# ...and above *this* the band is essentially solid, which is a scanner shadow
# or a black border, not text. Clipping lives between the two: enough ink to be
# content, not so much as to be the machine's own frame. Reporting a lid shadow
# as "the student's answer is cut off" would train graders to ignore the check.
EDGE_SATURATED = 0.60

# How much of the readable stack must agree on one rotation before that angle is
# applied to pages OSD could not orient, and how many readable pages are needed
# for "the stack" to mean anything at all. A single readable page is an anecdote;
# three quarters of a bundle pointing the same way is how a stack gets fed.
DOMINANT_ROTATION_MAJORITY = 0.75
MIN_PAGES_FOR_DOMINANT_ROTATION = 2

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


class ScanIssueKind(str, Enum):
    """What is wrong with a scan. Values are the stable JSON/CLI identifiers."""

    ROTATED_PAGE = "rotated_page"
    PAGE_ORDER = "page_order"
    BLANK_PAGE = "blank_page"
    LOW_RESOLUTION = "low_resolution"
    MARGIN_CLIPPED = "margin_clipped"
    PAGE_COUNT_MISMATCH = "page_count_mismatch"


class OrientationUnavailableError(RuntimeError):
    """Tesseract's orientation detection could not run at all.

    Distinct from "this page has too little text to orient", which is an
    ordinary per-page outcome. This one means the *tool* is missing (no
    Tesseract binary, no ``osd.traineddata``), so the whole rotation check has
    to be reported as not-performed rather than as passed.
    """


@dataclass(frozen=True)
class ScanIssue:
    """One problem found in a scan, ready to print or serialize.

    ``page`` is 1-based, or ``None`` for a whole-document finding (page count).
    ``fixable`` marks the issues :func:`apply_rotation_fixes` can correct — today
    only rotation, since nothing can invent resolution or un-clip a margin.
    """

    kind: ScanIssueKind
    severity: ScanSeverity
    page: int | None
    detail: str
    fixable: bool = False


@dataclass(frozen=True)
class PageScanFeatures:
    """Everything measured from one rendered page, in one pass.

    Every field is ``None``-able and ``None`` always means *not measurable*,
    never *fine* — the decision layer must be able to tell a page that passed a
    check from a page the check could not be run on.
    """

    page: int  # 1-based
    # Clockwise degrees the page must be turned to stand upright, per Tesseract
    # OSD. ``None`` = OSD gave no usable reading for this page.
    rotation: int | None = None
    orientation_confidence: float | None = None
    # Ink fraction of the page's interior (see INTERIOR_INSET).
    ink_fraction: float | None = None
    # Ink fraction of the (left, right, top, bottom) edge bands.
    edge_ink: tuple[float, float, float, float] | None = None
    # Effective resolution of the page's dominant raster image. ``None`` for a
    # born-digital page, whose vector text has no resolution to be short of.
    dpi: float | None = None
    # The page number this sheet says it is, read off its own footer or QR code.
    # ``None`` = no number could be read, which is an ordinary outcome: an
    # unnumbered title page has none to read, and a poor scan may hide one.
    page_number: int | None = None
    # Which tier read it (see PageNumberSource). ``None`` exactly when
    # ``page_number`` is ``None``; kept as its own field rather than inferred so
    # the trust level survives into the report and the JSON.
    page_number_source: PageNumberSource | None = None


@dataclass(frozen=True)
class RotationPlan:
    """Which pages to turn, by how much, and how sure we are.

    ``angles`` maps 1-based page number to clockwise degrees. ``inferred_pages``
    are the ones taking the stack's dominant rotation because their own
    orientation could not be read — worth showing a human separately, since they
    rest on the stack's evidence rather than their own. ``unverified_pages`` are
    the pages with no usable reading *and* no dominant rotation to fall back on:
    genuinely unchecked, and reported as such rather than as clean.
    """

    angles: Mapping[int, int] = field(default_factory=dict)
    inferred_pages: tuple[int, ...] = ()
    unverified_pages: tuple[int, ...] = ()
    dominant: int | None = None

    def __bool__(self) -> bool:
        """Truthy when there is something to rotate."""
        return bool(self.angles)


@dataclass(frozen=True)
class PageOrderWindow:
    """One submission's worth of pages whose numbering does not run forwards.

    ``first_page``/``last_page`` are 1-based positions in the *document* (so in a
    bundle the third submission's window is pages 13-18, not 1-6), and
    ``numbers`` is what each of those pages read, ``None`` where nothing could be
    read. ``break_page`` is the first position whose number does not advance on
    the one before it — the single most useful thing to print, since it points at
    the sheet to pick up rather than at the range to squint through.
    """

    first_page: int
    last_page: int
    numbers: tuple[int | None, ...] = ()
    break_page: int = 0


@dataclass(frozen=True)
class PageOrderPlan:
    """What the scan's own page numbers say about the order its pages sit in.

    ``checked`` is the honest counterpart of ``ScanReport.orientation_available``,
    and it lives here rather than on the report because the two "could not check"
    answers have different causes: orientation fails because a *tool* is missing,
    discovered mid-document, while page order goes unchecked because the caller
    could not say how long a submission is — a fact known before any page is
    read. ``note`` says which it was, in words meant for the person running it.

    Deliberately **not** a list of "wrong" pages. A page number is only evidence
    relative to its neighbours, so the finding is per submission window; calling
    an individual page misplaced would mean choosing between the two sides of a
    swap with nothing to choose on.
    """

    submission_length: int | None = None
    detected: tuple[int | None, ...] = ()
    unread_pages: tuple[int, ...] = ()
    out_of_order_windows: tuple[PageOrderWindow, ...] = ()
    evidence: PageOrderEvidence = "none"
    checked: bool = False
    note: str = ""

    def __bool__(self) -> bool:
        """Truthy when at least one submission's pages are out of order."""
        return bool(self.out_of_order_windows)


@dataclass(frozen=True)
class ScanReport:
    """The pre-flight verdict for one PDF."""

    source: str
    page_count: int
    features: tuple[PageScanFeatures, ...] = ()
    issues: tuple[ScanIssue, ...] = ()
    rotation_plan: RotationPlan = field(default_factory=RotationPlan)
    page_order_plan: PageOrderPlan = field(default_factory=PageOrderPlan)
    # False when OSD could not run at all — see OrientationUnavailableError. The
    # caller must not present the report as "no rotation problems" in that case.
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


# --------------------------------------------------------------------------- #
# The pure decision layer
# --------------------------------------------------------------------------- #


def dominant_rotation(
    features: Sequence[PageScanFeatures],
    *,
    min_confidence: float = DEFAULT_MIN_ORIENTATION_CONFIDENCE,
) -> int | None:
    """The one non-zero rotation a strong majority of readable pages agree on.

    Returns ``None`` when the stack is upright, split, or too small to have an
    opinion. This is what lets a near-blank page — the kind OSD can never
    orient, and the kind an exam is full of — be turned with the rest of the
    stack it was fed in with.
    """
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
    """Decide what to turn, from per-page orientation readings."""
    dominant = dominant_rotation(features, min_confidence=min_confidence)
    angles: dict[int, int] = {}
    inferred: list[int] = []
    unverified: list[int] = []

    for page in features:
        angle = _usable_rotation(page, min_confidence=min_confidence)
        if angle is None:
            # No reading of its own: follow the stack if the stack is decisive,
            # otherwise record it as genuinely unchecked.
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
    """Decide whether the pages sit in the order their own numbers claim.

    The test is that each submission's page numbers **run forwards**, not that
    they equal their position. That is deliberate and it is what keeps the check
    quiet on scans that are perfectly fine: an exam with an unnumbered cover
    sheet reads ``None, 1, 2, 3`` where its positions are 1-4, and a template
    that prints "Page 1 of 3" starting on the second sheet does the same. Both
    would light up every page of an equals-its-position check, and a check that
    cries wolf on ordinary exams is one people switch off. Going *backwards*,
    meanwhile, has no innocent explanation — nor does the same number twice.

    ``submission_length`` is required because a bundle's numbering restarts at 1
    for every student, so without it there is no way to tell a legitimate reset
    from a page that belongs 40 sheets earlier. With none given the plan comes
    back ``checked=False``: an expectation nobody supplied is not a passed check.
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
        # Nothing to reason from. Reported as unchecked rather than as a window
        # full of ``None``s that trivially "runs forwards": an exam whose footers
        # are all unreadable must not come back looking like an exam in order.
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
        # Windows are cut by counting, so a bundle that is not a whole number of
        # submissions has no trustworthy boundaries: every window after the
        # missing sheet would straddle two students and read as jumbled. That is
        # an artefact of the page count, which is already reported at high
        # severity — and reporting it a second time as dozens of ordering
        # problems would bury the one finding worth acting on.
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
    """Whether an ordering problem may be corrected without asking a human.

    The single definition of that judgement, so the splitter and the setup screen
    can never disagree about when reordering runs by itself. Two ways to qualify:

    * the page numbers were **decoded, not read** — every one came from a QR code
      or a born-digital text layer, where a wrong answer is not a thing that
      happens; or
    * a **blank template** is available, which supplies the true sequence of
      printed page labels and lets the reorder pass reject any reading the
      template never prints — so a misread number moves nothing.

    Otherwise the numbers came off scanned pixels, where a 6 read as an 8 would
    have the reorder pass move a page that was in the right place. There the
    finding is worth showing to somebody and the fix is worth their say-so.
    """
    if not plan:
        return False
    return plan.evidence == "exact" or has_blank_template


def _first_backward_page(chunk: Sequence[PageScanFeatures]) -> int | None:
    """The 1-based page where this window's numbering stops going forwards.

    Pages with no reading are skipped rather than treated as a break — they carry
    no evidence, and an unnumbered cover between two numbered sheets must not
    read as a jump backwards.
    """
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
    """How much the readings can be trusted — the weakest source wins."""
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
    """Flag a bundle that is not a whole number of submissions.

    With a fixed page count per student, a bundle with a spare or missing sheet
    does not fail — it shifts every boundary after the gap, so the last students
    in the stack are graded on someone else's paper.
    """
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
    """Flag one submission PDF whose length differs from what it should be.

    The expectation is worded without naming its source, because the caller may
    have taken it from a blank template *or* from a stated pages-per-submission
    — and a message that claims "the template" when no template was given sends
    the reader looking for a file that does not exist.
    """
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
    """Turn per-page measurements into a prioritized issue list.

    ``pages_per_submission`` adds the bundle divisibility check (one scan holding
    many students); ``expected_page_count`` the exact-length check (one scan
    holding one student, measured against the template). Pass neither when the
    expected shape is unknown — an absent expectation is not a passed check.
    Either one also gives the page-order check its submission length, since both
    are saying the same thing: this is how many pages one student's work is.

    ``rotation_plan`` and ``page_order_plan`` may be passed in when the caller has
    already computed them (:func:`build_scan_report` does), so the plan that is
    reported and the plan that is applied are always the same object.
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

    # Most severe first, then in reading order, with document-level findings
    # ahead of per-page ones at the same severity — a wrong page count explains
    # the individual oddities below it.
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
    """Assemble the full report from measurements (pure; no files touched).

    ``submission_length`` overrides where the page-order check gets its window
    from. It exists for the one case the two page-count expectations cannot
    express: a directory of already-split PDFs with no template given, where each
    file *is* one submission and its own length is the answer — an expectation
    that would be a tautology if fed to ``expected_page_count`` (a file is always
    as long as itself) but is exactly the right window for reading page numbers.
    """
    plan = plan_rotations(features, min_confidence=min_orientation_confidence)
    if check_page_order:
        order_plan = plan_page_order(
            features,
            submission_length=(
                submission_length or pages_per_submission or expected_page_count
            ),
        )
    else:
        # Mirrors ``orientation_note``'s "…skipped": a check that was turned off
        # has to be visibly absent, never quietly passed.
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
    """A page's orientation reading, or ``None`` if it is not worth acting on."""
    if page.rotation is None:
        return None
    confidence = page.orientation_confidence
    if confidence is not None and confidence < min_confidence:
        return None
    return page.rotation % 360


def _page_order_issues(plan: PageOrderPlan) -> list[ScanIssue]:
    """One finding per submission whose pages do not run forwards.

    Per window rather than per page on purpose: a swap makes two pages disagree
    and nothing says which of them moved, so naming one would be a coin toss
    dressed up as a diagnosis. The window plus the page where the sequence turns
    back is the most precise honest statement available.

    ``fixable`` stays ``False`` even when :func:`page_order_fix_is_safe` would
    allow an automatic correction, because that flag means specifically "``--fix``
    on this tool can repair it", and reordering is not something this tool can
    do — it happens per submission after the split. The remedy is named in the
    text instead, and differs by evidence: with exact page numbers it is a switch
    to turn on, and with OCR-read ones it is a pair of eyes.
    """
    if not plan.out_of_order_windows:
        return []

    if plan.evidence == "exact":
        remedy = (
            "these page numbers are exact (QR code or PDF text layer), so "
            "splitting with --fix-order will put them back in order"
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
    """Render a window's readings, with "?" for a page that had none."""
    return ", ".join("?" if number is None else str(number) for number in numbers)


def _page_issues(
    page: PageScanFeatures,
    *,
    dpi_floor: float,
    blank_ink_fraction: float,
) -> list[ScanIssue]:
    """Non-rotation findings for a single page."""
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
    """Which edges carry content running off the page.

    A band is only evidence of clipping between the two thresholds: below
    ``EDGE_INK_MIN`` it is dust or a printed rule, above ``EDGE_SATURATED`` it is
    the scanner's own shadow or a black border, and calling either one a cut-off
    answer would make the check noise a grader learns to skip.
    """
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


# --------------------------------------------------------------------------- #
# Measurement (renders pages; needs Tesseract for orientation only)
# --------------------------------------------------------------------------- #


def measure_page(
    image, *, raster_dpi: float | None = None, page: int = 1
) -> PageScanFeatures:
    """Measure one already-rendered page.

    Split out from :func:`analyze_scan_pdf` so the image-level measurements can
    be exercised on a plain PIL image with no PDF involved. Orientation is
    *not* included here — it is the one measurement that can fail globally, and
    that decision belongs to the caller driving the whole document.

    ``raster_dpi`` is the *scan's own* resolution (from :func:`_page_raster_dpi`),
    which has nothing to do with the DPI ``analyze_scan_pdf`` renders at — hence
    the different name, since confusing the two would silently compare the
    scanner's output against our own render setting.
    """
    return PageScanFeatures(
        page=page,
        ink_fraction=_interior_ink_fraction(image),
        edge_ink=_edge_ink_fractions(image),
        dpi=raster_dpi,
    )


def detect_page_orientation(image) -> tuple[int, float]:
    """Clockwise degrees needed to upright a page, and OSD's confidence in it.

    Raises :class:`OrientationUnavailableError` when Tesseract's OSD cannot run
    at all (missing binary or missing ``osd.traineddata``) — a whole-document
    failure the caller must report rather than swallow. A page that merely has
    too little text to orient raises ``ValueError``, which is an ordinary
    per-page outcome.
    """
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
        # "Too few characters", an unreadable render, an odd page — this page
        # simply has no orientation to read, and the next one might.
        raise ValueError(str(exc) or exc.__class__.__name__) from exc

    try:
        rotation = int(data.get("rotate", 0)) % 360
        confidence = float(data.get("orientation_conf", 0.0))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"unreadable orientation result: {data!r}") from exc
    return rotation, confidence


# Substrings that mean OSD is not installed, as opposed to not applicable here.
_OSD_MISSING_MARKERS = (
    "osd.traineddata",
    "failed loading language 'osd'",
    "tesseract couldn't load any languages",
    "tessdata",
)


def detect_page_number(
    page, image, *, max_pages: int | None = None, rotation: int = 0
) -> tuple[int | None, PageNumberSource | None]:
    """Read what page number a sheet claims to be, cheapest and surest first.

    Returns the number and which tier produced it, or ``(None, None)`` when no
    tier could read one — an ordinary outcome, not a failure.

    The tiers are deliberately the first three of ``reorder.reorder_pdf_pages``'s
    cascade, sharing its detectors so the pre-flight and the fix can never
    disagree about what a page says it is. What is **not** shared is that
    function's fourth tier, the Ollama vision model: a pre-flight has to run on
    any machine, and one that quietly needed a language-model server to answer
    "is my scan alright?" would either fail on the laptop that most needs it or
    turn a quick check into a long one. The cost of leaving it out is only that a
    stylized handwritten page number goes unread, which the report says plainly.

    ``rotation`` is the clockwise angle this page needs to stand upright, and it
    matters for the OCR tier alone: a footer only reads as a footer when the page
    is the right way up, so an upside-down stack would otherwise report every
    page number as unreadable and the order check would silently do nothing on
    exactly the scans in the worst trouble. QR codes carry their own orientation
    and a text layer has no pixels, so neither needs the turn.
    """
    from opengrader.core.ocr.qr_reader import read_code_from_image
    from opengrader.core.submissions.reorder import (
        detect_page_number_tesseract,
        detect_page_number_text_layer,
    )

    # Tier 1 — born-digital text layer: exact, and free of both rasterization
    # and OCR. A scanned page has no text layer and returns None.
    number = detect_page_number_text_layer(page, max_pages=max_pages)
    if number is not None:
        return number, "text_layer"

    # Tier 2 — an Opengrader QR code, if the sheet was generated with them. The
    # page is already rendered, so this costs a decode, and it is exact.
    try:
        code = read_code_from_image(image)
    except Exception:
        code = None  # best-effort, exactly as every other QR read site treats it
    if code is not None and code.page is not None:
        return code.page, "qr"

    # Tier 3 — OCR of the footer strip. A reading rather than a decode, which is
    # what `page_order_fix_is_safe` keys on.
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
    """Turn a page image upright for OCR, losslessly.

    ``rotation`` is clockwise degrees to upright (OSD's convention, the same one
    :func:`apply_rotation_fixes` writes into ``/Rotate``); PIL's ``rotate`` turns
    counter-clockwise, hence the negation. ``expand=True`` keeps a 90°/270° turn
    from cropping the page to its old aspect ratio. Multiples of 90 resample
    nothing, so this costs a transpose and no image quality.
    """
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
    """Run the pre-flight over one PDF.

    Each page is rendered **once** and every measurement taken off that single
    image (the same pattern as ``boundary._analyze_pages``), because rendering
    dominates the cost of everything here except OSD itself. The page-number read
    rides on that same image, which is most of why detecting a mis-ordered scan
    belongs in this pass rather than in a sweep of its own.

    The moment OSD proves unavailable the orientation check is abandoned for the
    whole document rather than retried per page — it is a missing tool, not a
    difficult page — and the report says so.

    ``single_submission`` marks a PDF that holds one student's work, so its own
    page count is how long a submission is. It is what lets ``scan-check`` read
    the page order of an already-split directory with no template to hand; on a
    bundle it must stay false, or every student's restart at page 1 would read as
    a jump backwards.

    Page-number reading is skipped entirely when no submission length can be
    resolved, since :func:`plan_page_order` could not use the result anyway —
    that keeps a bundle checked without ``--pages-per-submission`` exactly as
    fast as it was before this check existed.
    """
    import fitz

    from opengrader.core.pdf_utils import render_page_to_image

    path = Path(pdf_path)
    doc = fitz.open(path)
    features: list[PageScanFeatures] = []
    orientation_available = check_orientation
    orientation_note = "" if check_orientation else "orientation check skipped"

    try:
        total = len(doc)

        # Resolved before the loop because it decides whether the page-number
        # read is worth doing at all, and it doubles as the sanity bound on what
        # counts as a plausible page number.
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
                    # Missing tool, not a hard page: stop asking, and make sure
                    # the report cannot be read as "rotation checked, all good".
                    orientation_available = False
                    orientation_note = (
                        f"orientation could not be checked ({exc}); install "
                        "Tesseract's osd.traineddata to enable it"
                    )
                except ValueError:
                    pass  # too little text on this page — left unverified
                else:
                    # `replace` rather than a fresh PageScanFeatures: rebuilding
                    # it field by field silently drops any measurement added to
                    # the dataclass later, which is a bug that shows up as a
                    # check quietly never firing rather than as an error.
                    measured = replace(
                        measured,
                        rotation=rotation,
                        orientation_confidence=confidence,
                    )

            if read_page_numbers:
                # Straighten the page for the OCR tier using its *own* reading
                # only. The stack-majority fallback in `plan_rotations` is not
                # available yet (it needs every page measured), and guessing here
                # would read a footer off a page we have no evidence about.
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
    """Ink fraction of the page with its edges trimmed (see INTERIOR_INSET)."""
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
    """Ink fraction of the (left, right, top, bottom) edge bands."""
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
    """Effective resolution of the scanned image filling a page, if there is one.

    A scan is one big image stretched over the page, so its real resolution is
    the image's pixel size divided by the page's physical size. Returns ``None``
    for a born-digital page (vector text has no DPI to be short of) and for a
    page whose images are all small insets, where "the scan's resolution" is not
    a meaningful question.
    """
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
        # Only an image covering most of the sheet is "the scan"; a logo or a
        # figure pasted into a born-digital page is not.
        if (box_width * box_height) / page_area < 0.5:
            continue
        dpi = min(pixel_width / (box_width / 72.0), pixel_height / (box_height / 72.0))
        best = dpi if best is None else max(best, dpi)
    return best


# --------------------------------------------------------------------------- #
# The fix
# --------------------------------------------------------------------------- #


def apply_rotation_fixes(
    source: str | Path,
    destination: str | Path,
    angles: Mapping[int, int] | RotationPlan,
) -> int:
    """Write a copy of ``source`` with the given pages turned upright.

    ``angles`` maps **1-based** page numbers to clockwise degrees (a
    :class:`RotationPlan` is accepted directly). The rotation is recorded as the
    PDF page's ``/Rotate`` attribute rather than re-rasterized, so the fix is
    lossless, instant, and honoured by every reader downstream — PyMuPDF renders
    the turned page, and pypdf carries the attribute into each split file.

    Always writes a **new file**: the input scan is frequently the only copy of a
    student's work, and a wrong orientation guess must never be able to damage
    it. Returns the number of pages turned.

    Raises ``ValueError`` for an angle that is not a multiple of 90 or a page
    number outside the document, since either means the caller's plan does not
    match the file it is being applied to.
    """
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
            # ``rotate`` accumulates, so correcting a 180° page leaves /Rotate at
            # 360. Readers apply it modulo 360 and PyMuPDF copes, but the value
            # is meant to be the page's final orientation, not a running total —
            # normalizing keeps the file honest for whatever reads it next.
            page.rotation = page.rotation % 360
            turned += 1
        writer.add_page(page)

    destination_path.parent.mkdir(parents=True, exist_ok=True)
    with open(destination_path, "wb") as handle:
        writer.write(handle)
    return turned


def default_upright_path(source: str | Path) -> Path:
    """Where a corrected copy of ``source`` goes when the caller names no path."""
    path = Path(source)
    return path.with_name(f"{path.stem}-upright{path.suffix}")


def discover_scan_pdfs(input_path: str | Path) -> list[Path]:
    """PDFs to check: the file itself, or every PDF directly in a directory.

    Deliberately not recursive — a split directory is flat, and walking into
    subdirectories would sweep up annotated copies and template PDFs that are
    not scans at all.
    """
    path = Path(input_path)
    if path.is_file():
        return [path]
    return sorted(
        candidate
        for candidate in path.glob("*.pdf")
        if candidate.is_file()
    )


def summarize_issues(issues: Iterable[ScanIssue]) -> str:
    """One-line count of what was found, for a status line."""
    counts = Counter(issue.severity for issue in issues)
    if not counts:
        return "no problems found"
    parts = [
        f"{counts[severity]} {severity}"
        for severity in ("high", "medium", "low")
        if counts.get(severity)
    ]
    return ", ".join(parts)
