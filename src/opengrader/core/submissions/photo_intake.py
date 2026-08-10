"""Phone-photo intake — turn a folder of JPEG/HEIC/PNG photos into per-student PDFs.

Real classes hand in phone photos, not clean scanner bundles: a folder of
JPEGs from a phone-upload form, one HEIC per page, sometimes a whole exam shot
crooked on a desk. Until this module existed that folder was simply not
ingestible — every other stage of the pipeline (``submission-splitter``,
``submission-extractor``, the TUI) assumes a flat, already-deskewed,
one-PDF-per-student scan. That gap is an adoption wall for any class that
can't get to a real scanner, which in practice is most of them.

The hard geometry problem here — recovering a flat page from a keystoned
photograph — is already solved in ``core/ocr/omr.py`` for the bubble-sheet
path: find four corner registration squares, solve the perspective transform
they imply, warp. This module generalizes that idea to *any* exam page, with
a second strategy (document-edge detection) for when there are no
Opengrader-drawn registration marks to find, because the whole point is to
also ingest exams Opengrader did not generate.

Two decisions this module makes on purpose, recorded here so they are not
accidentally reversed later:

* **Fiducials before edges, always.** A page with Opengrader's own corner
  squares can be registered exactly — the same code path the bubble-sheet
  reader trusts to score real answers — while edge detection is a heuristic
  that can be fooled by a table edge or a strong shadow. When both might
  apply, the more accurate one wins by being tried first and returning
  immediately on success; edge detection never even runs.
* **Fail soft, never hard.** If no plausible page quad can be found at all,
  the photo is *not* dropped. It falls back to a best-effort deskew (small
  in-plane rotation only, no keystone correction) and the report says why.
  A flat, un-rectified import still has a real chance downstream — the
  extractor does its own layout detection per page — while a submission that
  was refused outright has none. Losing a student's exam because a table
  edge was in frame is a far worse failure than importing a mediocre page.
  See :func:`rectify_image`.

A third, smaller rule: **inputs are read-only.** Every image under
``--input`` is opened and never written to; every output (rectified pages,
the PDFs built from them) goes under ``--output``. ``scan-check --fix``
established this rule for scans (it always writes a corrected *copy*) and
this module follows it for the same reason — a phone photo is frequently the
only copy of a student's work.

DPI policy: the embedded PDF pages must clear ``DEFAULT_DPI_FLOOR`` (imported
from ``core/submissions/scan_check.py`` rather than repeated — that constant
is the handwriting-OCR floor the rest of the pipeline already enforces, and
two copies of it would drift). A modern phone photo is comfortably above that
floor already, so the native resolution is always kept; only a distant or
low-resolution capture gets upscaled, and only up to the floor, never beyond
it — inventing pixels does not invent detail.
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

import numpy as np
from PIL import Image, ImageOps

from opengrader.core.submissions.scan_check import DEFAULT_DPI_FLOOR
from opengrader.core.submissions.splitter import _sanitize_filename

if TYPE_CHECKING:  # pragma: no cover - typing only
    # cv2 (opencv, ~120ms cold) is imported lazily at each call site below —
    # photo intake is an opt-in ingest path (a folder of phone photos), not
    # something every TUI startup needs, and this module is loaded eagerly by
    # the setup screen.
    import cv2

# --------------------------------------------------------------------------- #
# Tunables
# --------------------------------------------------------------------------- #

# Physical page sizes this module knows how to target, in inches
# (width, height), portrait. The dict — not the CLI's --paper choices — is
# the thing to extend if a class hands in a different paper size.
PAPER_SIZES_IN: dict[str, tuple[float, float]] = {
    "letter": (8.5, 11.0),
    "a4": (8.27, 11.69),
}

# The DPI floor this module clears rather than repeats. See scan_check.py's
# DEFAULT_DPI_FLOOR for where the number itself comes from and why it's
# there — this module just imports it so the two can never drift apart.
DEFAULT_OUTPUT_DPI = DEFAULT_DPI_FLOOR

# How close a measured quad's aspect ratio has to be to the target paper's
# before it gets snapped to it exactly. Perspective correction of a photo
# taken a few degrees off square still measures a slightly wrong ratio even
# after the transform is solved (the corner-finding itself has a pixel or two
# of slop) — left uncorrected, that error stretches every normalized bbox
# every later stage reads by the same fraction. 4% comfortably covers that
# slop while still leaving room to *not* force a document that genuinely
# isn't letter/A4 shaped (a half-sheet, a workbook page) into the wrong box.
ASPECT_SNAP_TOLERANCE = 0.04

# Minimum area, as a fraction of the whole photo frame, for a 4-point contour
# to be considered "the page" during edge detection, rather than a shadow, a
# desk edge, or a second sheet caught in the background. Deliberately
# generous — a page photographed with reasonable framing fills most of the
# shot, so a smaller quad is more likely to be furniture than paper.
MIN_DOCUMENT_AREA_FRACTION = 0.30

# How far the detected page quad is pulled inward before warping, as a fraction
# of the page. The detected boundary lies on the paper/background edge, so
# warping it as measured drags a rim of desk, table or floor into the rectified
# page — see `_warp_quad` for the two things that rim goes on to break.
#
# The rim has two sources and the larger one sets this number. Sampling bleed at
# the boundary is only a pixel or two; the real cost is *edge-localization
# error* — Canny plus `approxPolyDP` on a slightly out-of-focus paper edge
# routinely places a side several pixels off, and always outward on the side it
# gets wrong. Measured against the synthetic photos in
# `tests/unit/core/submissions/test_photo_intake.py`, 0.5% still left a visible
# band on one edge and 1% cleared every edge at both the test's page scale and a
# full-resolution phone photo's. 1% of a US-Letter sheet is about 2 mm, which no
# exam writes in, and the rectified marker position is unchanged to within half
# a percent — so this buys the fix without measurably moving the geometry
# everything downstream reads.
QUAD_INSET_FRACTION = 0.01

# Canny's own thresholds are derived per-image from the median pixel value
# (see `_auto_canny`) rather than fixed, because a phone photo's exposure
# swings far more than a scanner's ever does. CANNY_SIGMA is how far the two
# thresholds are allowed to spread from that median — the standard
# "auto-canny" heuristic, chosen over one fixed pair because no single
# (50, 150)-style pair survives both a sunlit desk and a dim classroom.
CANNY_SIGMA = 0.33

# Dilation after Canny closes small gaps in the page outline (a crease, a
# staple-shadow break) so `findContours` sees one closed boundary instead of
# several arcs that never connect into a quad.
EDGE_DILATE_ITERATIONS = 2

# `approxPolyDP`'s epsilon as a fraction of the contour's perimeter. Large
# enough that a slightly bowed page edge (paper is never perfectly flat)
# still collapses to 4 points; small enough that a genuinely 5- or 6-sided
# blob (a hand, a book spine) doesn't get forced into a quad it isn't.
APPROX_POLY_EPSILON_FRACTION = 0.02

# Gaussian blur applied before Otsu-binarizing a photo for fiducial search.
# The extra blur (beyond what `omr._binarize` does for a rendered scan) is
# there because a phone sensor adds real noise a PDF render never has.
FIDUCIAL_BLUR_KERNEL = (5, 5)

# Mild CLAHE clip limit for the lighting-normalization pass. Kept low on
# purpose — see `_normalize_lighting`'s docstring for what this can and
# cannot do to a faint pencil mark.
LIGHTING_CLAHE_CLIP_LIMIT = 1.5
LIGHTING_CLAHE_TILE_GRID = (8, 8)

# Small-angle rotation search for the deskew-only fallback, in degrees. A
# phone photo that could not be quad-rectified is usually still held roughly
# upright by the person taking it, so this recovers the "roughly" without
# attempting the harder keystone problem the quad detector already failed at.
DESKEW_MAX_ANGLE = 15.0

# Image extensions this module will pick up out of a directory. HEIC/HEIF
# need the optional `photos` extra to actually decode — see `_heif_available`.
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".heic", ".heif"})
_HEIC_SUFFIXES = frozenset({".heic", ".heif"})

# Deliberately optional, the same way `math`'s pix2tex is (see pyproject.toml
# for that comment, which this one is modelled on). pillow-heif exists solely
# to let Pillow decode Apple's default photo format; an instructor whose
# students all send JPEGs — the common case — should never be made to install
# it. `_heif_available` uses `importlib.util.find_spec` rather than a bare
# import so a JPEG/PNG-only run costs nothing to check and never imports the
# package it isn't using.
PHOTOS_EXTRA = "uv sync --extra photos"

# Splitter naming convention (see core/submissions/splitter.py's
# plan_submission_ranges): a fixed prefix and zero-padded index for
# submissions that carry no other name. Matched here, not repeated as a
# magic literal, so a submission this tool writes and one the splitter writes
# always look the same to a human scanning an output directory.
DEFAULT_SUBMISSION_PREFIX = "submission"
DEFAULT_ZERO_PAD_WIDTH = 4


class PhotoIntakeError(RuntimeError):
    """A problem with the input this module cannot work around.

    Distinct from a single unreadable photo (recorded as an ``ImageOutcome``
    with ``method="unreadable"`` and the run continues) — this is for
    conditions that make the whole run meaningless: no images found, an
    unknown paper size, or a HEIC photo with no way to decode it.
    """


# --------------------------------------------------------------------------- #
# Report shapes
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ImageOutcome:
    """What happened to one input photo.

    ``method`` is one of ``"fiducials"``, ``"edges"``, ``"deskew"`` (rectified,
    in decreasing order of confidence) or ``"unreadable"`` (the file could not
    be decoded at all and was dropped). ``note`` explains *why* when that isn't
    obvious from the method alone — e.g. why edge detection fell through to
    deskew, or the decode error for an unreadable file.
    """

    source: Path
    method: str
    note: str = ""


@dataclass(frozen=True)
class StudentSubmission:
    """One PDF written by this run, and the photos that went into it.

    ``token`` is the filename/subdirectory-derived student identifier when
    the grouping rule found one, and ``None`` when the file is named by
    position instead (see ``_output_filename``).
    """

    token: str | None
    output_pdf: Path
    pages: tuple[Path, ...]


@dataclass(frozen=True)
class PhotoIntakeReport:
    """The full outcome of one ``import_photos`` run."""

    grouping_mode: str
    outcomes: tuple[ImageOutcome, ...]
    submissions: tuple[StudentSubmission, ...]
    output_dir: Path
    dpi: float


@dataclass(frozen=True)
class PhotoGroup:
    """A candidate student's photos, before rectification runs."""

    token: str | None
    images: tuple[Path, ...]


