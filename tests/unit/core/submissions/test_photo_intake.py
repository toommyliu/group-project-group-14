"""Tests for phone-photo intake (core/submissions/photo_intake.py).

The geometry tests build a synthetic "page" with a known marker position,
perspective-warp it onto a larger background canvas at a known quad (the way
a phone photo keystones a flat page), and check that rectification recovers
the marker's *normalized* position — not just that some PDF got written.
That is the real test of this feature: a plausible-looking rectification that
is actually wrong would still produce a PDF.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest
from PIL import Image

from opengrader.core.submissions import photo_intake as pi

# --------------------------------------------------------------------------- #
# Synthetic photo builders
# --------------------------------------------------------------------------- #

PAGE_W, PAGE_H = 340, 440  # 8.5:11 aspect at 40 px/inch, matching "letter"
BG_W, BG_H = 900, 1200


def _blank_page(*, border: bool = True) -> np.ndarray:
    page = np.full((PAGE_H, PAGE_W, 3), 255, dtype=np.uint8)
    if border:
        cv2_rectangle(page, (4, 4), (PAGE_W - 5, PAGE_H - 5), (0, 0, 0), thickness=6)
    return page


def cv2_rectangle(img, pt1, pt2, color, thickness):  # thin wrapper, avoids top-level cv2 import clutter
    import cv2

    cv2.rectangle(img, pt1, pt2, color, thickness)


def _draw_marker(page: np.ndarray, norm_pos: tuple[float, float], *, size: int = 9) -> None:
    """A small red *bar* (not a square) at a normalized (x, y) position.

    Deliberately elongated rather than square: `find_fiducial_candidates`
    accepts any sufficiently square, solid, plausibly-sized blob as a
    registration-mark candidate, and a square marker of similar size to the
    real fiducials would occasionally get treated as a fifth one. A bar fails
    the aspect check outright, so the marker can never be mistaken for a
    corner mark regardless of where it's placed on the page.
    """
    x = int(norm_pos[0] * PAGE_W)
    y = int(norm_pos[1] * PAGE_H)
    half_h = max(2, size // 3)
    page[max(0, y - half_h) : y + half_h, max(0, x - size) : x + size] = (220, 20, 20)


def _draw_fiducials(page: np.ndarray, *, side: int = 14, margin: int = 18) -> None:
    """Four solid black squares near the corners, sized like omr's registration marks."""
    for (x0, y0) in (
        (margin, margin),
        (PAGE_W - margin - side, margin),
        (margin, PAGE_H - margin - side),
        (PAGE_W - margin - side, PAGE_H - margin - side),
    ):
        page[y0 : y0 + side, x0 : x0 + side] = (0, 0, 0)


