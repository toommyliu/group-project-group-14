"""The grade-check and grade-release CLIs.

The verdict logic is tested in ``tests/unit/core/test_release.py``; what these
cover is the part a user meets — the exit codes a script depends on, the
refusals, and the fact that a preview writes nothing.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opengrader.cli.grade_check import main as grade_check_main
from opengrader.cli.grade_check import run_cli as grade_check
from opengrader.cli.grade_release import run_cli as grade_release
from opengrader.core.release import read_releases, resolve_releases_dir


def _submission(points: str = "3") -> str:
    return f"""---
student_name: "Ada Lovelace"
student_id: "101001"
course_code: "CS146"
assignment_title: "Quiz 1"
source_pdf: ""
student_score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: What is a loop?
  - Response text: a repeat
  - Possible points: 5
  - Points: {points}
  - Rubric: Correct
  - Analysis:
"""


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "subs"
    root.mkdir()
    (root / "sub-0001.submission.md").write_text(_submission())
    return root


def _regrade(folder: Path, points: str) -> None:
    (folder / "sub-0001.submission.md").write_text(_submission(points))


def _ungrade(folder: Path) -> None:
    """Leave q1 genuinely ungraded — no points *and* no rubric selection."""
    (folder / "sub-0001.submission.md").write_text(
        _submission("").replace("  - Rubric: Correct", "  - Rubric:")
    )


# --------------------------------------------------------------------------- #
# grade-check
# --------------------------------------------------------------------------- #


def test_grade_check_reports_ready(folder: Path, capsys):
    context = grade_check(["--submissions", str(folder)])
    output = capsys.readouterr().out

    assert context.readiness.ready
    assert "Ready to release." in output
    assert "1/1 answers graded" in output


def test_grade_check_fails_on_blocking_for_scripts(folder: Path):
    (folder / "sub-0002.submission.md").write_text(
        _submission("").replace("  - Rubric: Correct", "  - Rubric:")
    )
    with pytest.raises(SystemExit) as exit_info:
        grade_check(["--submissions", str(folder), "--fail-on-blocking"])
    assert exit_info.value.code == 2


def test_grade_check_json_carries_the_verdict(folder: Path, capsys):
    grade_check(["--submissions", str(folder), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["ready"] is True
    assert payload["total_answers"] == 1
    assert payload["previous_release"] is None


def test_grade_check_writes_nothing(folder: Path, capsys):
    before = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    grade_check(["--submissions", str(folder)])
    capsys.readouterr()

    after = {p: p.read_bytes() for p in folder.rglob("*") if p.is_file()}
    assert before == after
    assert not (folder / ".opengrader").exists()


def test_grade_check_main_returns_none(folder: Path, capsys):
    """The console entry point must not return, or the script exits 1.

    A ``console_scripts`` wrapper runs ``sys.exit(main())``, so a returned
    report would be printed as an error message on a successful run.
    """
    assert grade_check_main(["--submissions", str(folder)]) is None
    capsys.readouterr()


def test_grade_check_names_unreadable_files_before_the_verdict(
    folder: Path, capsys
):
    (folder / "broken.submission.md").write_bytes(b"\xff\xfe not utf-8 \x00")
    grade_check(["--submissions", str(folder)])
    output = capsys.readouterr().out

    assert output.index("could not be read") < output.index("Ready to release.")


# --------------------------------------------------------------------------- #
# grade-release
# --------------------------------------------------------------------------- #


def test_create_previews_without_yes(folder: Path, capsys):
    grade_release(["create", "--submissions", str(folder)])
    output = capsys.readouterr().out

    assert "Re-run with --yes" in output
    assert read_releases(folder) == []


def test_create_writes_with_yes(folder: Path, capsys):
    grade_release(
        ["create", "--submissions", str(folder), "--label", "in class", "--yes"]
    )
    output = capsys.readouterr().out

    releases = read_releases(folder)
    assert len(releases) == 1
    assert releases[0].label == "in class"
    assert releases[0].release_id in output
    assert "blocks nothing" in output


def test_create_refuses_a_blocking_cohort(folder: Path, capsys):
    _ungrade(folder)
    with pytest.raises(SystemExit) as exit_info:
        grade_release(["create", "--submissions", str(folder), "--yes"])

    assert exit_info.value.code == 2
    assert read_releases(folder) == []
    assert "--force" in capsys.readouterr().err


def test_force_records_over_a_blocking_cohort(folder: Path, capsys):
    _ungrade(folder)
    grade_release(
        ["create", "--submissions", str(folder), "--yes", "--force"]
    )
    output = capsys.readouterr().out

    assert len(read_releases(folder)) == 1
    assert "--force" in output  # it says it was overridden


def test_a_release_writes_nothing_into_a_submission(folder: Path, capsys):
    submission = folder / "sub-0001.submission.md"
    before = submission.read_bytes()
    grade_release(["create", "--submissions", str(folder), "--yes"])
    capsys.readouterr()

    assert submission.read_bytes() == before
    # Everything it wrote is inside the dot-dir.
    assert resolve_releases_dir(folder).exists()


def test_diff_names_the_question_that_changed(folder: Path, capsys):
    grade_release(["create", "--submissions", str(folder), "--yes"])
    capsys.readouterr()
    _regrade(folder, "5")

    grade_release(["diff", "--submissions", str(folder)])
    output = capsys.readouterr().out

    assert "q1" in output
    assert "raised" in output
    assert "3 → 5" in output


def test_diff_json_is_machine_readable(folder: Path, capsys):
    grade_release(["create", "--submissions", str(folder), "--yes"])
    capsys.readouterr()
    _regrade(folder, "5")

    grade_release(["diff", "--submissions", str(folder), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["changes"][0]["question_id"] == "q1"
    assert payload["changes"][0]["after"] == 5.0


def test_diff_without_a_release_says_so(folder: Path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        grade_release(["diff", "--submissions", str(folder)])
    assert exit_info.value.code == 1
    assert "grade-release create" in capsys.readouterr().err


def test_list_and_show(folder: Path, capsys):
    grade_release(["create", "--submissions", str(folder), "--yes"])
    capsys.readouterr()

    grade_release(["list", "--submissions", str(folder)])
    listing = capsys.readouterr().out
    assert "1 release(s)" in listing

    grade_release(["show", "--submissions", str(folder)])
    shown = capsys.readouterr().out
    assert "Ada Lovelace" in shown
    assert "3/5" in shown


def test_show_rejects_an_unknown_release(folder: Path, capsys):
    grade_release(["create", "--submissions", str(folder), "--yes"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as exit_info:
        grade_release(["show", "--submissions", str(folder), "--release", "rel-nope"])
    assert exit_info.value.code == 1
    assert "grade-release list" in capsys.readouterr().err


def test_a_release_never_blocks_a_later_grade_change(folder: Path, capsys):
    """The feature's central promise, asserted rather than assumed."""
    from opengrader.core.submissions.student_file import (
        StudentSubmissionGradeUpdate,
        load_student_submission_markdown,
        update_student_submission_file,
    )

    grade_release(["create", "--submissions", str(folder), "--yes"])
    capsys.readouterr()

    path = folder / "sub-0001.submission.md"
    update_student_submission_file(
        path,
        StudentSubmissionGradeUpdate(question_id="q1", points_awarded=5.0),
    )
    assert load_student_submission_markdown(path).questions[0].points_awarded == 5.0
