from opengrader.core.pdf.templates.constants import QuestionType
from opengrader.core.pdf.templates.spec import build_layout_spec


def test_build_layout_spec_uses_prompt_bbox_and_keeps_full_and_answer_bboxes() -> None:
    spec = build_layout_spec(
        unit_id="unit-1",
        versions=["A"],
        page_w=100.0,
        page_h=200.0,
        field_rects={
            "student_name": (10.0, 10.0, 50.0, 20.0),
            "student_id": (10.0, 30.0, 50.0, 40.0),
        },
        question_meta=[
            {
                "id": "q1",
                "rect": (10.0, 50.0, 90.0, 170.0),
                "prompt_rect": (10.0, 50.0, 90.0, 70.0),
                "answer_rect": (12.0, 72.0, 88.0, 166.0),
                "page_number": 1,
                "points": 10,
                "prompt": "Explain your answer.",
                "subquestions": [],
            }
        ],
    )

    q1 = spec["pages"][0]["questions"][0]
    assert q1["bbox"] == [0.1, 0.25, 0.8, 0.1]
    assert q1["prompt_bbox"] == [0.1, 0.25, 0.8, 0.1]
    assert q1["full_bbox"] == [0.1, 0.25, 0.8, 0.6]
    assert q1["answer_bbox"] == [0.12, 0.36, 0.76, 0.47]
    assert q1["response_type"] == QuestionType.FREE_RESPONSE


def test_build_layout_spec_uses_subquestion_prompt_bbox() -> None:
    spec = build_layout_spec(
        unit_id="unit-1",
        versions=["A"],
        page_w=100.0,
        page_h=100.0,
        field_rects={
            "student_name": (1.0, 1.0, 10.0, 4.0),
            "student_id": (1.0, 5.0, 10.0, 8.0),
        },
        question_meta=[
            {
                "id": "q2",
                "rect": (10.0, 10.0, 90.0, 90.0),
                "prompt_rect": (10.0, 10.0, 90.0, 20.0),
                "page_number": 1,
                "points": 10,
                "prompt": "Answer all parts",
                "subquestions": [
                    {
                        "id": "q2a",
                        "label": "a",
                        "rect": (12.0, 25.0, 88.0, 55.0),
                        "prompt_rect": (12.0, 25.0, 88.0, 33.0),
                        "answer_rect": (14.0, 33.0, 86.0, 53.0),
                        "points": 5,
                        "prompt": "Part a",
                    }
                ],
            }
        ],
    )

    q2 = spec["pages"][0]["questions"][0]
    q2a = q2["subquestions"][0]

    assert q2["bbox"] == [0.1, 0.1, 0.8, 0.1]
    assert q2["full_bbox"] == [0.1, 0.1, 0.8, 0.8]

    assert q2a["bbox"] == [0.12, 0.25, 0.76, 0.08]
    assert q2a["full_bbox"] == [0.12, 0.25, 0.76, 0.3]
    assert q2a["answer_bbox"] == [0.14, 0.33, 0.72, 0.2]
