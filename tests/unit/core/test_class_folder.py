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
    find_answer_key_source,
    find_assignment_folders,
    is_workspace,
    scan_class_folder,
    section_key,
    section_name,
)


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
    # Names that say nothing; ones naming a section each are section scans.
    _touch(tmp_path / "morning.pdf")
    _touch(tmp_path / "afternoon.pdf")

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


def test_a_csv_roster_with_its_heading_row_is_used(tmp_path) -> None:
    _touch(tmp_path / "scan.pdf")
    _touch(tmp_path / "roster.csv", "First Name,Last Name,SID\nAda,Lovelace,1\n")

    found = scan_class_folder(tmp_path)

    assert found.roster == tmp_path / "roster.csv"
    assert found.notes == ()


def test_find_roster_is_the_scans_answer_without_the_rest_of_the_scan(tmp_path) -> None:
    from opengrader.core.class_folder import find_roster

    assert find_roster(tmp_path) is None
    _touch(tmp_path / "enrolled.tsv", "1\tAda Lovelace\n")
    assert find_roster(tmp_path) == tmp_path / "enrolled.tsv"
    # Two files that could each be it are no answer, exactly as in the scan.
    _touch(tmp_path / "other.tsv", "2\tGrace Hopper\n")
    assert find_roster(tmp_path) is None
    assert scan_class_folder(tmp_path).roster is None


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


def test_the_instructors_solutions_are_offered_as_the_answer_key_source(tmp_path) -> None:
    _touch(tmp_path / "scans.pdf")
    _touch(tmp_path / "blank-template.pdf")
    source = _touch(tmp_path / "Quiz 1 Solutions.docx", "not really a docx")

    assert find_answer_key_source(tmp_path) == source


def test_an_answer_key_source_is_never_guessed(tmp_path) -> None:
    # Nothing named like one.
    _touch(tmp_path / "scans.pdf")
    assert find_answer_key_source(tmp_path) is None

    # Two that could be it.
    _touch(tmp_path / "answer-key.pdf")
    _touch(tmp_path / "solutions.txt", "1. What is 2 + 2?\n")
    assert find_answer_key_source(tmp_path) is None


def test_a_finished_answer_key_or_a_template_is_not_its_own_source(tmp_path) -> None:
    _touch(tmp_path / "solutions.md", QA_TEXT)
    _touch(tmp_path / "answer-sheet-template.pdf")

    assert find_answer_key_source(tmp_path) is None


# One scan per class section --------------------------------------------------


@pytest.mark.parametrize(
    ("name", "section"),
    [
        ("section-01.pdf", "Section 01"),
        ("Sec B scans.pdf", "Section B"),
        ("sect_2.tsv", "Section 2"),
        ("CS146_Sec03_quiz.pdf", "Section 03"),
        ("period 3.pdf", "Period 3"),
        ("period1.pdf", "Period 1"),
        ("second.pdf", None),
        ("sections-a.pdf", None),
        ("sec-ond.pdf", None),
        ("submissions.pdf", None),
    ],
)
def test_a_files_section_is_read_from_its_name(name: str, section: str | None) -> None:
    assert section_name(name) == section


def test_section_spellings_of_one_section_match() -> None:
    assert section_key("Section 01") == section_key("section 1") == "section 1"
    assert section_key("Section 1") != section_key("Section 10")


def test_scans_named_after_their_sections_are_one_assignment(tmp_path) -> None:
    _touch(tmp_path / "qa.md", QA_TEXT)
    two = _touch(tmp_path / "section-2.pdf")
    ten = _touch(tmp_path / "section-10.pdf")
    one = _touch(tmp_path / "section-01.pdf")

    found = scan_class_folder(tmp_path)

    assert found.scan is None
    assert found.ambiguity(ROLE_SCAN) is None
    assert [(item.path, item.section) for item in found.section_scans] == [
        (one, "Section 01"),
        (two, "Section 2"),
        (ten, "Section 10"),
    ]
    line = next(line for line in describe_class_folder(found) if "scanned" in line)
    assert line.startswith("✓") and "section-01.pdf (Section 01)" in line


def test_section_scans_in_one_subfolder_are_not_a_folder_of_split_pdfs(tmp_path) -> None:
    _touch(tmp_path / "qa.md", QA_TEXT)
    _touch(tmp_path / "scans" / "Sec A.pdf")
    _touch(tmp_path / "scans" / "Sec B.pdf")

    found = scan_class_folder(tmp_path)

    assert found.scan is None
    assert [item.section for item in found.section_scans] == ["Section A", "Section B"]


def test_a_folder_of_split_pdfs_is_still_one(tmp_path) -> None:
    _touch(tmp_path / "split" / "submission-0001.pdf")
    _touch(tmp_path / "split" / "submission-0002.pdf")

    found = scan_class_folder(tmp_path)

    assert found.scan == tmp_path / "split"
    assert found.section_scans == ()


