from pathlib import Path

import fitz
import pytest
from PIL import Image
from pypdf import PdfReader

from opengrader.core.pdf_utils import ink_fraction
from opengrader.core.submissions.scan_check import (
    BLANK_INK_FRACTION,
    CRITICAL_DPI,
    DEFAULT_DPI_FLOOR,
    DEFAULT_MIN_ORIENTATION_CONFIDENCE,
    OrientationUnavailableError,
    PageScanFeatures,
    RotationPlan,
    ScanIssueKind,
    analyze_scan_pdf,
    apply_rotation_fixes,
    build_scan_issues,
    build_scan_report,
    default_upright_path,
    detect_page_orientation,
    discover_scan_pdfs,
    dominant_rotation,
    flag_bundle_page_count,
    flag_submission_page_count,
    measure_page,
    page_order_fix_is_safe,
    plan_page_order,
    plan_rotations,
    summarize_issues,
)


def _page(page: int, **overrides) -> PageScanFeatures:
    defaults = dict(
        rotation=0,
        orientation_confidence=10.0,
        ink_fraction=0.05,
        edge_ink=(0.0, 0.0, 0.0, 0.0),
        dpi=300.0,
    )
    defaults.update(overrides)
    return PageScanFeatures(page=page, **defaults)


def _kinds(issues) -> list[ScanIssueKind]:
    return [issue.kind for issue in issues]


def test_upright_stack_plans_no_rotation() -> None:
    assert plan_rotations([_page(1), _page(2), _page(3)]).angles == {}


def test_single_rotated_page_is_planned() -> None:
    plan = plan_rotations([_page(1), _page(2, rotation=180), _page(3)])
    assert plan.angles == {2: 180}
    assert plan.inferred_pages == ()


def test_whole_stack_upside_down() -> None:
    features = [_page(index, rotation=180) for index in range(1, 5)]
    plan = plan_rotations(features)
    assert plan.angles == {1: 180, 2: 180, 3: 180, 4: 180}
    assert plan.dominant == 180


def test_low_confidence_reading_is_not_acted_on() -> None:
    below = DEFAULT_MIN_ORIENTATION_CONFIDENCE / 2
    plan = plan_rotations(
        [_page(1), _page(2, rotation=90, orientation_confidence=below)]
    )
    assert plan.angles == {}
    assert plan.unverified_pages == (2,)


def test_unreadable_page_follows_a_decisive_stack() -> None:
    features = [
        _page(1, rotation=180),
        _page(2, rotation=180),
        _page(3, rotation=180),
        _page(4, rotation=None, orientation_confidence=None),
    ]
    plan = plan_rotations(features)
    assert plan.angles[4] == 180
    assert plan.inferred_pages == (4,)
    assert plan.unverified_pages == ()


def test_unreadable_page_is_left_alone_when_the_stack_disagrees() -> None:
    features = [
        _page(1, rotation=0),
        _page(2, rotation=180),
        _page(3, rotation=None, orientation_confidence=None),
    ]
    plan = plan_rotations(features)
    assert 3 not in plan.angles
    assert plan.unverified_pages == (3,)


def test_one_readable_page_is_not_a_stack() -> None:
    features = [_page(1, rotation=90), _page(2, rotation=None)]
    assert dominant_rotation(features) is None


def test_upright_majority_never_becomes_a_dominant_rotation() -> None:
    features = [_page(1), _page(2), _page(3), _page(4, rotation=None)]
    plan = plan_rotations(features)
    assert plan.dominant is None
    assert plan.angles == {}


def test_rotation_plan_is_falsey_when_there_is_nothing_to_turn() -> None:
    assert not RotationPlan()
    assert plan_rotations([_page(1, rotation=90)])


def test_rotated_page_is_high_severity_and_fixable() -> None:
    issues = build_scan_issues([_page(1, rotation=180)])
    assert len(issues) == 1
    assert issues[0].kind is ScanIssueKind.ROTATED_PAGE
    assert issues[0].severity == "high"
    assert issues[0].fixable is True
    assert issues[0].page == 1


def test_inferred_rotation_says_it_followed_the_stack() -> None:
    features = [
        _page(1, rotation=180),
        _page(2, rotation=180),
        _page(3, rotation=None),
    ]
    issues = build_scan_issues(features)
    inferred = next(issue for issue in issues if issue.page == 3)
    assert "rest of the stack" in inferred.detail


