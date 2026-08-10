"""Bubble-sheet geometry and mark decisions — pure, no PDF and no scanner."""

import pytest

from opengrader.core.bubble_sheet import (
    form_key_for,
    shuffle_questions,
    BubbleQuestion,
    BubbleSheetConfig,
    CompanionQuestion,
    MarkConfig,
    build_bubble_sheet_layout,
    decide_question_mark,
    rows_per_column,
)


def _questions(count: int) -> list[BubbleQuestion]:
    return [BubbleQuestion(question_id=f"q{i}") for i in range(1, count + 1)]


# ── layout ────────────────────────────────────────────────────────────────


def test_every_question_gets_a_row_with_one_bubble_per_choice() -> None:
    layout = build_bubble_sheet_layout(_questions(3))
    assert layout.question_ids == ("q1", "q2", "q3")
    assert all(len(row.bubbles) == 5 for row in layout.rows)
    assert layout.rows[0].choice_labels() == ("A", "B", "C", "D", "E")


def test_rows_fill_a_column_before_starting_the_next() -> None:
    config = BubbleSheetConfig()
    per_column = rows_per_column(config, first_page=True)
    layout = build_bubble_sheet_layout(_questions(per_column + 1), config=config)
    first, last = layout.rows[0], layout.rows[-1]
    assert last.page == 1
    # The overflow row starts a new column: further right, back at the top.
    assert last.bubbles[0].cx > first.bubbles[0].cx
    assert last.bubbles[0].cy == pytest.approx(first.bubbles[0].cy)


def test_overflow_past_the_last_column_starts_a_new_page() -> None:
    config = BubbleSheetConfig()
    capacity = rows_per_column(config, first_page=True) * config.columns
    layout = build_bubble_sheet_layout(_questions(capacity + 1), config=config)
    assert layout.page_count == 2
    assert layout.rows[-1].page == 2
    assert len(layout.fiducials) == 8  # four per page


def test_a_continuation_page_holds_more_rows_than_the_first() -> None:
    # Page 1 gives up space to the title and identity block.
    config = BubbleSheetConfig()
    assert rows_per_column(config, first_page=False) > rows_per_column(
        config, first_page=True
    )


def test_fifty_questions_still_fit_on_one_page() -> None:
    # The default config's stated purpose; a regression here silently doubles
    # every instructor's paper and scanning cost.
    assert build_bubble_sheet_layout(_questions(50)).page_count == 1


def test_every_bubble_and_fiducial_sits_inside_the_page() -> None:
    layout = build_bubble_sheet_layout(_questions(50))
    for row in layout.rows:
        for bubble in row.bubbles:
            assert 0.0 < bubble.cx - bubble.radius
            assert bubble.cx + bubble.radius < 1.0
            assert 0.0 < bubble.cy < 1.0
    for mark in layout.fiducials:
        assert 0.0 < mark.cx < 1.0 and 0.0 < mark.cy < 1.0


def test_fiducials_are_the_outermost_marks_on_the_page() -> None:
    # The reader assigns corners by taking the extreme candidates, so no
    # bubble may ever sit outside a registration square.
    layout = build_bubble_sheet_layout(_questions(50))
    marks = layout.fiducials_on_page(1)
    left = min(m.cx for m in marks)
    right = max(m.cx for m in marks)
    top = min(m.cy for m in marks)
    bottom = max(m.cy for m in marks)
    for row in layout.rows:
        for bubble in row.bubbles:
            assert left < bubble.cx < right
            assert top < bubble.cy < bottom


def test_rows_do_not_overlap_vertically() -> None:
    layout = build_bubble_sheet_layout(_questions(50))
    column = [row for row in layout.rows if row.bubbles[0].cx == layout.rows[0].bubbles[0].cx]
    for earlier, later in zip(column, column[1:]):
        assert earlier.bbox[1] + earlier.bbox[3] <= later.bbox[1] + 1e-9


def test_a_rows_bbox_covers_its_label_and_every_bubble() -> None:
    row = build_bubble_sheet_layout(_questions(1)).rows[0]
    x, y, width, height = row.bbox
    assert x < row.bubbles[0].cx - row.bubbles[0].radius
    assert x + width > row.bubbles[-1].cx + row.bubbles[-1].radius
    assert y < row.bubbles[0].cy < y + height


