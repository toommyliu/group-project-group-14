from pathlib import Path

from opengrader.core.submissions import (
    load_student_submission_document,
    load_student_submission_summary,
)


def test_load_student_submission_summary_reads_frontmatter_fields(tmp_path: Path) -> None:
    submission_path = tmp_path / "submission-0002.submission.md"
    submission_path.write_text(
        "\n".join(
            [
                "---",
                'name: "pe ade eee"',
                'student_id: ""',
                'course_code: "CS146"',
                'assignment_title: "Quiz 1"',
                'assignment_version: "B"',
                'source_pdf: "submission-0002.pdf"',
                'score: ""',
                "---",
                "",
                "- q1:",
            ]
        ),
        encoding="utf-8",
    )

    summary = load_student_submission_summary(submission_path)

    assert summary.name == "pe ade eee"
    assert summary.student_id == ""
    assert summary.course_code == "CS146"
    assert summary.assignment_title == "Quiz 1"
    assert summary.assignment_version == "B"
    assert summary.source_pdf == "submission-0002.pdf"
    assert summary.score == ""
    assert summary.markdown_path == submission_path


def test_load_student_submission_document_reads_response_image_paths(tmp_path: Path) -> None:
    submission_dir = tmp_path / "responses" / "submission-0002"
    submission_dir.mkdir(parents=True)
    image_path = submission_dir / "q1.png"
    image_path.write_bytes(b"fake")

    submission_path = tmp_path / "submission-0002.submission.md"
    submission_path.write_text(
        "\n".join(
            [
                "---",
                'name: "pe ade eee"',
                'student_id: ""',
                'course_code: "CS146"',
                'assignment_title: "Quiz 1"',
                'assignment_version: "B"',
                'source_pdf: "submission-0002.pdf"',
                'score: ""',
                "---",
                "",
                "- q1:",
                "  - Response image: ![q1 response](responses/submission-0002/q1.png)",
            ]
        ),
        encoding="utf-8",
    )

    document = load_student_submission_document(submission_path)

    assert document.summary.assignment_version == "B"
    assert len(document.questions) == 1
    assert document.questions[0].question_id == "Q1"
    assert document.questions[0].response_image_path == image_path.resolve()