def test_low_resolution_severity_tracks_how_low() -> None:
    soft = build_scan_issues([_page(1, dpi=DEFAULT_DPI_FLOOR - 10)])
    unusable = build_scan_issues([_page(1, dpi=CRITICAL_DPI - 10)])
    assert soft[0].kind is ScanIssueKind.LOW_RESOLUTION
    assert soft[0].severity == "medium"
    assert unusable[0].severity == "high"
    assert not any(issue.fixable for issue in soft + unusable)


def test_born_digital_page_has_no_resolution_to_fail() -> None:
    assert build_scan_issues([_page(1, dpi=None)]) == ()


def test_blank_page_is_reported_but_only_as_a_hint() -> None:
    issues = build_scan_issues([_page(1, ink_fraction=BLANK_INK_FRACTION / 2)])
    assert _kinds(issues) == [ScanIssueKind.BLANK_PAGE]
    assert issues[0].severity == "low"
    assert issues[0].fixable is False


def test_content_running_off_an_edge_is_flagged_with_the_side() -> None:
    issues = build_scan_issues([_page(1, edge_ink=(0.0, 0.10, 0.0, 0.0))])
    assert _kinds(issues) == [ScanIssueKind.MARGIN_CLIPPED]
    assert "right edge" in issues[0].detail


def test_a_solid_edge_is_a_scanner_shadow_not_clipped_content() -> None:
    assert build_scan_issues([_page(1, edge_ink=(0.99, 0.0, 0.0, 0.0))]) == ()


def test_faint_edge_speckle_is_not_clipping() -> None:
    assert build_scan_issues([_page(1, edge_ink=(0.001, 0.0, 0.0, 0.0))]) == ()


def test_bundle_page_count_must_divide_evenly() -> None:
    issue = flag_bundle_page_count(13, 6)
    assert issue is not None
    assert issue.severity == "high"
    assert issue.page is None
    assert flag_bundle_page_count(12, 6) is None


def test_submission_page_count_must_match_the_template() -> None:
    issue = flag_submission_page_count(5, 6)
    assert issue is not None
    assert "fewer" in issue.detail
    assert flag_submission_page_count(6, 6) is None


def test_page_count_check_is_skipped_without_an_expectation() -> None:
    issues = build_scan_issues([_page(1), _page(2), _page(3)])
    assert issues == ()


def _numbered(*readings: tuple[int | None, str | None]) -> list[PageScanFeatures]:
    return [
        PageScanFeatures(page=index, page_number=number, page_number_source=source)
        for index, (number, source) in enumerate(readings, start=1)
    ]


def _qr(*numbers: int) -> list[tuple[int | None, str | None]]:
    return [(number, "qr") for number in numbers]


def _ocr(*numbers: int) -> list[tuple[int | None, str | None]]:
    return [(number, "ocr") for number in numbers]


_UNREAD: tuple[int | None, str | None] = (None, None)


def test_a_bundle_whose_pages_run_forwards_is_left_alone() -> None:
    plan = plan_page_order(
        _numbered(*_qr(1, 2, 3), *_qr(1, 2, 3)), submission_length=3
    )
    assert plan.checked is True
    assert not plan
    assert plan.evidence == "exact"


def test_pages_that_go_backwards_are_flagged_by_submission() -> None:
    plan = plan_page_order(
        _numbered(*_qr(1, 2, 3), *_qr(1, 3, 2)), submission_length=3
    )
    assert bool(plan) is True
    (window,) = plan.out_of_order_windows
    assert (window.first_page, window.last_page) == (4, 6)
    assert window.numbers == (1, 3, 2)
    assert window.break_page == 6


def test_a_number_repeated_inside_one_submission_is_out_of_order() -> None:
    plan = plan_page_order(_numbered(*_qr(1, 2, 2)), submission_length=3)
    assert bool(plan) is True
    assert plan.out_of_order_windows[0].break_page == 3


def test_numbering_that_starts_late_is_not_a_fault() -> None:
    plan = plan_page_order(
        _numbered(_UNREAD, *_ocr(1, 2, 3)), submission_length=4
    )
    assert not plan
    assert plan.checked is True
    assert plan.unread_pages == (1,)


def test_an_unread_page_between_two_numbers_does_not_break_the_run() -> None:
    plan = plan_page_order(
        _numbered(*_ocr(1), _UNREAD, *_ocr(3)), submission_length=3
    )
    assert not plan
    assert plan.unread_pages == (2,)


def test_page_order_is_not_checked_without_a_submission_length() -> None:
    plan = plan_page_order(_numbered(*_qr(1, 2, 3)))
    assert plan.checked is False
    assert not plan
    assert "--pages-per-submission" in plan.note


