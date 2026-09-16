from opengrader.core.submissions.student_file import (
    StudentInkFragment,
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    parse_student_submission_markdown,
    render_student_submission_markdown,
)


def test_student_ink_images_and_positions_round_trip() -> None:
    question = StudentSubmissionQuestion(
        question_id="q1",
        prompt="Write anywhere in the box.",
        possible_points=1,
        response_image_path="responses/q1.png",
        response_text="left\nright",
        ink_image_path="responses/q1.ink.png",
        ink_fragments=(
            StudentInkFragment(
                page_number=1,
                bbox_norm=(0.1, 0.2, 0.08, 0.03),
                text="left",
                confidence=98.4,
                source="vision-model",
            ),
            StudentInkFragment(
                page_number=1,
                bbox_norm=(0.75, 0.7, 0.1, 0.04),
                text="right",
            ),
        ),
    )
    document = StudentSubmissionDocument(
        student_name="",
        student_id="",
        course_code=None,
        assignment_title="Test",
        assignment_version=None,
        source_pdf="scan.pdf",
        questions=[question],
    )

    rendered = render_student_submission_markdown(document)
    parsed = parse_student_submission_markdown(rendered)

    assert parsed.questions[0].ink_image_path == "responses/q1.ink.png"
    assert parsed.questions[0].ink_fragments == question.ink_fragments