# --------------------------------------------------------------------------- #
# Natural sort — see the module docstring; a plain sort is a real defect here
# --------------------------------------------------------------------------- #

_NUMERIC_CHUNK_RE = re.compile(r"(\d+)")


def _natural_sort_key(name: str) -> tuple:
    """Numeric-aware sort key so "p2" sorts before "p10".

    A plain lexicographic sort compares character by character and puts
    "p10" before "p2" ('1' < '2'), which silently reverses two of a
    student's pages the moment a submission reaches ten photos — exactly the
    kind of defect that stays invisible until a class is large enough to
    trip it.
    """
    parts = _NUMERIC_CHUNK_RE.split(name)
    return tuple(int(part) if part.isdigit() else part.lower() for part in parts)


def discover_images(input_path: Path) -> list[Path]:
    """Every photo directly in ``input_path``, naturally sorted.

    Not recursive — mirrors ``scan_check.discover_scan_pdfs``, which is flat
    for the same reason: a directory of per-student subfolders is a different,
    deliberately separate case (see `discover_student_subdirectories`), and
    walking into it here would double-count those images.
    """
    if input_path.is_file():
        return [input_path]
    files = [
        path
        for path in input_path.iterdir()
        if path.is_file()
        and path.suffix.lower() in IMAGE_SUFFIXES
        and not path.name.startswith(".")
    ]
    return sorted(files, key=lambda path: _natural_sort_key(path.name))


