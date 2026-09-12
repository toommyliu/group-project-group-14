from opengrader.core.submissions import student_file
from opengrader.core.submissions.student_file import StudentSubmissionDocument


def _doc(extra_pages: tuple[int, ...] = ()) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name="Ann Lee",
        student_id="111111111",
        course_code="CMPE",
        assignment_title="Quiz",
        assignment_version=None,
        source_pdf="ann.pdf",
        extra_pages=extra_pages,
    )


def test_absent_when_there_are_no_extra_pages() -> None:
    markdown = student_file.render_student_submission_markdown(_doc())
    assert "extra_pages" not in markdown


def test_extra_pages_round_trip() -> None:
    markdown = student_file.render_student_submission_markdown(_doc((4, 5)))
    assert "extra_pages: [4, 5]" in markdown
    assert student_file.parse_student_submission_markdown(markdown).extra_pages == (4, 5)


def test_missing_key_parses_as_empty() -> None:
    markdown = student_file.render_student_submission_markdown(_doc())
    assert student_file.parse_student_submission_markdown(markdown).extra_pages == ()


def test_pages_are_sorted_and_deduplicated_on_parse() -> None:
    markdown = student_file.render_student_submission_markdown(_doc())
    markdown = markdown.replace("student_score:", "extra_pages: [5, 2, 5]\nstudent_score:", 1)
    assert student_file.parse_student_submission_markdown(markdown).extra_pages == (2, 5)


def test_bad_entries_are_dropped_rather_than_raising() -> None:
    markdown = student_file.render_student_submission_markdown(_doc())
    markdown = markdown.replace(
        "student_score:", 'extra_pages: [3, "x", 0, true]\nstudent_score:', 1
    )
    assert student_file.parse_student_submission_markdown(markdown).extra_pages == (3,)


def test_non_list_value_is_ignored() -> None:
    markdown = student_file.render_student_submission_markdown(_doc())
    markdown = markdown.replace("student_score:", "extra_pages: 3\nstudent_score:", 1)
    assert student_file.parse_student_submission_markdown(markdown).extra_pages == ()


def test_identity_rewrite_preserves_extra_pages() -> None:
    markdown = student_file.render_student_submission_markdown(_doc((4,)))
    rewritten = student_file.set_student_identity_markdown(
        markdown, student_id="222222222", student_name="Bo Ng"
    )
    parsed = student_file.parse_student_submission_markdown(rewritten)
    assert parsed.student_id == "222222222"
    assert parsed.extra_pages == (4,)