def test_a_question_may_carry_its_own_choice_labels() -> None:
    layout = build_bubble_sheet_layout(
        [BubbleQuestion(question_id="q1", choice_labels=("True", "False"))]
    )
    assert layout.rows[0].choice_labels() == ("True", "False")


def test_row_labels_strip_the_leading_q() -> None:
    layout = build_bubble_sheet_layout(
        [BubbleQuestion(question_id="q5a"), BubbleQuestion(question_id="q7", display_label="VII")]
    )
    assert [row.label for row in layout.rows] == ["5a", "VII"]


def test_no_questions_still_produces_a_registered_page() -> None:
    layout = build_bubble_sheet_layout([])
    assert layout.rows == () and layout.page_count == 1
    assert len(layout.fiducials) == 4


def test_a_config_whose_rows_cannot_fit_is_rejected_up_front() -> None:
    # Better a clear error at generation than a sheet with bubbles off the edge.
    with pytest.raises(ValueError, match="do not fit"):
        build_bubble_sheet_layout(_questions(1), config=BubbleSheetConfig(columns=4))


def test_invalid_configs_are_rejected() -> None:
    with pytest.raises(ValueError):
        BubbleSheetConfig(columns=0)
    with pytest.raises(ValueError):
        BubbleSheetConfig(choice_labels=())
    with pytest.raises(ValueError):
        BubbleSheetConfig(row_pitch=0.0)


def test_points_title_companions_and_order_ride_along_with_the_layout() -> None:
    layout = build_bubble_sheet_layout(
        [BubbleQuestion(question_id="q1", points=10.0)],
        assignment_title="Quiz 1",
        companions=(CompanionQuestion(question_id="q2", points=5.0),),
        question_order=("q1", "q2"),
    )
    assert layout.rows[0].points == 10.0
    assert layout.assignment_title == "Quiz 1"
    assert layout.companions[0].question_id == "q2"
    assert layout.question_order == ("q1", "q2")


def test_layout_is_deterministic() -> None:
    # The generator and the reader build it separately; if it were not
    # reproducible they would disagree about where the bubbles are.
    first = build_bubble_sheet_layout(_questions(30))
    second = build_bubble_sheet_layout(_questions(30))
    assert first == second


# ── mark decisions ────────────────────────────────────────────────────────


def test_the_darkest_bubble_wins_when_it_is_clear_of_the_rest() -> None:
    reading = decide_question_mark("q1", [("A", 0.02), ("B", 0.95), ("C", 0.03)])
    assert reading.status == "marked"
    assert reading.choice_label == "B"
    assert reading.response_text == "B"
    assert reading.confidence > 90


def test_nothing_filled_is_blank_not_a_guess() -> None:
    reading = decide_question_mark("q1", [("A", 0.02), ("B", 0.05)])
    assert reading.status == "blank"
    assert reading.choice_label == "" and reading.response_text == ""


def test_a_confident_blank_scores_higher_than_a_marginal_one() -> None:
    clean = decide_question_mark("q1", [("A", 0.0)])
    marginal = decide_question_mark("q1", [("A", 0.34)])
    assert clean.confidence > marginal.confidence


def test_two_filled_bubbles_are_ambiguous_rather_than_the_darker_one() -> None:
    reading = decide_question_mark("q1", [("A", 0.98), ("B", 0.96), ("C", 0.02)])
    assert reading.status == "ambiguous"
    assert reading.choice_label == ""
    assert reading.confidence == 0.0
    assert "multiple marks" in reading.detail


def test_an_erasure_ghost_does_not_make_a_clear_mark_ambiguous() -> None:
    # A rubbed-out answer leaves a light smudge; the real mark must still win.
    reading = decide_question_mark("q1", [("A", 0.30), ("B", 0.92)])
    assert reading.status == "marked" and reading.choice_label == "B"


def test_confidence_falls_as_the_runner_up_closes_in() -> None:
    clear = decide_question_mark("q1", [("A", 0.95), ("B", 0.02)])
    contested = decide_question_mark("q1", [("A", 0.95), ("B", 0.60)])
    assert clear.confidence > contested.confidence


