from opengrader.tui.data.qa_question_content import QaQuestionContent

from .fakes import DEFAULT_IMAGE_PATH


def test_quiz_1_loads_questions_from_qa_multi_defaulting_to_first_version() -> None:
    content = QaQuestionContent(DEFAULT_IMAGE_PATH)

    seeds = content.build_question_seeds(assignment_title="Quiz 1")

    assert len(seeds) == 11
    assert seeds[0].question_id == "Q1"
    assert "Convert decimal 5 to binary" in seeds[0].prompt
    assert [seed.question_id for seed in seeds][-2:] == ["Q6C", "Q6D"]
    assert seeds[0].answers == ["101"]
    assert seeds[4].answers == [
        "Track the largest and second largest while iterating once"
    ]
    assert seeds[0].rubric_options[0].label == "Correct"
    assert seeds[0].rubric_options[0].points_delta == 0.0


def test_quiz_1_uses_requested_exam_version() -> None:
    content = QaQuestionContent(DEFAULT_IMAGE_PATH)

    seeds = content.build_question_seeds(
        assignment_title="Quiz 1",
        assignment_version="B",
    )

    assert len(seeds) == 11
    assert "Convert decimal 6 to binary" in seeds[0].prompt
    assert seeds[0].answers == ["110"]
    assert seeds[1].answers == ["ls", "dir"]
    assert seeds[4].question_id == "Q5A"
    assert any(
        option.label == "Partially Correct: Binary structure is close"
        and option.points_delta == -2.0
        for option in seeds[0].rubric_options
    )


def test_quiz_1_uses_submission_images_when_submission_markdown_is_provided() -> None:
    content = QaQuestionContent(DEFAULT_IMAGE_PATH)

    seeds = content.build_question_seeds(
        assignment_title="Quiz 1",
        assignment_version="A",
        submission_markdown_path=(
            "samples/cs146-midterm1/student-submissions/submission-0001.submission.md"
        ),
    )

    assert seeds[0].question_id == "Q1"
    assert seeds[0].image_path.replace("\\", "/").endswith(
        "responses/submission-0001/q1.png"
    )
    assert any(seed.question_id == "Q5A" for seed in seeds)
