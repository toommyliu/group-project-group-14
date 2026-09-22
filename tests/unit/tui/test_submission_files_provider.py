import os
from contextlib import nullcontext
from pathlib import Path
import shutil

import fitz
import pytest

from opengrader.core.concurrency import ConcurrentModificationError, file_mtime_ns
from opengrader.core.grading.files import FileGradingSession
from opengrader.core.qa_file import (
    QARubricFlatOption,
    flatten_rubric_options,
    load_qa_markdown,
)
from opengrader.core.submissions import (
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)
from opengrader.core.history import GradeJournal, resolve_history_dir
from opengrader.core.submissions.regrade import add_regrade_request
from opengrader.core.submissions.review import ReviewReason
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


def _write_simple_submission(
    path: Path, *, name: str, version: str = "", response_image: str = ""
) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f'student_name: "{name}"',
                'student_id: "1"',
                'course_code: "CMPE"',
                'assignment_title: "Quiz"',
                f'assignment_version: "{version}"',
                'source_pdf: "student.pdf"',
                'student_score: ""',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: Convert.",
                f"  - Response image: {response_image}",
                "  - Response text: ",
                "  - Possible points: ",
                "  - Points: ",
                "  - Analysis: ",
                "",
            ]
        )
    )


def _write_submission_with_source_metadata(
    path: Path,
    *,
    source_pdf: str,
) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                'student_name: "Student 1"',
                'student_id: "1"',
                'course_code: "CMPE"',
                'assignment_title: "Quiz"',
                'assignment_version: ""',
                f'source_pdf: "{source_pdf}"',
                'student_score: ""',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: Convert.",
                "  - Response image: ",
                '  - Source segments: [{"page":1,"bbox":[0.2,0.3,0.5,0.2]}]',
                "  - Response text: ",
                "  - Possible points: ",
                "  - Points: ",
                "  - Analysis: ",
                "",
            ]
        )
    )


def _create_blank_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=300, height=400)
    doc.save(path)
    doc.close()


def _write_simple_submissions(path: Path, *, count: int = 2) -> None:
    path.mkdir()
    for index in range(count):
        _write_simple_submission(
            path / f"student-{index + 1}.submission.md",
            name=f"Student {index + 1}",
        )


