from PIL import Image

from opengrader.core.ocr import layout
from opengrader.core.question_types import FREE_RESPONSE


def _rendered_pages(*page_numbers: int) -> dict[int, tuple[Image.Image, int, int]]:
    image = Image.new("RGB", (1000, 1400), "white")
    return {page_number: (image, 1000, 1400) for page_number in page_numbers}


def _anchor(page: int, question: int, y_norm: float) -> layout._QuestionAnchor:
    y = int(round(y_norm * 1400))
    return layout._QuestionAnchor(
        page_number=page,
        question_number=question,
        bbox_px=(120, y, 145, y + 28),
        line_bbox_px=(120, y, 780, y + 28),
        confidence=95.0,
    )


def _box(x: float, y: float, w: float, h: float) -> layout.LayoutPrimitive:
    return layout.LayoutPrimitive("box", [x, y, w, h], 92.0)


def test_detect_questions_prefers_top_next_page_answer_box(monkeypatch) -> None:
    monkeypatch.setattr(layout, "classify_question_image", lambda _image: FREE_RESPONSE)

    questions = layout._detect_questions(
        anchors=[_anchor(1, 1, 0.72), _anchor(2, 2, 0.55)],
        rendered_pages=_rendered_pages(1, 2),
        primitives_by_page={
            2: [
                _box(0.10, 0.08, 0.82, 0.24),
                _box(0.10, 0.55, 0.82, 0.24),
            ]
        },
        review_reasons=[],
    )

    assert [question.question_id for question in questions] == ["q1", "q2"]
    assert questions[0].continuation_segments == [(2, [0.1, 0.08, 0.82, 0.24])]
    assert questions[1].continuation_segments == []


def test_detect_questions_continues_last_question_to_top_next_page_box(
    monkeypatch,
) -> None:
    monkeypatch.setattr(layout, "classify_question_image", lambda _image: FREE_RESPONSE)

    questions = layout._detect_questions(
        anchors=[_anchor(1, 1, 0.72)],
        rendered_pages=_rendered_pages(1, 2),
        primitives_by_page={2: [_box(0.10, 0.08, 0.82, 0.24)]},
        review_reasons=[],
    )

    assert len(questions) == 1
    assert questions[0].continuation_segments == [(2, [0.1, 0.08, 0.82, 0.24])]


def test_detect_questions_uses_top_band_fallback_with_review_reason(
    monkeypatch,
) -> None:
    monkeypatch.setattr(layout, "classify_question_image", lambda _image: FREE_RESPONSE)
    review_reasons: list[str] = []

    questions = layout._detect_questions(
        anchors=[_anchor(1, 1, 0.72), _anchor(2, 2, 0.55)],
        rendered_pages=_rendered_pages(1, 2),
        primitives_by_page={},
        review_reasons=review_reasons,
    )

    assert len(questions[0].continuation_segments) == 1
    page_number, continuation = questions[0].continuation_segments[0]
    assert page_number == 2
    assert continuation[1] == 0.04
    assert continuation[3] > 0.40
    assert "q1: continuation_fallback_band" in review_reasons


def test_detect_questions_does_not_continue_when_prompt_is_not_low_enough(
    monkeypatch,
) -> None:
    monkeypatch.setattr(layout, "classify_question_image", lambda _image: FREE_RESPONSE)

    questions = layout._detect_questions(
        anchors=[_anchor(1, 1, 0.66), _anchor(2, 2, 0.55)],
        rendered_pages=_rendered_pages(1, 2),
        primitives_by_page={2: [_box(0.10, 0.08, 0.82, 0.24)]},
        review_reasons=[],
    )

    assert questions[0].continuation_segments == []


def test_detect_questions_does_not_steal_next_question_box_at_top_of_page(
    monkeypatch,
) -> None:
    monkeypatch.setattr(layout, "classify_question_image", lambda _image: FREE_RESPONSE)
    review_reasons: list[str] = []

    questions = layout._detect_questions(
        anchors=[_anchor(1, 1, 0.72), _anchor(2, 2, 0.09)],
        rendered_pages=_rendered_pages(1, 2),
        primitives_by_page={2: [_box(0.10, 0.08, 0.82, 0.24)]},
        review_reasons=review_reasons,
    )

    assert questions[0].continuation_segments == []
    assert review_reasons == []