def test_page_order_defers_to_an_unresolved_page_count() -> None:
    plan = plan_page_order(_numbered(*_qr(1, 2, 3, 1)), submission_length=3)
    assert plan.checked is False
    assert "whole number" in plan.note


def test_page_order_is_not_checked_when_nothing_could_be_read() -> None:
    plan = plan_page_order(_numbered(_UNREAD, _UNREAD, _UNREAD), submission_length=3)
    assert plan.checked is False
    assert plan.evidence == "none"
    assert "no page number could be read" in plan.note


def test_evidence_is_exact_only_when_every_reading_was_decoded() -> None:
    assert plan_page_order(_numbered(*_qr(1, 2)), submission_length=2).evidence == (
        "exact"
    )
    mixed = _numbered((1, "text_layer"), (2, "qr"))
    assert plan_page_order(mixed, submission_length=2).evidence == "exact"
    half = _numbered((1, "qr"), (2, "ocr"))
    assert plan_page_order(half, submission_length=2).evidence == "ocr"


def test_only_exact_evidence_authorizes_an_unattended_fix() -> None:
    exact = plan_page_order(_numbered(*_qr(1, 3, 2)), submission_length=3)
    ocr = plan_page_order(_numbered(*_ocr(1, 3, 2)), submission_length=3)

    assert page_order_fix_is_safe(exact) is True
    assert page_order_fix_is_safe(ocr) is False
    assert page_order_fix_is_safe(ocr, has_blank_template=True) is True
    in_order = plan_page_order(_numbered(*_qr(1, 2, 3)), submission_length=3)
    assert page_order_fix_is_safe(in_order, has_blank_template=True) is False


def test_out_of_order_pages_are_high_severity_and_not_fixable_here() -> None:
    issues = build_scan_issues(_numbered(*_qr(1, 3, 2)), pages_per_submission=3)
    (issue,) = [i for i in issues if i.kind is ScanIssueKind.PAGE_ORDER]
    assert issue.severity == "high"
    assert issue.page == 1
    assert issue.fixable is False
    assert "--split every-n" in issue.detail


def test_the_remedy_offered_depends_on_how_the_numbers_were_read() -> None:
    ocr_issues = build_scan_issues(_numbered(*_ocr(1, 3, 2)), pages_per_submission=3)
    (issue,) = [i for i in ocr_issues if i.kind is ScanIssueKind.PAGE_ORDER]
    assert "--split every-n" not in issue.detail
    assert "misread" in issue.detail
    assert "--reorder" in issue.detail


def test_an_unread_page_number_is_rendered_as_a_question_mark() -> None:
    issues = build_scan_issues(
        _numbered((1, "qr"), _UNREAD, (3, "qr"), (2, "qr")),
        pages_per_submission=4,
    )
    (issue,) = [i for i in issues if i.kind is ScanIssueKind.PAGE_ORDER]
    assert "1, ?, 3, 2" in issue.detail


def test_a_skipped_page_order_check_says_so() -> None:
    report = build_scan_report(
        _numbered(*_qr(1, 3, 2)),
        source="scan.pdf",
        pages_per_submission=3,
        check_page_order=False,
    )
    assert report.page_order_plan.checked is False
    assert "skipped" in report.page_order_plan.note
    assert ScanIssueKind.PAGE_ORDER not in _kinds(report.issues)


def test_issues_are_sorted_most_severe_first_then_in_reading_order() -> None:
    features = [
        _page(1, ink_fraction=0.0),
        _page(2, dpi=100.0),
        _page(3, rotation=180),
    ]
    issues = build_scan_issues(features, pages_per_submission=2)
    severities = [issue.severity for issue in issues]
    assert severities == sorted(severities, key=["high", "medium", "low"].index)
    assert issues[0].kind is ScanIssueKind.PAGE_COUNT_MISMATCH
    assert issues[0].page is None


def test_report_carries_the_plan_that_produced_its_issues() -> None:
    report = build_scan_report([_page(1, rotation=90)], source="scan.pdf")
    assert report.rotation_plan.angles == {1: 90}
    assert report.fixable_issues == report.issues
    assert report.counts_by_severity() == {"high": 1, "medium": 0, "low": 0}


def test_report_records_that_orientation_was_never_checked() -> None:
    report = build_scan_report(
        [_page(1, rotation=None, orientation_confidence=None)],
        source="scan.pdf",
        orientation_available=False,
        orientation_note="no osd.traineddata",
    )
    assert report.orientation_available is False
    assert report.issues == ()
    assert report.orientation_note


