from opengrader.tui.formatting import format_question_label, format_question_type


def test_format_question_label_humanizes_simple_ids() -> None:
    assert format_question_label("q1") == "Question 1"
    assert format_question_label("Q2") == "Question 2"


def test_format_question_label_keeps_subquestion_suffixes() -> None:
    assert format_question_label("q5a") == "Question 5a"
    assert format_question_label("q6d") == "Question 6d"


def test_format_question_label_passes_through_custom_ids() -> None:
    assert format_question_label("bonus-question") == "bonus-question"
    assert format_question_label("qx") == "qx"
    assert format_question_label("") == ""


def test_format_question_type_humanizes_literals() -> None:
    assert format_question_type("free_response") == "Free Response"
    assert format_question_type("multiple_choice") == "Multiple Choice"


def test_format_question_type_returns_empty_for_unknown_values() -> None:
    assert format_question_type("") == ""
    assert format_question_type("essay") == ""


def test_format_review_reason_humanizes_known_reasons() -> None:
    from opengrader.tui.formatting import format_review_reason

    assert format_review_reason("empty_response") == "Empty response"
    assert format_review_reason("missing_student_id") == "Missing student ID"
    assert format_review_reason("something_new") == "Something new"


def test_format_review_row_respects_anonymity() -> None:
    from opengrader.tui.data.models import ReviewItem
    from opengrader.tui.formatting import format_review_row

    item = ReviewItem(
        student_item_id="s1",
        student_name="Tommy Liu",
        student_display_id="123456789",
        question_id="q1",
        reason="empty_response",  # type: ignore[arg-type]
        severity="low",  # type: ignore[arg-type]
        detail="verify the scan",
    )
    anon = format_review_row(item, teacher_mode=False).plain
    named = format_review_row(item, teacher_mode=True).plain
    assert "123456789" in anon and "Tommy Liu" not in anon
    assert "Tommy Liu" in named
    assert "Empty response" in anon and "Question 1" in anon


def _consistency_item(kind: str, severity: str):
    from opengrader.core.submissions.consistency import ConsistencyKind
    from opengrader.tui.data.models import (
        ConsistencyGradeVariant,
        ConsistencyMember,
        ConsistencyReportItem,
    )

    ann = ConsistencyMember(
        student_item_id="s1", student_id="111", student_name="Ann Smith", source_pdf_name="a.pdf"
    )
    bob = ConsistencyMember(
        student_item_id="s2", student_id="222", student_name="Bob Lee", source_pdf_name="b.pdf"
    )
    return ConsistencyReportItem(
        question_id="q1",
        assignment_version="A",
        kind=ConsistencyKind(kind),
        severity=severity,  # type: ignore[arg-type]
        answer_key="paris",
        sample_response_text="Paris",
        variants=(
            ConsistencyGradeVariant(points_awarded=10, rubric_labels=(), members=(ann,)),
            ConsistencyGradeVariant(points_awarded=5, rubric_labels=("Partial",), members=(bob,)),
        ),
    )


def test_format_consistency_headline_and_answer() -> None:
    from opengrader.tui.formatting import (
        format_consistency_answer,
        format_consistency_headline,
    )

    item = _consistency_item("points_divergence", "high")
    headline = format_consistency_headline(item).plain
    assert "Question 1" in headline
    assert "version A" in headline
    assert "Different scores" in headline
    assert "2 students" in headline
    assert format_consistency_answer(item).plain == "answer: “Paris”"


def test_format_consistency_variant_grade_hides_members() -> None:
    from opengrader.tui.formatting import format_consistency_variant_grade

    item = _consistency_item("points_divergence", "high")
    grade = format_consistency_variant_grade(item.variants[1]).plain
    assert "5 pts" in grade and "Partial" in grade
    assert "Bob" not in grade and "222" not in grade


def test_format_consistency_member_respects_anonymity() -> None:
    from opengrader.tui.formatting import format_consistency_member

    item = _consistency_item("points_divergence", "high")
    ann, bob = item.variants[0].members[0], item.variants[1].members[0]

    named = format_consistency_member(ann, teacher_mode=True).plain
    assert "→" in named and "Ann Smith" in named

    anon = format_consistency_member(bob, teacher_mode=False).plain
    assert "222" in anon and "Bob Lee" not in anon


def test_format_consistency_member_marks_non_navigable() -> None:
    from opengrader.tui.data.models import ConsistencyMember
    from opengrader.tui.formatting import format_consistency_member

    orphan = ConsistencyMember(
        student_item_id="", student_id="999", student_name="No Match", source_pdf_name=""
    )
    row = format_consistency_member(orphan, teacher_mode=True).plain
    assert "→" not in row and "can't open" in row


def test_format_history_row_shows_actor_and_respects_anonymity() -> None:
    from opengrader.core.history.journal import GradeJournalEntry
    from opengrader.tui.formatting import format_history_row

    entry = GradeJournalEntry(
        timestamp="2026-07-20T18:30:00+00:00",
        actor="prof_kim",
        kind="grade",
        student_id="123456789",
        student_name="Tommy Liu",
        question_id="q1",
        summary="9/10 pts",
    )
    anon = format_history_row(entry, teacher_mode=False).plain
    named = format_history_row(entry, teacher_mode=True).plain
    assert "prof_kim" in anon
    assert "123456789" in anon and "Tommy Liu" not in anon
    assert "Tommy Liu" in named
    assert "2026-07-20 18:30" in anon and "9/10 pts" in anon


