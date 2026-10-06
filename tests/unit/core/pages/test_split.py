from pathlib import Path

import numpy as np

from opengrader.core.pages.catalog import PageCatalog, PageGroup, PageMatch, ScanPage
from opengrader.core.pages.layout import PageLayout
from opengrader.core.pages.split import plan_submissions, shared_order_note


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


def test_an_expected_page_count_checks_the_split_but_does_not_decide_it():
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
