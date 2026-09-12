from opengrader.tui.state.grading_session import (
    QuestionGrade,
    RubricOption,
    StudentGradingRun,
)


def _option(
    label: str,
    points_delta: float,
    selection_mode: str = "exclusive",
) -> RubricOption:
    kind = (
        "correct"
        if label == "Correct"
        else "incorrect"
        if label == "Incorrect"
        else "partial"
    )
    return RubricOption(label, points_delta, kind, selection_mode)


def _run(options: list[RubricOption], *, points: float = 10) -> StudentGradingRun:
    return StudentGradingRun(
        student_id=1,
        student_name="Student",
        assignment_name="Quiz",
        questions=[
            QuestionGrade(
                question_id="q1",
                image_path="",
                rubric_options=options,
                points=points,
            )
        ],
        max_points=points,
    )


def test_exclusive_selection_clears_checklist_selections() -> None:
    run = _run(
        [
            _option("Correct", 0),
            _option("Partially Correct - A", -2, "checklist"),
            _option("Partially Correct - B", -8, "checklist"),
            _option("Incorrect", -10),
        ]
    )

    run.apply_option(1)
    run.apply_option(2)
    run.apply_option(0)

    assert run.current_question is not None
    assert run.current_question.selected_option_indices == {0}


def test_checklist_selection_clears_exclusive_selection_and_composes() -> None:
    run = _run(
        [
            _option("Correct", 0),
            _option("Partially Correct - A", -2, "checklist"),
            _option("Partially Correct - B", -8, "checklist"),
            _option("Incorrect", -10),
        ]
    )

    run.apply_option(0)
    run.apply_option(1)
    run.apply_option(2)

    assert run.current_question is not None
    assert run.current_question.selected_option_indices == {1, 2}
    assert run.current_question.status == "graded"


def test_toggling_final_checklist_item_clears_grade() -> None:
    run = _run(
        [
            _option("Correct", 0),
            _option("Partially Correct - A", -2, "checklist"),
            _option("Incorrect", -10),
        ]
    )

    run.apply_option(1)
    run.apply_option(1)

    assert run.current_question is not None
    assert run.current_question.selected_option_indices == set()
    assert run.current_question.status == "ungraded"


def test_award_mode_computes_from_zero() -> None:
    run = _run(
        [
            _option("Correct", 10),
            _option("Partially Correct - A", 4, "checklist"),
            _option("Partially Correct - B", 6, "checklist"),
            _option("Incorrect", 0),
        ]
    )

    run.apply_option(1)

    assert run.current_question is not None
    assert run.current_question.score_adjustment == -6


def test_deduction_mode_computes_from_max_points() -> None:
    run = _run(
        [
            _option("Correct", 0),
            _option("Partially Correct - A", -2, "checklist"),
            _option("Partially Correct - B", -8, "checklist"),
            _option("Incorrect", -10),
        ]
    )

    run.apply_option(1)

    assert run.current_question is not None
    assert run.current_question.score_adjustment == -2


def _bonus(label: str, points_delta: float) -> RubricOption:
    return RubricOption(label, points_delta, "bonus", "checklist")


def _standard_with_bonus() -> list[RubricOption]:
    return [
        _option("Correct", 0),
        _option("Partially Correct - A", -2, "checklist"),
        _option("Partially Correct - B", -8, "checklist"),
        _option("Incorrect", -10),
        _bonus("Bonus - Elegant solution", 2),
    ]


def test_bonus_combines_with_exclusive_selection() -> None:
    run = _run(_standard_with_bonus())

    run.apply_option(0)
    run.apply_option(4)

    assert run.current_question is not None
    assert run.current_question.selected_option_indices == {0, 4}
    assert run.current_question.score_adjustment == 2


def test_exclusive_selection_keeps_bonus() -> None:
    run = _run(_standard_with_bonus())

    run.apply_option(4)
    run.apply_option(0)
    assert run.current_question is not None
    assert run.current_question.selected_option_indices == {0, 4}

    run.apply_option(3)
    assert run.current_question.selected_option_indices == {3, 4}
    assert run.current_question.score_adjustment == -8


def test_bonus_combines_with_partial_checklist() -> None:
    run = _run(_standard_with_bonus())

    run.apply_option(1)
    run.apply_option(4)

    assert run.current_question is not None
    assert run.current_question.selected_option_indices == {1, 4}
    assert run.current_question.score_adjustment == 0

    run.apply_option(2)
    assert run.current_question.selected_option_indices == {1, 2, 4}


def test_bonus_toggles_off_without_touching_base() -> None:
    run = _run(_standard_with_bonus())

    run.apply_option(0)
    run.apply_option(4)
    run.apply_option(4)

    assert run.current_question is not None
    assert run.current_question.selected_option_indices == {0}
    assert run.current_question.score_adjustment == 0


def test_bonus_only_selection_adds_to_ungraded_base() -> None:
    run = _run(_standard_with_bonus())

    run.apply_option(4)

    assert run.current_question is not None
    assert run.current_question.selected_option_indices == {4}
    assert run.current_question.score_adjustment == 2


def test_bonus_added_after_clamp_in_award_mode() -> None:
    run = _run(
        [
            _option("Correct", 10),
            _option("Incorrect", 0),
            _bonus("Bonus - Extra insight", 3),
        ]
    )

    run.apply_option(0)
    run.apply_option(2)

    assert run.current_question is not None
    assert run.current_question.score_adjustment == 3