def test_submission_files_provider_loads_flat_and_nested_submission_files(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    submissions_path = tmp_path / "student-submissions"
    nested_dir = submissions_path / "student-1"
    nested_dir.mkdir(parents=True)
    _write_simple_submission(
        submissions_path / "student-0.submission.md",
        name="Flat Student",
    )
    _write_simple_submission(
        nested_dir / "student-1.submission.md",
        name="Nested Student",
    )

    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    assert [student.full_name for student in provider.list_students(course_id=1)] == [
        "Flat Student",
        "Nested Student",
    ]


def test_submission_files_provider_returns_student_specific_response_images(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    submissions_path = tmp_path / "student-submissions"
    expected_images = []
    for index in (1, 2):
        student_dir = submissions_path / f"student-{index}"
        images_dir = student_dir / "responses"
        images_dir.mkdir(parents=True)
        image_path = images_dir / "q1.png"
        image_path.touch()
        _write_simple_submission(
            student_dir / f"student-{index}.submission.md",
            name=f"Student {index}",
            response_image="![q1](responses/q1.png)",
        )
        expected_images.append(str(image_path))

    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    first_student = provider.build_question_seeds(student_item_id="s1")
    second_student = provider.build_question_seeds(student_item_id="s2")

    assert first_student[0].image_path == expected_images[0]
    assert second_student[0].image_path == expected_images[1]


def test_submission_files_provider_recovers_version_from_question_markers(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    qa_path.write_text(
        "\n".join(
            [
                "---",
                "title: Quiz 1",
                "---",
                "",
                "## Version B",
                "",
                "- (1 points) Prompt [B]",
                "  - **Rubric**",
                "    - Correct (+0)",
                "    - Partially Correct (-0.5)",
                "    - Incorrect (-1)",
                "",
            ]
        )
    )
    submission_path = tmp_path / "student.submission.md"
    _write_simple_submission(submission_path, name="Student", version="1")
    content = submission_path.read_text()
    submission_path.write_text(
        content.replace("  - Prompt: Convert.", "  - Prompt: Prompt [B]")
    )

    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submission_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    seeds = provider.build_question_seeds(student_item_id="s1")
    assert seeds[0].prompt == "Prompt [B]"


def test_submission_files_provider_falls_back_to_first_version_when_unknown(
    tmp_path: Path,
) -> None:
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
                'student_name: "Student"',
                'student_id: "1"',
                'course_code: "CMPE"',
                'assignment_title: "Quiz"',
                'assignment_version: "Z"',
                'source_pdf: "student.pdf"',
                'student_score: ""',
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

    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submission_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    seeds = provider.build_question_seeds(student_item_id="s1")
    assert seeds[0].prompt == "Prompt"


def test_submission_files_provider_save_question_grade_updates_selected_file(
    tmp_path: Path,
) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
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
        submissions_path / "submission-0001" / "submission-0001.submission.md"
    )
    second = load_student_submission_markdown(
        submissions_path / "submission-0002" / "submission-0002.submission.md"
    )
    assert first.questions[0].possible_points == 10
    assert first.questions[0].points_awarded == 7
    assert first.questions[0].rubric_label == "Partially Correct"
    assert second.questions[0].points_awarded is None

    reloaded = provider.build_question_seeds(student_item_id="s1")
    assert reloaded[0].points_awarded == 7
    assert reloaded[0].selected_rubric_labels == ("Partially Correct",)


def test_save_question_transcription_persists_text_and_marks_verified(
    tmp_path: Path,
) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    provider.save_question_transcription(
        student_item_id="s1",
        question_id="q1",
        response_text="corrected handwriting reading",
    )

    saved = load_student_submission_markdown(
        submissions_path / "submission-0001" / "submission-0001.submission.md"
    )
    assert saved.questions[0].response_text == "corrected handwriting reading"
    assert saved.questions[0].response_confidence == 100.0

    history = provider.read_grade_history()
    assert any(entry.kind == "transcription" for entry in history)


def test_comment_library_round_trips_through_the_provider(tmp_path: Path) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    assert provider.read_comment_library().snippets == ()

    library = provider.read_comment_library().with_snippet("See Lecture 5")
    provider.save_comment_library(library)

    assert (submissions_path / ".opengrader" / "comments.json").exists()
    reloaded = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    assert [s.text for s in reloaded.read_comment_library().snippets] == [
        "See Lecture 5"
    ]


def test_regrade_requests_flow_through_the_provider(tmp_path: Path) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    assert provider.build_regrade_items() == []

    add_regrade_request(
        submissions_path, student_id="987654321", reason="Q1 method valid", question_id="q1"
    )

    items = provider.build_regrade_items()
    assert len(items) == 1
    item = items[0]
    assert item.is_navigable
    assert item.student_item_id
    assert item.question_id == "q1"
    assert item.reason == "Q1 method valid"

    review = provider.build_review_items()
    assert any(r.reason is ReviewReason.OPEN_REGRADE_REQUEST for r in review)
    assert review[0].reason is ReviewReason.OPEN_REGRADE_REQUEST
    assert review[0].student_item_id == item.student_item_id

    provider.resolve_regrade(item.request_id, note="regraded +3")
    assert provider.build_regrade_items() == []
    assert not any(
        r.reason is ReviewReason.OPEN_REGRADE_REQUEST
        for r in provider.build_review_items()
    )


def test_regrade_unmatched_student_still_listed_but_not_navigable(tmp_path: Path) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    add_regrade_request(submissions_path, student_id="000000000", reason="not on file")
    items = provider.build_regrade_items()
    assert len(items) == 1
    assert items[0].student_item_id == ""
    assert not items[0].is_navigable


def test_saving_a_grade_writes_an_accountability_journal_entry(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENGRADER_GRADER", "prof_kim")
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
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
        analysis="Missed the edge case.",
    )

    from opengrader.core.history import read_journal, resolve_history_dir

    history_dir = resolve_history_dir(submissions_path)
    assert history_dir == submissions_path / ".opengrader" / "history"
    entries = read_journal(history_dir)
    assert [e.kind for e in entries] == ["grade", "comment"]
    grade = entries[0]
    assert grade.actor == "prof_kim"
    assert grade.student_id == "123456789"
    assert grade.new == {"points": 7, "rubric": ["Partially Correct"]}
    assert grade.old["points"] is None
    assert entries[1].new == {"analysis": "Missed the edge case."}


def test_build_review_items_binds_flags_to_nav_ids(tmp_path: Path) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    items = provider.build_review_items()

    assert items, "sample submissions have blank responses to flag"
    assert all(item.student_item_id in {"s1", "s2"} for item in items)
    ranks = [item.severity_rank for item in items]
    assert ranks == sorted(ranks)
    assert all(item.student_display_id for item in items)


def _write_ambiguous_submission(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                'student_name: "Tommy Liu"',
                'student_id: ""',
                'course_code: "CS146"',
                'assignment_title: "Quiz 1"',
                'assignment_version: "A"',
                'source_pdf: "../../split-submissions/submission-0009.pdf"',
                'student_score: ""',
                "id_candidates:",
                '  - id: "123456789"',
                '    name: "Tommy Liu"',
                '  - id: "987654321"',
                '    name: "Tommy Liu"',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: Convert.",
                "  - Response image: ",
                "  - Response text: something",
                "  - Possible points: ",
                "  - Points: ",
                "  - Analysis: ",
                "",
            ]
        )
    )


def test_build_review_items_carries_source_pdf_and_candidates(tmp_path: Path) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    _write_ambiguous_submission(
        submissions_path / "submission-0001" / "submission-0001.submission.md"
    )
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    items = provider.build_review_items()
    identity = next(item for item in items if item.is_identity_flag)

    assert identity.source_pdf_name == "submission-0009.pdf"
    assert [(c.student_id, c.name) for c in identity.id_candidates] == [
        ("123456789", "Tommy Liu"),
        ("987654321", "Tommy Liu"),
    ]


