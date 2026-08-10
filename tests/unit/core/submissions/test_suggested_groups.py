from opengrader.core.question_types import (
    FREE_RESPONSE,
    MULTIPLE_CHOICE,
    NUMERIC,
)
from opengrader.core.submissions import (
    GroupableResponse,
    group_responses,
    diff_answer_spans,
    similarity_ratio,
    suggest_group_merges,
    summarize_grouping_effort,
)
from opengrader.core.submissions.suggested_groups import (
    DEFAULT_SUGGESTION_MIN_LENGTH,
)

# Three renderings of one answer. The first two differ only in wording a grader
# would not distinguish; the third is a genuinely different answer, so the
# fixtures cover both what must merge and what must not.
_MERGE_SORT = (
    "Merge sort splits the list in half, sorts each half recursively, and then "
    "merges the two sorted halves back together in linear time."
)
_MERGE_SORT_REWORDED = (
    "Merge sort splits the list in half, sorts each half recursively, and then "
    "merges the two sorted halves back together in linear time flat."
)
_QUICK_SORT = (
    "Quicksort picks a pivot, partitions the remaining elements around it, and "
    "recursively sorts the two partitions on either side of the pivot."
)


def _groups(*responses: tuple[str, str], question_type: str = FREE_RESPONSE):
    """Build exact answer groups from (member_id, response_text) pairs."""
    return group_responses(
        GroupableResponse(
            member_id=member_id,
            response_text=text,
            question_type=question_type,
        )
        for member_id, text in responses
    )


def test_near_identical_groups_are_proposed_for_merging():
    groups = _groups(
        ("s1", _MERGE_SORT),
        ("s2", _MERGE_SORT),
        ("s3", _MERGE_SORT_REWORDED),
    )
    # The exact tier keeps the reworded answer apart — that is the gap this
    # module exists to close.
    assert len(groups) == 2

    suggestions = suggest_group_merges(groups)

    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.group_count == 2
    # The larger group leads, so the grader reads the answer most students gave.
    assert suggestion.representative.member_ids == ("s1", "s2")
    assert suggestion.member_ids == ("s1", "s2", "s3")
    assert suggestion.size == 3
    assert suggestion.added_size == 1
    assert suggestion.min_score >= 0.85


def test_genuinely_different_answers_are_never_proposed():
    groups = _groups(("s1", _MERGE_SORT), ("s2", _QUICK_SORT))

    assert suggest_group_merges(groups) == []


def test_objective_question_types_are_left_to_the_exact_tier():
    """Fuzzy merging must never reach multiple choice or numeric answers.

    "A" against "B", or "42" against "4.2", is a character away from a false
    merge that would silently mis-score every student in the group — and the
    exact tier already groups these correctly and completely.
    """
    choices = _groups(
        ("s1", "A"),
        ("s2", "B"),
        question_type=MULTIPLE_CHOICE,
    )
    numbers = _groups(
        ("s1", "1234567890123"),
        ("s2", "1234567890124"),
        question_type=NUMERIC,
    )

    assert suggest_group_merges(choices) == []
    assert suggest_group_merges(numbers) == []


def test_blank_bucket_never_merges():
    """A blank answer is an absence, not a short answer that resembles others."""
    groups = _groups(("s1", ""), ("s2", "   "), ("s3", _MERGE_SORT))

    assert suggest_group_merges(groups) == []


def test_short_answers_are_below_the_length_guard():
    """Below the guard, a ratio says more about length than about meaning."""
    short_a = "42 mL of it"
    short_b = "12 mL of it"
    assert len(short_a) < DEFAULT_SUGGESTION_MIN_LENGTH + 2

    assert suggest_group_merges(_groups(("s1", short_a), ("s2", short_b))) == []


def test_complete_linkage_refuses_to_chain_through_a_near_miss():
    """A must not join C's cluster just because both are close to B.

    The three answers below form a chain: a-b and b-c clear the threshold but
    a-c does not (0.91 / 0.93 / 0.84 measured). Connected components — what the
    similarity audit deliberately uses — would union all three and hand two
    visibly different answers one grade. Complete linkage requires *every* pair
    to clear the floor, so the chain is cut and only the true pair is proposed.
    """
    head = (
        "Merge sort divides the input list into two halves, sorts each half "
        "recursively, and then merges the two sorted halves back together. "
    )
    a = head + "Every single level does linear work."
    b = head + "Each separate tier does linear work."
    c = head + "Each separate tier needs a full pass."

    # The chain really is a chain: the ends are below the threshold, the links
    # are above it. (Guards the fixture, so a metric change fails loudly here
    # rather than silently making the test vacuous.)
    assert similarity_ratio(a, c) < 0.85 <= similarity_ratio(a, b)
    assert similarity_ratio(b, c) >= 0.85

    suggestions = suggest_group_merges(_groups(("s1", a), ("s2", b), ("s3", c)))

    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert suggestion.group_count == 2
    # b and c are the pair that clears every internal comparison; a is left out.
    assert set(suggestion.member_ids) == {"s2", "s3"}
    assert suggestion.min_score >= 0.85


