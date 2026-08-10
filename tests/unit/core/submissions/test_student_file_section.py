"""Tests for the class-section frontmatter field on student submissions."""

from opengrader.core.submissions import student_file
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    set_student_section_markdown,
)


def _doc(section: str | None = None) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name="Ann Lee",
        student_id="111111111",
        course_code="CMPE",
        assignment_title="Quiz",
        assignment_version=None,
        source_pdf="ann.pdf",
        section=section,
    )


def test_section_absent_renders_null() -> None:
    """No section → `section: null`, consistent with the other optional scalars."""
    markdown = student_file.render_student_submission_markdown(_doc(section=None))
    assert "section: null" in markdown


def test_section_present_round_trips() -> None:
    markdown = student_file.render_student_submission_markdown(_doc(section="SectionA"))
    assert 'section: "SectionA"' in markdown
    parsed = student_file.parse_student_submission_markdown(markdown)
    assert parsed.section == "SectionA"


def test_missing_section_parses_as_none() -> None:
    markdown = student_file.render_student_submission_markdown(_doc(section=None))
    parsed = student_file.parse_student_submission_markdown(markdown)
    assert parsed.section is None


def test_set_section_appends_when_absent() -> None:
    original = student_file.render_student_submission_markdown(_doc(section=None))
    updated = set_student_section_markdown(original, section="SectionB")
    parsed = student_file.parse_student_submission_markdown(updated)
    assert parsed.section == "SectionB"


def test_set_section_overwrites_existing() -> None:
    original = student_file.render_student_submission_markdown(_doc(section="Old"))
    updated = set_student_section_markdown(original, section="New")
    parsed = student_file.parse_student_submission_markdown(updated)
    assert parsed.section == "New"
    # Only one section line, not a duplicate.
    assert updated.count("section:") == 1
