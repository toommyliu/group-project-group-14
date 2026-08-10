from pathlib import Path

from opengrader.core.anonymize import (
    read_name_map,
    redact_submission_names,
    restore_submission_names,
    write_name_map,
)
from opengrader.core.submissions.student_file import load_student_submission_markdown


def _write_submission(
    path: Path,
    *,
    student_id: str = "123456789",
    student_name: str = "Tommy Liu",
) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f'student_name: "{student_name}"',
                f'student_id: "{student_id}"',
                'course_code: "CS146"',
                'assignment_title: "Quiz 1"',
                'assignment_version: "A"',
                'source_pdf: "s.pdf"',
                'student_score: ""',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: ",
                '  - Source segments: [{"page":1,"bbox":[0.2,0.3,0.5,0.2]}]',
                "  - Response text: an answer",
                "  - Possible points: 10",
                "  - Points: ",
                "  - Rubric:",
                "  - Analysis: ",
                "",
            ]
        )
    )


def test_redact_strips_names_keeps_ids_and_returns_map(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_id="111111111", student_name="Ann Lee")
    _write_submission(subs / "b.submission.md", student_id="222222222", student_name="Bo Ng")

    name_map = redact_submission_names(subs)

    # Names gone from the files, ids intact.
    for filename, sid in (("a.submission.md", "111111111"), ("b.submission.md", "222222222")):
        doc = load_student_submission_markdown(subs / filename)
        assert doc.student_name == ""
        assert doc.student_id == sid

    # The map carries what was removed, keyed by filename.
    assert name_map["a.submission.md"] == {"student_id": "111111111", "student_name": "Ann Lee"}
    assert name_map["b.submission.md"] == {"student_id": "222222222", "student_name": "Bo Ng"}


def test_redact_then_restore_round_trips_via_name_map(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_id="111111111", student_name="Ann Lee")

    name_map = redact_submission_names(subs)
    map_file = write_name_map(tmp_path / "names.json", name_map)

    restored = restore_submission_names(subs, read_name_map(map_file))

    assert restored == 1
    doc = load_student_submission_markdown(subs / "a.submission.md")
    assert doc.student_name == "Ann Lee"
    assert doc.student_id == "111111111"


def test_redact_discovers_nested_submissions(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    (subs / "submission-0001").mkdir(parents=True)
    _write_submission(
        subs / "submission-0001" / "submission-0001.submission.md",
        student_name="Nested Nadia",
    )

    name_map = redact_submission_names(subs)

    assert "submission-0001/submission-0001.submission.md" in name_map
    doc = load_student_submission_markdown(
        subs / "submission-0001" / "submission-0001.submission.md"
    )
    assert doc.student_name == ""


def test_restore_ignores_files_absent_from_map(tmp_path: Path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_name="Ann Lee")
    _write_submission(subs / "b.submission.md", student_name="Bo Ng")

    redact_submission_names(subs)
    # A partial map only mentions a; b must be left as-is (still redacted).
    restored = restore_submission_names(
        subs, {"a.submission.md": {"student_id": "123456789", "student_name": "Ann Lee"}}
    )

    assert restored == 1
    assert load_student_submission_markdown(subs / "a.submission.md").student_name == "Ann Lee"
    assert load_student_submission_markdown(subs / "b.submission.md").student_name == ""