def test_confidence_stays_within_zero_and_one_hundred() -> None:
    for fills in ([("A", 1.0), ("B", 0.0)], [("A", 0.36), ("B", 0.0)], [("A", 0.5)]):
        assert 0.0 <= decide_question_mark("q1", fills).confidence <= 100.0


def test_thresholds_are_configurable() -> None:
    faint = [("A", 0.25), ("B", 0.02)]
    assert decide_question_mark("q1", faint).status == "blank"
    lenient = MarkConfig(min_fill=0.20)
    assert decide_question_mark("q1", faint, config=lenient).status == "marked"


def test_no_bubbles_at_all_is_blank_not_a_crash() -> None:
    assert decide_question_mark("q1", []).status == "blank"


def test_a_single_choice_question_can_still_be_marked() -> None:
    reading = decide_question_mark("q1", [("A", 0.9)])
    assert reading.status == "marked" and reading.choice_label == "A"


# ── scrambled forms ───────────────────────────────────────────────────────


def test_a_form_key_is_stable_for_the_same_seed() -> None:
    # Stability is the whole basis for reading a scrambled sheet back: a key
    # that changed between runs would make an already-printed sheet unreadable.
    assert form_key_for("quiz|100001") == form_key_for("quiz|100001")
    assert form_key_for("quiz|100001") != form_key_for("quiz|100002")


def test_a_form_key_avoids_characters_a_human_miskeys() -> None:
    # It is printed on the sheet so a person can type it back when a QR will
    # not scan; O/0 and I/1 would make that unreliable.
    key = form_key_for("anything")
    assert len(key) == 6
    assert not (set(key) & set("O0I1"))


def test_shuffling_is_deterministic_in_the_form_key() -> None:
    questions = _questions(10)
    first = shuffle_questions(questions, "ABC123")
    second = shuffle_questions(questions, "ABC123")
    assert [q.question_id for q in first] == [q.question_id for q in second]


def test_different_form_keys_give_different_orders() -> None:
    questions = _questions(12)
    orders = {
        tuple(q.question_id for q in shuffle_questions(questions, form_key_for(f"s{i}")))
        for i in range(12)
    }
    # Not a strict guarantee for tiny question sets, but with 12 questions
    # (12! orders) a collision among 12 draws would signal a broken seed.
    assert len(orders) == 12


def test_shuffling_keeps_every_question_exactly_once() -> None:
    questions = _questions(20)
    shuffled = shuffle_questions(questions, "FORMXY")
    assert sorted(q.question_id for q in shuffled) == sorted(
        q.question_id for q in questions
    )


def test_a_shuffled_question_keeps_its_real_id_and_gains_a_printed_number() -> None:
    # This split is what makes scrambling invisible downstream: the row still
    # knows it is q7 while the student sees it as number 3.
    questions = _questions(6)
    shuffled = shuffle_questions(questions, "FORMXY")
    assert [q.display_label for q in shuffled] == ["1", "2", "3", "4", "5", "6"]
    assert {q.question_id for q in shuffled} == {q.question_id for q in questions}


def test_an_empty_form_key_means_the_unscrambled_order() -> None:
    questions = _questions(8)
    assert shuffle_questions(questions, "") == questions


def test_changing_the_question_set_reshuffles_rather_than_shifting() -> None:
    # Seeding on the ids too, so adding a question does not leave most of the
    # old order in place (which would leak the original ordering).
    five = shuffle_questions(_questions(5), "FORMXY")
    six = shuffle_questions(_questions(6), "FORMXY")
    assert [q.question_id for q in five] != [q.question_id for q in six[:5]]


def test_a_shuffled_layout_prints_sequential_row_labels() -> None:
    layout = build_bubble_sheet_layout(shuffle_questions(_questions(6), "FORMXY"))
    assert [row.label for row in layout.rows] == ["1", "2", "3", "4", "5", "6"]
    # ...while still carrying the real ids, in the printed order.
    assert sorted(layout.question_ids) == sorted(f"q{i}" for i in range(1, 7))
