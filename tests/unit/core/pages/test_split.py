from pathlib import Path

import numpy as np

from opengrader.core.pages.catalog import PageCatalog, PageGroup, PageMatch, ScanPage
from opengrader.core.pages.layout import PageLayout
from opengrader.core.pages.split import plan_submissions, shared_order_note, unclear_starts


def _catalog(sequence: list[str | None], *, pages: int = 3) -> PageCatalog:
    groups = {
        str(n): PageGroup(id=str(n), reference=ScanPage(Path("scan.pdf"), n - 1), reference_turns=0,
                          layout=PageLayout(), page_number=n)
        for n in range(1, pages + 1)
    }
    matches = [PageMatch(ScanPage(Path("scan.pdf"), i), group, np.eye(3) if group else None, 100)
               for i, group in enumerate(sequence)]
    return PageCatalog(groups=groups, matches=matches)


def _pages(catalog: PageCatalog) -> list[list[int]]:
    return [[m.page.index for m in s.pages] for s in plan_submissions(catalog)]


def test_each_copy_of_page_one_starts_a_submission():
    catalog = _catalog(["1", "2", "3", "1", "2", "3"])

    planned = plan_submissions(catalog)

    assert [[m.page.index for m in s.pages] for s in planned] == [[0, 1, 2], [3, 4, 5]]
    assert all(not s.warnings for s in planned)


def test_a_missing_page_is_flagged_without_shifting_the_next_student():
    planned = plan_submissions(_catalog(["1", "3", "1", "2", "3"]))

    assert [[m.page.index for m in s.pages] for s in planned] == [[0, 1], [2, 3, 4]]
    assert any("Missing page(s) 2" in w for w in planned[0].warnings)
    assert not planned[1].warnings


def test_a_student_missing_page_one_still_gets_their_own_submission():
    planned = plan_submissions(_catalog(["1", "2", "3", "2", "3"]))

    assert [[m.page.index for m in s.pages] for s in planned] == [[0, 1, 2], [3, 4]]
    assert any("Missing page(s) 1" in w for w in planned[1].warnings)


def test_pages_out_of_order_are_reported_and_left_as_scanned():
    planned = plan_submissions(_catalog(["1", "3", "2", "1", "2", "3"]))

    assert [m.page.index for m in planned[0].pages] == [0, 1, 2]
    assert not planned[0].reordered
    assert any("out of order" in w and "left as scanned" in w and "1, 3, 2" in w for w in planned[0].warnings)
    assert not planned[1].warnings


def test_reorder_puts_out_of_order_pages_back_in_order_and_notes_it():
    planned = plan_submissions(_catalog(["1", "3", "2", "1", "2", "3"]), reorder=True)

    assert [m.page.index for m in planned[0].pages] == [0, 2, 1]
    assert planned[0].reordered
    assert any("out of order" in w and "sorted by page number" in w for w in planned[0].warnings)


def test_a_page_scanned_just_before_its_students_page_one_goes_with_that_student():
    planned = plan_submissions(_catalog(["1", "2", "3", "2", "1", "3"]), reorder=True)

    assert [[m.page.index for m in s.pages] for s in planned] == [[0, 1, 2], [4, 3, 5]]
    assert not planned[0].warnings


def test_unrecognized_pages_are_kept_in_place_and_flagged():
    planned = plan_submissions(_catalog(["1", "2", None, "3"]))

    assert [m.page.index for m in planned[0].pages] == [0, 1, 2, 3]
    assert any("did not match any printed page" in w and "left where" in w for w in planned[0].warnings)
    assert not any("out of order" in w for w in planned[0].warnings)


def test_reorder_moves_nothing_when_the_numbers_already_run_forwards():
    planned = plan_submissions(_catalog(["1", "2", None, "3"]), reorder=True)

    assert [m.page.index for m in planned[0].pages] == [0, 1, 2, 3]
    assert not planned[0].reordered


def test_reorder_keeps_unrecognized_pages_at_the_end_of_a_sorted_submission():
    planned = plan_submissions(_catalog(["1", "3", None, "2"]), reorder=True)

    assert [m.page.index for m in planned[0].pages] == [0, 3, 1, 2]
    assert any("kept at the end" in w for w in planned[0].warnings)


def test_the_same_break_in_most_submissions_reads_as_a_misread_number():
    catalog = _catalog(["1", "3", "2"] * 4 + ["1", "2", "3"])

    planned = plan_submissions(catalog)

    assert [[m.page.index for m in s.pages] for s in planned] == [[i, i + 1, i + 2] for i in range(0, 15, 3)]
    assert all(s.order_shared for s in planned[:4]) and not planned[4].order_shared
    assert all(any("probably misread" in w for w in s.warnings) for s in planned[:4])
    note = shared_order_note(planned)
    assert note is not None and "4 of 5 submissions" in note and "page 2 right after page 3" in note


