"""What Home's single path box makes of a folder (core/open_target.py)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from opengrader.core.open_target import detect_target

REPO = Path(__file__).resolve().parents[3]
SAMPLE = REPO / "samples" / "cs146-quiz1"


@pytest.fixture
def quiz(tmp_path: Path) -> Path:
    target = tmp_path / "quiz"
    shutil.copytree(SAMPLE, target, ignore=shutil.ignore_patterns(".opengrader"))
    return target


def test_a_class_folder_is_ready_to_grade(quiz: Path) -> None:
    target = detect_target(quiz)

    assert (target.kind, target.openable) == ("folder", True)
    assert target.qa_file == quiz / "qa.md"
    assert target.submissions == quiz / "student-submissions"
    assert target.lines[0].startswith("Ready to grade")


@pytest.mark.parametrize("pick", ["student-submissions", "qa.md"])
def test_the_submissions_folder_or_the_answer_key_find_the_same_class(
    quiz: Path, pick: str
) -> None:
    target = detect_target(quiz / pick)

    assert target.openable
    assert target.qa_file == quiz / "qa.md"
    assert target.submissions == quiz / "student-submissions"


def test_two_answer_keys_are_not_guessed_and_a_chosen_one_wins(quiz: Path) -> None:
    # `qa.md` is a preferred name and would win; two plain names tie.
    (quiz / "qa.md").rename(quiz / "quiz-a.md")
    shutil.copy(quiz / "quiz-a.md", quiz / "qa-copy.md")

    ambiguous = detect_target(quiz)
    chosen = detect_target(quiz, qa_file=quiz / "qa-copy.md")

    assert ambiguous.kind == "folder" and not ambiguous.openable
    assert ambiguous.needs_answer_key
    assert chosen.openable and chosen.qa_file == quiz / "qa-copy.md"


def test_a_chosen_answer_key_that_does_not_exist_is_named(quiz: Path) -> None:
    target = detect_target(quiz, qa_file=quiz / "nope.md")

    assert not target.openable
    assert "nope.md" in target.lines[0]


def test_unprocessed_scans_open_in_tools(tmp_path: Path) -> None:
    (tmp_path / "scans.pdf").write_bytes(b"%PDF-1.4\n")

    target = detect_target(tmp_path)

    assert (target.kind, target.openable) == ("scans", True)
    assert "scans.pdf" in target.lines[0]


def test_a_workspace_is_recognised_by_its_database(tmp_path: Path) -> None:
    (tmp_path / ".opengrader").mkdir()
    (tmp_path / ".opengrader" / "workspace.sqlite3").write_bytes(b"")

    target = detect_target(tmp_path)

    assert (target.kind, target.openable, target.folder) == ("workspace", True, tmp_path)


@pytest.mark.parametrize("path", ["", "   "])
def test_an_empty_box_asks_for_a_folder(path: str) -> None:
    assert detect_target(path).lines == ("Type or browse to a folder.",)


def test_a_missing_or_empty_folder_says_why(tmp_path: Path) -> None:
    missing = detect_target(tmp_path / "gone")
    empty = detect_target(tmp_path)

    assert missing.kind == "unknown" and "No such folder" in missing.lines[0]
    assert empty.kind == "unknown" and "Nothing to grade" in empty.lines[0]


def test_one_scan_per_section_opens_in_tools(tmp_path: Path) -> None:
    shutil.copy(SAMPLE / "qa.md", tmp_path / "qa.md")
    for name in ("section-01.pdf", "section-02.pdf"):
        shutil.copy(SAMPLE / "submissions.pdf", tmp_path / name)

    target = detect_target(tmp_path)

    assert target.kind == "scans"
    assert "2 sections" in target.lines[0]
