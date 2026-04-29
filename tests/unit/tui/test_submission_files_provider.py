from pathlib import Path
import shutil

import pytest

from opengrader.core.qa_file import (
    QARubricFlatOption,
    flatten_rubric_options,
    load_qa_markdown,
)
from opengrader.core.submissions import load_student_submission_markdown
from opengrader.tui.data import RubricOptionSeed
from opengrader.tui.data.submission_files_provider import SubmissionFilesProvider

from .fakes import DEFAULT_IMAGE_PATH


def _seed(
    label: str,
    points_delta: float,
    selection_mode: str = "exclusive",
) -> RubricOptionSeed:
    kind = (
        "correct" if label == "Correct" else "incorrect" if label == "Incorrect" else "partial"
    )
    return RubricOptionSeed(label, points_delta, kind, selection_mode)


def _flat(
    label: str,
    points_delta: float,
    selection_mode: str = "exclusive",
) -> QARubricFlatOption:
    kind = (
        "correct" if label == "Correct" else "incorrect" if label == "Incorrect" else "partial"
    )
    return QARubricFlatOption(label, points_delta, kind, selection_mode)


def _copy_sample_submissions(tmp_path: Path) -> Path:
    source = Path("samples/cs146-quiz1/student-submissions")
    destination = tmp_path / "student-submissions"
    shutil.copytree(source, destination)
    return destination


