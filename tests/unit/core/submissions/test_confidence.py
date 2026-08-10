"""OCR/transcription confidence: format round-trip + Tesseract aggregation."""

import pytesseract

from opengrader.core.question_types import FREE_RESPONSE
from opengrader.core.submissions import processor
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    parse_student_submission_markdown,
    render_student_submission_markdown,
    set_student_identity_markdown,
)


def _question(response_confidence=None, **kw) -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=kw.get("question_id", "q1"),
        prompt="",
        possible_points=10,
        response_image_path=None,
        response_text=kw.get("response_text", "an answer"),
        question_type=FREE_RESPONSE,
        response_confidence=response_confidence,
    )


def _doc(questions, **kw) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=kw.get("student_name", "Ann"),
        student_id=kw.get("student_id", "1"),
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version="A",
        source_pdf="s.pdf",
        name_confidence=kw.get("name_confidence"),
        id_confidence=kw.get("id_confidence"),
        questions=questions,
    )


def test_question_confidence_round_trips():
    doc = _doc([_question(response_confidence=87.5)])
    parsed = parse_student_submission_markdown(render_student_submission_markdown(doc))
    assert parsed.questions[0].response_confidence == 87.5


def test_absent_question_confidence_is_omitted_and_none():
    doc = _doc([_question(response_confidence=None)])
    rendered = render_student_submission_markdown(doc)
    assert "Confidence:" not in rendered  # not emitted when unset
    parsed = parse_student_submission_markdown(rendered)
    assert parsed.questions[0].response_confidence is None


def test_whole_number_confidence_has_no_trailing_zero():
    doc = _doc([_question(response_confidence=100.0)])
    rendered = render_student_submission_markdown(doc)
    assert "  - Confidence: 100\n" in rendered


def test_field_confidence_round_trips():
    doc = _doc([_question()], name_confidence=92.0, id_confidence=41.0)
    rendered = render_student_submission_markdown(doc)
    assert "student_name_confidence: 92" in rendered
    assert "student_id_confidence: 41" in rendered
    parsed = parse_student_submission_markdown(rendered)
    assert parsed.name_confidence == 92.0
    assert parsed.id_confidence == 41.0


def test_absent_field_confidence_is_omitted():
    rendered = render_student_submission_markdown(_doc([_question()]))
    assert "student_name_confidence" not in rendered
    assert "student_id_confidence" not in rendered


def test_assigning_identity_drops_stale_id_confidence():
    doc = _doc([_question()], student_id="", id_confidence=38.0, name_confidence=55.0)
    content = render_student_submission_markdown(doc)
    assert "student_id_confidence" in content

    updated = set_student_identity_markdown(
        content, student_id="123456789", student_name="Ann Smith"
    )
    # A human-assigned identity is verified, so OCR confidence for it is dropped.
    assert "student_id_confidence" not in updated
    assert "student_name_confidence" not in updated
    parsed = parse_student_submission_markdown(updated)
    assert parsed.student_id == "123456789"
    assert parsed.id_confidence is None


def test_ocr_image_with_confidence_averages_word_confidence(monkeypatch, tmp_path):
    fake = {
        "text": ["The", "", "answer", "is"],
        "conf": ["90", "-1", "80", "70"],
    }

    class _FakeImg:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    # pytesseract is imported locally inside _ocr_image_with_confidence
    # (deferred so the pytesseract import doesn't tax every TUI startup — see
    # core/submissions/processor.py), so it must be patched on the real
    # pytesseract module rather than a processor.pytesseract module-level
    # binding, which no longer exists.
    monkeypatch.setattr(processor.Image, "open", lambda _p: _FakeImg())
    monkeypatch.setattr(pytesseract, "image_to_data", lambda *a, **k: fake)

    text, conf = processor._ocr_image_with_confidence(tmp_path / "x.png")
    assert text == "The answer is"  # empty token dropped
    # -1 (no confidence) excluded; mean of 90, 80, 70.
    assert conf == (90 + 80 + 70) / 3


def test_ocr_image_with_confidence_none_when_empty(monkeypatch, tmp_path):
    class _FakeImg:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(processor.Image, "open", lambda _p: _FakeImg())
    monkeypatch.setattr(
        pytesseract, "image_to_data", lambda *a, **k: {"text": ["", " "], "conf": ["-1", "-1"]}
    )
    text, conf = processor._ocr_image_with_confidence(tmp_path / "x.png")
    assert text == ""
    assert conf is None
