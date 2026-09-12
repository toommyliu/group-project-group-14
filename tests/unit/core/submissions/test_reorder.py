from opengrader.core.submissions.reorder import _order_pages_by_template


def test_in_order_pages_stay_in_place() -> None:
    order, warnings, overflowed = _order_pages_by_template(
        [None, 2, 3, 4],
        (None, 2, 3, 4),
    )
    assert order == [0, 1, 2, 3]
    assert warnings == []


def test_shuffled_pages_follow_template_order() -> None:
    order, warnings, overflowed = _order_pages_by_template(
        [3, 2, 4],
        (2, 3, 4),
    )
    assert order == [1, 0, 2]
    assert warnings == []


def test_unnumbered_title_page_stays_first() -> None:
    order, warnings, overflowed = _order_pages_by_template(
        [3, None, 2, 4],
        (None, 2, 3, 4),
    )
    assert order == [1, 2, 0, 3]
    assert warnings == []


def test_detected_number_not_in_template_is_treated_as_unnumbered() -> None:
    order, warnings, overflowed = _order_pages_by_template(
        [9, 2, 3],
        (1, 2, 3),
    )
    assert order == [0, 1, 2]
    assert len(warnings) == 1
    assert "not printed" in warnings[0]


def test_duplicate_detection_keeps_first_occurrence() -> None:
    order, warnings, overflowed = _order_pages_by_template(
        [2, 2, 3],
        (1, 2, 3),
    )
    assert order == [1, 0, 2]
    assert len(warnings) == 1
    assert "first occurrence" in warnings[0]


def test_extra_scan_pages_go_to_the_back() -> None:
    order, warnings, overflowed = _order_pages_by_template(
        [1, 2, None, None],
        (1, 2),
    )
    assert order == [0, 1, 2, 3]
    assert warnings == []


def test_multiple_unnumbered_slots_fill_in_scan_order() -> None:
    order, warnings, overflowed = _order_pages_by_template(
        [None, 2, None],
        (None, 2, None),
    )
    assert order == [0, 1, 2]
    assert warnings == []


import fitz  # noqa: E402

from opengrader.core.submissions.reorder import (  # noqa: E402
    _parse_page_number,
    detect_page_number_text_layer,
)


def test_parse_page_number_variants() -> None:
    assert _parse_page_number("Page 12", max_pages=20) == 12
    assert _parse_page_number("Page Twelve", max_pages=20) == 12
    assert _parse_page_number("Page XII", max_pages=20) == 12
    assert _parse_page_number("3 / 10", max_pages=10) == 3
    assert _parse_page_number("- 4 -", max_pages=10) == 4
    assert _parse_page_number("", max_pages=10) is None
    assert _parse_page_number("Page 99", max_pages=6) is None


def _born_digital_page(footer: str | None = None, body: str | None = None):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    if footer:
        page.insert_text((280, 770), footer)
    if body:
        page.insert_text((280, 100), body)
    return doc, page


def test_text_layer_reads_footer_page_number() -> None:
    doc, page = _born_digital_page(footer="Page 2")
    try:
        assert detect_page_number_text_layer(page, max_pages=6) == 2
    finally:
        doc.close()


def test_text_layer_ignores_number_in_body() -> None:
    doc, page = _born_digital_page(body="See Page 5 for the formula")
    try:
        assert detect_page_number_text_layer(page, max_pages=6) is None
    finally:
        doc.close()


def test_text_layer_returns_none_for_imageless_scan_page() -> None:
    doc, page = _born_digital_page()
    try:
        assert detect_page_number_text_layer(page, max_pages=6) is None
    finally:
        doc.close()


def test_extra_work_pages_pushes_undetected_to_end() -> None:
    order, warnings, overflowed = _order_pages_by_template(
        [1, None, 3, 4],
        (1, 2, 3, 4),
        extra_work_pages=True,
    )
    assert order == [0, 2, 3, 1]
    assert any("Moved 1 extra page" in w for w in warnings)
    assert overflowed == {1}


def test_without_flag_undetected_fills_the_gap() -> None:
    order, warnings, overflowed = _order_pages_by_template(
        [1, None, 3, 4],
        (1, 2, 3, 4),
    )
    assert order == [0, 1, 2, 3]


def test_extra_work_pages_still_fills_unnumbered_title_slot() -> None:
    order, warnings, overflowed = _order_pages_by_template(
        [None, 2, 3, None],
        (None, 2, 3),
        extra_work_pages=True,
    )
    assert order == [0, 1, 2, 3]
    assert any("Moved 1 extra page" in w for w in warnings)
    assert overflowed == {3}


def test_no_overflow_reported_without_the_flag() -> None:
    _order, _warnings, overflowed = _order_pages_by_template(
        [1, None, 3, 4],
        (1, 2, 3, 4),
    )
    assert overflowed == set()


from pathlib import Path  # noqa: E402

from opengrader.core.submissions.reorder import (  # noqa: E402
    PageNumberLayoutInfo,
    ReorderResult,
    reorder_pdf_pages,
)


def _scan_with_footers(path: Path, footers: list[str | None]) -> Path:
    doc = fitz.open()
    for footer in footers:
        page = doc.new_page(width=612, height=792)
        if footer:
            page.insert_text((280, 770), footer)
    doc.save(str(path))
    doc.close()
    return path


def _template(total_pages: int) -> PageNumberLayoutInfo:
    return PageNumberLayoutInfo(
        bbox_norm=(0.4, 0.95, 0.2, 0.04),
        page_w=612,
        page_h=792,
        total_pages=total_pages,
        page_labels=tuple(range(1, total_pages + 1)),
    )


def test_reorder_reports_the_extra_page_it_moved_to_the_end(tmp_path: Path) -> None:
    pdf = _scan_with_footers(tmp_path / "s.pdf", ["1", None, "2", "3"])
    result = reorder_pdf_pages(
        pdf, blank_template_layout=_template(3), extra_work_pages=True
    )
    assert result.changed is True
    assert result.extra_work_pages == (4,)


def test_reorder_reports_extra_pages_even_when_nothing_moved(tmp_path: Path) -> None:
    pdf = _scan_with_footers(tmp_path / "s.pdf", ["1", "2", "3", None])
    result = reorder_pdf_pages(
        pdf, blank_template_layout=_template(3), extra_work_pages=True
    )
    assert result.changed is False
    assert result.extra_work_pages == (4,)


def test_reorder_reports_nothing_without_the_flag(tmp_path: Path) -> None:
    pdf = _scan_with_footers(tmp_path / "s.pdf", ["1", "2", "3", None])
    result = reorder_pdf_pages(pdf, blank_template_layout=_template(4))
    assert result.extra_work_pages == ()


def test_single_page_pdf_is_a_no_op(tmp_path: Path) -> None:
    pdf = _scan_with_footers(tmp_path / "s.pdf", ["1"])
    assert reorder_pdf_pages(pdf) == ReorderResult(changed=False)