def discover_student_subdirectories(input_path: Path) -> list[Path] | None:
    """One subdirectory per student, if that is what ``input_path`` holds.

    Returns ``None`` (meaning: try the next grouping rule) unless *every*
    image under ``input_path`` lives inside a subdirectory. A mix of loose
    files and folders is ambiguous about what the loose ones belong to, and
    guessing wrongly here would silently misfile a student's page — so a
    mixed directory falls through to the flat-file rules instead.
    """
    if not input_path.is_dir():
        return None
    loose = [
        path
        for path in input_path.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    if loose:
        return None
    subdirs = sorted(
        (
            path
            for path in input_path.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ),
        key=lambda path: _natural_sort_key(path.name),
    )
    with_images = [d for d in subdirs if discover_images(d)]
    return with_images or None


# A leading alnum token before the first "-" or "_" — "s1234-p1.jpg" and
# "s1234_2.heic" both yield "s1234". Filenames with no separator ("p2.jpg")
# don't match, which is what routes an unlabeled folder to the "ungrouped"
# fallback instead of an accidental single-character grouping.
_TOKEN_SEPARATOR_RE = re.compile(r"^([A-Za-z0-9]+)[-_]")


def _filename_token(path: Path) -> str | None:
    match = _TOKEN_SEPARATOR_RE.match(path.stem)
    return match.group(1) if match else None


def _group_by_filename_token(
    images: Sequence[Path],
) -> dict[str, list[Path]] | None:
    """Group by the leading filename token, or ``None`` if it doesn't apply.

    Requires *every* filename to carry a token — one stray "IMG_0231.jpg" in
    an otherwise "s1234-p1.jpg"-style folder means the convention isn't being
    followed consistently, and grouping the stragglers under a fake token
    would be worse than falling back to one submission per photo and saying
    so plainly in the report.
    """
    tokens = [_filename_token(path) for path in images]
    if not images or any(token is None for token in tokens):
        return None
    groups: dict[str, list[Path]] = {}
    for path, token in zip(images, tokens):
        groups.setdefault(token, []).append(path)
    return groups


def plan_groups(
    input_path: Path, *, pages_per_photo_set: int | None = None
) -> tuple[str, list[PhotoGroup]]:
    """Decide how photos become student submissions.

    Returns ``(mode, groups)``. Tried in this order, matching the feature
    brief exactly:

    1. A single image file — one one-page submission.
    2. ``pages_per_photo_set`` — a fixed number of consecutive photos (in
       natural-sort order) per student, mirroring
       ``submission-splitter --pages-per-submission``.
    3. One subdirectory per student.
    4. A shared filename token before a separator.
    5. Otherwise: each photo is its own one-page submission, and the caller
       is told so via the ``"ungrouped"`` mode rather than left to guess why
       there are as many PDFs as photos.
    """
    if input_path.is_file():
        return "single_file", [PhotoGroup(token=None, images=(input_path,))]

    if pages_per_photo_set:
        images = discover_images(input_path)
        if not images:
            raise PhotoIntakeError(f"no images found in {input_path}")
        groups = [
            PhotoGroup(token=None, images=tuple(images[start : start + pages_per_photo_set]))
            for start in range(0, len(images), pages_per_photo_set)
        ]
        return "pages_per_photo_set", groups

    subdirs = discover_student_subdirectories(input_path)
    if subdirs is not None:
        groups = [
            PhotoGroup(token=directory.name, images=tuple(discover_images(directory)))
            for directory in subdirs
        ]
        return "subdirectories", groups

    images = discover_images(input_path)
    if not images:
        raise PhotoIntakeError(f"no images found in {input_path}")

    token_groups = _group_by_filename_token(images)
    if token_groups is not None:
        # Walk the naturally-sorted flat list once to record each token's
        # first appearance, so students come out in roughly the order their
        # photos were taken rather than in dict-insertion or alphabetical
        # token order.
        order: list[str] = []
        for path in images:
            token = _filename_token(path)
            assert token is not None  # guaranteed by token_groups being non-None
            if token not in order:
                order.append(token)
        groups = [
            PhotoGroup(token=token, images=tuple(token_groups[token]))
            for token in order
        ]
        return "filename_token", groups

    return "ungrouped", [PhotoGroup(token=None, images=(path,)) for path in images]


def _output_filename(group: PhotoGroup, index: int) -> str:
    """Match `submission-splitter`'s naming: a token when one exists, else
    the same ``<prefix>-<zero-padded index>`` scheme it uses for anonymous
    submissions."""
    if group.token:
        return f"{_sanitize_filename(group.token)}.pdf"
    return f"{DEFAULT_SUBMISSION_PREFIX}-{index:0{DEFAULT_ZERO_PAD_WIDTH}d}.pdf"


# --------------------------------------------------------------------------- #
# HEIC support (optional) — see PHOTOS_EXTRA above
# --------------------------------------------------------------------------- #

_heif_registered = False


def _heif_available() -> bool:
    """Is pillow-heif importable, without importing it?

    `find_spec` answers this for the price of a path lookup, so a directory
    of ordinary JPEGs never pays for a check of a package it doesn't need —
    matching `math_ocr.pix2tex_installed`'s reasoning exactly.
    """
    try:
        return importlib.util.find_spec("pillow_heif") is not None
    except (ImportError, ValueError):
        return False


def _register_heif_opener() -> None:
    """Register Pillow's HEIC opener, once per process."""
    global _heif_registered
    if _heif_registered:
        return
    import pillow_heif  # noqa: PLC0415 — deliberately lazy; see PHOTOS_EXTRA

    pillow_heif.register_heif_opener()
    _heif_registered = True


def _require_heic_support(paths: Sequence[Path]) -> None:
    """Raise up front if any input is HEIC/HEIF and Pillow can't read it.

    Checked once, before any photo is processed or any output written,
    rather than per-image mid-run — a class where every photo is HEIC should
    fail with one clear message naming the fix, not after grinding through
    however many JPEGs happened to sort first.
    """
    offending = [path for path in paths if path.suffix.lower() in _HEIC_SUFFIXES]
    if not offending:
        return
    if _heif_available():
        _register_heif_opener()
        return
    raise PhotoIntakeError(
        f"{offending[0].name} is a HEIC/HEIF photo, which Pillow cannot read "
        f"without the optional pillow-heif package. Install it with: {PHOTOS_EXTRA}"
    )


def load_oriented_image(path: Path) -> Image.Image:
    """Open one photo as RGB, honouring its EXIF orientation tag.

    A phone stores the raw sensor frame plus a rotation flag rather than
    rotating the pixels itself, so reading the pixels without the flag is the
    single most common reason a "correctly held" photo comes back sideways.
    `ImageOps.exif_transpose` applies that flag and must run before any
    geometry below touches the image, or the whole rest of the pipeline would
    be solving for the wrong page orientation.
    """
    if path.suffix.lower() in _HEIC_SUFFIXES:
        _require_heic_support([path])
    with Image.open(path) as opened:
        opened.load()
        transposed = ImageOps.exif_transpose(opened)
    return transposed.convert("RGB")


# --------------------------------------------------------------------------- #
# Page-quad finding
# --------------------------------------------------------------------------- #


def _fiducial_quad_in_region(gray_region: np.ndarray) -> np.ndarray | None:
    """Global-Otsu fiducial search over one region.

    This is `core.ocr.omr`'s own approach — Otsu threshold, then
    `find_fiducial_candidates` / `select_corner_marks` — reused rather than
    reimplemented so a change to what counts as a valid registration mark
    only ever has to happen in one place. It works exactly as it does for a
    rendered scan when ``gray_region`` *is* essentially the page, which is
    the assumption `_find_fiducial_quad` is responsible for arranging.
    """
    import cv2  # see the TYPE_CHECKING import above for why this is deferred
    from opengrader.core.ocr import omr  # omr.py also imports cv2 at module scope

    blurred = cv2.GaussianBlur(gray_region, FIDUCIAL_BLUR_KERNEL, 0)
    _threshold, binary = cv2.threshold(
        blurred, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    height, width = gray_region.shape[:2]
    candidates = omr.find_fiducial_candidates(binary.astype(np.uint8))
    corners = omr.select_corner_marks(candidates, width, height)
    if corners is None:
        return None
    return np.array(
        [corners["tl"], corners["tr"], corners["br"], corners["bl"]],
        dtype=np.float32,
    )


def _quad_bounds(
    quad: np.ndarray, shape: tuple[int, int], *, margin: float = 0.02
) -> tuple[int, int, int, int]:
    """Axis-aligned pixel bounds of ``quad``, padded a little and clipped to the frame.

    The small margin is a hedge against the edge detector's own quad running
    a few pixels inside the true page boundary (a soft shadow can eat into
    the contour) — enough to not clip a fiducial that sits close to the edge,
    not so much that the crop pulls much background back in.
    """
    height, width = shape
    x0, y0 = quad.min(axis=0)
    x1, y1 = quad.max(axis=0)
    pad_x = (x1 - x0) * margin
    pad_y = (y1 - y0) * margin
    return (
        max(0, int(x0 - pad_x)),
        max(0, int(y0 - pad_y)),
        min(width, int(x1 + pad_x)),
        min(height, int(y1 + pad_y)),
    )


def _whiten_outside_quad(gray: np.ndarray, quad: np.ndarray) -> np.ndarray:
    """Flatten everything outside ``quad`` to white.

    A quad's own *axis-aligned* bounding box still has real background in its
    four corners whenever the page is tilted (the quad is inscribed in the
    box, not equal to it) — a keystoned photo almost always is tilted, so
    that background is the common case, not an edge case. Left alone it is
    enough to pull Otsu's threshold away from page-white/ink and back towards
    page/background, the same failure this whole detour around a plain global
    threshold exists to avoid. Painting the outside white makes the crop look
    like blank margin, which is exactly what it would be on a real, unskewed
    page — safe because registration marks are always well inside the page
    edge, never in this discarded region.
    """
    import cv2  # see _fiducial_quad_in_region for why this is deferred

    mask = np.zeros(gray.shape[:2], dtype=np.uint8)
    cv2.fillConvexPoly(mask, quad.astype(np.int32), 255)
    return np.where(mask > 0, gray, 255).astype(np.uint8)


def _find_fiducial_quad(
    gray: np.ndarray, *, document_quad: np.ndarray | None = None
) -> np.ndarray | None:
    """Try Opengrader's own corner registration marks first.

    `_fiducial_quad_in_region`'s global-Otsu approach assumes the frame it is
    given more or less *is* the page — true for a rendered scan, but not for
    a phone photo that also frames a desk or a background scene: a global
    threshold over the whole photo splits page-white from background rather
    than ink from paper, and the registration squares vanish into whichever
    side the background happened to fall on. So this narrows to the page
    first, using ``document_quad`` (the same edge-detected quad
    `_find_document_quad` would produce as its own fallback result, passed in
    by the caller so it is computed only once): everything outside it is
    painted white (`_whiten_outside_quad`) and the result cropped to its
    bounding box, so the search sees page-and-ink only, the way `omr.py`
    already assumes. If no such quad is available — a close-up photo where
    the page already fills the frame — the search falls back to the whole
    image, which is exactly the case the global-Otsu assumption was built
    for in the first place.

    Returns the four corners as ``[tl, tr, br, bl]`` in the *original*
    ``gray``'s coordinates, or ``None``.
    """
    if document_quad is not None:
        x0, y0, x1, y1 = _quad_bounds(document_quad, gray.shape[:2])
        if x1 > x0 and y1 > y0:
            flattened = _whiten_outside_quad(gray, document_quad)
            found = _fiducial_quad_in_region(flattened[y0:y1, x0:x1])
            if found is not None:
                return found + np.array([x0, y0], dtype=np.float32)
    return _fiducial_quad_in_region(gray)


def _order_points(pts: np.ndarray) -> np.ndarray:
    """Sort four unordered (x, y) points into ``[tl, tr, br, bl]``.

    The standard sum/difference rule: top-left has the smallest x+y (closest
    to the image origin) and bottom-right the largest; top-right has the
    smallest y-x and bottom-left the largest. Holds for any convex quad
    regardless of which vertex `findContours` happened to start winding at,
    which is what makes it safe to use on an edge-detected quad with no
    inherent labelling (fiducial corners don't need this — `omr` already
    hands them back labelled by which physical corner each mark sits at).
    """
    pts = np.asarray(pts, dtype=np.float32)
    total = pts.sum(axis=1)
    diff = pts[:, 1] - pts[:, 0]  # y - x
    ordered = np.zeros((4, 2), dtype=np.float32)
    ordered[0] = pts[np.argmin(total)]  # tl
    ordered[2] = pts[np.argmax(total)]  # br
    ordered[1] = pts[np.argmin(diff)]  # tr
    ordered[3] = pts[np.argmax(diff)]  # bl
    return ordered


def _auto_canny(gray: np.ndarray, sigma: float = CANNY_SIGMA) -> np.ndarray:
    """Canny with thresholds derived from the image's own median.

    A phone photo's exposure varies far more than a scanner's ever does — a
    fixed threshold pair that works on a sunlit desk washes out in a dim
    classroom and vice versa. Deriving the thresholds from the median pixel
    value (the well-known "auto-canny" heuristic) keeps one code path working
    across both without per-photo tuning.
    """

    import cv2  # see _fiducial_quad_in_region for why this is deferred
    median = float(np.median(gray))
    lower = int(max(0, (1.0 - sigma) * median))
    upper = int(min(255, (1.0 + sigma) * median))
    return cv2.Canny(gray, lower, upper)


def _find_document_quad(gray: np.ndarray) -> np.ndarray | None:
    """The largest plausible 4-point contour — the general "any page" path.

    Runs only when there are no registration marks to lock onto: an exam
    Opengrader did not generate, or a photo where the marks are out of frame.
    Picks the largest convex quadrilateral above `MIN_DOCUMENT_AREA_FRACTION`
    of the frame and rejects everything else, on the assumption that a page
    photographed with reasonable framing is the single largest rectangular
    thing in the shot; anything smaller is more likely to be furniture.
    """

    import cv2  # see _fiducial_quad_in_region for why this is deferred
    height, width = gray.shape[:2]
    frame_area = float(height * width)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = _auto_canny(blurred)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=EDGE_DILATE_ITERATIONS)

    contours, _hierarchy = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    best: np.ndarray | None = None
    best_area = MIN_DOCUMENT_AREA_FRACTION * frame_area
    for contour in contours:
        area = cv2.contourArea(contour)
        if area <= best_area:
            continue
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, APPROX_POLY_EPSILON_FRACTION * perimeter, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        best_area = area
        best = approx.reshape(4, 2).astype(np.float32)

    if best is None:
        return None
    return _order_points(best)


def _measure_output_size(
    quad: np.ndarray, *, paper_size_in: tuple[float, float], dpi_floor: float
) -> tuple[int, int, float]:
    """Pixel size for the rectified page, derived from the quad itself.

    Deliberately built from the *measured* edge lengths rather than a fixed
    canvas: a phone photo's native resolution is usually well above what the
    page needs, and resampling down to some fixed size would throw pixels
    away for nothing. Two adjustments on top of that measurement:

    * the aspect ratio is snapped to the target paper's when it's already
      close (see `ASPECT_SNAP_TOLERANCE`) — correcting the residual error a
      steep photo angle leaves even after the perspective transform;
    * the whole thing is scaled *up* (never down) when the measurement would
      leave the embedded PDF below `dpi_floor` — never scaled down, because a
      sharper source photo should never be punished for it.

    Raises ``ValueError`` for a degenerate quad (near-zero-length edge),
    which the caller treats as "this candidate wasn't real" and falls
    through to the next strategy.
    """
    tl, tr, br, bl = quad
    top = float(np.hypot(*(tr - tl)))
    bottom = float(np.hypot(*(br - bl)))
    left = float(np.hypot(*(bl - tl)))
    right = float(np.hypot(*(br - tr)))
    if min(top, bottom, left, right) <= 1.0:
        raise ValueError("degenerate page quad")

    measured_w = (top + bottom) / 2.0
    measured_h = (left + right) / 2.0

    paper_w_in, paper_h_in = paper_size_in
    target_aspect = paper_w_in / paper_h_in
    measured_aspect = measured_w / measured_h
    if abs(measured_aspect - target_aspect) / target_aspect <= ASPECT_SNAP_TOLERANCE:
        measured_h = measured_w / target_aspect

    dpi = measured_w / paper_w_in
    if dpi < dpi_floor:
        scale = dpi_floor / dpi
        measured_w *= scale
        measured_h *= scale
        dpi = dpi_floor

    return max(1, int(round(measured_w))), max(1, int(round(measured_h))), dpi


def _warp_quad(array: np.ndarray, quad: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
    """Perspective-warp the ``[tl, tr, br, bl]`` quad onto a flat ``out_w x out_h`` canvas.

    The quad is pulled inward by :data:`QUAD_INSET_FRACTION` first. A detected
    page boundary sits *on* the edge between paper and whatever the photo was
    taken against, so warping the quad as measured samples that darker
    surrounding surface into a thin rim around the whole rectified page. The rim
    is small enough to look like nothing and costly enough to matter twice: it
    reads to `scan-check` as content clipped at all four margins (a medium
    finding on every imported photo, which is exactly how a useful pre-flight
    check gets ignored), and to `core/ocr/layout.py`'s primitive detector as a
    page-sized rectangle it then has to reason about. Losing a hair of the
    outermost paper costs nothing — nobody writes answers in the last 0.5% of a
    sheet — so the inset is the cheap side of the trade.
    """

    import cv2  # see _fiducial_quad_in_region for why this is deferred
    inset_quad = _inset_quad(quad, QUAD_INSET_FRACTION)
    destination = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(inset_quad.astype(np.float32), destination)
    return cv2.warpPerspective(
        array,
        transform,
        (out_w, out_h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def _inset_quad(quad: np.ndarray, fraction: float) -> np.ndarray:
    """Shrink a ``[tl, tr, br, bl]`` quad toward its own centre by ``fraction``.

    Scaling about the centroid rather than trimming the warped output keeps the
    inset uniform *in page space*: on a keystoned photo the far edge occupies
    fewer pixels than the near one, so trimming a fixed pixel count afterwards
    would cut unequally into the two ends of the sheet.
    """
    points = quad.astype(np.float64)
    centre = points.mean(axis=0)
    return centre + (points - centre) * (1.0 - fraction)


def _deskew(image: Image.Image) -> Image.Image:
    """Best-effort small-angle straightening when no page quad was found.

    Not a substitute for perspective correction — this removes only in-plane
    rotation and does nothing for keystone. It is the path of last resort:
    the alternative to trying is importing the photo exactly as the camera
    captured it, which is what happens if this, too, finds nothing to act on
    (e.g. a page with too little ink to have an opinion about its own angle).

    The angle comes from `cv2.minAreaRect` over the page's own ink mask — the
    smallest rectangle enclosing the dark pixels tilts with the page. That is
    a coarser and much cheaper signal than a text-line Hough transform, and
    it's enough for the few degrees a held-not-mounted phone typically adds.
    """

    import cv2  # see _fiducial_quad_in_region for why this is deferred
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    _threshold, binary = cv2.threshold(
        blurred, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    coords = cv2.findNonZero(binary)
    if coords is None or len(coords) < 50:
        return image  # too little ink to have an opinion; leave it alone

    angle = cv2.minAreaRect(coords)[-1]
    # minAreaRect's angle convention lands in (-90, 0]; fold it to the
    # nearest upright orientation rather than trusting which side it called
    # "width", which can be off by 90 depending on the mask's own aspect.
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 0.3 or abs(angle) > DESKEW_MAX_ANGLE:
        # Either already level, or too large a "skew" to trust — past this
        # point the ink mask is as likely to be a hand or a shadow as the
        # page, and rotating on it would make the photo worse, not better.
        return image

    return image.rotate(angle, resample=Image.BICUBIC, fillcolor=(255, 255, 255), expand=True)


def _normalize_lighting(image: Image.Image) -> Image.Image:
    """Even out a phone photo's shadows and exposure gradient, conservatively.

    A phone photo of a flat page routinely has a brightness gradient across
    the frame — the corner away from the window or flash reads darker — and
    that gradient is exactly what fights legibility of faint pencil.
    Contrast-limited adaptive histogram equalization (CLAHE) is applied to
    the lightness channel only, with a deliberately low clip limit.

    What this CAN do: brighten a shadowed corner enough that a pencil mark
    which was borderline-visible there becomes legible, without touching hue
    or saturation (only the L channel of LAB is touched).
    What this CANNOT do: invent contrast for a mark the sensor never
    captured — CLAHE redistributes the histogram that is already there, it
    does not sharpen or denoise. The *contrast-limited* clip is what stops it
    from doing what plain histogram equalization would: blow a light grey
    pencil stroke up to flat white because it happened to sit in a sparse
    part of the page's tonal range. If in doubt this stays conservative —
    a photo that still looks a little flat is a far cheaper mistake than one
    where the "fix" erased the faintest handwriting on the page.
    """

    import cv2  # see _fiducial_quad_in_region for why this is deferred
    array = np.asarray(image.convert("RGB"))
    lab = cv2.cvtColor(array, cv2.COLOR_RGB2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=LIGHTING_CLAHE_CLIP_LIMIT, tileGridSize=LIGHTING_CLAHE_TILE_GRID
    )
    l_channel = clahe.apply(l_channel)
    normalized = cv2.merge((l_channel, a_channel, b_channel))
    rgb = cv2.cvtColor(normalized, cv2.COLOR_LAB2RGB)
    return Image.fromarray(rgb)


def rectify_image(
    image: Image.Image,
    *,
    paper: str = "letter",
    dpi_floor: float = DEFAULT_OUTPUT_DPI,
    allow_perspective: bool = True,
) -> tuple[Image.Image, str, str]:
    """Straighten one already EXIF-oriented photo. Returns ``(image, method, note)``.

    ``method`` is ``"fiducials"``, ``"edges"`` or ``"deskew"`` — see
    `ImageOutcome`. This function never raises for "couldn't find a good
    quad"; per the module's fail-soft rule, that outcome is the deskew
    fallback, not an exception. It *does* propagate a genuinely unknown
    ``paper`` name, since that is a caller mistake rather than a property of
    the photo.
    """

    import cv2  # see _fiducial_quad_in_region for why this is deferred
    paper_size = PAPER_SIZES_IN.get(paper)
    if paper_size is None:
        raise PhotoIntakeError(
            f"unknown paper size {paper!r}; choose from {sorted(PAPER_SIZES_IN)}"
        )

    rgb = image.convert("RGB")
    array = np.asarray(rgb)
    gray = cv2.cvtColor(array, cv2.COLOR_RGB2GRAY)

    if allow_perspective:
        # Computed once and handed to the fiducial search too (as the ROI it
        # narrows to before its own Otsu pass) rather than run twice — see
        # `_find_fiducial_quad`'s docstring for why it needs this at all.
        document_quad = _find_document_quad(gray)
        quad = _find_fiducial_quad(gray, document_quad=document_quad)
        method = "fiducials"
        if quad is None:
            quad = document_quad
            method = "edges"
        if quad is not None:
            try:
                out_w, out_h, _dpi = _measure_output_size(
                    quad, paper_size_in=paper_size, dpi_floor=dpi_floor
                )
                warped = _warp_quad(array, quad, out_w, out_h)
            except ValueError:
                pass  # degenerate quad; fall through to deskew below
            else:
                rectified = _normalize_lighting(Image.fromarray(warped))
                return rectified, method, ""

    note = (
        "--no-perspective requested; deskew only"
        if not allow_perspective
        else (
            "no page quad could be found (no registration marks, and no "
            "confident document edge); imported flat rather than dropped"
        )
    )
    deskewed = _normalize_lighting(_deskew(rgb))
    return deskewed, "deskew", note


# --------------------------------------------------------------------------- #
# PDF assembly
# --------------------------------------------------------------------------- #


def _write_pdf(pages: Sequence[Image.Image], destination: Path, *, dpi: float) -> None:
    """Write one multi-page PDF, embedded at ``dpi`` (see the module docstring).

    ``resolution=dpi`` is what fixes the PDF page's *physical* size to
    ``pixel_size / dpi`` inches — it is the one knob that determines what
    `scan_check._page_raster_dpi` (and everything downstream that reasons
    about DPI) will measure back, independent of how many pixels the source
    photo actually had.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    first, *rest = (page.convert("RGB") for page in pages)
    first.save(
        destination,
        format="PDF",
        save_all=True,
        append_images=rest,
        resolution=dpi,
    )


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


def import_photos(
    input_path: str | Path,
    output_dir: str | Path,
    *,
    pages_per_photo_set: int | None = None,
    dpi: float = DEFAULT_OUTPUT_DPI,
    paper: str = "letter",
    perspective: bool = True,
    progress_callback: Callable[[str], None] | None = None,
) -> PhotoIntakeReport:
    """Import a folder (or one file) of phone photos into per-student PDFs.

    Never modifies ``input_path`` — every photo is opened read-only and every
    output is written under ``output_dir`` (created if needed). See the
    module docstring for the grouping rules, the fiducials-then-edges
    strategy order, and why a photo that can't be rectified is imported flat
    rather than dropped.
    """
    source = Path(input_path)
    if not source.exists():
        raise PhotoIntakeError(f"input path not found: {source}")
    destination_dir = Path(output_dir)

    mode, groups = plan_groups(source, pages_per_photo_set=pages_per_photo_set)
    if not groups:
        raise PhotoIntakeError(f"no images found in {source}")

    # Checked once, up front, over every image this run will touch — see
    # `_require_heic_support`'s docstring for why that's better than
    # discovering the same problem partway through a batch.
    _require_heic_support([path for group in groups for path in group.images])

    emit = progress_callback or (lambda _message: None)

    outcomes: list[ImageOutcome] = []
    submissions: list[StudentSubmission] = []

    for index, group in enumerate(groups, start=1):
        pages: list[Image.Image] = []
        page_sources: list[Path] = []
        for image_path in group.images:
            emit(f"Importing {image_path.name}…")
            try:
                oriented = load_oriented_image(image_path)
            except PhotoIntakeError:
                raise  # a missing HEIC decoder is a run-stopping problem
            except Exception as exc:  # noqa: BLE001 — a corrupt photo is not fatal
                outcomes.append(
                    ImageOutcome(source=image_path, method="unreadable", note=str(exc))
                )
                continue

            rectified, method, note = rectify_image(
                oriented, paper=paper, dpi_floor=dpi, allow_perspective=perspective
            )
            outcomes.append(ImageOutcome(source=image_path, method=method, note=note))
            pages.append(rectified)
            page_sources.append(image_path)

        if not pages:
            # Every photo in this group was unreadable; nothing to write, and
            # the per-image outcomes above already explain why.
            continue

        destination = destination_dir / _output_filename(group, index)
        _write_pdf(pages, destination, dpi=dpi)
        submissions.append(
            StudentSubmission(token=group.token, output_pdf=destination, pages=tuple(page_sources))
        )

    return PhotoIntakeReport(
        grouping_mode=mode,
        outcomes=tuple(outcomes),
        submissions=tuple(submissions),
        output_dir=destination_dir,
        dpi=dpi,
    )
