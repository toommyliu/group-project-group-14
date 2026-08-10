"""Persistence + surgical rewrite of resolved/ambiguous student identity.

Covers the `id_candidates` frontmatter round trip (recorded when a duplicate
name leaves the id blank) and `set_student_identity_*`, the review-queue write
path that assigns an id and drops the now-resolved candidates.
"""

from pathlib import Path

import pytest

from opengrader.core.submissions.student_file import (
    IdentityCandidate,
    StudentSubmissionDocument,
    load_student_submission_markdown,
    parse_student_submission_markdown,
    render_student_submission_markdown,
    set_student_identity_file,
    set_student_identity_markdown,
)


def _document(**overrides) -> StudentSubmissionDocument:
    base = dict(
        student_name="Tommy Liu",
        student_id="",
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version="A",
        source_pdf="submission-0009.pdf",
    )
    base.update(overrides)
    return StudentSubmissionDocument(**base)


def test_candidates_round_trip_through_render_and_parse() -> None:
    document = _document(
        id_candidates=(
            IdentityCandidate("123456789", "Tommy Liu"),
            IdentityCandidate("987654321", "Tommy Liu"),
        )
    )

    reparsed = parse_student_submission_markdown(
        render_student_submission_markdown(document)
    )

    assert reparsed.id_candidates == document.id_candidates


def test_no_candidates_emits_no_frontmatter_key() -> None:
    # The common (resolved) case must not add noise to the file.
    rendered = render_student_submission_markdown(_document(student_id="123456789"))
    assert "id_candidates" not in rendered


def test_set_identity_assigns_id_and_drops_candidates() -> None:
    original = render_student_submission_markdown(
        _document(
            id_candidates=(
                IdentityCandidate("123456789", "Tommy Liu"),
                IdentityCandidate("987654321", "Tommy Liu"),
            )
        )
    )

    updated = set_student_identity_markdown(
        original, student_id="987654321", student_name="Tommy Liu"
    )

    reparsed = parse_student_submission_markdown(updated)
    assert reparsed.student_id == "987654321"
    assert reparsed.student_name == "Tommy Liu"
    assert reparsed.id_candidates == ()
    assert "id_candidates" not in updated


def test_set_identity_leaves_question_blocks_untouched() -> None:
    document = _document(
        id_candidates=(IdentityCandidate("123456789", "Tommy Liu"),)
    )
    original = render_student_submission_markdown(document)
    body = original.split("---", 2)[2]

    updated = set_student_identity_markdown(original, student_id="123456789")

    # Only the frontmatter changed; everything after the closing --- is intact.
    assert updated.split("---", 2)[2] == body


def test_set_identity_file_persists_and_returns_new_mtime(tmp_path: Path) -> None:
    path = tmp_path / "s.submission.md"
    path.write_text(
        render_student_submission_markdown(
            _document(id_candidates=(IdentityCandidate("123456789", "Tommy Liu"),))
        )
    )

    new_mtime = set_student_identity_file(path, student_id="123456789")

    assert isinstance(new_mtime, int)
    reloaded = load_student_submission_markdown(path)
    assert reloaded.student_id == "123456789"
    assert reloaded.id_candidates == ()


def test_set_identity_markdown_rejects_missing_frontmatter() -> None:
    with pytest.raises(ValueError):
        set_student_identity_markdown("no frontmatter here", student_id="1")
