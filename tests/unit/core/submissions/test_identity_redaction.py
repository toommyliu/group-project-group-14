"""Identity redaction for the whole-page scan view.

The banding rules are pure, so most of this runs without a Tesseract binary;
the handful of tests that exercise `plan_identity_redaction` end to end render
their own images and are skipped where OCR is unavailable.
"""

import pytest
from PIL import Image, ImageDraw

from opengrader.core.submissions.identity_redaction import (
    DEFAULT_TOP_FRACTION,
    RedactionPlan,
    TextLine,
    bands_from_lines,
    clip_bands,
    line_is_identity_field,
    plan_identity_redaction,
)


# --- which lines count as an identity field -------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "Name: ________________",
        "Name",
        "Student ID",
        "Student ID: 012345678",
        "Student Number ______",
        "SID",
        "Printed Name",
        "Last Name    First Name",
        "Signature: ____",
        "E-mail: ann@example.edu",
        "ID #",
    ],
)
def test_header_fields_are_redacted(text):
    assert line_is_identity_field(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # The reason lines are matched as *phrases* and length-guarded: a prompt
        # that happens to contain "name" is not a form field.
        "Name three properties of a binary heap and justify each one",
        "Rename the variable and explain why the compiler rejected it",
        "CMPE 195 Midterm Exam",
        "Answer every question in the box provided",
        "",
        "   ",
    ],
)
def test_prompts_are_left_alone(text):
    assert line_is_identity_field(text) is False


def test_a_long_line_with_a_colon_is_still_treated_as_a_field():
    # Over-redacting is the safe direction: a fill-in rule or colon marks a form
    # field however wordy OCR made it.
    assert line_is_identity_field(
        "Student Name: ____________________ please print clearly in block capitals"
    ) is True


# --- banding geometry ------------------------------------------------------ #


def test_bands_span_the_full_width_and_pad_the_line():
    lines = [TextLine(text="Name: ______", top=100, bottom=120)]
    (band,) = bands_from_lines(lines, page_height=1000)
    x, y, width, height = band
    # Full width: the value written next to a label is unbounded to the right.
    assert (x, width) == (0.0, 1.0)
    # Padded above and below the line's own 20px height.
    assert y == pytest.approx((100 - 20 * 0.7) / 1000)
    assert y + height == pytest.approx((120 + 20 * 1.2) / 1000)


def test_adjacent_fields_merge_into_one_band():
    lines = [
        TextLine(text="Name: ______", top=100, bottom=130),
        TextLine(text="Student ID: ______", top=150, bottom=180),
    ]
    assert len(bands_from_lines(lines, page_height=1000)) == 1


def test_separated_fields_stay_separate_bands():
    lines = [
        TextLine(text="Name: ______", top=100, bottom=120),
        TextLine(text="Signature: ______", top=600, bottom=620),
    ]
    assert len(bands_from_lines(lines, page_height=1000)) == 2


def test_bands_are_clamped_to_the_page():
    lines = [TextLine(text="Name: ______", top=2, bottom=20)]
    (band,) = bands_from_lines(lines, page_height=1000)
    assert band[1] == 0.0


def test_no_identity_line_means_no_bands():
    lines = [TextLine(text="CMPE 195 Midterm", top=100, bottom=120)]
    assert bands_from_lines(lines, page_height=1000) == ()


def test_unknown_page_height_yields_nothing():
    assert bands_from_lines([TextLine("Name:", 0, 10)], page_height=0) == ()


# --- never covering the answer -------------------------------------------- #


def test_a_band_is_cut_off_above_the_answer_region():
    band = (0.0, 0.10, 1.0, 0.30)
    (clipped,) = clip_bands([band], protect=[(0.1, 0.25, 0.8, 0.4)])
    assert clipped == (0.0, 0.10, 1.0, pytest.approx(0.15))


def test_a_band_starting_inside_the_answer_region_is_dropped():
    # Hiding the answer under review would be worse than the leak it prevents.
    assert clip_bands([(0.0, 0.30, 1.0, 0.10)], protect=[(0.1, 0.25, 0.8, 0.4)]) == ()


def test_a_band_clear_of_the_answer_survives_untouched():
    band = (0.0, 0.02, 1.0, 0.10)
    assert clip_bands([band], protect=[(0.1, 0.50, 0.8, 0.2)]) == (band,)


def test_no_protected_region_leaves_bands_alone():
    band = (0.0, 0.02, 1.0, 0.10)
    assert clip_bands([band], protect=[]) == (band,)


# --- the OCR-backed plan --------------------------------------------------- #