def test_assign_student_identity_persists_id_and_clears_candidates(
    tmp_path: Path,
) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    sub_path = submissions_path / "submission-0001" / "submission-0001.submission.md"
    _write_ambiguous_submission(sub_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    provider.assign_student_identity(
        student_item_id="s1",
        student_id="987654321",
        student_name="Tommy Liu",
    )

    reloaded = load_student_submission_markdown(sub_path)
    assert reloaded.student_id == "987654321"
    assert reloaded.id_candidates == ()
    assert not any(
        item.is_identity_flag and item.student_item_id == "s1"
        for item in provider.build_review_items()
    )


def test_assign_student_identity_writes_journal_entry(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENGRADER_GRADER", "ta_bob")
    submissions_path = _copy_sample_submissions(tmp_path)
    _write_ambiguous_submission(
        submissions_path / "submission-0001" / "submission-0001.submission.md"
    )
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    provider.assign_student_identity(
        student_item_id="s1", student_id="987654321", student_name="Tommy Liu"
    )

    history = provider.read_grade_history()
    assert [(e.actor, e.kind) for e in history] == [("ta_bob", "identity")]
    assert "987654321" in history[0].summary


def test_read_grade_history_returns_journal_entries(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("OPENGRADER_GRADER", "ta_bob")
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    assert provider.read_grade_history() == []

    provider.save_question_grade(
        student_item_id="s1",
        question_id="q1",
        possible_points=10,
        points_awarded=9,
        rubric_labels=("Correct",),
    )
    history = provider.read_grade_history()
    assert [e.actor for e in history] == ["ta_bob"]
    assert history[0].kind == "grade"


def test_journal_write_failure_never_blocks_a_grade(
    tmp_path: Path, monkeypatch
) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    def _boom(**kwargs):  # noqa: ANN003, ANN202
        raise OSError("history dir is read-only")

    monkeypatch.setattr(provider._journal, "append", _boom)

    provider.save_question_grade(
        student_item_id="s1",
        question_id="q1",
        possible_points=10,
        points_awarded=5,
        rubric_labels=("Correct",),
    )
    reloaded = provider.build_question_seeds(student_item_id="s1")
    assert reloaded[0].points_awarded == 5


def test_update_student_submission_file_rejects_stale_mtime(tmp_path: Path) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    submission_file = (
        submissions_path / "submission-0001" / "submission-0001.submission.md"
    )
    stale_mtime = file_mtime_ns(submission_file)
    assert stale_mtime is not None

    os.utime(submission_file, ns=(stale_mtime + 1_000_000, stale_mtime + 1_000_000))

    with pytest.raises(ConcurrentModificationError):
        update_student_submission_file(
            submission_file,
            StudentSubmissionGradeUpdate(question_id="q1", points_awarded=5),
            expected_mtime_ns=stale_mtime,
        )


def test_submission_files_provider_detects_external_submission_change(
    tmp_path: Path,
) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    submission_file = (
        submissions_path / "submission-0001" / "submission-0001.submission.md"
    )
    current = file_mtime_ns(submission_file)
    assert current is not None
    os.utime(submission_file, ns=(current + 1_000_000, current + 1_000_000))

    with pytest.raises(ConcurrentModificationError):
        provider.save_question_grade(
            student_item_id="s1",
            question_id="q1",
            possible_points=10,
            points_awarded=7,
            rubric_labels=("Partially Correct",),
        )


def test_submission_files_provider_save_succeeds_after_reload(
    tmp_path: Path,
) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
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
    provider.save_question_grade(
        student_item_id="s1",
        question_id="q1",
        possible_points=10,
        points_awarded=10,
        rubric_labels=("Correct",),
    )

    reloaded = provider.build_question_seeds(student_item_id="s1")
    assert reloaded[0].points_awarded == 10
    assert reloaded[0].selected_rubric_labels == ("Correct",)


def test_submission_files_provider_save_question_analysis_preserves_grade(
    tmp_path: Path,
) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
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
        submissions_path / "submission-0001" / "submission-0001.submission.md"
    )
    assert document.questions[0].points_awarded == 7
    assert document.questions[0].rubric_label == "Partially Correct"
    assert document.questions[0].analysis == "Useful explanation."


def test_submission_files_provider_saves_question_comment_annotation(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    split_dir = tmp_path / "split-submissions"
    split_dir.mkdir()
    source_pdf = split_dir / "student.pdf"
    _create_blank_pdf(source_pdf)
    submissions_path = tmp_path / "student-submissions"
    submissions_path.mkdir()
    _write_submission_with_source_metadata(
        submissions_path / "student.submission.md",
        source_pdf="../split-submissions/student.pdf",
    )
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    annotated_path = provider.save_question_comment_annotation(
        student_item_id="s1",
        question_id="q1",
        comment="Show the conversion steps.",
    )

    assert annotated_path == submissions_path / "annotated-pdfs" / "student.annotated.pdf"
    with fitz.open(annotated_path) as document:
        comment_annots = [
            annot
            for annot in document[0].annots()
            if annot.info.get("subject", "").startswith("cm:")
        ]
        assert len(comment_annots) == 1
        info = comment_annots[0].info
        assert info.get("title", "") == ""
        assert "opengrader" not in info["subject"].lower()
        assert "q1" not in info["subject"].lower()
        assert info["content"] == "Show the conversion steps."


def test_submission_files_provider_requires_source_metadata_for_annotation(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    submissions_path = tmp_path / "student-submissions"
    _write_simple_submissions(submissions_path, count=1)
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    with pytest.raises(ValueError, match="regenerate submission files"):
        provider.save_question_comment_annotation(
            student_item_id="s1",
            question_id="q1",
            comment="Needs work.",
        )


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


def _write_graded_submission(
    path: Path,
    *,
    student_id: str,
    student_name: str,
    response: str,
    points: float,
) -> None:
    from opengrader.core.submissions import (
        StudentSubmissionDocument,
        StudentSubmissionQuestion,
        render_student_submission_markdown,
    )

    document = StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code="CMPE195A",
        assignment_title="Quiz 1",
        assignment_version=None,
        source_pdf=f"submission-{student_id}.pdf",
        questions=[
            StudentSubmissionQuestion(
                question_id="q1",
                prompt="What is the capital of France?",
                possible_points=50,
                response_image_path=None,
                response_text=response,
                points_awarded=points,
                question_type="free_response",
            )
        ],
    )
    path.write_text(render_student_submission_markdown(document))


def test_build_consistency_report_flags_same_answer_different_grade(
    tmp_path: Path,
) -> None:
    submissions_path = tmp_path / "student-submissions"
    submissions_path.mkdir()
    _write_graded_submission(
        submissions_path / "ann.submission.md",
        student_id="111111111",
        student_name="Ann Smith",
        response="Paris",
        points=50,
    )
    _write_graded_submission(
        submissions_path / "bob.submission.md",
        student_id="222222222",
        student_name="Bob Lee",
        response="paris.",
        points=30,
    )

    provider = SubmissionFilesProvider(
        qa_file_path="samples/cmpe195a-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    items = provider.build_consistency_report()

    assert len(items) == 1
    item = items[0]
    assert item.question_id == "q1"
    assert item.severity == "high"
    assert item.member_count == 2
    assert {v.points_awarded for v in item.variants} == {50, 30}

    members = [m for variant in item.variants for m in variant.members]
    assert {m.student_item_id for m in members} == {"s1", "s2"}
    assert all(m.is_navigable for m in members)
    by_item_id = {m.student_item_id: m for m in members}
    assert by_item_id["s1"].student_id == "111111111"
    assert by_item_id["s2"].student_id == "222222222"


def _write_submission_without_possible_points(
    path: Path, *, name: str, student_id: str, points: float
) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f'student_name: "{name}"',
                f'student_id: "{student_id}"',
                'course_code: "CMPE"',
                'assignment_title: "Quiz"',
                'assignment_version: ""',
                f'source_pdf: "{name}.pdf"',
                'student_score: ""',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: Convert.",
                "  - Response image: ",
                "  - Response text: an answer",
                "  - Possible points: ",
                f"  - Points: {points}",
                "  - Rubric: Correct",
                "  - Analysis: ",
                "",
            ]
        )
    )


def test_build_item_analysis_report_uses_qa_points_for_difficulty(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    submissions_path = tmp_path / "student-submissions"
    submissions_path.mkdir()
    _write_submission_without_possible_points(
        submissions_path / "a.submission.md", name="Ann", student_id="1", points=10
    )
    _write_submission_without_possible_points(
        submissions_path / "b.submission.md", name="Bob", student_id="2", points=0
    )

    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    report = provider.build_item_analysis_report()

    assert report.class_size == 2
    (item,) = report.items
    assert item.question_id == "q1"
    assert item.difficulty == 0.5


def _write_sectioned_submission(
    path: Path, *, name: str, student_id: str, section: str, points: float
) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f'student_name: "{name}"',
                f'student_id: "{student_id}"',
                'course_code: "CMPE"',
                'assignment_title: "Quiz"',
                'assignment_version: ""',
                f'section: "{section}"',
                f'source_pdf: "{name}.pdf"',
                'student_score: ""',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: Convert.",
                "  - Response image: ",
                "  - Response text: an answer",
                "  - Possible points: ",
                f"  - Points: {points}",
                "  - Rubric: Correct",
                "  - Analysis: ",
                "",
            ]
        )
    )


