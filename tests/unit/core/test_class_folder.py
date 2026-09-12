from __future__ import annotations

from pathlib import Path

import pytest

from opengrader.core.class_folder import (
    GRADING_ROLES,
    ROLE_QA,
    ROLE_SUBMISSIONS,
    ROLE_ROSTER,
    ROLE_SCAN,
    ROLE_TEMPLATE,
    describe_class_folder,
    scan_class_folder,
)

REPO = Path(__file__).resolve().parents[3]

QA_TEXT = """---
title: Quiz
---

- (10 points) What is 2 + 2?
  - **Correct**
    - 4
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""


def _touch(path: Path, text: str = "%PDF-1.4\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_every_role_is_found_in_a_tidy_class_folder(tmp_path) -> None:
    scan = _touch(tmp_path / "scans.pdf")
    qa = _touch(tmp_path / "qa.md", QA_TEXT)
    roster = _touch(tmp_path / "roster.tsv", "1\tAda\n2\tGrace\n")
    template = _touch(tmp_path / "blank-template.pdf")

    found = scan_class_folder(tmp_path)

    assert found.scan == scan
    assert found.qa_file == qa
    assert found.roster == roster
    assert found.blank_template == template
    assert found.ambiguities == ()


def test_markdown_that_is_not_an_answer_key_is_ignored(tmp_path) -> None:
    _touch(tmp_path / "scan.pdf")
    _touch(tmp_path / "notes.md", "# Notes\n\nbring pencils\n")
    _touch(tmp_path / "s1.submission.md", QA_TEXT)
    key = _touch(tmp_path / "midterm.md", QA_TEXT)

    assert scan_class_folder(tmp_path).qa_file == key


def test_apple_double_and_hidden_files_are_never_candidates(tmp_path) -> None:
    scan = _touch(tmp_path / "scan.pdf")
    _touch(tmp_path / "._scan.pdf")
    _touch(tmp_path / ".hidden.pdf")
    _touch(tmp_path / "._qa.md", QA_TEXT)

    found = scan_class_folder(tmp_path)
    assert found.scan == scan
    assert found.qa_file is None
    assert found.ambiguities == ()


def test_a_conventional_answer_key_name_breaks_a_tie(tmp_path) -> None:
    _touch(tmp_path / "scan.pdf")
    _touch(tmp_path / "old-version.md", QA_TEXT)
    solutions = _touch(tmp_path / "solutions.md", QA_TEXT)

    assert scan_class_folder(tmp_path).qa_file == solutions


def test_two_answer_keys_with_no_tiebreak_are_ambiguous(tmp_path) -> None:
    _touch(tmp_path / "scan.pdf")
    _touch(tmp_path / "version-a.md", QA_TEXT)
    _touch(tmp_path / "version-b.md", QA_TEXT)

    found = scan_class_folder(tmp_path)
    assert found.qa_file is None
    ambiguity = found.ambiguity(ROLE_QA)
    assert ambiguity is not None
    assert [path.name for path in ambiguity.candidates] == ["version-a.md", "version-b.md"]


def test_two_unlabelled_pdfs_are_ambiguous_rather_than_guessed(tmp_path) -> None:
    _touch(tmp_path / "period1.pdf")
    _touch(tmp_path / "period2.pdf")

    found = scan_class_folder(tmp_path)
    assert found.scan is None
    assert found.ambiguity(ROLE_SCAN) is not None


def test_a_scan_word_in_the_name_picks_the_bundle(tmp_path) -> None:
    _touch(tmp_path / "syllabus.pdf")
    scan = _touch(tmp_path / "submissions.pdf")

    assert scan_class_folder(tmp_path).scan == scan


def test_an_answer_key_pdf_is_not_mistaken_for_the_scan(tmp_path) -> None:
    scan = _touch(tmp_path / "exam.pdf")
    _touch(tmp_path / "exam-answers.pdf")

    found = scan_class_folder(tmp_path)
    assert found.scan == scan
    assert any("exam-answers.pdf" in note for note in found.notes)


def test_without_a_bundle_a_folder_of_split_pdfs_is_the_scan(tmp_path) -> None:
    _touch(tmp_path / "split" / "submission-0001.pdf")
    _touch(tmp_path / "submissions" / "submission-0001" / "stray.pdf")

    found = scan_class_folder(tmp_path)
    assert found.scan == tmp_path / "split"
    assert found.ambiguities == ()


def test_a_bundle_wins_over_a_previous_runs_split_folder(tmp_path) -> None:
    scan = _touch(tmp_path / "bundle.pdf")
    _touch(tmp_path / "split" / "submission-0001.pdf")

    assert scan_class_folder(tmp_path).scan == scan


def test_a_tab_separated_txt_is_a_roster_but_prose_is_not(tmp_path) -> None:
    _touch(tmp_path / "scan.pdf")
    _touch(tmp_path / "readme.txt", "Grade these by Friday.\n")
    roster = _touch(tmp_path / "period1.txt", "1\tAda\n2\tGrace\n")

    assert scan_class_folder(tmp_path).roster == roster


def test_a_csv_roster_is_explained_not_used(tmp_path) -> None:
    _touch(tmp_path / "scan.pdf")
    _touch(tmp_path / "roster.csv", "id,name\n1,Ada\n")

    found = scan_class_folder(tmp_path)
    assert found.roster is None
    assert any("tab-separated" in note for note in found.notes)


def test_two_templates_are_reported(tmp_path) -> None:
    _touch(tmp_path / "scan.pdf")
    _touch(tmp_path / "template-vA.pdf")
    _touch(tmp_path / "template-vB.pdf")

    found = scan_class_folder(tmp_path)
    assert found.blank_template is None
    assert found.ambiguity(ROLE_TEMPLATE) is not None
    assert found.scan == tmp_path / "scan.pdf"


def test_a_missing_folder_raises(tmp_path) -> None:
    with pytest.raises(NotADirectoryError):
        scan_class_folder(tmp_path / "nope")


def test_the_description_has_one_line_per_role_plus_notes(tmp_path) -> None:
    _touch(tmp_path / "scan.pdf")
    _touch(tmp_path / "template-vA.pdf")
    _touch(tmp_path / "template-vB.pdf")
    _touch(tmp_path / "roster.csv", "id,name\n")

    lines = describe_class_folder(scan_class_folder(tmp_path))
    assert lines[0].startswith("✓ scanned bundle") and lines[0].endswith("scan.pdf")
    assert lines[1].startswith("· answer key")
    assert lines[2].startswith("· roster")
    assert lines[3].startswith("? blank template")
    assert "template-vA.pdf, template-vB.pdf" in lines[3]
    assert lines[4].startswith("! roster.csv")


def test_a_real_sample_class_folder(tmp_path) -> None:
    sample = REPO / "samples" / "cmpe30-quiz1"
    if not sample.is_dir():  # pragma: no cover
        pytest.skip("sample data not present")

    found = scan_class_folder(sample)
    assert found.scan == sample / "submissions.pdf"
    assert found.qa_file == sample / "qa.md"
    assert found.found(ROLE_ROSTER) is None
    assert found.submissions == sample / "student-submissions"
    assert found.ambiguities == ()


def _student(cohort: Path, name: str) -> None:
    _touch(cohort / name / f"{name}.submission.md", "---\n---\n")


def test_the_pipelines_output_is_found_as_the_submissions(tmp_path) -> None:
    _student(tmp_path / "submissions", "submission-0001")
    _student(tmp_path / "submissions", "submission-0002")

    assert scan_class_folder(tmp_path).submissions == tmp_path / "submissions"


def test_a_grader_packet_opens_as_a_class_folder(tmp_path) -> None:
    qa = _touch(tmp_path / "qa-multi.md", QA_TEXT)
    _touch(tmp_path / "README.txt", "Opengrader grader packet\n")
    _student(tmp_path / "submissions", "og-1a2b3c4d")

    found = scan_class_folder(tmp_path)
    assert found.qa_file == qa
    assert found.submissions == tmp_path / "submissions"


def test_pointing_at_the_submissions_folder_itself_uses_it(tmp_path) -> None:
    _student(tmp_path, "submission-0001")
    _student(tmp_path, "submission-0002")

    found = scan_class_folder(tmp_path)
    assert found.submissions == tmp_path
    assert describe_class_folder(found, GRADING_ROLES)[1] == "✓ submissions     this folder"


def test_flat_submission_files_make_the_folder_itself_the_submissions(tmp_path) -> None:
    _touch(tmp_path / "a.submission.md", "---\n---\n")

    assert scan_class_folder(tmp_path).submissions == tmp_path


def test_the_pipelines_own_folder_breaks_a_tie(tmp_path) -> None:
    _student(tmp_path / "student-submissions", "submission-0001")
    _student(tmp_path / "submissions", "submission-0001")

    assert scan_class_folder(tmp_path).submissions == tmp_path / "submissions"


def test_two_unrelated_cohorts_are_ambiguous(tmp_path) -> None:
    _student(tmp_path / "period1", "submission-0001")
    _student(tmp_path / "period2", "submission-0001")

    found = scan_class_folder(tmp_path)
    assert found.submissions is None
    ambiguity = found.ambiguity(ROLE_SUBMISSIONS)
    assert ambiguity is not None
    assert [path.name for path in ambiguity.candidates] == ["period1", "period2"]


def test_a_folder_with_no_submissions_yet_says_so(tmp_path) -> None:
    _touch(tmp_path / "scan.pdf")

    found = scan_class_folder(tmp_path)
    assert found.submissions is None
    assert describe_class_folder(found, GRADING_ROLES, include_notes=False) == [
        "· answer key      none found",
        "· submissions     none found",
    ]