def test_summarize_issues_counts_by_severity() -> None:
    issues = build_scan_issues([_page(1, rotation=180, ink_fraction=0.0)])
    assert summarize_issues(issues) == "1 high, 1 low"
    assert summarize_issues([]) == "no problems found"


EDGE_SOLID = 0.6


def _canvas(width: int = 400, height: int = 500) -> Image.Image:
    return Image.new("L", (width, height), color=255)


def test_blank_paper_measures_as_blank() -> None:
    features = measure_page(_canvas())
    assert features.ink_fraction == 0.0
    assert features.edge_ink == (0.0, 0.0, 0.0, 0.0)


def test_written_page_measures_as_inked() -> None:
    image = _canvas()
    for y in range(100, 300):
        for x in range(80, 320):
            image.putpixel((x, y), 0)
    assert measure_page(image).ink_fraction > BLANK_INK_FRACTION


def test_a_dark_border_does_not_make_a_blank_page_look_written_on() -> None:
    image = _canvas()
    for y in range(image.height):
        for x in list(range(0, 3)) + list(range(image.width - 3, image.width)):
            image.putpixel((x, y), 0)
    features = measure_page(image)
    assert features.ink_fraction == 0.0
    assert features.edge_ink is not None and features.edge_ink[0] > EDGE_SOLID


def test_ink_touching_an_edge_is_visible_in_the_edge_measurement() -> None:
    image = _canvas()
    for y in range(200, 260):
        for x in range(0, 30):
            image.putpixel((x, y), 0)
    features = measure_page(image)
    assert features.edge_ink is not None
    left, right, top, bottom = features.edge_ink
    assert left > 0 and right == 0 and top == 0 and bottom == 0
    assert _kinds(build_scan_issues([features])) == [ScanIssueKind.MARGIN_CLIPPED]


def test_ink_fraction_of_an_unmeasurable_image_is_none() -> None:
    class Unopenable:
        def convert(self, _mode):
            raise OSError("truncated")

    assert ink_fraction(Unopenable()) is None


def _pdf(path: Path, pages: int = 2, *, text: str = "Question 1") -> Path:
    doc = fitz.open()
    for _index in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 200), text)
    doc.save(str(path))
    doc.close()
    return path


def test_analyze_measures_every_page_and_reports_the_source(tmp_path: Path) -> None:
    report = analyze_scan_pdf(
        _pdf(tmp_path / "scan.pdf", pages=3), check_orientation=False
    )
    assert report.page_count == 3
    assert [features.page for features in report.features] == [1, 2, 3]
    assert report.source.endswith("scan.pdf")
    assert report.orientation_available is False


def test_analyze_folds_in_the_page_count_expectation(tmp_path: Path) -> None:
    report = analyze_scan_pdf(
        _pdf(tmp_path / "scan.pdf", pages=5),
        pages_per_submission=2,
        check_orientation=False,
    )
    assert ScanIssueKind.PAGE_COUNT_MISMATCH in _kinds(report.issues)


def test_born_digital_pages_are_not_flagged_for_resolution(tmp_path: Path) -> None:
    report = analyze_scan_pdf(_pdf(tmp_path / "scan.pdf"), check_orientation=False)
    assert all(features.dpi is None for features in report.features)
    assert ScanIssueKind.LOW_RESOLUTION not in _kinds(report.issues)


def test_a_low_dpi_scanned_page_is_caught(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (612, 792), color="white").save(image_path)
    pdf_path = tmp_path / "lowres.pdf"
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_image(fitz.Rect(0, 0, 612, 792), filename=str(image_path))
    doc.save(str(pdf_path))
    doc.close()

    report = analyze_scan_pdf(pdf_path, check_orientation=False)
    assert report.features[0].dpi is not None
    assert report.features[0].dpi < CRITICAL_DPI
    low = [
        issue for issue in report.issues if issue.kind is ScanIssueKind.LOW_RESOLUTION
    ]
    assert low and low[0].severity == "high"


def _footer_pdf(path: Path, numbers) -> Path:
    doc = fitz.open()
    for number in numbers:
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 200), "Question text")
        page.insert_text((290, 740), f"Page {number}")
    doc.save(str(path))
    doc.close()
    return path