def _sectioned_provider(tmp_path: Path) -> SubmissionFilesProvider:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    subs = tmp_path / "student-submissions"
    subs.mkdir()
    _write_sectioned_submission(
        subs / "a.submission.md", name="Ann", student_id="1", section="MW", points=10
    )
    _write_sectioned_submission(
        subs / "b.submission.md", name="Bob", student_id="2", section="MW", points=0
    )
    _write_sectioned_submission(
        subs / "c.submission.md", name="Cy", student_id="3", section="TTh", points=5
    )
    return SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=subs,
        default_image_path=DEFAULT_IMAGE_PATH,
    )


def test_available_sections_lists_distinct_sorted(tmp_path: Path) -> None:
    provider = _sectioned_provider(tmp_path)
    assert provider.available_sections() == ["MW", "TTh"]


def test_available_sections_empty_without_sections(tmp_path: Path) -> None:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    subs = tmp_path / "student-submissions"
    subs.mkdir()
    _write_submission_without_possible_points(
        subs / "a.submission.md", name="Ann", student_id="1", points=10
    )
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=subs,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    assert provider.available_sections() == []


def test_item_analysis_all_sections_covers_whole_cohort(tmp_path: Path) -> None:
    provider = _sectioned_provider(tmp_path)
    report = provider.build_item_analysis_report()
    assert report.class_size == 3


