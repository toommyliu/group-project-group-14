import pytest

from local_grader.core.ocr.spec import SpecError, validate_spec


def test_validate_spec_accepts_minimal_valid_spec() -> None:
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


def test_validate_spec_rejects_invalid_bbox() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
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


def test_validate_spec_rejects_deprecated_response_type() -> None:
    spec = {
        "schema_version": "1.0",
        "unit_id": "unit-1",
        "pages": [
            {
                "page_number": 1,
                "anchors": [{"id": "tl", "bbox": [0.02, 0.02, 0.03, 0.03]}],
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
                "fields": [
                    {"id": "student_name", "bbox": [0.1, 0.1, 0.2, 0.05], "type": "name"},
                    {"id": "student_id", "bbox": [0.1, 0.2, 0.2, 0.05], "type": "sid"},
                    {"id": "email", "bbox": [0.1, 0.3, 0.2, 0.05], "type": "email"},
                    {"id": "section", "bbox": [0.1, 0.4, 0.2, 0.05], "type": "section"},
                    {"id": "custom_field", "bbox": [0.1, 0.5, 0.2, 0.05], "type": "custom"},
                    {"id": "version", "bbox": [0.1, 0.6, 0.2, 0.05], "type": "version_marker", "expected": "A"},
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
                "fields": [{"id": "unknown", "bbox": [0.1, 0.1, 0.2, 0.05], "type": "unknown"}],
                "questions": [],
            }
        ],
    }

    with pytest.raises(SpecError, match="type must be one of"):
        validate_spec(spec)
