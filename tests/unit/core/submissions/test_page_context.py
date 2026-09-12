from opengrader.core.source_location import QuestionSourceSegment
from opengrader.core.submissions.page_context import (
    PageContextEntry,
    build_page_context,
    covered_pages,
    infer_extra_pages,
)


def _segment(page: int, y: float = 0.2) -> QuestionSourceSegment:
    return QuestionSourceSegment(page_number=page, bbox_norm=(0.1, y, 0.6, 0.1))


def test_single_segment_yields_its_page_with_the_crop_highlighted() -> None:
    context = build_page_context([_segment(2)])
    assert context == (
        PageContextEntry(
            page_number=2,
            kind="response",
            highlights=((0.1, 0.2, 0.6, 0.1),),
        ),
    )


def test_segments_on_one_page_collapse_into_a_single_entry() -> None:
    context = build_page_context([_segment(1, 0.2), _segment(1, 0.6)])
    assert len(context) == 1
    assert context[0].highlights == ((0.1, 0.2, 0.6, 0.1), (0.1, 0.6, 0.6, 0.1))


def test_response_pages_are_ordered_before_extra_pages() -> None:
    context = build_page_context([_segment(3), _segment(1)], extra_pages=[5, 4])
    assert [(e.page_number, e.kind) for e in context] == [
        (1, "response"),
        (3, "response"),
        (4, "extra"),
        (5, "extra"),
    ]


def test_extra_page_that_is_also_a_response_page_stays_a_response_entry() -> None:
    context = build_page_context([_segment(2)], extra_pages=[2, 3])
    assert [(e.page_number, e.kind) for e in context] == [(2, "response"), (3, "extra")]


def test_extra_pages_are_deduplicated_and_sorted() -> None:
    context = build_page_context([], extra_pages=[4, 2, 4, 2])
    assert [e.page_number for e in context] == [2, 4]


def test_nonsense_page_numbers_are_dropped() -> None:
    assert build_page_context([_segment(0)], extra_pages=[0, -1]) == ()


def test_a_question_with_no_segments_still_reaches_the_extra_pages() -> None:
    context = build_page_context([], extra_pages=[3])
    assert [(e.page_number, e.kind) for e in context] == [(3, "extra")]


def test_covered_pages_lists_pages_a_crop_came_from() -> None:
    assert covered_pages([_segment(1), _segment(3), _segment(1)]) == frozenset({1, 3})


def test_uncovered_pages_are_inferred_as_extra() -> None:
    assert infer_extra_pages(4, [_segment(1), _segment(2)]) == (3, 4)


def test_gaps_between_covered_pages_count_as_extra() -> None:
    assert infer_extra_pages(3, [_segment(1), _segment(3)]) == (2,)


def test_fully_covered_scan_has_no_extra_pages() -> None:
    assert infer_extra_pages(2, [_segment(1), _segment(2)]) == ()


def test_no_segments_means_no_conclusion_rather_than_every_page() -> None:
    assert infer_extra_pages(5, []) == ()


def test_unknown_page_count_infers_nothing() -> None:
    assert infer_extra_pages(0, [_segment(1)]) == ()