from opengrader.core.analytics import build_item_analysis
from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)
from opengrader.tui.formatting import (
    format_item_difficulty,
    format_item_distractor,
    format_item_headline,
    format_item_separation,
)


def _q(qid, qtype, response, points, possible=10.0):
    return StudentSubmissionQuestion(
        question_id=qid,
        prompt="",
        possible_points=possible,
        response_image_path=None,
        response_text=response,
        points_awarded=points,
        question_type=qtype,
    )


def _cohort_item(qtype=FREE_RESPONSE, n=10):
    docs = []
    for i in range(n):
        strong = i >= n // 2
        docs.append(
            StudentSubmissionDocument(
                student_name=f"S{i}",
                student_id=str(i),
                course_code="C",
                assignment_title="Q",
                assignment_version=None,
                source_pdf=f"{i}.pdf",
                questions=[
                    _q("q1", qtype, "A" if strong else "B", 10.0 if strong else 0.0),
                    _q("q2", FREE_RESPONSE, "x", 10.0 if strong else 0.0),
                ],
            )
        )
    return next(it for it in build_item_analysis(docs).items if it.question_id == "q1")


def test_format_item_headline_includes_label_type_and_count() -> None:
    item = _cohort_item(MULTIPLE_CHOICE)
    plain = format_item_headline(item).plain
    assert "Question 1" in plain
    assert "Multiple Choice" in plain
    assert "10 responses" in plain


def test_format_item_difficulty_shows_percent_and_band() -> None:
    item = _cohort_item()
    plain = format_item_difficulty(item).plain
    assert "Difficulty" in plain
    assert "50%" in plain
    assert "moderate" in plain


def test_format_item_separation_shows_signed_value() -> None:
    item = _cohort_item()
    plain = format_item_separation(item).plain
    assert "Separation" in plain
    assert "+1.00" in plain
    assert "excellent" in plain


def test_format_item_separation_handles_insufficient_data() -> None:
    doc = StudentSubmissionDocument(
        student_name="S",
        student_id="1",
        course_code="C",
        assignment_title="Q",
        assignment_version=None,
        source_pdf="1.pdf",
        questions=[_q("q1", FREE_RESPONSE, "x", 10.0)],
    )
    item = build_item_analysis([doc]).items[0]
    plain = format_item_separation(item).plain
    assert "n/a" in plain


def test_format_item_distractor_marks_key() -> None:
    item = _cohort_item(MULTIPLE_CHOICE)
    key = next(d for d in item.distractors if d.is_likely_key)
    plain = format_item_distractor(key, max_count=key.count).plain
    assert "← key" in plain
    assert "choosers" in plain


def test_format_comment_snippet_row_shows_hotkey_and_uses() -> None:
    from opengrader.core.comments import CommentSnippet
    from opengrader.tui.formatting import format_comment_snippet_row

    row = format_comment_snippet_row(
        CommentSnippet(text="See Lecture 5", uses=3), hotkey=1
    ).plain
    assert row.startswith("1 ")
    assert "See Lecture 5" in row
    assert "3×" in row


def test_format_comment_snippet_row_without_hotkey_or_uses() -> None:
    from opengrader.core.comments import CommentSnippet
    from opengrader.tui.formatting import format_comment_snippet_row

    row = format_comment_snippet_row(
        CommentSnippet(text="Off-by-one", uses=0), hotkey=None
    ).plain
    assert "Off-by-one" in row
    assert "×" not in row
    assert not row.strip().startswith(("1", "2", "3"))


from opengrader.tui.formatting import format_confidence_chip


def test_confidence_chip_high_is_green_no_verify() -> None:
    chip = format_confidence_chip(94.0, has_response=True)
    assert chip is not None
    assert "94%" in chip.plain
    assert "verify" not in chip.plain


def test_confidence_chip_low_prompts_verify() -> None:
    chip = format_confidence_chip(41.0, has_response=True)
    assert chip is not None
    assert "41%" in chip.plain
    assert "verify" in chip.plain


def test_confidence_chip_none_with_response_is_unverified() -> None:
    chip = format_confidence_chip(None, has_response=True)
    assert chip is not None
    assert "unverified" in chip.plain
    assert "%" not in chip.plain


def test_confidence_chip_none_without_response_is_hidden() -> None:
    assert format_confidence_chip(None, has_response=False) is None


def test_confidence_chip_medium_band_no_verify() -> None:
    chip = format_confidence_chip(70.0, has_response=True)
    assert chip is not None
    assert "70%" in chip.plain
    assert "verify" not in chip.plain


def test_page_context_label_distinguishes_source_and_extra_pages() -> None:
    from opengrader.core.submissions.page_context import PageContextEntry
    from opengrader.tui.formatting import format_page_context_label

    source = format_page_context_label(
        PageContextEntry(page_number=2, kind="response", highlights=((0.1, 0.1, 0.2, 0.2),)),
        0,
        3,
    )
    assert "Page 2" in source.plain
    assert "answer region highlighted" in source.plain
    assert "(1/3)" in source.plain

    extra = format_page_context_label(
        PageContextEntry(page_number=4, kind="extra"), 2, 3
    )
    assert "extra work page" in extra.plain
    assert "(3/3)" in extra.plain