def _composite(page: np.ndarray, quad_dst: np.ndarray, *, bg_size=(BG_W, BG_H)) -> np.ndarray:
    """Perspective-warp ``page`` onto a noisy background at ``quad_dst``."""
    import cv2

    bg_w, bg_h = bg_size
    rng = np.random.default_rng(7)
    background = np.clip(
        128 + rng.normal(0, 6, (bg_h, bg_w, 3)), 0, 255
    ).astype(np.uint8)

    src = np.array(
        [[0, 0], [PAGE_W - 1, 0], [PAGE_W - 1, PAGE_H - 1], [0, PAGE_H - 1]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(src, quad_dst.astype(np.float32))
    warped_page = cv2.warpPerspective(page, transform, (bg_w, bg_h))
    mask = cv2.warpPerspective(
        np.full((PAGE_H, PAGE_W), 255, dtype=np.uint8), transform, (bg_w, bg_h)
    )
    composite = background.copy()
    composite[mask > 0] = warped_page[mask > 0]
    return composite


def _mild_quad() -> np.ndarray:
    """A page quad occupying most of the frame with a modest keystone tilt."""
    return np.array(
        [[90, 120], [790, 60], [810, 1140], [70, 1100]],
        dtype=np.float32,
    )


def _marker_centroid_norm(image: Image.Image) -> tuple[float, float] | None:
    """Normalized centroid of the reddish marker pixels in a rectified image."""
    array = np.asarray(image.convert("RGB")).astype(np.int16)
    red_mask = (
        (array[:, :, 0] > 150)
        & (array[:, :, 1] < 100)
        & (array[:, :, 2] < 100)
    )
    ys, xs = np.nonzero(red_mask)
    if len(xs) == 0:
        return None
    height, width = array.shape[:2]
    return float(np.mean(xs)) / width, float(np.mean(ys)) / height


# --------------------------------------------------------------------------- #
# Geometry: fiducial path
# --------------------------------------------------------------------------- #


def test_fiducial_quad_perspective_correction_recovers_marker_position() -> None:
    page = _blank_page(border=False)
    _draw_fiducials(page)
    marker_pos = (0.6, 0.35)
    _draw_marker(page, marker_pos)

    photo = _composite(page, _mild_quad())
    rectified, method, note = pi.rectify_image(
        Image.fromarray(photo), paper="letter", dpi_floor=50, allow_perspective=True
    )

    assert method == "fiducials"
    assert note == ""
    recovered = _marker_centroid_norm(rectified)
    assert recovered is not None
    assert recovered[0] == pytest.approx(marker_pos[0], abs=0.035)
    assert recovered[1] == pytest.approx(marker_pos[1], abs=0.035)


# --------------------------------------------------------------------------- #
# Geometry: document-edge path
# --------------------------------------------------------------------------- #


def test_document_edge_perspective_correction_recovers_marker_position() -> None:
    page = _blank_page(border=True)
    marker_pos = (0.25, 0.7)
    _draw_marker(page, marker_pos)

    photo = _composite(page, _mild_quad())
    rectified, method, note = pi.rectify_image(
        Image.fromarray(photo), paper="letter", dpi_floor=50, allow_perspective=True
    )

    assert method == "edges"
    assert note == ""
    recovered = _marker_centroid_norm(rectified)
    assert recovered is not None
    assert recovered[0] == pytest.approx(marker_pos[0], abs=0.04)
    assert recovered[1] == pytest.approx(marker_pos[1], abs=0.04)


def test_fiducials_are_preferred_over_edge_detection() -> None:
    # A page with fiducials still contrasts with the background enough for
    # edge detection to find a quad too (see the fiducial test above, which
    # has no explicit border and still gets one) — so both strategies apply
    # here, and the more accurate one, fiducials, must win.
    page = _blank_page(border=False)
    _draw_fiducials(page)
    photo = _composite(page, _mild_quad())

    _rectified, method, _note = pi.rectify_image(
        Image.fromarray(photo), paper="letter", dpi_floor=50, allow_perspective=True
    )
    assert method == "fiducials"


# --------------------------------------------------------------------------- #
# Fallbacks
# --------------------------------------------------------------------------- #


def test_no_perspective_flag_forces_deskew_even_with_fiducials_present() -> None:
    page = _blank_page(border=True)
    _draw_fiducials(page)
    photo = _composite(page, _mild_quad())

    _rectified, method, note = pi.rectify_image(
        Image.fromarray(photo), paper="letter", dpi_floor=50, allow_perspective=False
    )
    assert method == "deskew"
    assert "no-perspective" in note


def test_deskew_only_fallback_when_no_quad_is_found() -> None:
    # No border, no fiducials, no background offset: nothing for either quad
    # strategy to lock onto. Must not raise — it falls back to deskew and
    # says why, per the module's fail-soft rule.
    blank = Image.fromarray(np.full((400, 300, 3), 255, dtype=np.uint8))
    rectified, method, note = pi.rectify_image(blank, paper="letter", allow_perspective=True)
    assert method == "deskew"
    assert "no page quad could be found" in note
    assert rectified.size[0] > 0 and rectified.size[1] > 0


def test_unknown_paper_size_raises() -> None:
    blank = Image.fromarray(np.full((100, 100, 3), 255, dtype=np.uint8))
    with pytest.raises(pi.PhotoIntakeError):
        pi.rectify_image(blank, paper="tabloid")


# --------------------------------------------------------------------------- #
# EXIF orientation
# --------------------------------------------------------------------------- #


def test_exif_orientation_is_honoured(tmp_path) -> None:  # noqa: ANN001
    raw = Image.new("RGB", (60, 40), "white")
    raw.paste((255, 0, 0), (0, 0, 10, 10))
    exif = raw.getexif()
    exif[274] = 6  # "rotate 90 CW to view correctly"
    path = tmp_path / "sideways.jpg"
    raw.save(path, exif=exif)

    # Sanity check: without honouring EXIF, the file's raw pixel size is 60x40.
    with Image.open(path) as unrotated:
        assert unrotated.size == (60, 40)

    oriented = pi.load_oriented_image(path)
    # Orientation 6 is a 90-degree rotation, so width and height swap.
    assert oriented.size == (40, 60)


# --------------------------------------------------------------------------- #
# Natural sort
# --------------------------------------------------------------------------- #


def test_natural_sort_orders_p2_before_p10() -> None:
    names = ["p10.jpg", "p1.jpg", "p2.jpg"]
    ordered = sorted(names, key=pi._natural_sort_key)
    assert ordered == ["p1.jpg", "p2.jpg", "p10.jpg"]


def test_discover_images_uses_natural_sort(tmp_path) -> None:  # noqa: ANN001
    for name in ("p10.jpg", "p2.jpg", "p1.jpg"):
        (tmp_path / name).write_bytes(b"not a real image, just a name test")
    ordered = pi.discover_images(tmp_path)
    assert [p.name for p in ordered] == ["p1.jpg", "p2.jpg", "p10.jpg"]


# --------------------------------------------------------------------------- #
# Grouping modes
# --------------------------------------------------------------------------- #


def _touch(path) -> None:  # noqa: ANN001
    path.write_bytes(b"stub")


def test_pages_per_photo_set_groups_fixed_counts(tmp_path) -> None:  # noqa: ANN001
    for name in ("a1.jpg", "a2.jpg", "a3.jpg", "a4.jpg"):
        _touch(tmp_path / name)
    mode, groups = pi.plan_groups(tmp_path, pages_per_photo_set=2)
    assert mode == "pages_per_photo_set"
    assert [tuple(p.name for p in g.images) for g in groups] == [
        ("a1.jpg", "a2.jpg"),
        ("a3.jpg", "a4.jpg"),
    ]


def test_filename_token_grouping(tmp_path) -> None:  # noqa: ANN001
    for name in ("s1234-p1.jpg", "s1234-p2.jpg", "s5678_1.jpg", "s5678_2.jpg"):
        _touch(tmp_path / name)
    mode, groups = pi.plan_groups(tmp_path)
    assert mode == "filename_token"
    by_token = {g.token: [p.name for p in g.images] for g in groups}
    assert by_token == {
        "s1234": ["s1234-p1.jpg", "s1234-p2.jpg"],
        "s5678": ["s5678_1.jpg", "s5678_2.jpg"],
    }


def test_subdirectory_grouping(tmp_path) -> None:  # noqa: ANN001
    student_a = tmp_path / "student_a"
    student_b = tmp_path / "student_b"
    student_a.mkdir()
    student_b.mkdir()
    _touch(student_a / "1.jpg")
    _touch(student_a / "2.jpg")
    _touch(student_b / "1.jpg")

    mode, groups = pi.plan_groups(tmp_path)
    assert mode == "subdirectories"
    by_token = {g.token: len(g.images) for g in groups}
    assert by_token == {"student_a": 2, "student_b": 1}


def test_ungrouped_fallback_when_no_convention_applies(tmp_path) -> None:  # noqa: ANN001
    for name in ("photo1.jpg", "photo2.jpg", "photo3.jpg"):
        _touch(tmp_path / name)
    mode, groups = pi.plan_groups(tmp_path)
    assert mode == "ungrouped"
    assert len(groups) == 3
    assert all(len(g.images) == 1 and g.token is None for g in groups)


def test_mixed_loose_files_and_subdirectories_falls_through(tmp_path) -> None:  # noqa: ANN001
    (tmp_path / "student_a").mkdir()
    _touch(tmp_path / "student_a" / "1.jpg")
    _touch(tmp_path / "loose.jpg")  # ambiguous: not inside a subdirectory

    assert pi.discover_student_subdirectories(tmp_path) is None


# --------------------------------------------------------------------------- #
# End-to-end: import_photos
# --------------------------------------------------------------------------- #


def _save_photo(path, marker_pos=(0.5, 0.5), *, fiducials: bool = True) -> None:  # noqa: ANN001
    page = _blank_page(border=not fiducials)
    if fiducials:
        _draw_fiducials(page)
    _draw_marker(page, marker_pos)
    photo = _composite(page, _mild_quad())
    Image.fromarray(photo).save(path)


def test_import_photos_writes_one_pdf_per_token_group(tmp_path) -> None:  # noqa: ANN001
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    _save_photo(input_dir / "s1-p1.jpg")
    _save_photo(input_dir / "s1-p2.jpg")
    _save_photo(input_dir / "s2-p1.jpg")

    output_dir = tmp_path / "out"
    report = pi.import_photos(input_dir, output_dir, dpi=50)

    assert report.grouping_mode == "filename_token"
    tokens = {s.token: len(s.pages) for s in report.submissions}
    assert tokens == {"s1": 2, "s2": 1}
    for submission in report.submissions:
        assert submission.output_pdf.exists()
        assert submission.output_pdf.name == f"{submission.token}.pdf"


def test_import_photos_does_not_modify_inputs(tmp_path) -> None:  # noqa: ANN001
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    photo_path = input_dir / "photo1.jpg"
    _save_photo(photo_path)
    original_bytes = photo_path.read_bytes()

    pi.import_photos(input_dir, tmp_path / "out", dpi=50)

    assert photo_path.read_bytes() == original_bytes


def test_import_photos_ungrouped_names_like_the_splitter(tmp_path) -> None:  # noqa: ANN001
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    _save_photo(input_dir / "photoA.jpg")
    _save_photo(input_dir / "photoB.jpg")

    report = pi.import_photos(input_dir, tmp_path / "out", dpi=50)
    names = sorted(s.output_pdf.name for s in report.submissions)
    assert names == ["submission-0001.pdf", "submission-0002.pdf"]


# --------------------------------------------------------------------------- #
# HEIC extra
# --------------------------------------------------------------------------- #


_PILLOW_HEIF_INSTALLED = importlib.util.find_spec("pillow_heif") is not None


@pytest.mark.skipif(
    _PILLOW_HEIF_INSTALLED, reason="pillow-heif is installed; this checks the absence path"
)
def test_heic_without_pillow_heif_fails_naming_the_extra(tmp_path) -> None:  # noqa: ANN001
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "page1.heic").write_bytes(b"not a real heic file")

    with pytest.raises(pi.PhotoIntakeError) as excinfo:
        pi.import_photos(input_dir, tmp_path / "out")

    message = str(excinfo.value)
    assert "pillow-heif" in message
    assert "uv sync --extra photos" in message


# --------------------------------------------------------------------------- #
# The background rim (regression)
# --------------------------------------------------------------------------- #


def test_rectified_page_carries_no_rim_of_background() -> None:
    """The surface the photo was taken on must not survive into the page.

    A detected page quad lies *on* the paper/background boundary, so warping it
    as measured samples the desk into a thin frame around the rectified page.
    That rim is easy to miss by eye and expensive twice over: `scan-check` reads
    it as content clipped at all four margins — a medium finding on every single
    imported photo, which is how a pre-flight check earns being ignored — and
    `core/ocr/layout.py`'s primitive detector reads it as a page-sized box.
    `_warp_quad` insets the quad by `QUAD_INSET_FRACTION` to prevent it.
    """
    # A borderless page: any dark pixel near an edge of the result came from the
    # background, since the page itself carries ink only in its middle band.
    page = _blank_page(border=False)
    _draw_marker(page, (0.5, 0.5))

    # A deliberately dark desk, so background pixels are unmistakable.
    photo = _composite(page, _mild_quad())
    rectified, method, _note = pi.rectify_image(
        Image.fromarray(photo), paper="letter", dpi_floor=50, allow_perspective=True
    )
    assert method == "edges"

    grey = np.asarray(rectified.convert("L"))
    height, width = grey.shape
    strip = max(2, min(height, width) // 100)  # the outermost ~1% of the page
    edges = {
        "top": grey[:strip, :],
        "bottom": grey[-strip:, :],
        "left": grey[:, :strip],
        "right": grey[:, -strip:],
    }
    for name, band in edges.items():
        dark_fraction = float((band < 128).mean())
        assert dark_fraction < 0.02, f"{name} edge retained background: {dark_fraction:.3f}"
