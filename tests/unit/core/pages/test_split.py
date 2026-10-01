from pathlib import Path

import numpy as np

from opengrader.core.pages.catalog import PageCatalog, PageGroup, PageMatch, ScanPage
from opengrader.core.pages.layout import PageLayout
from opengrader.core.pages.split import plan_submissions


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


def test_pages_out_of_order_are_put_back_in_order_and_noted():
    planned = plan_submissions(_catalog(["1", "3", "2", "1", "2", "3"]))

    assert [m.page.index for m in planned[0].pages] == [0, 2, 1]
    assert any("out of order" in w for w in planned[0].warnings)


def test_a_page_scanned_just_before_its_students_page_one_goes_with_that_student():
    planned = plan_submissions(_catalog(["1", "2", "3", "2", "1", "3"]))

    assert [[m.page.index for m in s.pages] for s in planned] == [[0, 1, 2], [4, 3, 5]]
    assert not planned[0].warnings


def test_unrecognized_pages_are_kept_and_flagged():
    planned = plan_submissions(_catalog(["1", "2", None, "3"]))

    assert [m.page.index for m in planned[0].pages] == [0, 1, 3, 2]
    assert any("did not match any printed page" in w for w in planned[0].warnings)


def test_a_repeated_page_starts_a_new_submission():
    planned = plan_submissions(_catalog(["1", "2", "2", "3"]))

    assert [[m.page.index for m in s.pages] for s in planned] == [[0, 1], [2, 3]]


def test_a_shuffled_student_next_to_one_missing_page_one_reads_as_two_students():
    planned = plan_submissions(_catalog(["2", "1", "3", "2", "3"]))

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