def _page(*, header: str | None, ink: bool = True) -> Image.Image:
    """A synthetic US-Letter-ish page at ~150 DPI."""
    image = Image.new("RGB", (1275, 1650), "white")
    draw = ImageDraw.Draw(image)
    if header is not None:
        # Large enough for Tesseract to read reliably at the default font.
        draw.text((80, 300), header, fill="black")
    if ink:
        draw.text((80, 1000), "the derivation continues on the back", fill="black")
    return image


def _ocr_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
    except Exception:
        return False
    return True


needs_ocr = pytest.mark.skipif(
    not _ocr_available(), reason="requires a Tesseract binary"
)


@needs_ocr
def test_a_named_header_is_redacted():
    plan = plan_identity_redaction(_page(header="Name: Ann Lee"))
    assert plan.available is True
    assert plan.bands, "the Name line should have produced a band"
    # The band sits over the header, not over the work further down the page.
    x, y, width, height = plan.bands[0]
    assert (x, width) == (0.0, 1.0)
    assert y < 300 / 1650 < y + height


@needs_ocr
def test_a_page_with_no_header_is_not_redacted():
    plan = plan_identity_redaction(_page(header=None))
    assert plan.available is True
    assert plan.bands == ()


@needs_ocr
def test_a_blank_page_is_not_redacted():
    # No text *and* no ink: a scratch sheet has nothing to hide, so the
    # fail-closed path must not fire and blacken half of it.
    plan = plan_identity_redaction(Image.new("RGB", (600, 800), "white"))
    assert plan.available is True
    assert plan.bands == ()


@needs_ocr
def test_an_unreadable_inked_header_hides_the_whole_search_strip():
    # Ink but nothing legible — a poor scan. Fail closed over everything we
    # searched rather than guess where the name is.
    image = Image.new("RGB", (600, 800), "white")
    ImageDraw.Draw(image).rectangle([20, 40, 580, 120], fill=(30, 30, 30))
    plan = plan_identity_redaction(image)
    assert plan.available is True
    (band,) = plan.bands
    assert band[1] == 0.0
    assert band[3] == pytest.approx(int(800 * DEFAULT_TOP_FRACTION) / 800)
    assert "too faint" in plan.reason


@needs_ocr
def test_an_unreadable_pencil_header_still_fails_closed():
    """A faint header must not read as blank paper.

    Regression: ink was measured at the born-digital grey cut, where a *scanned*
    pen stroke — and a pencil one especially — is nearly invisible (a pencil name
    measured 0.000000). The fail-closed branch therefore decided the strip was
    blank, hid nothing, and showed the student's name during anonymous grading.
    Measured at the scanned cut the same strip reads well above the threshold.
    """
    image = Image.new("RGB", (600, 800), "white")
    # Light grey, like pencil or a washed-out photocopy — legible to a human,
    # far from black, and too indistinct for OCR to turn into words.
    ImageDraw.Draw(image).rectangle([20, 40, 580, 90], fill=(165, 165, 165))

    plan = plan_identity_redaction(image)

    assert plan.available is True
    assert plan.bands, "a faint header must still be hidden, not treated as blank"
    assert "too faint" in plan.reason


@needs_ocr
def test_genuinely_blank_paper_still_hides_nothing():
    # The other side of the same decision: a scratch sheet has nothing to
    # conceal, and blacking out its top would cost the grader real work for no
    # privacy gain. Paper grain must not be mistaken for ink.
    import numpy as np

    grain = np.clip(
        np.random.default_rng(0).normal(242, 8, (800, 600, 3)), 0, 255
    ).astype("uint8")
    plan = plan_identity_redaction(Image.fromarray(grain, "RGB"))

    assert plan.available is True
    assert plan.bands == ()


def test_ocr_failure_is_reported_as_unavailable(monkeypatch):
    # No working Tesseract means we cannot locate the header, so the caller must
    # refuse the page rather than show an unredacted one.
    import pytesseract

    def boom(*args, **kwargs):
        raise pytesseract.TesseractNotFoundError()

    monkeypatch.setattr(pytesseract, "image_to_data", boom)
    plan = plan_identity_redaction(Image.new("RGB", (600, 800), "white"))
    assert plan.available is False
    assert plan.bands == ()
    assert "could not be located" in plan.reason


def test_an_unmeasurable_image_is_unavailable():
    class NoSize:
        size = (0, 0)

    plan = plan_identity_redaction(NoSize())
    assert plan == RedactionPlan(
        bands=(), available=False, reason="the page could not be measured"
    )
