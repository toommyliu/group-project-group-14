from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE, NUMERIC
from opengrader.core.submissions.answer_groups import (
    GroupableResponse,
    group_responses,
    normalize_answer_key,
)


def test_numeric_normalization_groups_equal_values() -> None:
    assert (
        normalize_answer_key("42", NUMERIC)
        == normalize_answer_key("42.0", NUMERIC)
        == normalize_answer_key("42 mL", NUMERIC)
        == "42"
    )
    assert normalize_answer_key("43", NUMERIC) == "43"
    assert normalize_answer_key("n/a", NUMERIC) == ""


def _fr(member_id: str, text: str) -> GroupableResponse:
    return GroupableResponse(member_id, text, FREE_RESPONSE)


def _mc(member_id: str, text: str) -> GroupableResponse:
    return GroupableResponse(member_id, text, MULTIPLE_CHOICE)


def test_free_response_normalization_collapses_cosmetic_differences() -> None:
    assert normalize_answer_key("  42 ", FREE_RESPONSE) == "42"
    assert normalize_answer_key("42.", FREE_RESPONSE) == "42"
    assert normalize_answer_key("Hello   World", FREE_RESPONSE) == "hello world"
    assert normalize_answer_key("HELLO WORLD", FREE_RESPONSE) == "hello world"


def test_free_response_preserves_inner_punctuation() -> None:
    assert normalize_answer_key("f(x)=x^2", FREE_RESPONSE) != normalize_answer_key(
        "f(x)=x", FREE_RESPONSE
    )


def test_blank_free_response_normalizes_to_empty_key() -> None:
    assert normalize_answer_key("", FREE_RESPONSE) == ""
    assert normalize_answer_key("   \n\t", FREE_RESPONSE) == ""
    assert normalize_answer_key("...", FREE_RESPONSE) == ""


def test_multiple_choice_normalization_ignores_choice_punctuation() -> None:
    assert normalize_answer_key("B)", MULTIPLE_CHOICE) == "B"
    assert normalize_answer_key("(b)", MULTIPLE_CHOICE) == "B"
    assert normalize_answer_key("B.", MULTIPLE_CHOICE) == "B"


def test_multiple_choice_multiselect_is_order_independent() -> None:
    assert normalize_answer_key("A, C", MULTIPLE_CHOICE) == normalize_answer_key(
        "C, A", MULTIPLE_CHOICE
    )


def test_group_responses_clusters_matching_answers() -> None:
    groups = group_responses(
        [
            _fr("s1", "42"),
            _fr("s2", "42 "),
            _fr("s3", "seven"),
        ]
    )
    assert [(g.key, g.member_ids) for g in groups] == [
        ("42", ("s1", "s2")),
        ("seven", ("s3",)),
    ]


def test_group_responses_orders_largest_first_then_blank_last() -> None:
    groups = group_responses(
        [
            _fr("s1", ""),
            _fr("s2", "a"),
            _fr("s3", "a"),
            _fr("s4", "b"),
        ]
    )
    assert [g.key for g in groups] == ["a", "b", ""]
    assert groups[-1].is_blank
    assert groups[-1].member_ids == ("s1",)


def test_group_responses_preserves_member_first_seen_order() -> None:
    groups = group_responses(
        [
            _fr("s3", "x"),
            _fr("s1", "x"),
            _fr("s2", "x"),
        ]
    )
    assert groups[0].member_ids == ("s3", "s1", "s2")


def test_group_responses_multiple_choice() -> None:
    groups = group_responses(
        [
            _mc("s1", "B)"),
            _mc("s2", "(b)"),
            _mc("s3", "C"),
        ]
    )
    assert [(g.key, g.size) for g in groups] == [("B", 2), ("C", 1)]


def test_group_sample_response_text_prefers_nonblank() -> None:
    groups = group_responses(
        [
            _fr("s1", "  "),
            _fr("s2", "  "),
        ]
    )
    assert len(groups) == 1
    assert groups[0].is_blank
    assert groups[0].sample_response_text == ""
