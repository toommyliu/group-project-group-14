from opengrader.core.submissions.reorder import _order_pages_by_template


def test_in_order_pages_stay_in_place() -> None:
    order, warnings = _order_pages_by_template(
        [None, 2, 3, 4],
        (None, 2, 3, 4),
    )
    assert order == [0, 1, 2, 3]
    assert warnings == []


def test_shuffled_pages_follow_template_order() -> None:
    # Scan order: page 3, page 2, page 4 — template order restored.
    order, warnings = _order_pages_by_template(
        [3, 2, 4],
        (2, 3, 4),
    )
    assert order == [1, 0, 2]
    assert warnings == []


def test_unnumbered_title_page_stays_first() -> None:
    # Template: unnumbered title page, then pages 2-4. The scanned title page
    # (no detected number) must fill the unnumbered slot at the front, not be
    # pushed to the back.
    order, warnings = _order_pages_by_template(
        [3, None, 2, 4],
        (None, 2, 3, 4),
    )
    assert order == [1, 2, 0, 3]
    assert warnings == []


def test_detected_number_not_in_template_is_treated_as_unnumbered() -> None:
    # OCR misreads a page as "9"; the template never prints 9, so the page is
    # placed by original order into the free slot instead of sorted by 9.
    order, warnings = _order_pages_by_template(
        [9, 2, 3],
        (1, 2, 3),
    )
    assert order == [0, 1, 2]
    assert len(warnings) == 1
    assert "not printed" in warnings[0]


def test_duplicate_detection_keeps_first_occurrence() -> None:
    order, warnings = _order_pages_by_template(
        [2, 2, 3],
        (1, 2, 3),
    )
    # First "2" claims the label-2 slot; the second fills the free slot (label 1).
    assert order == [1, 0, 2]
    assert len(warnings) == 1
    assert "first occurrence" in warnings[0]


def test_extra_scan_pages_go_to_the_back() -> None:
    # Scratch paper beyond the template page count stays at the end, stable.
    order, warnings = _order_pages_by_template(
        [1, 2, None, None],
        (1, 2),
    )
    assert order == [0, 1, 2, 3]
    assert warnings == []


def test_multiple_unnumbered_slots_fill_in_scan_order() -> None:
    # Two unnumbered template positions; two undetected scan pages keep their
    # relative order when filling them.
    order, warnings = _order_pages_by_template(
        [None, 2, None],
        (None, 2, None),
    )
    assert order == [0, 1, 2]
    assert warnings == []
