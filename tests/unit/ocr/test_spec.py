import pytest

from opengrader.core.ocr.spec import (
    SpecError,
    lookup_version_by_anchors,
    validate_spec,
)
from opengrader.core.pdf.templates.constants import QuestionType


def test_validate_spec_accepts_minimal_valid_spec() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "title_bbox": [0.2, 0.05, 0.6, 0.08],
                "fields": [
                    {
                        "id": "student_id",
                        "bbox": [0.1, 0.1, 0.2, 0.05],
                        "type": "sid",
                        "required": True,
                        "whitelist": "0123456789",
                        "psm": 7,
                        "confidence_threshold": 70,
                    }
                ],
                "questions": [],
            }
        ],
    }

    errors = validate_spec(spec)
    assert errors == []


def test_validate_spec_rejects_missing_title_bbox_on_first_page() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "fields": [
                    {
                        "id": "student_id",
                        "bbox": [0.1, 0.1, 0.2, 0.05],
                        "type": "sid",
                    }
                ],
                "questions": [],
            }
        ],
    }

    with pytest.raises(SpecError, match="title_bbox"):
        validate_spec(spec)


def test_validate_spec_rejects_invalid_title_bbox() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "title_bbox": [1.1, 0.05, 0.6, 0.08],
                "fields": [
                    {
                        "id": "student_id",
                        "bbox": [0.1, 0.1, 0.2, 0.05],
                        "type": "sid",
                    }
                ],
                "questions": [],
            }
        ],
    }

    with pytest.raises(SpecError, match="title_bbox"):
        validate_spec(spec)


def test_validate_spec_rejects_invalid_bbox() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "title_bbox": [0.2, 0.05, 0.6, 0.08],
                "fields": [
                    {
                        "id": "student_name",
                        "bbox": [1.2, 0.1, 0.2, 0.05],
                        "type": "name",
                    }
                ],
                "questions": [],
            }
        ],
    }

    with pytest.raises(SpecError):
        # validate_spec itself can raise due validate_page() path
        validate_spec(spec)


def test_validate_spec_rejects_invalid_field_overrides() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "title_bbox": [0.2, 0.05, 0.6, 0.08],
                "fields": [
                    {
                        "id": "version",
                        "bbox": [0.8, 0.1, 0.1, 0.05],
                        "type": "version_marker",
                        "expected": "A",
                        "psm": 99,
                    }
                ],
                "questions": [],
            }
        ],
    }

    with pytest.raises(SpecError):
        validate_spec(spec)


# TODO: remove useless test
def test_validate_spec_rejects_deprecated_response_type() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "title_bbox": [0.2, 0.05, 0.6, 0.08],
                "fields": [],
                "questions": [
                    {
                        "id": "q1",
                        "bbox": [0.1, 0.2, 0.5, 0.2],
                        "response_type": "mcq",
                    }
                ],
            }
        ],
    }

    with pytest.raises(SpecError, match="response_type must be one of"):
        validate_spec(spec)


def test_validate_spec_accepts_all_field_types() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "title_bbox": [0.2, 0.05, 0.6, 0.08],
                "fields": [
                    {
                        "id": "student_name",
                        "bbox": [0.1, 0.1, 0.2, 0.05],
                        "type": "text",
                    },
                    {"id": "student_id", "bbox": [0.1, 0.2, 0.2, 0.05], "type": "sid"},
                    {"id": "section", "bbox": [0.1, 0.4, 0.2, 0.05], "type": "section"},
                    {
                        "id": "custom_field",
                        "bbox": [0.1, 0.5, 0.2, 0.05],
                        "type": "custom",
                    },
                    {
                        "id": "version",
                        "bbox": [0.1, 0.6, 0.2, 0.05],
                        "type": "version_marker",
                        "expected": "A",
                    },
                ],
                "questions": [],
            }
        ],
    }

    errors = validate_spec(spec)
    assert errors == []


def test_validate_spec_rejects_unknown_field_type() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "title_bbox": [0.2, 0.05, 0.6, 0.08],
                "fields": [
                    {"id": "unknown", "bbox": [0.1, 0.1, 0.2, 0.05], "type": "unknown"}
                ],
                "questions": [],
            }
        ],
    }

    with pytest.raises(SpecError, match="type must be one of"):
        validate_spec(spec)


def test_lookup_version_by_anchors_prefers_exact_match() -> None:
    spec = {
        "versions": {
            "A": ["tl", "tr", "bl"],
            "B": ["tl", "tr", "br"],
        }
    }

    assert lookup_version_by_anchors({"tl", "tr", "bl"}, spec) == "A"


