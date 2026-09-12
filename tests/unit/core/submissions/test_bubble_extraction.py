import fitz

from opengrader.core.bubble_sheet import (
    BubbleQuestion,
    BubbleReading,
    CompanionQuestion,
    build_bubble_sheet_layout,
)
from opengrader.core.pdf.templates.bubble_sheet import render_bubble_sheet
from opengrader.core.question_types import FREE_RESPONSE, MULTIPLE_CHOICE
from opengrader.core.submissions.bubble_extraction import (
    build_bubble_sheet_document,
    build_bubble_submission_questions,
)
from opengrader.core.submissions.review import ReviewReason, flag_submission
from opengrader.core.submissions.student_file import (
    parse_student_submission_markdown,
    render_student_submission_markdown,
)

QUESTIONS = [BubbleQuestion(question_id=f"q{i}", points=10.0) for i in range(1, 6)]


def _filled_sheet(tmp_path, marks):  # noqa: ANN001
    layout = build_bubble_sheet_layout(QUESTIONS, assignment_title="Quiz 1")
    doc = fitz.Document()
    render_bubble_sheet(doc, layout, title="Quiz 1", student_id="1001", version="A")
    page = doc[0]
    width, height = page.rect.width, page.rect.height
    for row in layout.rows:
        wanted = marks.get(row.question_id)
        if not wanted:
            continue
        wanted = (wanted,) if isinstance(wanted, str) else wanted
        for bubble in row.bubbles:
            if bubble.choice_label in wanted:
                page.draw_circle(
                    fitz.Point(bubble.cx * width, bubble.cy * height),
                    bubble.radius * width * 0.92,
                    color=(0, 0, 0), fill=(0, 0, 0), width=0,
                )
    path = tmp_path / "scan.pdf"
    doc.save(str(path))
    doc.close()
    return layout, path


def test_a_read_sheet_becomes_a_normal_submission_document(tmp_path) -> None:  # noqa: ANN001
    layout, pdf = _filled_sheet(tmp_path, {"q1": "B", "q2": "D", "q4": "A"})
    result = build_bubble_sheet_document(
        pdf, tmp_path / "out", layout, assignment_title="Quiz 1", student_id="1001"
    )
    document = result.document
    assert [q.question_id for q in document.questions] == ["q1", "q2", "q3", "q4", "q5"]
    assert document.questions[0].response_text == "B"
    assert document.questions[0].question_type == MULTIPLE_CHOICE
    assert document.questions[0].possible_points == 10.0
    assert result.marked_count == 3
    assert result.blank_count == 2


def test_the_document_round_trips_through_the_submission_format(tmp_path) -> None:  # noqa: ANN001
    layout, pdf = _filled_sheet(tmp_path, {"q1": "C"})
    document = build_bubble_sheet_document(
        pdf, tmp_path / "out", layout, student_id="1001"
    ).document
    rendered = render_student_submission_markdown(document)
    reparsed = parse_student_submission_markdown(rendered)
    assert [q.question_id for q in reparsed.questions] == [
        q.question_id for q in document.questions
    ]
    assert reparsed.questions[0].response_text == "C"


def test_each_question_gets_a_crop_of_its_own_bubble_row(tmp_path) -> None:  # noqa: ANN001
    layout, pdf = _filled_sheet(tmp_path, {"q1": "B"})
    document = build_bubble_sheet_document(
        pdf, tmp_path / "out", layout, student_id="1001"
    ).document
    for question in document.questions:
        assert question.response_image_path == f"responses/{question.question_id}.png"
        assert (tmp_path / "out" / "responses" / f"{question.question_id}.png").exists()


def test_each_question_records_where_on_the_page_it_came_from(tmp_path) -> None:  # noqa: ANN001
    layout, pdf = _filled_sheet(tmp_path, {"q1": "B"})
    document = build_bubble_sheet_document(
        pdf, tmp_path / "out", layout, student_id="1001"
    ).document
    segment = document.questions[0].source_segments[0]
    assert segment.page_number == 1
    assert segment.bbox_norm == layout.rows[0].bbox


def test_a_confident_mark_is_recorded_as_high_confidence(tmp_path) -> None:  # noqa: ANN001
    layout, pdf = _filled_sheet(tmp_path, {"q1": "B"})
    document = build_bubble_sheet_document(
        pdf, tmp_path / "out", layout, student_id="1001"
    ).document
    assert document.questions[0].response_confidence == 100.0


def test_a_double_marked_question_is_blank_zero_confidence_and_flagged(tmp_path) -> None:  # noqa: ANN001
    layout, pdf = _filled_sheet(tmp_path, {"q1": ("B", "D")})
    result = build_bubble_sheet_document(
        pdf, tmp_path / "out", layout, student_id="1001", student_name="Ada"
    )
    question = result.document.questions[0]
    assert question.response_text == ""
    assert question.response_confidence == 0.0
    assert result.ambiguous_count == 1

    reasons = {flag.reason for flag in flag_submission(result.document)}
    assert ReviewReason.UNREADABLE_RESPONSE in reasons


def test_a_skipped_question_is_a_confident_blank_and_not_flagged(tmp_path) -> None:  # noqa: ANN001
    layout, pdf = _filled_sheet(tmp_path, {"q1": "A"})
    document = build_bubble_sheet_document(
        pdf, tmp_path / "out", layout, student_id="1001", student_name="Ada"
    ).document
    blank = document.questions[1]
    assert blank.response_text == "" and blank.response_confidence > 60
    flags = flag_submission(document)
    assert not [
        f for f in flags
        if f.reason is ReviewReason.UNREADABLE_RESPONSE and f.question_id == "q2"
    ]


def test_an_unregistered_page_leaves_every_question_unverified(tmp_path) -> None:  # noqa: ANN001
    layout = build_bubble_sheet_layout(QUESTIONS)
    blank = fitz.Document()
    blank.new_page(width=612, height=792)
    path = tmp_path / "notasheet.pdf"
    blank.save(str(path))
    blank.close()

    result = build_bubble_sheet_document(path, tmp_path / "out", layout)
    assert result.warnings
    assert all(q.response_confidence is None for q in result.document.questions)
    assert all(q.response_text == "" for q in result.document.questions)


def test_companion_questions_get_an_empty_block_in_answer_key_order(tmp_path) -> None:  # noqa: ANN001
    layout = build_bubble_sheet_layout(
        [BubbleQuestion(question_id="q1", points=10.0),
         BubbleQuestion(question_id="q3", points=10.0)],
        companions=(
            CompanionQuestion(question_id="q2", points=25.0, question_type=FREE_RESPONSE),
        ),
        question_order=("q1", "q2", "q3"),
    )
    readings = (
        BubbleReading(question_id="q1", status="marked", choice_label="A", confidence=99.0),
    )
    questions = build_bubble_submission_questions(layout, readings, {}, tmp_path)
    assert [q.question_id for q in questions] == ["q1", "q2", "q3"]
    companion = questions[1]
    assert companion.question_type == FREE_RESPONSE
    assert companion.possible_points == 25.0
    assert companion.source_segments == ()
    assert companion.response_confidence is None


def test_questions_are_emitted_even_when_the_reader_saw_nothing(tmp_path) -> None:  # noqa: ANN001
    layout = build_bubble_sheet_layout([BubbleQuestion(question_id="q1")])
    questions = build_bubble_submission_questions(layout, (), {}, tmp_path)
    assert [q.question_id for q in questions] == ["q1"]
    assert questions[0].response_confidence is None
