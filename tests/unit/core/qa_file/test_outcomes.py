"""Tests for the optional ``**Outcome**`` question tag.

The bullet follows the ``**Comment**`` precedent exactly, so the invariants are
the same ones that matter there: both authoring forms parse, the tag round-trips,
and a QA file that doesn't use it is completely unaffected.
"""

from __future__ import annotations

from opengrader.core.qa_file import (
    outcomes_by_question,
    parse_qa_markdown,
    possible_points_by_question,
    render_qa_markdown,
)

HEADER = "---\ntitle: Quiz\n---\n\n"


def _doc(body: str):  # noqa: ANN202
    return parse_qa_markdown(HEADER + body)


# ── authoring forms ──────────────────────────────────────────────────────────


def test_the_inline_form_tags_a_question() -> None:
    doc = _doc(
        "- (4 points) Explain.\n"
        "  - **Correct**\n    - x\n"
        "  - **Outcome** CLO3\n"
    )
    assert doc.questions[0].outcomes == ("CLO3",)


def test_a_question_may_carry_several_outcomes() -> None:
    doc = _doc(
        "- (4 points) Explain.\n"
        "  - **Correct**\n    - x\n"
        "  - **Outcome** CLO3\n"
        "  - **Outcome** CLO1\n"
    )
    # Authoring order is preserved — it's the instructor's own ordering.
    assert doc.questions[0].outcomes == ("CLO3", "CLO1")


def test_the_nested_form_parses() -> None:
    doc = _doc(
        "- (4 points) Explain.\n"
        "  - **Correct**\n    - x\n"
        "  - **Outcomes**\n    - CLO1\n    - CLO2\n"
    )
    assert doc.questions[0].outcomes == ("CLO1", "CLO2")


def test_a_comma_separated_list_is_accepted() -> None:
    doc = _doc(
        "- (4 points) Explain.\n"
        "  - **Correct**\n    - x\n"
        "  - **Outcome** CLO1, CLO2 , CLO3\n"
    )
    assert doc.questions[0].outcomes == ("CLO1", "CLO2", "CLO3")


def test_outcome_names_are_kept_verbatim() -> None:
    """They are the instructor's vocabulary — normalizing case or punctuation
    would silently merge two outcomes they meant to keep apart."""
    doc = _doc(
        "- (4 points) Explain.\n"
        "  - **Correct**\n    - x\n"
        "  - **Outcome** Applies Bayes' rule\n"
        "  - **Outcome** ABET-2\n"
    )
    assert doc.questions[0].outcomes == ("Applies Bayes' rule", "ABET-2")


def test_a_duplicate_tag_is_collapsed() -> None:
    """A double-count would inflate the outcome's coverage."""
    doc = _doc(
        "- (4 points) Explain.\n"
        "  - **Correct**\n    - x\n"
        "  - **Outcome** CLO1\n"
        "  - **Outcome** CLO1\n"
    )
    assert doc.questions[0].outcomes == ("CLO1",)


# ── round trip ───────────────────────────────────────────────────────────────


def test_outcomes_survive_a_round_trip() -> None:
    doc = _doc(
        "- (4 points) Explain.\n"
        "  - **Correct**\n    - x\n"
        "  - **Outcome** CLO1\n"
        "  - **Outcome** CLO2\n"
    )
    rendered = render_qa_markdown(doc)
    assert "**Outcome** CLO1" in rendered
    assert parse_qa_markdown(rendered).questions[0].outcomes == ("CLO1", "CLO2")


def test_rendering_is_stable_across_repeated_round_trips() -> None:
    doc = _doc(
        "- (4 points) Explain.\n  - **Correct**\n    - x\n  - **Outcomes** CLO1, CLO2\n"
    )
    once = render_qa_markdown(doc)
    twice = render_qa_markdown(parse_qa_markdown(once))
    assert once == twice


def test_a_file_without_outcomes_emits_no_outcome_line() -> None:
    """The whole point of the optional-bullet pattern: files that don't use it
    are untouched."""
    doc = _doc("- (4 points) Explain.\n  - **Correct**\n    - x\n")
    rendered = render_qa_markdown(doc)
    assert "Outcome" not in rendered
    assert doc.questions[0].outcomes == ()


# ── subquestions and inheritance ─────────────────────────────────────────────


SUBQ = (
    "- (6 points) Multi-part.\n"
    "  - **Outcome** CLO2\n"
    "  - (3 points) A.\n    - **Correct**\n      - a\n"
    "  - (3 points) B.\n    - **Correct**\n      - b\n    - **Outcome** CLO4\n"
)


def test_a_subquestion_carries_its_own_outcome() -> None:
    doc = _doc(SUBQ)
    assert doc.questions[0].subquestions[1].outcomes == ("CLO4",)


def test_a_subquestion_inherits_the_parent_tag() -> None:
    """Tagging a multi-part question once is the natural thing to write, and the
    parent isn't graded — the parts are — so the tag has to reach them."""
    doc = _doc(SUBQ)
    assert doc.questions[0].outcomes_for("q1a") == ("CLO2",)


def test_a_subquestion_tag_overrides_the_parent() -> None:
    doc = _doc(SUBQ)
    assert doc.questions[0].outcomes_for("q1b") == ("CLO4",)


def test_graded_outcomes_unions_the_parts() -> None:
    doc = _doc(SUBQ)
    assert doc.questions[0].graded_outcomes == ("CLO2", "CLO4")


def test_subquestion_outcomes_round_trip() -> None:
    doc = _doc(SUBQ)
    reparsed = parse_qa_markdown(render_qa_markdown(doc))
    assert reparsed.questions[0].outcomes == ("CLO2",)
    assert reparsed.questions[0].subquestions[1].outcomes == ("CLO4",)


# ── the bridge to the analytics ──────────────────────────────────────────────


def test_outcomes_by_question_maps_graded_ids_only() -> None:
    """A parent with parts contributes q1a/q1b, never q1 — those are the ids the
    submission files carry and the ids that have points."""
    doc = _doc(SUBQ + "- (4 points) Plain.\n  - **Correct**\n    - z\n  - **Outcome** CLO1\n")
    assert outcomes_by_question(doc) == {
        "q1a": ("CLO2",),
        "q1b": ("CLO4",),
        "q2": ("CLO1",),
    }


def test_untagged_questions_are_absent_from_the_map() -> None:
    doc = _doc("- (4 points) Plain.\n  - **Correct**\n    - z\n")
    assert outcomes_by_question(doc) == {}


def test_possible_points_by_question_covers_parts_and_wholes() -> None:
    doc = _doc(SUBQ + "- (4 points) Plain.\n  - **Correct**\n    - z\n")
    assert possible_points_by_question(doc) == {"q1a": 3.0, "q1b": 3.0, "q2": 4.0}


def test_versions_are_merged_because_an_outcome_belongs_to_the_material() -> None:
    doc = parse_qa_markdown(
        HEADER
        + "## Version A\n\n- (4 points) A-side.\n  - **Correct**\n    - a\n"
        "  - **Outcome** CLO1\n\n"
        "## Version B\n\n- (4 points) B-side.\n  - **Correct**\n    - b\n"
        "  - **Outcome** CLO2\n"
    )
    # Same id in both versions; the tags union rather than one winning.
    assert set(outcomes_by_question(doc)["q1"]) == {"CLO1", "CLO2"}