def test_item_analysis_scoped_to_section(tmp_path: Path) -> None:
    provider = _sectioned_provider(tmp_path)
    mw = provider.build_item_analysis_report(section="MW")
    assert mw.class_size == 2
    tth = provider.build_item_analysis_report(section="TTh")
    assert tth.class_size == 1


def _page_context_fixture(
    tmp_path: Path,
    *,
    pages: int,
    extra_pages_frontmatter: str = "",
) -> SubmissionFilesProvider:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    split_dir = tmp_path / "split-submissions"
    split_dir.mkdir()
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=300, height=400)
    doc.save(split_dir / "student.pdf")
    doc.close()

    submissions_path = tmp_path / "student-submissions"
    submissions_path.mkdir()
    md = submissions_path / "student.submission.md"
    _write_submission_with_source_metadata(
        md, source_pdf="../split-submissions/student.pdf"
    )
    if extra_pages_frontmatter:
        md.write_text(
            md.read_text().replace(
                "student_score:", f"{extra_pages_frontmatter}\nstudent_score:", 1
            )
        )

    return SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )


def test_page_context_starts_with_the_question_source_page(tmp_path: Path) -> None:
    provider = _page_context_fixture(tmp_path, pages=1)
    seed = provider.build_question_seeds(student_item_id="s1")[0]

    assert seed.source_pdf_path.endswith("student.pdf")
    assert [(e.page_number, e.kind) for e in seed.page_context] == [(1, "response")]
    assert seed.page_context[0].highlights == ((0.2, 0.3, 0.5, 0.2),)


def test_uncovered_pages_are_offered_as_extra_work(tmp_path: Path) -> None:
    provider = _page_context_fixture(tmp_path, pages=3)
    seed = provider.build_question_seeds(student_item_id="s1")[0]

    assert [(e.page_number, e.kind) for e in seed.page_context] == [
        (1, "response"),
        (2, "extra"),
        (3, "extra"),
    ]
    assert "outside the answer crops" in seed.warnings[0]


def test_extra_pages_frontmatter_wins_over_inference(tmp_path: Path) -> None:
    provider = _page_context_fixture(
        tmp_path, pages=3, extra_pages_frontmatter="extra_pages: [3]"
    )
    seed = provider.build_question_seeds(student_item_id="s1")[0]

    assert [(e.page_number, e.kind) for e in seed.page_context] == [
        (1, "response"),
        (3, "extra"),
    ]


def test_extra_pages_are_clamped_to_the_pdf(tmp_path: Path) -> None:
    provider = _page_context_fixture(
        tmp_path, pages=2, extra_pages_frontmatter="extra_pages: [2, 9]"
    )
    seed = provider.build_question_seeds(student_item_id="s1")[0]

    assert [e.page_number for e in seed.page_context] == [1, 2]


def test_missing_source_pdf_yields_no_page_context(tmp_path: Path) -> None:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    submissions_path = tmp_path / "student-submissions"
    submissions_path.mkdir()
    _write_submission_with_source_metadata(
        submissions_path / "student.submission.md", source_pdf="../missing.pdf"
    )
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    seed = provider.build_question_seeds(student_item_id="s1")[0]
    assert seed.page_context == ()
    assert seed.source_pdf_path == ""


def _write_mc_qa(path: Path, questions: int) -> None:
    lines = ["---", "title: Pattern", "---", ""]
    for number in range(1, questions + 1):
        lines += [
            f"- (1 points) Question {number}?",
            "  - **Choices**",
            "    - A) option A",
            "    - B) option B",
            "    - C) option C",
            "    - D) option D",
            "  - **Correct**",
            "    - A",
            "",
        ]
    path.write_text("\n".join(lines))


def _write_mc_submission(path: Path, *, student_id: str, name: str, answers: list[str]) -> None:
    lines = [
        "---",
        f'student_name: "{name}"',
        f'student_id: "{student_id}"',
        'course_code: "CS146"',
        'assignment_title: "Quiz"',
        'assignment_version: ""',
        f'source_pdf: "{student_id}.pdf"',
        'student_score: ""',
        "---",
        "",
    ]
    for position, answer in enumerate(answers, start=1):
        lines += [
            f"- q{position}:",
            "  - Question type: Multiple Choice",
            "  - Prompt: ",
            f"  - Response text: {answer}",
            "  - Possible points: 1",
            "  - Points: ",
            "  - Rubric:",
            "  - Analysis: ",
            "",
        ]
    path.write_text("\n".join(lines))


def _mc_cohort(tmp_path: Path, *, copier: bool = True) -> SubmissionFilesProvider:
    import random

    rng = random.Random(11)
    qa_path = tmp_path / "qa.md"
    _write_mc_qa(qa_path, 40)
    submissions = tmp_path / "subs"
    submissions.mkdir()
    answers = {
        index: ["A" if rng.random() < 0.65 else rng.choice("BCD") for _ in range(40)]
        for index in range(40)
    }
    if copier:
        answers[1] = list(answers[0])
    for index, row in answers.items():
        _write_mc_submission(
            submissions / f"s{index}.submission.md",
            student_id=f"{index:03d}",
            name=f"Student {index:03d}",
            answers=row,
        )
    return SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions,
        default_image_path=DEFAULT_IMAGE_PATH,
    )


