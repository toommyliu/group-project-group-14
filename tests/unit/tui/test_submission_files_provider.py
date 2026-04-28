from pathlib import Path

import pytest

from opengrader.tui.data.submission_files_provider import SubmissionFilesProvider

from .fakes import DEFAULT_IMAGE_PATH


def test_submission_files_provider_loads_students_and_questions() -> None:
    provider = SubmissionFilesProvider(
        qa_file_path="samples/qa-multi.md",
        submissions_path="samples/cs146-quiz1/student-submissions",
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    students = provider.list_students(course_id=1)
    seeds = provider.build_question_seeds(student_item_id=None)

    assert [student.full_name for student in students] == [
        "Tommy Liu",
        "submission-0002.submission",
    ]
    assert [seed.question_id for seed in seeds] == [
        "q1",
        "q2",
        "q3",
        "q4",
        "q5a",
        "q5b",
        "q5c",
        "q6a",
        "q6b",
        "q6c",
        "q6d",
    ]
    assert seeds[0].points == 10
    assert seeds[0].prompt == "Convert decimal 5 to binary [A]."
    assert [option.label for option in seeds[0].rubric_options] == [
        "Correct",
        "Partially Correct - Binary form attempted but value incorrect",
        "Partially Correct - Conversion process mostly incorrect",
        "Incorrect",
    ]


def test_submission_files_provider_returns_student_specific_response_images() -> None:
    provider = SubmissionFilesProvider(
        qa_file_path="samples/qa-multi.md",
        submissions_path="samples/cs146-quiz1/student-submissions",
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    first_student = provider.build_question_seeds(student_item_id="s1")
    second_student = provider.build_question_seeds(student_item_id="s2")

    assert first_student[0].response_text == "no"
    assert second_student[0].response_text != first_student[0].response_text
    assert second_student[0].prompt == "Convert decimal 6 to binary [B]."
    assert first_student[0].image_path.endswith("responses/submission-0001/q1.png")
    assert second_student[0].image_path.endswith("responses/submission-0002/q1.png")
    assert Path(first_student[0].image_path).exists()
    assert Path(second_student[0].image_path).exists()


def test_submission_files_provider_raises_for_unknown_version(tmp_path: Path) -> None:
    qa_path = tmp_path / "qa.md"
    qa_path.write_text(
        "\n".join(
            [
                "---",
                "title: Quiz",
                "---",
                "",
                "## Version A",
                "",
                "- (1 points) Prompt",
                "  - **Rubric**",
                "    - Correct (+0)",
                "    - Partially Correct (-0.5)",
                "    - Incorrect (-1)",
                "",
            ]
        )
    )
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(
        "\n".join(
            [
                "---",
                'name: "Student"',
                'student_id: "1"',
                'course_code: "CMPE"',
                'assignment_title: "Quiz"',
                'assignment_version: "Z"',
                'source_pdf: "student.pdf"',
                'score: ""',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: Prompt",
                "  - Response image: ",
                "  - Response text: ",
                "  - Possible points: ",
                "  - Points: ",
                "  - Analysis: ",
                "",
            ]
        )
    )

    with pytest.raises(ValueError, match="available QA versions: A"):
        SubmissionFilesProvider(
            qa_file_path=qa_path,
            submissions_path=submission_path,
            default_image_path=DEFAULT_IMAGE_PATH,
        )