def _write_simple_qa(path: Path) -> None:
    path.write_text(
        """---
title: Quiz
---

- (10 points) Convert.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""
    )


def _write_simple_submission(path: Path, *, name: str, version: str = "") -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f'name: "{name}"',
                'student_id: "1"',
                'course_code: "CMPE"',
                'assignment_title: "Quiz"',
                f'assignment_version: "{version}"',
                'source_pdf: "student.pdf"',
                'score: ""',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: Convert.",
                "  - Response image: ",
                "  - Response text: ",
                "  - Possible points: ",
                "  - Points: ",
                "  - Analysis: ",
                "",
            ]
        )
    )


def _write_simple_submissions(path: Path, *, count: int = 2) -> None:
    path.mkdir()
    for index in range(count):
        _write_simple_submission(
            path / f"student-{index + 1}.submission.md",
            name=f"Student {index + 1}",
        )


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
        "Partially Correct - Testing this is the second rubric item being edited of partial credit",
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


def test_submission_files_provider_save_question_grade_updates_selected_file(
    tmp_path: Path,
) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/qa-multi.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    provider.save_question_grade(
        student_item_id="s1",
        question_id="q1",
        possible_points=10,
        points_awarded=7,
        rubric_labels=("Partially Correct",),
    )

    first = load_student_submission_markdown(
        submissions_path / "submission-0001.submission.md"
    )
    second = load_student_submission_markdown(
        submissions_path / "submission-0002.submission.md"
    )
    assert first.questions[0].possible_points == 10
    assert first.questions[0].points_awarded == 7
    assert first.questions[0].rubric_label == "Partially Correct"
    assert second.questions[0].points_awarded is None

    reloaded = provider.build_question_seeds(student_item_id="s1")
    assert reloaded[0].points_awarded == 7
    assert reloaded[0].selected_rubric_labels == ("Partially Correct",)


def test_submission_files_provider_save_question_analysis_preserves_grade(
    tmp_path: Path,
) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/qa-multi.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    provider.save_question_grade(
        student_item_id="s1",
        question_id="q1",
        possible_points=10,
        points_awarded=7,
        rubric_labels=("Partially Correct",),
    )
    provider.save_question_analysis(
        student_item_id="s1",
        question_id="q1",
        analysis="Useful explanation.",
    )

    document = load_student_submission_markdown(
        submissions_path / "submission-0001.submission.md"
    )
    assert document.questions[0].points_awarded == 7
    assert document.questions[0].rubric_label == "Partially Correct"
    assert document.questions[0].analysis == "Useful explanation."


def test_submission_files_provider_save_rubric_options_updates_qa_and_grades(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    submissions_path = tmp_path / "submissions"
    _write_simple_qa(qa_path)
    _write_simple_submissions(submissions_path)
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    for student_item_id in ("s1", "s2"):
        provider.save_question_grade(
            student_item_id=student_item_id,
            question_id="q1",
            possible_points=10,
            points_awarded=5,
            rubric_labels=("Partially Correct",),
        )

    original_options = provider.build_question_seeds(student_item_id="s1")[
        0
    ].rubric_options
    result = provider.save_rubric_options(
        student_item_id="s1",
        question_id="q1",
        original_options=original_options,
        options=[
            _seed("Correct", 0.0),
            _seed("Partially Correct", -2.0),
            _seed("Incorrect", -10.0),
        ],
    )

    assert result.propagated_count == 2
    qa_doc = load_qa_markdown(qa_path)
    assert flatten_rubric_options(qa_doc.questions[0].rubric) == [
        _flat("Correct", 0.0),
        _flat("Partially Correct", -2.0),
        _flat("Incorrect", -10.0),
    ]
    for submission_name in ("student-1.submission.md", "student-2.submission.md"):
        document = load_student_submission_markdown(submissions_path / submission_name)
        assert document.questions[0].points_awarded == 8
        assert document.questions[0].rubric_labels == ("Partially Correct",)


def test_submission_files_provider_rubric_propagation_requires_label_and_points(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    submissions_path = tmp_path / "submissions"
    _write_simple_qa(qa_path)
    _write_simple_submissions(submissions_path)
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    provider.save_question_grade(
        student_item_id="s1",
        question_id="q1",
        possible_points=10,
        points_awarded=5,
        rubric_labels=("Partially Correct",),
    )
    provider.save_question_grade(
        student_item_id="s2",
        question_id="q1",
        possible_points=10,
        points_awarded=4,
        rubric_labels=("Partially Correct",),
    )

    original_options = provider.build_question_seeds(student_item_id="s1")[
        0
    ].rubric_options
    result = provider.save_rubric_options(
        student_item_id="s1",
        question_id="q1",
        original_options=original_options,
        options=[
            _seed("Correct", 0.0),
            _seed("Partially Correct", -2.0),
            _seed("Incorrect", -10.0),
        ],
    )

    first = load_student_submission_markdown(
        submissions_path / "student-1.submission.md"
    )
    second = load_student_submission_markdown(
        submissions_path / "student-2.submission.md"
    )
    assert result.propagated_count == 1
    assert first.questions[0].points_awarded == 8
    assert second.questions[0].points_awarded == 4


def test_submission_files_provider_rubric_addition_does_not_rewrite_grades(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    submissions_path = tmp_path / "submissions"
    _write_simple_qa(qa_path)
    _write_simple_submissions(submissions_path, count=1)
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    original_options = provider.build_question_seeds(student_item_id="s1")[
        0
    ].rubric_options
    result = provider.save_rubric_options(
        student_item_id="s1",
        question_id="q1",
        original_options=original_options,
        options=[
            _seed("Correct", 0.0),
            _seed("Partially Correct - Small issue", -2.0, "checklist"),
            _seed("Partially Correct - Major issue", -8.0, "checklist"),
            _seed("Incorrect", -10.0),
        ],
    )

    document = load_student_submission_markdown(
        submissions_path / "student-1.submission.md"
    )
    assert result.propagated_count == 0
    assert document.questions[0].points_awarded is None
    assert flatten_rubric_options(load_qa_markdown(qa_path).questions[0].rubric) == [
        _flat("Correct", 0.0),
        _flat("Partially Correct - Small issue", -2.0, "checklist"),
        _flat("Partially Correct - Major issue", -8.0, "checklist"),
        _flat("Incorrect", -10.0),
    ]


def test_submission_files_provider_rubric_delete_unused_option_succeeds(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    submissions_path = tmp_path / "submissions"
    _write_simple_qa(qa_path)
    _write_simple_submissions(submissions_path, count=1)
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    original_options = provider.build_question_seeds(student_item_id="s1")[
        0
    ].rubric_options
    provider.save_rubric_options(
        student_item_id="s1",
        question_id="q1",
        original_options=original_options,
        options=[
            _seed("Correct", 0.0),
            _seed("Partially Correct - Small issue", -2.0, "checklist"),
            _seed("Partially Correct - Major issue", -8.0, "checklist"),
            _seed("Incorrect", -10.0),
        ],
    )
    expanded_options = provider.build_question_seeds(student_item_id="s1")[
        0
    ].rubric_options

    provider.save_rubric_options(
        student_item_id="s1",
        question_id="q1",
        original_options=expanded_options,
        options=[
            _seed("Correct", 0.0),
            _seed("Partially Correct - Major issue", -10.0, "checklist"),
            _seed("Incorrect", -10.0),
        ],
    )

    assert flatten_rubric_options(load_qa_markdown(qa_path).questions[0].rubric) == [
        _flat("Correct", 0.0),
        _flat("Partially Correct - Major issue", -10.0, "checklist"),
        _flat("Incorrect", -10.0),
    ]


def test_submission_files_provider_rubric_delete_used_option_is_blocked(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    submissions_path = tmp_path / "submissions"
    _write_simple_qa(qa_path)
    _write_simple_submissions(submissions_path, count=1)
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    original_options = provider.build_question_seeds(student_item_id="s1")[
        0
    ].rubric_options
    provider.save_rubric_options(
        student_item_id="s1",
        question_id="q1",
        original_options=original_options,
        options=[
            _seed("Correct", 0.0),
            _seed("Partially Correct - Small issue", -2.0, "checklist"),
            _seed("Partially Correct - Major issue", -8.0, "checklist"),
            _seed("Incorrect", -10.0),
        ],
    )
    provider.save_question_grade(
        student_item_id="s1",
        question_id="q1",
        possible_points=10,
        points_awarded=8,
        rubric_labels=("Partially Correct - Small issue",),
    )
    expanded_options = provider.build_question_seeds(student_item_id="s1")[
        0
    ].rubric_options

    with pytest.raises(ValueError, match="Cannot delete rubric option used by 1"):
        provider.save_rubric_options(
            student_item_id="s1",
            question_id="q1",
            original_options=expanded_options,
            options=[
                _seed("Correct", 0.0),
                _seed("Partially Correct - Major issue", -10.0, "checklist"),
                _seed("Incorrect", -10.0),
            ],
        )


def test_submission_files_provider_saves_and_reloads_multiple_labels(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    qa_path.write_text(
        """---
title: Quiz
---

- (10 points) Convert.
  - **Rubric**
    - Correct (+10)
    - Partially Correct
      - Binary digits mostly correct (+4)
      - Shows conversion setup (+6)
    - Incorrect (+0)
"""
    )
    submissions_path = tmp_path / "submissions"
    _write_simple_submissions(submissions_path, count=1)
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    provider.save_question_grade(
        student_item_id="s1",
        question_id="q1",
        possible_points=10,
        points_awarded=10,
        rubric_labels=(
            "Partially Correct - Binary digits mostly correct",
            "Partially Correct - Shows conversion setup",
        ),
    )

    submission_path = submissions_path / "student-1.submission.md"
    assert (
        "Rubric: Partially Correct\n"
        "    - Binary digits mostly correct\n"
        "    - Shows conversion setup"
    ) in submission_path.read_text()
    reloaded = provider.build_question_seeds(student_item_id="s1")
    assert reloaded[0].selected_rubric_labels == (
        "Partially Correct - Binary digits mostly correct",
        "Partially Correct - Shows conversion setup",
    )


def test_submission_files_provider_rubric_edits_are_version_specific(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    qa_path.write_text(
        """---
title: Quiz
---

## Version A

- (10 points) Convert A.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)

