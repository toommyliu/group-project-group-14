from __future__ import annotations

from pathlib import Path

import pytest

from opengrader.cli.transcribe_eval import main, run_cli
from opengrader.core.history import AspectValue, GradeJournal, journal_payload, resolve_history_dir
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    render_student_submission_markdown,
)


def _write_cohort(root: Path, *, questions: int = 4) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    document = StudentSubmissionDocument(
        student_name="Tommy Liu",
        student_id="012345",
        course_code="CS146",
        assignment_title="Quiz 1",
        assignment_version=None,
        source_pdf="submission-0001.pdf",
        questions=[
            StudentSubmissionQuestion(
                question_id=f"q{index + 1}",
                prompt="Prompt",
                possible_points=None,
                response_image_path=f"responses/q{index + 1}.png",
                response_text="an answer",
            )
            for index in range(questions)
        ],
    )
    (root / "tommy.submission.md").write_text(
        render_student_submission_markdown(document), encoding="utf-8"
    )
    return root


def _correct(
    root: Path,
    *,
    question_id: str,
    machine: str,
    human: str,
    confidence: float | None = 42.0,
    source: str | None = "tesseract",
    actor: str = "prof_kim",
) -> None:
    journal = GradeJournal(resolve_history_dir(root), actor=actor)
    journal.append(
        kind="transcription",
        student_id="012345",
        student_name="Tommy Liu",
        question_id=question_id,
        summary="fixed",
        old=journal_payload(
            "transcription",
            AspectValue(text=machine, confidence=confidence, source=source),
        ),
        new=journal_payload(
            "transcription", AspectValue(text=human, confidence=100.0, source="human")
        ),
    )


def test_reports_accuracy_from_recorded_corrections(tmp_path: Path, capsys) -> None:
    root = _write_cohort(tmp_path / "subs")
    _correct(root, question_id="q1", machine="teh cat", human="the cat")
    _correct(root, question_id="q2", machine="the cat", human="the cat")

    report = run_cli(["--input", str(root)])
    out = capsys.readouterr().out

    assert report is not None
    assert report.overall.samples == 2
    assert report.overall.exact == 1
    assert "Checked 2 of 4 transcribed answers (50%)" in out
    assert "Tesseract OCR" in out


def test_writes_nothing_at_all(tmp_path: Path) -> None:
    root = _write_cohort(tmp_path / "subs")
    _correct(root, question_id="q1", machine="teh cat", human="the cat")

    before = {
        path: path.stat().st_mtime_ns
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }

    run_cli(["--input", str(root)])

    after = {
        path: path.stat().st_mtime_ns
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    assert before == after


def test_a_tool_re_read_is_excluded_and_explained(tmp_path: Path, capsys) -> None:
    root = _write_cohort(tmp_path / "subs")
    _correct(
        root,
        question_id="q1",
        machine="xxx",
        human="yyy",
        actor="submission-extractor:refine",
    )

    report = run_cli(["--input", str(root)])
    out = capsys.readouterr().out

    assert report is not None
    assert report.overall.samples == 0
    assert "No corrected transcriptions found" in out
    assert "only ever re-read by a tool" in out


def test_json_carries_the_full_uncollapsed_curve(tmp_path: Path, capsys) -> None:
    import json

    root = _write_cohort(tmp_path / "subs")
    _correct(root, question_id="q1", machine="teh cat", human="the cat", confidence=30.0)

    run_cli(["--input", str(root), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["overall"]["samples"] == 1
    assert payload["coverage"]["answers_with_text"] == 4
    assert len(payload["floor_curve"]) == 21
    assert payload["worst"][0]["machine_text"] == "teh cat"


def test_pooling_two_cohorts_counts_each_once(tmp_path: Path) -> None:
    first = _write_cohort(tmp_path / "fall")
    second = _write_cohort(tmp_path / "spring")
    _correct(first, question_id="q1", machine="teh cat", human="the cat")
    _correct(second, question_id="q1", machine="teh dog", human="the dog")

    report = run_cli(["--input", str(first), "--input", str(second)])

    assert report is not None
    assert report.overall.samples == 2
    assert report.coverage.answers_with_text == 8


def test_a_folder_named_twice_is_counted_once(tmp_path: Path, capsys) -> None:
    root = _write_cohort(tmp_path / "subs")
    _correct(root, question_id="q1", machine="teh cat", human="the cat")

    report = run_cli(["--input", str(root), "--input", str(root)])
    err = capsys.readouterr().err

    assert report is not None
    assert report.overall.samples == 1
    assert "named more than once" in err


def test_a_missing_folder_is_a_warning_not_a_crash(tmp_path: Path, capsys) -> None:
    report = run_cli(["--input", str(tmp_path / "nope")])
    err = capsys.readouterr().err

    assert report is not None
    assert "not found" in err


@pytest.mark.parametrize(
    "argv",
    [
        ["--target-catch", "0"],
        ["--target-catch", "1.5"],
        ["--tolerance", "-1"],
    ],
)
def test_bad_arguments_are_refused(tmp_path: Path, argv: list[str], capsys) -> None:
    root = _write_cohort(tmp_path / "subs")

    assert run_cli(["--input", str(root), *argv]) is None
    assert "error:" in capsys.readouterr().err


def test_main_returns_an_exit_code_not_a_report(tmp_path: Path) -> None:
    root = _write_cohort(tmp_path / "subs")

    assert main(["--input", str(root)]) == 0
    assert main(["--input", str(root), "--target-catch", "9"]) == 1


def test_pooled_cohorts_keep_students_apart(tmp_path: Path) -> None:
    fall = _write_cohort(tmp_path / "fall-2025" / "subs")
    spring = _write_cohort(tmp_path / "spring-2026" / "subs")
    _correct(fall, question_id="q1", machine="teh cat", human="the cat")
    _correct(spring, question_id="q1", machine="teh dog", human="the dog")

    report = run_cli(["--input", str(fall), "--input", str(spring)])

    assert report is not None
    assert report.overall.samples == 2
    assert {sample.cohort for sample in report.worst} == {
        "fall-2025/subs",
        "spring-2026/subs",
    }


def test_a_single_cohort_carries_no_label(tmp_path: Path) -> None:
    root = _write_cohort(tmp_path / "subs")
    _correct(root, question_id="q1", machine="teh cat", human="the cat")

    report = run_cli(["--input", str(root)])

    assert report is not None
    assert report.worst[0].cohort == ""
