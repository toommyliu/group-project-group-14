from opengrader.core.submissions.boundary import submission_warnings


def test_a_short_submission_is_called_incomplete() -> None:
    (warning,) = submission_warnings(2, 3)
    assert "incomplete scan, 2 of 3 expected pages; 1 page(s) missing" in warning


def test_a_long_submission_is_called_unusual() -> None:
    (warning,) = submission_warnings(5, 3)
    assert "unusual length, 5 pages for 3 expected" in warning


def test_a_submission_of_the_expected_length_has_no_warning() -> None:
    assert submission_warnings(3, 3, [1, 2, 3]) == ()
    assert submission_warnings(3, None) == ()


def test_repeated_and_missing_page_numbers_are_named() -> None:
    warnings = submission_warnings(3, 3, [1, 1, 3])
    assert any("repeated printed page numbers" in w for w in warnings)
    assert any("missing printed page(s) 2" in w for w in warnings)


def test_missing_numbers_are_named_only_when_every_page_was_read() -> None:
    assert not any("missing printed" in w for w in submission_warnings(3, 3, [1, None, 3]))


def test_the_label_names_the_submission() -> None:
    (warning,) = submission_warnings(1, 2, label="submission-0004")
    assert warning.startswith("submission-0004: ")


def test_scan_warning_survives_split_extraction_and_markdown(tmp_path) -> None:
    import fitz
    from opengrader.core.submissions.splitter import plan_submission_ranges, split_scanned_submission_pdf
    from opengrader.core.submissions.processor import build_student_submission_document_unified
    from opengrader.core.submissions.student_file import load_student_submission_markdown
    from opengrader.core.submissions.review import flag_submission, ReviewReason

    source = tmp_path / "scan.pdf"
    with fitz.open() as doc:
        for number in [1, 2, 3, 1, 2]:
            page = doc.new_page()
            page.insert_text((60, 70), "Name: Student  Student ID: 123456789")
            page.insert_text((60, 250), "1. (10 points) Explain recursion.")
            page.insert_text((290, 780), str(number))
        doc.save(source)
    ranges = plan_submission_ranges(total_pages=5, pages_per_submission=3)
    assert [(r.start_page, r.end_page) for r in ranges] == [(1, 3), (4, 5)]
    paths = split_scanned_submission_pdf(source, tmp_path / "split", ranges)
    _, markdown = build_student_submission_document_unified(
        paths[1], tmp_path / "extracted", None, 72, True, None, True, False, None, lambda message: None,
    )
    document = load_student_submission_markdown(markdown)
    flags = [flag for flag in flag_submission(document) if flag.reason == ReviewReason.SCAN_INCOMPLETE]
    assert [flag.detail for flag in flags] == list(ranges[1].warnings)
    assert "2 of 3" in flags[0].detail