def test_page_numbers_are_read_off_the_text_layer(tmp_path: Path) -> None:
    report = analyze_scan_pdf(
        _footer_pdf(tmp_path / "scan.pdf", [1, 2, 3]),
        pages_per_submission=3,
        check_orientation=False,
    )
    assert [f.page_number for f in report.features] == [1, 2, 3]
    assert {f.page_number_source for f in report.features} == {"text_layer"}
    assert report.page_order_plan.evidence == "exact"
    assert not report.page_order_plan


def test_a_real_out_of_order_scan_is_flagged(tmp_path: Path) -> None:
    report = analyze_scan_pdf(
        _footer_pdf(tmp_path / "jumbled.pdf", [1, 2, 3, 1, 3, 2]),
        pages_per_submission=3,
        check_orientation=False,
    )
    order = [i for i in report.issues if i.kind is ScanIssueKind.PAGE_ORDER]
    assert len(order) == 1, "only the affected submission should be reported"
    assert order[0].page == 4
    assert page_order_fix_is_safe(report.page_order_plan) is True


def test_a_split_submission_is_measured_against_its_own_length(
    tmp_path: Path,
) -> None:
    report = analyze_scan_pdf(
        _footer_pdf(tmp_path / "submission-0001.pdf", [1, 3, 2]),
        single_submission=True,
        check_orientation=False,
    )
    assert report.page_order_plan.submission_length == 3
    assert bool(report.page_order_plan) is True


def test_a_bundle_is_never_treated_as_one_long_submission(tmp_path: Path) -> None:
    report = analyze_scan_pdf(
        _footer_pdf(tmp_path / "bundle.pdf", [1, 2, 1, 2]),
        pages_per_submission=2,
        check_orientation=False,
    )
    assert not report.page_order_plan


def test_no_page_numbers_are_read_when_the_window_is_unknown(
    tmp_path: Path, monkeypatch
) -> None:
    from opengrader.core.submissions import scan_check as module

    calls: list[int] = []
    monkeypatch.setattr(
        module,
        "detect_page_number",
        lambda *a, **k: calls.append(1) or (None, None),
    )
    report = analyze_scan_pdf(
        _footer_pdf(tmp_path / "scan.pdf", [1, 2, 3]), check_orientation=False
    )
    assert calls == []
    assert report.page_order_plan.checked is False


def test_a_qr_stamped_sheet_reads_as_exact_evidence(tmp_path: Path) -> None:
    from opengrader.core.qr_codes import OpengraderCode, render_qr_png

    pdf_path = tmp_path / "qr.pdf"
    doc = fitz.open()
    for number in (1, 3, 2):
        page = doc.new_page(width=612, height=792)
        page.insert_image(
            fitz.Rect(460, 40, 570, 150),
            stream=render_qr_png(
                OpengraderCode(version="A", page=number, total_pages=3)
            ),
        )
    doc.save(str(pdf_path))
    doc.close()

    report = analyze_scan_pdf(
        pdf_path, pages_per_submission=3, check_orientation=False
    )
    assert [f.page_number for f in report.features] == [1, 3, 2]
    assert {f.page_number_source for f in report.features} == {"qr"}
    assert page_order_fix_is_safe(report.page_order_plan) is True


def test_orientation_failure_disables_the_check_for_the_whole_document(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[int] = []

    def _unavailable(_image):
        calls.append(1)
        raise OrientationUnavailableError("osd.traineddata not found")

    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation", _unavailable
    )
    report = analyze_scan_pdf(_pdf(tmp_path / "scan.pdf", pages=4))

    assert len(calls) == 1
    assert report.orientation_available is False
    assert "osd.traineddata" in report.orientation_note
    assert ScanIssueKind.ROTATED_PAGE not in _kinds(report.issues)


def test_a_page_with_too_little_text_stays_unverified(
    tmp_path: Path, monkeypatch
) -> None:
    def _per_page(_image):
        raise ValueError("Too few characters")

    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation", _per_page
    )
    report = analyze_scan_pdf(_pdf(tmp_path / "scan.pdf", pages=2))

    assert report.orientation_available is True
    assert report.rotation_plan.unverified_pages == (1, 2)


def test_analyze_detects_a_rotated_page(tmp_path: Path, monkeypatch) -> None:
    def _fake(_image):
        return 180, 12.0

    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation", _fake
    )
    report = analyze_scan_pdf(_pdf(tmp_path / "scan.pdf", pages=2))
    assert report.rotation_plan.angles == {1: 180, 2: 180}
    assert all(issue.fixable for issue in report.high_severity)


