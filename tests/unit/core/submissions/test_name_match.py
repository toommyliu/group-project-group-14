import pytest

from opengrader.core.submissions.name_match import name_similarity, name_tokens


def test_tokens_undo_case_accents_and_ocr_digits() -> None:
    assert name_tokens("T0mmy L1u") == ("tommy", "llu")
    assert name_tokens("José O'Brien-Smith") == ("jose", "obrien", "smith")
    assert name_tokens("") == ()


@pytest.mark.parametrize(
    "read",
    ["Liu, Tommy", "Liu Tommy", "TOMMY LIU", "tommy  liu"],
)
def test_order_case_and_punctuation_do_not_matter(read: str) -> None:
    assert name_similarity(read, "Tommy Liu").score == pytest.approx(1.0)


def test_ocr_digit_swaps_score_as_a_whole_name() -> None:
    assert name_similarity("T0mmy L1u", "Tommy Liu").score >= 0.85


@pytest.mark.parametrize(
    "read, roster",
    [
        ("Tommy", "Tommy Liu"),  # first name only
        ("Liu", "Tommy Liu"),  # last name only
        ("T. Liu", "Tommy Liu"),  # initial + last name
        ("Liu T", "Tommy Liu"),
        ("Andy Cortes", "Andrew Cortes"),  # nickname over the formal name
    ],
)
def test_part_of_a_name_is_partial(read: str, roster: str) -> None:
    found = name_similarity(read, roster)
    assert found.partial
    assert found.score < 0.85


@pytest.mark.parametrize(
    "read, roster",
    [
        ("T", "Tommy Liu"),  # an initial alone is not a name
        ("Amy Smith", "Tommy Liu"),
        ("Tommy Smith", "Andrew Liu"),  # last name of one, initial of none
        ("", "Tommy Liu"),
    ],
)
def test_unrelated_names_are_not_partial(read: str, roster: str) -> None:
    assert not name_similarity(read, roster).partial
