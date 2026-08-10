from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE, NUMERIC
from opengrader.core.submissions import (
    SimilarityKind,
    build_similarity_flags,
    similarity_ratio,
)
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)

# A substantive answer, comfortably above the default min-length guard, reused
# so tests exercise the metric rather than the length filter.
_LONG_A = (
    "A binary search halves the search interval each step, comparing the target "
    "to the middle element and discarding the half that cannot contain it."
)
_LONG_A_PARAPHRASE_TRIVIAL = (
    "A binary search halves the search interval each step; comparing the target "
    "to the middle element and discarding the half that cannot contain it!"
)
_LONG_B = (
    "Depth-first search explores as far as possible along each branch before "
    "backtracking, using a stack to remember where to resume the traversal."
)
# A moderate rewrite of _LONG_A: shares most wording but rephrases the tail, so
# it lands in the medium band (~0.87) — above the flag threshold, below the
# near-verbatim "high" cutoff.
_LONG_A_MEDIUM = _LONG_A.replace(
    "discarding the half that cannot contain it.",
    "then it throws away the half that cannot possibly hold it.",
)


def _question(
    question_id: str = "q1",
    *,
    question_type: str = FREE_RESPONSE,
    response_text: str = _LONG_A,
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
        rubric_labels=(),
        question_type=question_type,
    )


def _doc(
    student_id: str,
    student_name: str,
    questions: list[StudentSubmissionQuestion],
    *,
    assignment_version: str | None = "A",
) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version=assignment_version,
        source_pdf=f"submission-{student_id}.pdf",
        questions=questions,
    )


def test_similarity_ratio_ignores_cosmetic_differences():
    assert similarity_ratio(_LONG_A, _LONG_A_PARAPHRASE_TRIVIAL) == 1.0


def test_similarity_ratio_blank_is_not_a_match():
    assert similarity_ratio("", "") == 0.0
    assert similarity_ratio("   ", "anything") == 0.0


def test_near_identical_answers_flagged():
    docs = [
        _doc("1", "Ann", [_question(response_text=_LONG_A)]),
        _doc("2", "Bob", [_question(response_text=_LONG_A_PARAPHRASE_TRIVIAL)]),
    ]
    flags = build_similarity_flags(docs)
    assert len(flags) == 1
    flag = flags[0]
    assert flag.kind == SimilarityKind.NEAR_DUPLICATE
    assert flag.question_id == "q1"
    assert flag.member_count == 2
    assert flag.max_score == 1.0
    assert flag.severity == "high"  # verbatim → high
    assert {m.student_name for m in flag.members} == {"Ann", "Bob"}
    assert len(flag.pairs) == 1


def test_distinct_answers_not_flagged():
    docs = [
        _doc("1", "Ann", [_question(response_text=_LONG_A)]),
        _doc("2", "Bob", [_question(response_text=_LONG_B)]),
    ]
    assert build_similarity_flags(docs) == []


def test_short_answers_are_never_compared():
    # Identical, but below the default min-length guard: a canonical short answer.
    docs = [
        _doc("1", "Ann", [_question(response_text="O(n log n)")]),
        _doc("2", "Bob", [_question(response_text="O(n log n)")]),
    ]
    assert build_similarity_flags(docs) == []


def test_min_length_is_configurable():
    docs = [
        _doc("1", "Ann", [_question(response_text="mitochondria powerhouse")]),
        _doc("2", "Bob", [_question(response_text="mitochondria powerhouse")]),
    ]
    assert build_similarity_flags(docs) == []
    flags = build_similarity_flags(docs, min_length=5)
    assert len(flags) == 1


def test_multiple_choice_and_numeric_are_ignored():
    docs = [
        _doc(
            "1",
            "Ann",
            [
                _question("q1", question_type=MULTIPLE_CHOICE, response_text="B"),
                _question("q2", question_type=NUMERIC, response_text="42.000000"),
            ],
        ),
        _doc(
            "2",
            "Bob",
            [
                _question("q1", question_type=MULTIPLE_CHOICE, response_text="B"),
                _question("q2", question_type=NUMERIC, response_text="42.000000"),
            ],
        ),
    ]
    assert build_similarity_flags(docs) == []