def test_rotation_is_written_to_a_copy_and_recorded_on_the_page(
    tmp_path: Path,
) -> None:
    source = _pdf(tmp_path / "scan.pdf", pages=3)
    before = source.read_bytes()
    destination = tmp_path / "fixed" / "scan.pdf"

    turned = apply_rotation_fixes(source, destination, {2: 180})

    assert turned == 1
    assert source.read_bytes() == before
    pages = PdfReader(str(destination)).pages
    assert [page.rotation for page in pages] == [0, 180, 0]


def test_rotation_accepts_a_plan_directly(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "scan.pdf", pages=2)
    plan = plan_rotations([_page(1, rotation=90), _page(2, rotation=90)])
    assert apply_rotation_fixes(source, tmp_path / "out.pdf", plan) == 2


def test_rotation_adds_to_a_page_that_is_already_turned(tmp_path: Path) -> None:
    source = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page(width=612, height=792).set_rotation(90)
    doc.save(str(source))
    doc.close()

    apply_rotation_fixes(source, tmp_path / "out.pdf", {1: 180})
    assert PdfReader(str(tmp_path / "out.pdf")).pages[0].rotation == 270


def test_a_rotation_that_is_not_a_quarter_turn_is_refused(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "scan.pdf")
    with pytest.raises(ValueError, match="multiple of 90"):
        apply_rotation_fixes(source, tmp_path / "out.pdf", {1: 45})
    assert not (tmp_path / "out.pdf").exists()


def test_a_page_outside_the_document_is_refused(tmp_path: Path) -> None:
    source = _pdf(tmp_path / "scan.pdf", pages=2)
    with pytest.raises(ValueError, match="outside"):
        apply_rotation_fixes(source, tmp_path / "out.pdf", {5: 180})
    assert not (tmp_path / "out.pdf").exists()


def test_analyze_then_fix_makes_the_pages_upright(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation",
        lambda _image: (180, 12.0),
    )
    source = _pdf(tmp_path / "scan.pdf", pages=3)
    report = analyze_scan_pdf(source)
    destination = tmp_path / "upright.pdf"

    assert apply_rotation_fixes(source, destination, report.rotation_plan) == 3
    assert [page.rotation for page in PdfReader(str(destination)).pages] == [180] * 3


def _osd_available() -> bool:
    try:
        detect_page_orientation(Image.new("L", (100, 100), color=255))
    except OrientationUnavailableError:
        return False
    except Exception:
        return True
    return True


needs_osd = pytest.mark.skipif(
    not _osd_available(), reason="requires Tesseract with osd.traineddata"
)


def _text_pdf(path: Path, *, rotation: int) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    body = ("The quick brown fox jumps over the lazy dog. " * 3 + "\n") * 12
    page.insert_textbox(fitz.Rect(60, 80, 552, 700), body, fontsize=12)
    page.set_rotation(rotation)
    doc.save(str(path))
    doc.close()
    return path


@needs_osd
@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_the_reported_rotation_really_makes_the_page_upright(
    tmp_path: Path, rotation: int
) -> None:
    source = _text_pdf(tmp_path / f"rot{rotation}.pdf", rotation=rotation)

    report = analyze_scan_pdf(source)
    assert report.rotation_plan.angles, "a turned page should have been detected"

    upright = tmp_path / f"rot{rotation}-upright.pdf"
    apply_rotation_fixes(source, upright, report.rotation_plan)

    assert analyze_scan_pdf(upright).rotation_plan.angles == {}


@needs_osd
def test_an_upright_page_is_left_alone(tmp_path: Path) -> None:
    report = analyze_scan_pdf(_text_pdf(tmp_path / "fine.pdf", rotation=0))
    assert report.orientation_available is True
    assert report.rotation_plan.angles == {}
    assert ScanIssueKind.ROTATED_PAGE not in _kinds(report.issues)


def test_default_upright_path_sits_beside_the_original() -> None:
    assert default_upright_path(Path("/scans/all.pdf")) == Path(
        "/scans/all-upright.pdf"
    )


def test_discovery_takes_a_file_or_a_flat_directory(tmp_path: Path) -> None:
    _pdf(tmp_path / "a.pdf")
    _pdf(tmp_path / "b.pdf")
    (tmp_path / "nested").mkdir()
    _pdf(tmp_path / "nested" / "c.pdf")
    (tmp_path / "notes.txt").write_text("not a scan")

    assert discover_scan_pdfs(tmp_path / "a.pdf") == [tmp_path / "a.pdf"]
    assert discover_scan_pdfs(tmp_path) == [tmp_path / "a.pdf", tmp_path / "b.pdf"]