def test_one_shuffled_student_is_not_a_class_wide_pattern():
    planned = plan_submissions(_catalog(["1", "2", "3"] * 3 + ["1", "3", "2"]))

    assert not planned[3].order_shared
    assert any("Check them by eye" in w for w in planned[3].warnings)
    assert shared_order_note(planned) is None


def test_template_positions_overrule_a_misread_page_number():
    catalog = _catalog(["1", "2", "3"] * 3)
    catalog.groups["2"].page_number = 8

    assert all(s.order_breaks == ((8, 3),) for s in plan_submissions(catalog))
    planned = plan_submissions(catalog, reorder=True, template_positions={"1": 1, "2": 2, "3": 3})

    assert [[m.page.index for m in s.pages] for s in planned] == [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
    assert not any(s.order_breaks for s in planned)


def test_reorder_follows_the_template_order_when_every_page_is_in_it():
    planned = plan_submissions(_catalog(["1", "2", "3"]), reorder=True, template_positions={"1": 2, "2": 1, "3": 3})

    assert [m.page.index for m in planned[0].pages] == [1, 0, 2]
    assert any("template" in w for w in planned[0].warnings)


def test_a_page_missing_from_the_template_falls_back_to_page_numbers():
    planned = plan_submissions(_catalog(["1", "3", "2"]), reorder=True, template_positions={"1": 1, "3": 2})

    assert [m.page.index for m in planned[0].pages] == [0, 2, 1]
    assert any("sorted by page number" in w for w in planned[0].warnings)


def test_a_repeated_page_starts_a_new_submission():
    planned = plan_submissions(_catalog(["1", "2", "2", "3"]))

    assert [[m.page.index for m in s.pages] for s in planned] == [[0, 1], [2, 3]]


def test_a_shuffled_student_next_to_one_missing_page_one_reads_as_two_students():
    planned = plan_submissions(_catalog(["2", "1", "3", "2", "3"]), reorder=True)

    assert [[m.page.index for m in s.pages] for s in planned] == [[1, 0, 2], [3, 4]]
    assert any("Missing page(s) 1" in w for w in planned[1].warnings)


def test_an_expected_page_count_never_overrules_the_page_numbers():
    planned = plan_submissions(_catalog(["1", "2", "3", "3", "1", "2", "3"]), expected_count=3)

    assert [[m.page.index for m in s.pages] for s in planned] == [[0, 1, 2], [3], [4, 5, 6]]
    assert not planned[0].warnings and not planned[2].warnings
    assert any("Missing page(s) 1, 2" in w for w in planned[1].warnings)


def test_a_submission_longer_than_expected_is_flagged():
    planned = plan_submissions(_catalog(["1", "2", None, "3"]), expected_count=3)

    assert any("4 page(s) scanned where 3 are expected" in w for w in planned[0].warnings)


def test_an_answer_continues_onto_the_next_numbered_page_wherever_it_was_scanned():
    from opengrader.core.pages.extract import AlignedPage, _continued_by

    catalog = _catalog(["1", "3", "2"])
    pages = [AlignedPage(i + 1, None, catalog.groups[g], None) for i, g in enumerate(["1", "3", "2"])]
    by_number = {p.group.page_number: p for p in pages}

    assert _continued_by(pages[0], by_number) is pages[2]
    assert _continued_by(pages[2], by_number) is pages[1]
    assert _continued_by(pages[1], by_number) is None


def test_printed_pages_are_matched_to_the_blank_template_by_their_print(tmp_path: Path):
    import fitz

    from opengrader.core.pages.split import template_positions

    def pdf(path: Path, order: list[int]) -> Path:
        doc = fitz.open()
        for number in order:
            page = doc.new_page(width=612, height=792)
            for line in range(12):
                page.insert_text((60, 90 + 50 * line), f"Question {number}.{line}: " + chr(64 + number) * (8 + 3 * line + number), fontsize=14)
        doc.save(str(path))
        doc.close()
        return path

    template, scan = pdf(tmp_path / "template.pdf", [1, 2, 3]), pdf(tmp_path / "scan.pdf", [3, 1, 2])
    blank = fitz.open()
    blank.new_page(width=612, height=792)
    blank.save(str(tmp_path / "blank.pdf"))
    groups = {
        gid: PageGroup(id=gid, reference=ScanPage(source, index), reference_turns=0, layout=PageLayout())
        for gid, source, index in [("a", scan, 0), ("b", scan, 1), ("c", scan, 2), ("other", tmp_path / "blank.pdf", 0)]
    }

    assert template_positions(PageCatalog(groups=groups, matches=[]), template) == {"a": 3, "b": 1, "c": 2}


def _unnumbered(catalog: PageCatalog, *groups: str) -> PageCatalog:
    for group in groups:
        catalog.groups[group].page_number = None
    return catalog


def test_a_scan_whose_page_numbers_are_read_shows_where_papers_start():
    assert unclear_starts(_catalog(["1", "2", "3", "1", "2", "3"])) is None


def test_blank_backs_never_make_a_scan_unclear():
    # Double-sided scanning of one-sided sheets: half the pages are blank.
    assert unclear_starts(_catalog(["1", None, "2", None, "3", None] * 2)) is None


def test_a_scan_with_no_page_numbers_is_unclear():
    catalog = _unnumbered(_catalog(["1", "2", "3"] * 3), "1", "2", "3")

    assert "no page was read as the first page" in unclear_starts(catalog)


def test_a_scan_with_few_page_numbers_is_unclear():
    catalog = _unnumbered(_catalog(["1", "2", "3"] * 3), "2", "3")

    assert "only 3 of 9 printed pages" in unclear_starts(catalog)


def test_a_scan_matching_no_printed_page_is_unclear():
    assert "no scanned page matched" in unclear_starts(_catalog([None, None, None]))


def _lengths(catalog: PageCatalog, expected_count: int | None = None) -> list[int]:
    return [len(s.pages) for s in plan_submissions(catalog, expected_count=expected_count)]


def test_with_no_page_number_read_the_expected_length_cuts_the_scan():
    catalog = _unnumbered(_catalog(["1", "2", "3"] * 5), "1", "2", "3")

    assert _lengths(catalog, expected_count=3) == [3, 3, 3, 3, 3]


def test_when_only_page_one_is_read_each_paper_starts_on_it():
    # Unread pages used to start papers for free: [1], [_, _, 1], [_, _, 1, _], …
    catalog = _unnumbered(_catalog(["1", "2", "3"] * 5), "2", "3")

    assert [[m.page.index for m in s.pages] for s in plan_submissions(catalog)] == [
        [i, i + 1, i + 2] for i in range(0, 15, 3)
    ]


def test_blank_backs_go_with_the_page_they_were_scanned_after():
    catalog = _catalog(["1", None, "2", None, "3", None] * 3)

    assert _lengths(catalog) == [6, 6, 6]
    assert all(s.pages[0].group == "1" for s in plan_submissions(catalog))


def test_a_scratch_sheet_stays_with_the_paper_it_was_handed_in_with():
    catalog = _catalog(["1", "2", "3", None, "1", "2", "3", None, None, "1", "2", "3"])

    assert _lengths(catalog) == [4, 5, 3]


def test_an_unread_page_beyond_the_expected_length_starts_no_paper_of_its_own():
    # Page 3's number went unread on the first paper: it is that paper's page,
    # not a one-page paper with every page missing.
    catalog = _catalog(["1", "2", "3", "1", "2", "3"])
    catalog.matches[2] = PageMatch(ScanPage(Path("scan.pdf"), 2), "x", np.eye(3), 100)
    catalog.groups["x"] = PageGroup(id="x", reference=ScanPage(Path("scan.pdf"), 2), reference_turns=0,
                                    layout=PageLayout(), page_number=None)

    assert _lengths(catalog, expected_count=3) == [3, 3]


def _coded(catalog: PageCatalog, *codes) -> PageCatalog:
    from opengrader.core.qr_codes import OpengraderCode

    for match, (student, version, page) in zip(catalog.matches, codes):
        match.code = OpengraderCode(student_id=student, version=version, page=page, total_pages=3)
    return catalog


def test_a_qr_page_number_overrules_a_misread_footer():
    catalog = _catalog(["1", "2", "3"] * 2)
    catalog.groups["2"].page_number = 8
    _coded(catalog, *[(None, "A", n) for n in (1, 2, 3, 1, 2, 3)])

    planned = plan_submissions(catalog)

    assert [len(s.pages) for s in planned] == [3, 3]
    assert not any(s.order_breaks for s in planned)


def test_qr_codes_naming_two_students_are_never_one_paper():
    # Page numbers alone read this as one paper: Ada's pages 1-2, Ben's page 3.
    catalog = _coded(_catalog(["1", "2", "3"]), ("ada", "A", 1), ("ada", "A", 2), ("ben", "A", 3))

    assert [[m.page.index for m in s.pages] for s in plan_submissions(catalog)] == [[0, 1], [2]]


def test_qr_codes_naming_two_versions_are_never_one_paper():
    catalog = _coded(_catalog(["1", "2", "3"]), (None, "A", 1), (None, "A", 2), (None, "B", 3))

    assert [[m.page.index for m in s.pages] for s in plan_submissions(catalog)] == [[0, 1], [2]]