def test_answer_pattern_report_binds_members_to_nav_ids(tmp_path) -> None:
    provider = _mc_cohort(tmp_path)

    view = provider.build_answer_pattern_report()

    assert len(view.items) == 1
    item = view.items[0]
    assert {member.student_id for member in item.members} == {"000", "001"}
    assert all(member.is_navigable for member in item.members)
    assert all(member.student_item_id.startswith("s") for member in item.members)
    assert item.first_question_id == item.shared_wrong[0].question_id


def test_answer_pattern_report_carries_its_own_scope(tmp_path) -> None:
    provider = _mc_cohort(tmp_path, copier=False)

    view = provider.build_answer_pattern_report()

    assert view.items == ()
    assert view.analyzed
    assert view.analyzed_question_count == 40
    assert view.pairs_compared > 0
    assert view.threshold > 0
    assert view.expected_by_chance > 0


def test_answer_pattern_report_needs_no_answer_key_argument() -> None:
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path="samples/cs146-quiz1/student-submissions",
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    view = provider.build_answer_pattern_report()

    assert view.items == ()
    assert not view.analyzed


def test_a_correction_records_the_reading_it_replaced_as_ground_truth(
    tmp_path: Path,
) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    markdown_path = (
        submissions_path / "submission-0001" / "submission-0001.submission.md"
    )
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    update_student_submission_file(
        markdown_path,
        StudentSubmissionGradeUpdate(
            question_id="q1",
            response_text="Ohm's Iaw",
            response_confidence=42.0,
            response_source="tesseract",
        ),
    )
    provider.reload_all()

    provider.save_question_transcription(
        student_item_id="s1",
        question_id="q1",
        response_text="Ohm's law",
    )

    entry = next(
        entry for entry in provider.read_grade_history() if entry.kind == "transcription"
    )
    assert entry.old["response_text"] == "Ohm's Iaw"
    assert entry.old["response_confidence"] == 42.0
    assert entry.old["response_source"] == "tesseract"
    assert entry.new["response_source"] == "human"

    saved = load_student_submission_markdown(markdown_path)
    assert saved.questions[0].response_source == "human"


def test_undoing_a_correction_restores_the_whole_reading(tmp_path: Path) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    markdown_path = (
        submissions_path / "submission-0001" / "submission-0001.submission.md"
    )
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    update_student_submission_file(
        markdown_path,
        StudentSubmissionGradeUpdate(
            question_id="q1",
            response_text="model reading",
            clear_response_confidence=True,
            response_source="vision-model",
        ),
    )
    provider.reload_all()

    provider.save_question_transcription(
        student_item_id="s1", question_id="q1", response_text="human reading"
    )
    provider.save_question_transcription(
        student_item_id="s1",
        question_id="q1",
        response_text="model reading",
        response_confidence=None,
        response_source="vision-model",
    )

    saved = load_student_submission_markdown(markdown_path)
    assert saved.questions[0].response_text == "model reading"
    assert saved.questions[0].response_source == "vision-model"
    assert saved.questions[0].response_confidence is None


def test_review_items_include_grades_awarded_before_a_rubric_edit(
    tmp_path: Path,
) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    student_id = load_student_submission_markdown(
        submissions_path / "submission-0001" / "submission-0001.submission.md"
    ).student_id

    journal = GradeJournal(resolve_history_dir(submissions_path), actor="prof_kim")
    journal.append(
        kind="grade",
        student_id=student_id,
        student_name="",
        question_id="q1",
        summary="8/10",
        old={"points": None, "rubric": []},
        new={"points": 8.0, "rubric": ["Correct"]},
    )
    journal.append(
        kind="rubric",
        student_id="",
        student_name="",
        question_id="q1",
        summary="Rubric edited",
        old={"options": ["Correct", "Incorrect"]},
        new={"options": ["Correct", "Partial", "Incorrect"]},
    )

    items = provider.build_review_items()
    stale = [
        item
        for item in items
        if item.reason is ReviewReason.GRADED_BEFORE_RUBRIC_CHANGE
    ]

    assert len(stale) == 1
    assert stale[0].question_id == "q1"
    assert stale[0].student_item_id == "s1"
    readiness = provider.build_readiness_report()
    assert any(
        issue.kind.value == "stale_rubric_grades" for issue in readiness.issues
    )


def _write_nested_submission(submissions_path: Path, key: str, *, name: str) -> None:
    submission_dir = submissions_path / key
    submission_dir.mkdir(parents=True, exist_ok=True)
    _write_simple_submission(submission_dir / f"{key}.submission.md", name=name)


def test_load_new_submissions_appends_only_new_submissions(tmp_path: Path) -> None:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    submissions_path = tmp_path / "submissions"
    submissions_path.mkdir()
    _write_nested_submission(submissions_path, "student-1", name="Student 1")

    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    assert [s.full_name for s in provider.list_students(1)] == ["Student 1"]

    _write_nested_submission(submissions_path, "student-2", name="Student 2")
    result = provider.load_new_submissions(["student-2"])

    assert result.added == ("Student 2",)
    assert result.skipped == ()
    assert [s.full_name for s in provider.list_students(1)] == [
        "Student 1",
        "Student 2",
    ]

    again = provider.load_new_submissions(["student-2"])
    assert again.added == ()
    assert [s.full_name for s in provider.list_students(1)] == [
        "Student 1",
        "Student 2",
    ]