def test_min_score_reports_the_weakest_link_in_the_cluster():
    close = _MERGE_SORT.replace("linear time.", "linear time!")
    looser = _MERGE_SORT_REWORDED

    suggestions = suggest_group_merges(
        _groups(("s1", _MERGE_SORT), ("s2", close), ("s3", looser))
    )

    assert len(suggestions) == 1
    suggestion = suggestions[0]
    scores = [pair.score for pair in suggestion.pairs]
    assert suggestion.min_score == min(scores)
    assert suggestion.max_score == max(scores)
    # Weakest pair first: it is the one that decides whether to accept.
    assert scores == sorted(scores)


def test_threshold_controls_how_eagerly_groups_merge():
    groups = _groups(("s1", _MERGE_SORT), ("s2", _QUICK_SORT))

    assert suggest_group_merges(groups, threshold=0.99) == []
    # Both answers are about sorting and share vocabulary, so a permissive
    # threshold does reach them — the knob works in both directions.
    assert suggest_group_merges(groups, threshold=0.30)


def test_result_is_deterministic_regardless_of_input_order():
    pairs = [
        ("s1", _MERGE_SORT),
        ("s2", _MERGE_SORT_REWORDED),
        ("s3", _MERGE_SORT),
        ("s4", _QUICK_SORT),
    ]
    forward = suggest_group_merges(_groups(*pairs))
    backward = suggest_group_merges(_groups(*reversed(pairs)))

    # Compare the clusters themselves, not the member order *within* an exact
    # group: that order is the exact tier's own first-seen ordering, so feeding
    # the students in backwards legitimately lists them backwards.
    def shape(suggestions):
        return [
            ([group.key for group in s.groups], set(s.member_ids), s.min_score)
            for s in suggestions
        ]

    assert shape(forward) == shape(backward)


def test_nothing_is_merged_when_there_is_only_one_group():
    assert suggest_group_merges(_groups(("s1", _MERGE_SORT), ("s2", _MERGE_SORT))) == []
    assert suggest_group_merges([]) == []


def test_grouping_effort_counts_distinct_answers_to_grade():
    groups = _groups(
        ("s1", _MERGE_SORT),
        ("s2", _MERGE_SORT),
        ("s3", _MERGE_SORT_REWORDED),
        ("s4", _QUICK_SORT),
        ("s5", ""),
    )
    suggestions = suggest_group_merges(groups)

    effort = summarize_grouping_effort("q1", groups, suggestions)

    assert effort.question_id == "q1"
    assert effort.responses == 5
    assert effort.blank_responses == 1
    # merge-sort, reworded merge-sort, quicksort, blank
    assert effort.exact_groups == 4
    assert effort.suggested_merges == 1
    assert effort.groups_after_merges == 3
    assert effort.saved_by_suggestions == 1
    assert effort.reduction_percent == (1 - 3 / 5) * 100


def test_grouping_effort_is_safe_on_an_empty_question():
    effort = summarize_grouping_effort("q1", [], [])

    assert effort.responses == 0
    assert effort.groups_after_merges == 0
    assert effort.reduction_percent is None


# --------------------------------------------------------------------------- #
# Showing *what* differs — the safeguard the similarity score cannot provide.
# --------------------------------------------------------------------------- #


def test_a_reversal_is_marked_even_though_it_scores_as_near_identical():
    """The case no threshold can catch: one inserted word flips the meaning."""
    reference = "This algorithm is stable and sorts in place."
    other = "This algorithm is not stable and sorts in place."
    # The metric genuinely cannot separate this from a real duplicate...
    assert similarity_ratio(reference, other) > 0.9

    spans = diff_answer_spans(reference, other)

    # ...so the difference is reported word by word instead.
    changed = [span.text for span in spans if span.changed]
    assert changed == ["not"]


def test_a_substitution_shows_both_sides():
    spans = diff_answer_spans("a stack is newest first", "a queue is oldest first")

    assert [(span.kind, span.text) for span in spans] == [
        ("same", "a"),
        ("removed", "stack"),
        ("added", "queue"),
        ("same", "is"),
        ("removed", "newest"),
        ("added", "oldest"),
        ("same", "first"),
    ]


def test_an_omission_is_shown_as_what_is_missing():
    """What an answer leaves out is as much a difference as what it adds."""
    spans = diff_answer_spans("sorts in place and is stable", "sorts in place")

    assert [(span.kind, span.text) for span in spans] == [
        ("same", "sorts in place"),
        ("removed", "and is stable"),
    ]


def test_cosmetic_differences_are_not_marked_as_changes():
    """Highlighting a trailing comma trains the reader to ignore highlights."""
    spans = diff_answer_spans(
        "It halves the interval, each step.", "it halves the interval each step"
    )

    assert all(not span.changed for span in spans)
    # The display text is the original, not the comparison form.
    assert "".join(span.text for span in spans) == "it halves the interval each step"


def test_identical_answers_have_no_marked_spans():
    spans = diff_answer_spans(_MERGE_SORT, _MERGE_SORT)

    assert [span.kind for span in spans] == ["same"]


def test_diffing_is_safe_on_empty_text():
    assert diff_answer_spans("", "") == ()
    assert [span.kind for span in diff_answer_spans("", "new words")] == ["added"]
    assert [span.kind for span in diff_answer_spans("old words", "")] == ["removed"]