## Version B

- (10 points) Convert B.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-4)
    - Incorrect (-10)
"""
    )
    submissions_path = tmp_path / "submissions"
    submissions_path.mkdir()
    _write_simple_submission(
        submissions_path / "student-a.submission.md",
        name="Student A",
        version="A",
    )
    _write_simple_submission(
        submissions_path / "student-b.submission.md",
        name="Student B",
        version="B",
    )
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    provider.save_question_grade(
        student_item_id="s1",
        question_id="q1",
        possible_points=10,
        points_awarded=5,
        rubric_labels=("Partially Correct",),
    )
    provider.save_question_grade(
        student_item_id="s2",
        question_id="q1",
        possible_points=10,
        points_awarded=6,
        rubric_labels=("Partially Correct",),
    )

    original_options = provider.build_question_seeds(student_item_id="s2")[
        0
    ].rubric_options
    result = provider.save_rubric_options(
        student_item_id="s2",
        question_id="q1",
        original_options=original_options,
        options=[
            _seed("Correct", 0.0),
            _seed("Partially Correct", -2.0),
            _seed("Incorrect", -10.0),
        ],
    )

    qa_doc = load_qa_markdown(qa_path)
    first = load_student_submission_markdown(
        submissions_path / "student-a.submission.md"
    )
    second = load_student_submission_markdown(
        submissions_path / "student-b.submission.md"
    )
    assert result.propagated_count == 1
    assert flatten_rubric_options(qa_doc.version_questions["A"][0].rubric) == [
        _flat("Correct", 0.0),
        _flat("Partially Correct", -5.0),
        _flat("Incorrect", -10.0),
    ]
    assert flatten_rubric_options(qa_doc.version_questions["B"][0].rubric) == [
        _flat("Correct", 0.0),
        _flat("Partially Correct", -2.0),
        _flat("Incorrect", -10.0),
    ]
    assert first.questions[0].points_awarded == 5
    assert second.questions[0].points_awarded == 8
