from opengrader.core.analytics import (
    DifficultyBand,
    SeparationBand,
    build_item_analysis,
    difficulty_band,
    separation_band,
)
from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)


def _question(
    question_id: str = "q1",
    *,
    question_type: str = FREE_RESPONSE,
    response_text: str = "an answer",
    points_awarded: float | None = None,
    possible_points: float | None = 10,
) -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=question_id,
        prompt="",
        possible_points=possible_points,
        response_image_path=None,
        response_text=response_text,
        points_awarded=points_awarded,
        question_type=question_type,
    )


def _doc(
    student_id: str,
    questions: list[StudentSubmissionQuestion],
    *,
    assignment_version: str | None = None,
    student_name: str = "",
) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=student_name or f"Student {student_id}",
        student_id=student_id,
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version=assignment_version,
        source_pdf=f"submission-{student_id}.pdf",
        questions=questions,
    )


# --- difficulty ------------------------------------------------------------


def test_difficulty_is_fraction_of_possible_earned():
    docs = [
        _doc("1", [_question(points_awarded=10, possible_points=10)]),
        _doc("2", [_question(points_awarded=0, possible_points=10)]),
        _doc("3", [_question(points_awarded=5, possible_points=10)]),
    ]
    report = build_item_analysis(docs)
    (item,) = report.items
    assert item.difficulty == 0.5
    assert item.difficulty_band is DifficultyBand.MODERATE
    assert item.mean_points == 5.0
    assert item.responses_count == 3


def test_difficulty_band_cutpoints():
    assert difficulty_band(0.95) is DifficultyBand.VERY_EASY
    assert difficulty_band(0.80) is DifficultyBand.EASY
    assert difficulty_band(0.60) is DifficultyBand.MODERATE
    assert difficulty_band(0.40) is DifficultyBand.HARD
    assert difficulty_band(0.10) is DifficultyBand.VERY_HARD
    assert difficulty_band(None) is None


def test_difficulty_none_when_no_point_value():
    docs = [_doc("1", [_question(points_awarded=0, possible_points=0)])]
    (item,) = build_item_analysis(docs).items
    assert item.difficulty is None
    assert item.difficulty_band is None


def test_ungraded_questions_are_ignored():
    docs = [
        _doc("1", [_question(points_awarded=10)]),
        _doc("2", [_question(points_awarded=None)]),  # not yet graded
    ]
    (item,) = build_item_analysis(docs).items
    assert item.responses_count == 1
    assert item.difficulty == 1.0


# --- separation ------------------------------------------------------------


def _two_question_cohort(q1_perfectly_separating: bool) -> list[StudentSubmissionDocument]:
    """10 students; q2 is a spread of totals. q1 either tracks it or is random."""
    docs = []
    for i in range(10):
        strong = i >= 5
        q2_points = 10.0 if strong else 0.0
        if q1_perfectly_separating:
            q1_points = 10.0 if strong else 0.0
        else:
            # Two weak + two strong earn it: exactly balanced against q2, so the
            # item–total correlation is 0 by construction (genuine noise).
            q1_points = 10.0 if i in (0, 1, 5, 6) else 0.0
        docs.append(
            _doc(
                str(i),
                [
                    _question("q1", points_awarded=q1_points),
                    _question("q2", points_awarded=q2_points),
                ],
            )
        )
    return docs


def test_separation_high_when_item_tracks_ability():
    report = build_item_analysis(_two_question_cohort(q1_perfectly_separating=True))
    q1 = next(item for item in report.items if item.question_id == "q1")
    assert q1.separation is not None
    assert q1.separation > 0.9
    assert q1.separation_band is SeparationBand.EXCELLENT


def test_separation_low_when_item_is_noise():
    report = build_item_analysis(_two_question_cohort(q1_perfectly_separating=False))
    q1 = next(item for item in report.items if item.question_id == "q1")
    assert q1.separation is not None
    assert abs(q1.separation) < 0.2
    assert q1.separation_band in (SeparationBand.WEAK, SeparationBand.NEGATIVE)


def test_separation_negative_flags_item_for_attention():
    # q1 is inversely related to overall ability: weak students get it, strong miss.
    docs = []
    for i in range(10):
        strong = i >= 5
        docs.append(
            _doc(
                str(i),
                [
                    _question("q1", points_awarded=0.0 if strong else 10.0),
                    _question("q2", points_awarded=10.0 if strong else 0.0),
                    _question("q3", points_awarded=10.0 if strong else 0.0),
                ],
            )
        )
    report = build_item_analysis(docs)
    q1 = next(item for item in report.items if item.question_id == "q1")
    assert q1.separation is not None and q1.separation < 0
    assert q1.separation_band is SeparationBand.NEGATIVE
    assert q1.needs_attention is True


def test_separation_insufficient_data_below_sample_floor():
    docs = [
        _doc("1", [_question(points_awarded=10)]),
        _doc("2", [_question(points_awarded=0)]),
    ]
    (item,) = build_item_analysis(docs).items
    assert item.separation is None
    assert item.separation_band is SeparationBand.INSUFFICIENT_DATA
    # difficulty still reported for a tiny cohort
    assert item.difficulty == 0.5


