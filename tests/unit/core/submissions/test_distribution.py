from __future__ import annotations

import json

from opengrader.core.submissions.distribution import (
    PageEvidence,
    distribution_plan_to_json,
    plan_submission_distribution,
)


def _evidence(*logical_pages: int | None) -> list[PageEvidence]:
    pages: list[PageEvidence] = []
    for index, logical_page in enumerate(logical_pages, start=1):
        pages.append(
            PageEvidence(
                physical_page=index,
                logical_page=logical_page,
                logical_page_confidence=100.0 if logical_page is not None else 0.0,
                is_first_page=(logical_page == 1),
                first_page_confidence=100.0 if logical_page == 1 else 0.0,
                evidence_reasons=(),
            )
        )
    return pages


def test_perfect_sequence_creates_clean_ranges() -> None:
    plan = plan_submission_distribution(
        page_evidence=_evidence(1, 2, 3, 1, 2, 3),
        pages_per_submission=3,
    )

    assert [page_range.name for page_range in plan.ranges] == [
        "submission-0001",
        "submission-0002",
    ]
    assert [page_range.physical_pages for page_range in plan.ranges] == [
        (1, 2, 3),
        (4, 5, 6),
    ]
    assert all(not page_range.needs_review for page_range in plan.ranges)
    assert not plan.needs_review


def test_missing_interior_page_records_gap_and_starts_next_student() -> None:
    plan = plan_submission_distribution(
        page_evidence=_evidence(1, 2, 4, 1, 2, 3, 4),
        pages_per_submission=4,
    )

    assert [page_range.physical_pages for page_range in plan.ranges] == [
        (1, 2, 3),
        (4, 5, 6, 7),
    ]
    assert plan.ranges[0].missing_pages == (3,)
    assert "missing_logical_page_3" in plan.ranges[0].review_reasons
    assert plan.ranges[1].missing_pages == ()


def test_missing_last_page_is_recorded_before_next_first_page() -> None:
    plan = plan_submission_distribution(
        page_evidence=_evidence(1, 2, 1, 2, 3),
        pages_per_submission=3,
    )

    assert [page_range.physical_pages for page_range in plan.ranges] == [
        (1, 2),
        (3, 4, 5),
    ]
    assert plan.ranges[0].missing_pages == (3,)
    assert plan.ranges[1].missing_pages == ()


def test_missing_first_page_creates_review_bucket() -> None:
    plan = plan_submission_distribution(
        page_evidence=_evidence(2, 3, 1, 2, 3),
        pages_per_submission=3,
    )

    assert [page_range.name for page_range in plan.ranges] == [
        "needs-review-0001",
        "submission-0001",
    ]
    assert plan.ranges[0].missing_pages == (1,)
    assert "missing_first_page_unknown_owner" in plan.ranges[0].review_reasons
    assert plan.ranges[0].needs_review


def test_unknown_page_between_known_neighbors_is_inferred() -> None:
    plan = plan_submission_distribution(
        page_evidence=_evidence(1, None, 3),
        pages_per_submission=3,
    )

    assert len(plan.ranges) == 1
    assert plan.ranges[0].logical_pages == (1, 2, 3)
    assert plan.ranges[0].missing_pages == ()
    assert not plan.ranges[0].needs_review
    assert plan.ranges[0].confidence < 100.0


def test_duplicate_logical_page_marks_range_for_review() -> None:
    plan = plan_submission_distribution(
        page_evidence=_evidence(1, 2, 2, 3),
        pages_per_submission=3,
    )

    assert len(plan.ranges) == 1
    assert plan.ranges[0].needs_review
    assert "duplicate_logical_page_2" in plan.ranges[0].review_reasons


def test_non_monotonic_non_reset_marks_range_for_review() -> None:
    plan = plan_submission_distribution(
        page_evidence=_evidence(1, 3, 2),
        pages_per_submission=3,
    )

    assert len(plan.ranges) == 1
    assert plan.ranges[0].needs_review
    assert "non_monotonic_logical_pages" in plan.ranges[0].review_reasons


def test_distribution_plan_json_contains_evidence_and_review_reasons() -> None:
    plan = plan_submission_distribution(
        page_evidence=_evidence(2, 3),
        pages_per_submission=3,
    )

    payload = json.loads(distribution_plan_to_json(plan))

    assert payload["needs_review"] is True
    assert payload["page_evidence"][0]["physical_page"] == 1
    assert payload["ranges"][0]["name"] == "needs-review-0001"
    assert payload["ranges"][0]["missing_pages"] == [1]
    assert "needs-review-0001: missing_first_page_unknown_owner" in payload[
        "review_reasons"
    ]