def test_load_new_submissions_leaves_existing_recorded_mtimes_untouched(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    submissions_path = tmp_path / "submissions"
    submissions_path.mkdir()
    _write_nested_submission(submissions_path, "student-1", name="Student 1")

    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    original_mtime = provider._submissions[0].grading.mtime_ns
    original_object = provider._submissions[0]

    _write_nested_submission(submissions_path, "student-2", name="Student 2")
    provider.load_new_submissions(["student-2"])

    assert provider._submissions[0] is original_object
    assert provider._submissions[0].grading.mtime_ns == original_mtime

    provider.save_question_grade(
        student_item_id="s1",
        question_id="q1",
        possible_points=10,
        points_awarded=10,
        rubric_labels=("Correct",),
    )


def test_load_new_submissions_ignores_a_key_with_no_file_yet(tmp_path: Path) -> None:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    submissions_path = tmp_path / "submissions"
    submissions_path.mkdir()
    _write_nested_submission(submissions_path, "student-1", name="Student 1")

    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    result = provider.load_new_submissions(["student-2"])
    assert result == provider.load_new_submissions([])
    assert [s.full_name for s in provider.list_students(1)] == ["Student 1"]


def _write_multi_version_qa(qa_path: Path) -> None:
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

- (10 points) Convert B, part 1.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
- (10 points) Convert B, part 2.
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""
    )