def test_separation_band_cutpoints():
    assert separation_band(0.45) is SeparationBand.EXCELLENT
    assert separation_band(0.35) is SeparationBand.GOOD
    assert separation_band(0.25) is SeparationBand.FAIR
    assert separation_band(0.10) is SeparationBand.WEAK
    assert separation_band(-0.05) is SeparationBand.NEGATIVE
    assert separation_band(None) is SeparationBand.INSUFFICIENT_DATA


# --- distractors -----------------------------------------------------------


def test_distractors_only_for_multiple_choice():
    docs = [_doc("1", [_question(question_type=FREE_RESPONSE, points_awarded=10)])]
    (item,) = build_item_analysis(docs).items
    assert item.distractors == ()


def test_distractor_tally_counts_and_key_inference():
    # 6 students: "A" is the key (full credit), "B"/"C" distractors (0 credit).
    picks = [("A", 10), ("A", 10), ("A", 10), ("B", 0), ("B", 0), ("C", 0)]
    docs = [
        _doc(
            str(i),
            [_question(question_type=MULTIPLE_CHOICE, response_text=choice, points_awarded=pts)],
        )
        for i, (choice, pts) in enumerate(picks)
    ]
    (item,) = build_item_analysis(docs).items
    by_choice = {d.choice: d for d in item.distractors}
    assert by_choice["A"].count == 3
    assert by_choice["A"].is_likely_key is True
    assert by_choice["B"].count == 2
    assert by_choice["B"].is_likely_key is False
    # largest share first
    assert item.distractors[0].choice == "A"
    assert abs(item.distractors[0].share - 0.5) < 1e-9


def test_distractor_normalizes_cosmetic_choice_differences():
    docs = [
        _doc("1", [_question(question_type=MULTIPLE_CHOICE, response_text="A)", points_awarded=10)]),
        _doc("2", [_question(question_type=MULTIPLE_CHOICE, response_text="a", points_awarded=10)]),
    ]
    (item,) = build_item_analysis(docs).items
    # "A)" and "a" collapse to one choice via normalize_answer_key
    assert len(item.distractors) == 1
    assert item.distractors[0].count == 2


def test_distractor_chooser_performance_tracks_ability():
    # Strong students pick A (key); weak students pick B (distractor).
    docs = []
    for i in range(8):
        strong = i >= 4
        docs.append(
            _doc(
                str(i),
                [
                    _question(
                        "q1",
                        question_type=MULTIPLE_CHOICE,
                        response_text="A" if strong else "B",
                        points_awarded=10.0 if strong else 0.0,
                    ),
                    _question("q2", points_awarded=10.0 if strong else 0.0),
                ],
            )
        )
    q1 = next(item for item in build_item_analysis(docs).items if item.question_id == "q1")
    by_choice = {d.choice: d for d in q1.distractors}
    assert by_choice["A"].mean_chooser_total_percent is not None
    assert by_choice["B"].mean_chooser_total_percent is not None
    # the distractor's choosers performed worse overall than the key's
    assert by_choice["B"].mean_chooser_total_percent < by_choice["A"].mean_chooser_total_percent


# --- versions & ordering ---------------------------------------------------


def test_versions_are_analyzed_separately():
    docs = [
        _doc("1", [_question("q1", points_awarded=10)], assignment_version="A"),
        _doc("2", [_question("q1", points_awarded=0)], assignment_version="B"),
    ]
    report = build_item_analysis(docs)
    assert len(report.items) == 2
    versions = {item.assignment_version for item in report.items}
    assert versions == {"A", "B"}


def test_items_sorted_natural_question_order():
    docs = [
        _doc(
            "1",
            [
                _question("q10", points_awarded=1),
                _question("q2", points_awarded=1),
                _question("q1", points_awarded=1),
            ],
        )
    ]
    report = build_item_analysis(docs)
    assert [item.question_id for item in report.items] == ["q1", "q2", "q10"]


def test_report_class_size_and_flagged_count():
    docs = [_doc(str(i), [_question(points_awarded=10)]) for i in range(3)]
    report = build_item_analysis(docs)
    assert report.class_size == 3
    assert report.flagged_count == 0


def test_possible_points_override_fills_blank_submission_values():
    # Submissions with blank Possible points (possible_points=None) get their
    # denominator from the caller-supplied QA map.
    docs = [
        _doc("1", [_question(points_awarded=10, possible_points=None)]),
        _doc("2", [_question(points_awarded=5, possible_points=None)]),
    ]
    without = build_item_analysis(docs).items[0]
    assert without.difficulty is None  # nothing to divide by

    with_map = build_item_analysis(
        docs, possible_points_by_question={("q1", None): 10.0}
    ).items[0]
    assert with_map.difficulty == 0.75  # (10 + 5) / 2 / 10


def test_empty_cohort_produces_empty_report():
    report = build_item_analysis([])
    assert report.items == ()
    assert report.class_size == 0