def test_versions_are_partitioned():
    docs = [
        _doc("1", "Ann", [_question(response_text=_LONG_A)], assignment_version="A"),
        _doc("2", "Bob", [_question(response_text=_LONG_A)], assignment_version="B"),
    ]
    assert build_similarity_flags(docs) == []


def test_threshold_is_respected():
    # Same opening, diverging tail: alike but not identical. A high threshold
    # rejects it; a low one accepts it.
    shared_head = _LONG_A + " "
    docs = [
        _doc("1", "Ann", [_question(response_text=shared_head + "It runs in logarithmic time.")]),
        _doc("2", "Bob", [_question(response_text=shared_head + "Completely different closing remark about apples and oranges entirely.")]),
    ]
    assert build_similarity_flags(docs, threshold=0.99) == []
    assert len(build_similarity_flags(docs, threshold=0.5)) == 1


def test_three_way_ring_is_one_cluster():
    docs = [
        _doc("1", "Ann", [_question(response_text=_LONG_A)]),
        _doc("2", "Bob", [_question(response_text=_LONG_A_PARAPHRASE_TRIVIAL)]),
        _doc("3", "Cid", [_question(response_text=_LONG_A)]),
    ]
    flags = build_similarity_flags(docs)
    assert len(flags) == 1
    flag = flags[0]
    assert flag.member_count == 3
    # Three students → three pairwise matches in the one cluster.
    assert len(flag.pairs) == 3
    # Pairs are strongest-first.
    scores = [pair.score for pair in flag.pairs]
    assert scores == sorted(scores, reverse=True)


def test_two_independent_clusters_on_same_question():
    other_a = (
        "Dynamic programming solves a problem by combining the solutions of "
        "overlapping subproblems, storing each subresult to avoid recomputation."
    )
    docs = [
        _doc("1", "Ann", [_question(response_text=_LONG_A)]),
        _doc("2", "Bob", [_question(response_text=_LONG_A_PARAPHRASE_TRIVIAL)]),
        _doc("3", "Cid", [_question(response_text=other_a)]),
        _doc("4", "Dan", [_question(response_text=other_a)]),
    ]
    flags = build_similarity_flags(docs)
    assert len(flags) == 2
    assert all(flag.member_count == 2 for flag in flags)


def test_severity_medium_for_strong_but_not_verbatim():
    # Alike but rephrased: above threshold, below the verbatim cutoff.
    docs = [
        _doc("1", "Ann", [_question(response_text=_LONG_A)]),
        _doc("2", "Bob", [_question(response_text=_LONG_A_MEDIUM)]),
    ]
    flags = build_similarity_flags(docs)
    assert len(flags) == 1
    assert 0.80 <= flags[0].max_score < 0.95
    assert flags[0].severity == "medium"


def test_ordering_high_severity_and_tightest_first():
    docs = [
        # q1: medium (strong but not verbatim)
        _doc("1", "Ann", [_question("q1", response_text=_LONG_A)]),
        _doc("2", "Bob", [_question("q1", response_text=_LONG_A_MEDIUM)]),
        # q2: high (verbatim)
        _doc("3", "Cid", [_question("q2", response_text=_LONG_B)]),
        _doc("4", "Dan", [_question("q2", response_text=_LONG_B)]),
    ]
    flags = build_similarity_flags(docs)
    assert [flag.question_id for flag in flags] == ["q2", "q1"]
    assert flags[0].severity == "high"
    assert flags[-1].severity == "medium"


def test_determinism_across_input_order():
    docs = [
        _doc("2", "Bob", [_question(response_text=_LONG_A_PARAPHRASE_TRIVIAL)]),
        _doc("1", "Ann", [_question(response_text=_LONG_A)]),
    ]
    first = build_similarity_flags(docs)
    second = build_similarity_flags(list(reversed(docs)))
    assert first == second