@pytest.mark.parametrize(
    "names",
    [
        ("section-01.pdf", "section-1.pdf"),  # the same section twice
        ("section-01.pdf", "morning.pdf"),  # one says nothing
    ],
)
def test_section_scans_must_each_name_a_different_section(tmp_path, names) -> None:
    for name in names:
        _touch(tmp_path / name)

    found = scan_class_folder(tmp_path)

    assert found.section_scans == ()
    assert found.ambiguity(ROLE_SCAN) is not None


def test_rosters_named_after_the_sections_go_with_their_scans(tmp_path) -> None:
    _touch(tmp_path / "section-01.pdf")
    _touch(tmp_path / "section-02.pdf")
    one = _touch(tmp_path / "roster-section-01.tsv", "1\tAda\n")
    two = _touch(tmp_path / "roster-section-02.tsv", "2\tBo\n")

    found = scan_class_folder(tmp_path)

    assert found.roster is None
    assert found.ambiguity(ROLE_ROSTER) is None
    assert [(item.path, item.section) for item in found.section_rosters] == [
        (one, "Section 01"),
        (two, "Section 02"),
    ]


def test_section_rosters_without_section_scans_stay_ambiguous(tmp_path) -> None:
    _touch(tmp_path / "submissions.pdf")
    _touch(tmp_path / "roster-section-01.tsv", "1\tAda\n")
    _touch(tmp_path / "roster-section-02.tsv", "2\tBo\n")

    found = scan_class_folder(tmp_path)

    assert found.section_rosters == ()
    assert found.ambiguity(ROLE_ROSTER) is not None


# A folder of assignments ------------------------------------------------------


def _assignment(folder: Path, title: str, *, graded: bool = True) -> Path:
    _touch(folder / "qa.md", QA_TEXT.replace("title: Quiz", f"title: {title}"))
    if graded:
        _touch(folder / "submissions" / "a" / "a.submission.md", "---\n---\n")
        _touch(folder / "submissions" / "b" / "b.submission.md", "---\n---\n")
    else:
        _touch(folder / "scan.pdf")
    return folder


def test_subfolders_with_their_own_answer_keys_are_assignments(tmp_path) -> None:
    quiz = _assignment(tmp_path / "Quiz 1", "Quiz 1")
    midterm = _assignment(tmp_path / "Midterm", "Midterm", graded=False)
    _touch(tmp_path / "notes" / "readme.txt", "not an assignment")

    found = find_assignment_folders(tmp_path)

    assert {item.folder for item in found.assignments} == {quiz, midterm}
    assert [item.folder for item in found.ready] == [quiz]
    assert found.is_workspace_candidate


def test_one_assignment_is_not_a_workspace(tmp_path) -> None:
    _assignment(tmp_path / "Quiz 1", "Quiz 1")

    assert not find_assignment_folders(tmp_path).is_workspace_candidate


def test_subfolders_sharing_one_answer_key_are_sections_not_assignments(tmp_path) -> None:
    one = _assignment(tmp_path / "Quiz 1 - Sec 1", "Quiz 1")
    two = _assignment(tmp_path / "Quiz 1 - Sec 2", "Quiz 1")
    _assignment(tmp_path / "Midterm", "Midterm")

    found = find_assignment_folders(tmp_path)

    assert found.same_key == ((one, two),)
    assert not found.is_workspace_candidate


def test_a_subfolder_that_is_a_workspace_is_never_nested(tmp_path) -> None:
    _assignment(tmp_path / "Quiz 1", "Quiz 1")
    _assignment(tmp_path / "Quiz 2", "Quiz 2")
    _touch(tmp_path / "Old" / ".opengrader" / "workspace.sqlite3", "")

    found = find_assignment_folders(tmp_path)

    assert found.workspaces == (tmp_path / "Old",)
    assert not found.is_workspace_candidate


def test_a_browser_session_on_a_class_folder_is_not_a_workspace(tmp_path) -> None:
    _touch(tmp_path / "real" / ".opengrader" / "workspace.sqlite3", "")
    session = tmp_path / "session"
    _touch(session / ".opengrader" / "workspace.sqlite3", "")
    _touch(session / ".opengrader" / "folder-session.json", "{}")

    assert is_workspace(tmp_path / "real")
    assert not is_workspace(session)
    assert not is_workspace(tmp_path)


def test_an_assignment_once_opened_in_the_browser_is_still_an_assignment(tmp_path) -> None:
    _assignment(tmp_path / "Quiz 1", "Quiz 1")
    quiz2 = _assignment(tmp_path / "Quiz 2", "Quiz 2")
    _touch(quiz2 / ".opengrader" / "workspace.sqlite3", "")
    _touch(quiz2 / ".opengrader" / "folder-session.json", "{}")

    found = find_assignment_folders(tmp_path)

    assert found.workspaces == ()
    assert len(found.assignments) == 2 and found.is_workspace_candidate