def test_load_new_submissions_skips_a_mismatched_question_set_instead_of_raising(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    _write_multi_version_qa(qa_path)
    submissions_path = tmp_path / "submissions"
    submissions_path.mkdir()
    submission_dir = submissions_path / "student-a"
    submission_dir.mkdir()
    _write_simple_submission(
        submission_dir / "student-a.submission.md", name="Student A", version="A"
    )

    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    assert [s.full_name for s in provider.list_students(1)] == ["Student A"]

    _write_nested_submission(submissions_path, "student-b", name="Student B")
    (submissions_path / "student-b" / "student-b.submission.md").write_text(
        (submissions_path / "student-b" / "student-b.submission.md")
        .read_text()
        .replace('assignment_version: ""', 'assignment_version: "B"')
    )

    result = provider.load_new_submissions(["student-b"])

    assert result.added == ()
    assert result.skipped == ("Student B",)
    assert [s.full_name for s in provider.list_students(1)] == ["Student A"]


def test_empty_folder_message_names_a_wrong_path_when_extraction_never_ran(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    with pytest.raises(ValueError, match="no \\*\\.submission\\.md files found"):
        SubmissionFilesProvider(
            qa_file_path=qa_path,
            submissions_path=empty_dir,
            default_image_path=DEFAULT_IMAGE_PATH,
        )


def test_empty_folder_message_says_extraction_in_progress_when_manifest_is_live(
    tmp_path: Path,
) -> None:
    from opengrader.core.submissions.manifest import ExtractionRecord, append_record

    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    append_record(
        output_dir,
        ExtractionRecord(
            key="student-1",
            source="/scans/student-1.pdf",
            source_sha256="aaa",
            settings_id="sss",
        ),
    )

    with pytest.raises(ValueError, match="Extraction is in progress"):
        SubmissionFilesProvider(
            qa_file_path=qa_path,
            submissions_path=output_dir,
            default_image_path=DEFAULT_IMAGE_PATH,
        )


def _spy(monkeypatch: pytest.MonkeyPatch, name: str) -> list[int]:
    import opengrader.tui.data.submission_files_provider as provider_module

    calls: list[int] = []
    original = getattr(provider_module, name)

    def wrapper(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(provider_module, name, wrapper)
    return calls


def test_similarity_report_is_memoized_until_a_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submissions_path = tmp_path / "student-submissions"
    submissions_path.mkdir()
    _write_graded_submission(
        submissions_path / "ann.submission.md",
        student_id="111111111",
        student_name="Ann Smith",
        response="Paris",
        points=50,
    )
    _write_graded_submission(
        submissions_path / "bob.submission.md",
        student_id="222222222",
        student_name="Bob Lee",
        response="Paris, France",
        points=50,
    )
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cmpe195a-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    calls = _spy(monkeypatch, "build_similarity_flags")

    first = provider.build_similarity_report()
    second = provider.build_similarity_report()

    assert first == second
    assert len(calls) == 1, "a repeat call must not recompute the O(n^2) audit"

    provider.save_question_grade(
        student_item_id="s1",
        question_id="q1",
        possible_points=50,
        points_awarded=40,
        rubric_labels=("Partially Correct",),
    )
    provider.build_similarity_report()
    assert len(calls) == 2, "a grade save must invalidate the cached report"


@pytest.mark.parametrize("pdf_failure", [False, True])
def test_item_analysis_report_is_memoized_until_a_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pdf_failure: bool
) -> None:
    qa_path = tmp_path / "qa.md"
    _write_simple_qa(qa_path)
    submissions_path = tmp_path / "student-submissions"
    submissions_path.mkdir()
    _write_submission_without_possible_points(
        submissions_path / "a.submission.md", name="Ann", student_id="1", points=10
    )
    _write_submission_without_possible_points(
        submissions_path / "b.submission.md", name="Bob", student_id="2", points=0
    )
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    calls = _spy(monkeypatch, "build_item_analysis")

    first = provider.build_item_analysis_report()
    second = provider.build_item_analysis_report()

    assert first == second
    assert len(calls) == 1
    assert first.items[0].difficulty == 0.5

    if pdf_failure:
        def broken_pdf(*args: object, **kwargs: object) -> Path:
            raise OSError("PDF is read-only")

        monkeypatch.setattr(FileGradingSession, "sync_pdf_annotations", broken_pdf)

    with pytest.raises(OSError) if pdf_failure else nullcontext():
        provider.save_question_grade(
            student_item_id="s1",
            question_id="q1",
            possible_points=10,
            points_awarded=5,
            rubric_labels=(),
        )
    updated = provider.build_item_analysis_report()
    assert updated.items[0].difficulty == 0.25
    assert len(calls) == 2


def test_item_analysis_section_argument_is_not_confused_with_unscoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = _sectioned_provider(tmp_path)
    calls = _spy(monkeypatch, "build_item_analysis")

    everyone = provider.build_item_analysis_report()
    mw_only = provider.build_item_analysis_report(section="MW")
    everyone_again = provider.build_item_analysis_report()
    mw_again = provider.build_item_analysis_report(section="MW")

    assert everyone.class_size == 3
    assert mw_only.class_size == 2
    assert everyone_again == everyone
    assert mw_again == mw_only
    assert len(calls) == 2


def test_outcome_mastery_section_argument_is_not_confused_with_unscoped(
    tmp_path: Path,
) -> None:
    qa_path = tmp_path / "qa.md"
    qa_path.write_text(
        "\n".join(
            [
                "---",
                "title: Quiz",
                "---",
                "",
                "- (10 points) Convert.",
                "  - **Outcome** LO1",
                "  - **Rubric**",
                "    - Correct (+0)",
                "    - Partially Correct (-5)",
                "    - Incorrect (-10)",
                "",
            ]
        )
    )
    subs = tmp_path / "student-submissions"
    subs.mkdir()
    _write_sectioned_submission(
        subs / "a.submission.md", name="Ann", student_id="1", section="MW", points=10
    )
    _write_sectioned_submission(
        subs / "b.submission.md", name="Bob", student_id="2", section="MW", points=0
    )
    _write_sectioned_submission(
        subs / "c.submission.md", name="Cy", student_id="3", section="TTh", points=5
    )
    provider = SubmissionFilesProvider(
        qa_file_path=qa_path,
        submissions_path=subs,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    everyone = provider.build_outcome_mastery_report()
    mw_only = provider.build_outcome_mastery_report(section="MW")

    assert everyone is not None and mw_only is not None
    assert everyone.class_size == 3
    assert mw_only.class_size == 2
    assert provider.build_outcome_mastery_report().class_size == 3


def test_filing_a_regrade_invalidates_the_review_queue(tmp_path: Path) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )

    before = provider.build_review_items()
    assert not any(item.reason is ReviewReason.OPEN_REGRADE_REQUEST for item in before)

    provider.file_regrade(student_id="987654321", reason="Q1 method valid", question_id="q1")

    after = provider.build_review_items()
    assert any(item.reason is ReviewReason.OPEN_REGRADE_REQUEST for item in after)

    provider.resolve_regrade(
        next(
            item
            for item in provider.build_regrade_items()
            if item.question_id == "q1"
        ).request_id
    )
    resolved = provider.build_review_items()
    assert not any(item.reason is ReviewReason.OPEN_REGRADE_REQUEST for item in resolved)


def test_reload_all_invalidates_the_similarity_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submissions_path = tmp_path / "student-submissions"
    submissions_path.mkdir()
    _write_graded_submission(
        submissions_path / "ann.submission.md",
        student_id="111111111",
        student_name="Ann Smith",
        response="Paris",
        points=50,
    )
    _write_graded_submission(
        submissions_path / "bob.submission.md",
        student_id="222222222",
        student_name="Bob Lee",
        response="Paris, France",
        points=50,
    )
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cmpe195a-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    calls = _spy(monkeypatch, "build_similarity_flags")

    provider.build_similarity_report()
    provider.build_similarity_report()
    assert len(calls) == 1

    _write_graded_submission(
        submissions_path / "bob.submission.md",
        student_id="222222222",
        student_name="Bob Lee",
        response="a completely different answer",
        points=10,
    )
    provider.reload_all()
    provider.build_similarity_report()
    assert len(calls) == 2, "reload_all must invalidate the cached report"


def test_review_queue_notices_another_process_writing_the_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    submissions_path = _copy_sample_submissions(tmp_path)
    provider = SubmissionFilesProvider(
        qa_file_path="samples/cs146-quiz1/qa.md",
        submissions_path=submissions_path,
        default_image_path=DEFAULT_IMAGE_PATH,
    )
    calls = _spy(monkeypatch, "read_journal")

    provider.build_review_items()
    after_first = len(calls)
    provider.build_review_items()
    assert len(calls) == after_first, "a repeat call must be served from the cache"

    GradeJournal(
        resolve_history_dir(submissions_path), actor="ta_other", session_id="other"
    ).append(
        kind="grade",
        student_id="987654321",
        student_name="Andy Cortes",
        question_id="q1",
        summary="6/10 pts",
    )

    provider.build_review_items()
    assert len(calls) > after_first, "an outside journal write must invalidate the cache"