def test_lookup_version_by_anchors_returns_none_on_ambiguous_tie() -> None:
    spec = {
        "versions": {
            "A": ["tl", "tr", "bl"],
            "B": ["tl", "tr", "br"],
        }
    }

    assert lookup_version_by_anchors({"tl", "tr"}, spec) is None


def test_validate_spec_accepts_multipart_questions() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "title_bbox": [0.2, 0.05, 0.6, 0.08],
                "fields": [],
                "questions": [
                    {
                        "id": "q1",
                        "bbox": [0.1, 0.2, 0.8, 0.5],
                        "response_type": QuestionType.FREE_RESPONSE,
                        "points": 20,
                        "subquestions": [
                            {
                                "id": "q1a",
                                "label": "a",
                                "bbox": [0.12, 0.28, 0.76, 0.18],
                                "response_type": QuestionType.FREE_RESPONSE,
                                "points": 10,
                            },
                            {
                                "id": "q1b",
                                "label": "b",
                                "bbox": [0.12, 0.48, 0.76, 0.18],
                                "response_type": QuestionType.FREE_RESPONSE,
                                "points": 10,
                            },
                        ],
                    }
                ],
            }
        ],
    }

    assert validate_spec(spec) == []


def test_validate_spec_rejects_cross_page_question_or_subquestion_ids() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "title_bbox": [0.2, 0.05, 0.6, 0.08],
                "fields": [],
                "questions": [
                    {
                        "id": "q1",
                        "bbox": [0.1, 0.2, 0.8, 0.3],
                        "response_type": QuestionType.FREE_RESPONSE,
                        "points": 10,
                        "subquestions": [
                            {
                                "id": "q1a",
                                "bbox": [0.12, 0.24, 0.76, 0.12],
                                "response_type": QuestionType.FREE_RESPONSE,
                                "points": 5,
                            }
                        ],
                    }
                ],
            },
            {
                "page_number": 2,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "title_bbox": [0.2, 0.05, 0.6, 0.08],
                "fields": [],
                "questions": [
                    {
                        "id": "q1",
                        "bbox": [0.1, 0.2, 0.8, 0.3],
                        "response_type": QuestionType.FREE_RESPONSE,
                        "points": 10,
                    },
                    {
                        "id": "q2",
                        "bbox": [0.1, 0.55, 0.8, 0.3],
                        "response_type": QuestionType.FREE_RESPONSE,
                        "points": 10,
                        "subquestions": [
                            {
                                "id": "q1a",
                                "bbox": [0.12, 0.6, 0.76, 0.12],
                                "response_type": QuestionType.FREE_RESPONSE,
                                "points": 5,
                            }
                        ],
                    },
                ],
            },
        ],
    }

    errors = validate_spec(spec)
    assert len(errors) == 2
    assert "cross-page continuation is not supported" in str(errors[0])
    assert "cross-page continuation is not supported" in str(errors[1])


def test_validate_spec_rejects_subquestion_outside_parent_bbox() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "title_bbox": [0.2, 0.05, 0.6, 0.08],
                "fields": [],
                "questions": [
                    {
                        "id": "q1",
                        "bbox": [0.1, 0.2, 0.5, 0.2],
                        "response_type": QuestionType.FREE_RESPONSE,
                        "points": 10,
                        "subquestions": [
                            {
                                "id": "q1a",
                                "bbox": [0.65, 0.2, 0.2, 0.1],
                                "response_type": QuestionType.FREE_RESPONSE,
                                "points": 5,
                            }
                        ],
                    }
                ],
            }
        ],
    }

    with pytest.raises(
        SpecError, match="bbox must be fully inside parent question bbox"
    ):
        validate_spec(spec)


def test_validate_spec_accepts_prompt_bbox_with_full_bbox_container() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
                "title_bbox": [0.2, 0.05, 0.6, 0.08],
                "fields": [],
                "questions": [
                    {
                        "id": "q1",
                        "bbox": [0.1, 0.2, 0.8, 0.08],
                        "full_bbox": [0.1, 0.2, 0.8, 0.6],
                        "response_type": QuestionType.FREE_RESPONSE,
                        "points": 10,
                        "subquestions": [
                            {
                                "id": "q1a",
                                "bbox": [0.12, 0.38, 0.76, 0.05],
                                "full_bbox": [0.12, 0.35, 0.76, 0.18],
                                "response_type": QuestionType.FREE_RESPONSE,
                                "points": 5,
                            }
                        ],
                    }
                ],
            }
        ],
    }

    assert validate_spec(spec) == []
