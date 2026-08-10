"""grade-restore end to end: a real journal, real submission files, real writes."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from opengrader.cli import grade_restore
from opengrader.core.history.journal import resolve_history_dir
from opengrader.core.submissions.student_file import load_student_submission_markdown

BASE = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)

SUBMISSION = """---
student_name: "Ada Lovelace"
student_id: "1001"
course_code: "CS146"
assignment_title: "Quiz 1"
assignment_version: "A"
source_pdf: "scan.pdf"
student_score: ""
---


- q1:
  - Question type: Free Response
  - Prompt:
  - Response image: ![q1 response](responses/q1.png)
  - Response text: |
    an answer
  - Possible points: 10
  - Points: {points}
  - Rubric:{rubric}
  - Analysis: {analysis}

- q2:
  - Question type: Free Response
  - Prompt:
  - Response image: ![q2 response](responses/q2.png)
  - Response text:
  - Possible points: 10
  - Points:
  - Rubric:
  - Analysis:
"""


@pytest.fixture
def cohort(tmp_path: Path) -> Path:
    subs = tmp_path / "subs"
    (subs / "s1").mkdir(parents=True)
    (subs / "s1" / "s1.submission.md").write_text(
        SUBMISSION.format(points="0", rubric=" Incorrect", analysis="Wrong.")
    )
    return subs


def _journal(subs: Path, entries: list[dict]) -> None:
    """Write one session file straight to the history dir."""
    history = resolve_history_dir(subs)
    history.mkdir(parents=True, exist_ok=True)
    path = history / "grade-journal-test.jsonl"
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n")


def _event(minutes, kind, actor, qid, old, new):  # noqa: ANN001
    return {
        "timestamp": (BASE + timedelta(minutes=minutes)).isoformat(),
        "actor": actor,
        "kind": kind,
        "student_id": "1001",
        "student_name": "Ada Lovelace",
        "question_id": qid,
        "summary": "",
        "old": old,
        "new": new,
    }


def _good_then_bad(subs: Path) -> None:
    """Alice grades 10/Correct; a batch run then overwrites it with 0/Incorrect."""
    _journal(
        subs,
        [
            _event(0, "grade", "alice", "q1",
                   {"points": None, "rubric": []},
                   {"points": 10, "rubric": ["Correct"]}),
            _event(0, "comment", "alice", "q1",
                   {"analysis": ""}, {"analysis": "Nice work."}),
            _event(30, "grade", "bot", "q1",
                   {"points": 10, "rubric": ["Correct"]},
                   {"points": 0, "rubric": ["Incorrect"]}),
            _event(30, "comment", "bot", "q1",
                   {"analysis": "Nice work."}, {"analysis": "Wrong."}),
        ],
    )


def test_log_summarizes_actors_and_what_is_restorable(cohort, capsys) -> None:  # noqa: ANN001
    _good_then_bad(cohort)
    grade_restore.run_cli(["log", "--submissions", str(cohort)])
    out = capsys.readouterr().out
    assert "4 journal entries" in out
    assert "alice" in out and "bot" in out
    assert "restorable: 4" in out


def test_diff_reports_changes_without_writing(cohort, capsys) -> None:  # noqa: ANN001
    _good_then_bad(cohort)
    before = (cohort / "s1" / "s1.submission.md").read_text()
    grade_restore.run_cli(
        ["diff", "--submissions", str(cohort), "--without-actor", "bot"]
    )
    out = capsys.readouterr().out
    assert "0 [Incorrect] → 10 [Correct]" in out
    assert (cohort / "s1" / "s1.submission.md").read_text() == before


def test_apply_without_yes_is_a_preview(cohort, capsys) -> None:  # noqa: ANN001
    _good_then_bad(cohort)
    before = (cohort / "s1" / "s1.submission.md").read_text()
    grade_restore.run_cli(
        ["apply", "--submissions", str(cohort), "--without-actor", "bot"]
    )
    assert "Re-run with --yes" in capsys.readouterr().out
    assert (cohort / "s1" / "s1.submission.md").read_text() == before


def test_apply_undoes_one_actor_and_leaves_the_other_alone(cohort, capsys) -> None:  # noqa: ANN001
    _good_then_bad(cohort)
    grade_restore.run_cli(
        ["apply", "--submissions", str(cohort), "--without-actor", "bot", "--yes"]
    )
    document = load_student_submission_markdown(cohort / "s1" / "s1.submission.md")
    question = document.questions[0]
    assert question.points_awarded == 10.0
    assert question.rubric_labels == ("Correct",)
    assert question.analysis == "Nice work."
    assert "Restored 2 change(s)" in capsys.readouterr().out


def test_apply_is_idempotent(cohort, capsys) -> None:  # noqa: ANN001
    _good_then_bad(cohort)
    args = ["apply", "--submissions", str(cohort), "--without-actor", "bot", "--yes"]
    grade_restore.run_cli(args)
    capsys.readouterr()
    grade_restore.run_cli(args)
    assert "Nothing to restore" in capsys.readouterr().out


def test_apply_restores_a_question_back_to_ungraded(cohort, capsys) -> None:  # noqa: ANN001
    # The batch graded a question nobody had touched; undoing it must clear the
    # Points line, not merely change the number.
    (cohort / "s1" / "s1.submission.md").write_text(
        SUBMISSION.format(points="0", rubric=" Incorrect", analysis="Wrong.")
    )
    _journal(
        cohort,
        [
            _event(0, "grade", "bot", "q1",
                   {"points": None, "rubric": []},
                   {"points": 0, "rubric": ["Incorrect"]}),
        ],
    )
    grade_restore.run_cli(
        ["apply", "--submissions", str(cohort), "--without-actor", "bot", "--yes"]
    )
    question = load_student_submission_markdown(
        cohort / "s1" / "s1.submission.md"
    ).questions[0]
    assert question.points_awarded is None
    assert question.rubric_labels == ()


def test_apply_restores_to_a_point_in_time(cohort, capsys) -> None:  # noqa: ANN001
    _good_then_bad(cohort)
    checkpoint = (BASE + timedelta(minutes=10)).isoformat()
    grade_restore.run_cli(
        ["apply", "--submissions", str(cohort), "--at", checkpoint, "--yes"]
    )
    question = load_student_submission_markdown(
        cohort / "s1" / "s1.submission.md"
    ).questions[0]
    assert question.points_awarded == 10.0


def test_apply_records_itself_in_the_journal(cohort) -> None:  # noqa: ANN001
    _good_then_bad(cohort)
    grade_restore.run_cli(
        ["apply", "--submissions", str(cohort), "--without-actor", "bot", "--yes",
         "--reason", "bad batch"]
    )
    from opengrader.core.history import read_journal

    entries = read_journal(resolve_history_dir(cohort))
    restored = [e for e in entries if "restored" in e.summary]
    assert len(restored) == 2
    assert all("bad batch" in e.summary for e in restored)
    # And the recorded old/new describe the write that actually happened.
    grade = next(e for e in restored if e.kind == "grade")
    assert grade.old["points"] == 0.0 and grade.new["points"] == 10.0


def test_question_filter_narrows_the_restore(cohort, capsys) -> None:  # noqa: ANN001
    _good_then_bad(cohort)
    grade_restore.run_cli(
        ["apply", "--submissions", str(cohort), "--without-actor", "bot",
         "--question", "q2", "--yes"]
    )
    out = capsys.readouterr().out
    assert "Nothing to restore" in out
    question = load_student_submission_markdown(
        cohort / "s1" / "s1.submission.md"
    ).questions[0]
    assert question.points_awarded == 0.0  # q1 untouched


def test_only_filter_restores_one_aspect(cohort) -> None:  # noqa: ANN001
    _good_then_bad(cohort)
    grade_restore.run_cli(
        ["apply", "--submissions", str(cohort), "--without-actor", "bot",
         "--only", "comment", "--yes"]
    )
    question = load_student_submission_markdown(
        cohort / "s1" / "s1.submission.md"
    ).questions[0]
    assert question.analysis == "Nice work."
    assert question.points_awarded == 0.0  # the grade was not in scope


def test_show_prints_the_reconstructed_state(cohort, capsys) -> None:  # noqa: ANN001
    _good_then_bad(cohort)
    grade_restore.run_cli(
        ["show", "--submissions", str(cohort), "--at", (BASE + timedelta(minutes=10)).isoformat()]
    )
    out = capsys.readouterr().out
    assert "Ada Lovelace" in out
    assert "grade: 10 [Correct]" in out


def test_an_empty_journal_exits_with_an_explanation(cohort, capsys) -> None:  # noqa: ANN001
    with pytest.raises(SystemExit) as exit_info:
        grade_restore.run_cli(["diff", "--submissions", str(cohort)])
    assert exit_info.value.code == 1
    assert "No grade history recorded" in capsys.readouterr().err


def test_an_unknown_aspect_is_rejected(cohort, capsys) -> None:  # noqa: ANN001
    _good_then_bad(cohort)
    with pytest.raises(SystemExit):
        grade_restore.run_cli(
            ["diff", "--submissions", str(cohort), "--only", "nonsense"]
        )
    assert "unknown aspect" in capsys.readouterr().err


def test_a_student_missing_from_disk_is_reported(cohort, capsys) -> None:  # noqa: ANN001
    _journal(
        cohort,
        [
            {
                "timestamp": BASE.isoformat(),
                "actor": "bot",
                "kind": "grade",
                "student_id": "9999",
                "student_name": "Ghost",
                "question_id": "q1",
                "summary": "",
                "old": {"points": None, "rubric": []},
                "new": {"points": 3, "rubric": []},
            }
        ],
    )
    grade_restore.run_cli(["diff", "--submissions", str(cohort)])
    captured = capsys.readouterr()
    assert "no submission file here" in captured.err
    assert "9999" in captured.err
