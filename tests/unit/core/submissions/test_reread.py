"""Reading a submission again keeps what a person did to it."""

from __future__ import annotations

from opengrader.core.submissions.reread import (
    ai_reading_upgrades,
    has_teacher_work,
    keep_teacher_work,
    same_student,
)
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    parse_student_submission_markdown,
    render_student_submission_markdown,
)


def _question(qid: str, text: str = "", **fields) -> StudentSubmissionQuestion:
    return StudentSubmissionQuestion(
        question_id=qid,
        prompt=f"Question {qid}",
        possible_points=10,
        response_image_path=f"responses/{qid}.png",
        response_text=text,
        **fields,
    )


def _doc(*questions: StudentSubmissionQuestion, **fields) -> StudentSubmissionDocument:
    base = dict(
        student_name="Ada",
        student_id="41",
        course_code=None,
        assignment_title="Quiz 1",
        assignment_version=None,
        source_pdf="../../student-pdfs/submission-0001.pdf",
        name_confidence=62.0,
        id_confidence=55.0,
        questions=list(questions),
    )
    base.update(fields)
    return StudentSubmissionDocument(**base)


def _merge(existing: StudentSubmissionDocument, fresh: StudentSubmissionDocument):
    kept = keep_teacher_work(existing, fresh, render_student_submission_markdown(fresh))
    return kept, parse_student_submission_markdown(kept.markdown)


def test_a_file_only_the_machine_wrote_has_no_teacher_work() -> None:
    assert not has_teacher_work(_doc(_question("q1", "x", response_source="tesseract")))


def test_grades_typed_in_readings_and_a_set_identity_are_teacher_work() -> None:
    assert has_teacher_work(_doc(_question("q1", points_awarded=0.0)))
    assert has_teacher_work(_doc(_question("q1", analysis="See me")))
    assert has_teacher_work(_doc(_question("q1", "101", response_source="human")))
    assert has_teacher_work(_doc(_question("q1"), id_confidence=None))
    assert has_teacher_work(_doc(_question("q1"), name_confidence=None))


def test_a_reread_keeps_grades_and_typed_in_readings_and_takes_the_rest() -> None:
    existing = _doc(
        _question("q1", "1o1", points_awarded=7.0, rubric_labels=("Incorrect",), analysis="Close"),
        _question("q2", "0b110", response_source="human"),
        _question("q3", "old"),
    )
    fresh = _doc(
        _question("q1", "1o1", response_source="tesseract"),
        _question("q2", "Ob11O", response_source="tesseract", response_confidence=40.0),
        _question("q3", "new", response_source="tesseract"),
    )
    kept, merged = _merge(existing, fresh)
    q1, q2, q3 = merged.questions

    assert (q1.points_awarded, q1.rubric_labels, q1.analysis) == (7.0, ("Incorrect",), "Close")
    assert (q2.response_text, q2.response_source, q2.response_confidence) == ("0b110", "human", None)
    assert q3.response_text == "new"
    assert q1.response_image_path.endswith("responses/q1.png") is True
    assert kept.kept_grades == 1 and not kept.warnings
    assert "(10 points) (10 points)" not in kept.markdown


def test_a_graded_answer_that_now_reads_differently_keeps_its_grade_and_says_so() -> None:
    existing = _doc(_question("q1", "1o1", points_awarded=7.0))
    fresh = _doc(_question("q1", "101", response_source="vision-model"))
    kept, merged = _merge(existing, fresh)

    assert merged.questions[0].points_awarded == 7.0
    assert merged.questions[0].response_text == "101"
    assert kept.regraded_ids == ("q1",)
    assert any("check them" in warning for warning in merged.scan_warnings)


def test_a_graded_answer_that_is_gone_is_named_with_its_points() -> None:
    existing = _doc(_question("q1", points_awarded=7.0), _question("q9", points_awarded=3.0))
    kept, merged = _merge(existing, _doc(_question("q1")))
    assert kept.dropped == (("q9", 3.0),)
    assert any("q9 (3 points)" in warning for warning in merged.scan_warnings)


def test_an_id_a_person_set_survives_and_a_machine_one_is_replaced() -> None:
    fresh = _doc(_question("q1"), student_id="47", student_name="Adda")
    _kept, merged = _merge(_doc(_question("q1"), student_id="12345", id_confidence=None), fresh)
    assert merged.student_id == "12345" and merged.id_confidence is None
    assert merged.student_name == "Adda"

    _kept, merged = _merge(_doc(_question("q1", points_awarded=1.0)), fresh)
    assert merged.student_id == "47"


def test_ai_upgrades_skip_graded_and_typed_in_answers() -> None:
    existing = _doc(
        _question("q1", "", points_awarded=7.0),
        _question("q2", "0b110", response_source="human"),
        _question("q3", ""),
        _question("q4", "same"),
    )
    fresh = _doc(
        _question("q1", "AI q1", response_source="vision-model"),
        _question("q2", "AI q2", response_source="vision-model"),
        _question("q3", "AI q3", response_source="vision-model", response_provider="ollama"),
        _question("q4", "same", response_source="vision-model"),
    )
    upgrades = ai_reading_upgrades(existing, fresh)
    assert [(u.question_id, u.new_text, u.new_provider) for u in upgrades] == [("q3", "AI q3", "ollama")]


def test_an_empty_ai_reading_never_replaces_one() -> None:
    existing = _doc(_question("q1", "tesseract text"))
    fresh = _doc(_question("q1", "  ", response_source="vision-model"))
    assert ai_reading_upgrades(existing, fresh) == ()


def test_merging_is_exact_on_a_file_with_nothing_to_carry() -> None:
    fresh = _doc(_question("q1", "x"))
    text = render_student_submission_markdown(fresh)
    assert keep_teacher_work(_doc(_question("q1", "y")), fresh, text).markdown == text


def test_the_same_student_is_the_same_id_or_without_ids_the_same_name() -> None:
    file = _doc(student_id="41", student_name="Ada Lovelace")
    assert same_student(file, _doc(student_id=" 41 ", student_name="Ada L."))
    assert not same_student(file, _doc(student_id="47", student_name="Ada Lovelace"))
    assert same_student(file, _doc(student_id="", student_name="ada  lovelace"))
    assert not same_student(file, _doc(student_id="", student_name="Alan Turing"))
    assert not same_student(_doc(student_id="", student_name=""), _doc(student_id="", student_name=""))
