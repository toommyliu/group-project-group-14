from pathlib import Path

import pytest

from opengrader.cli import run_command
from opengrader.cli import claim as cli
from opengrader.core.claims import read_claims

SUBMISSION = """---
student_name: "{name}"
student_id: "{student_id}"
course_code: "CMPE"
assignment_title: "Quiz"
assignment_version: ""
source_pdf: ""
student_score: ""
---

- q1:
  - Question type: Free Response
  - Prompt: One.
  - Response text: a
  - Possible points: 10
  - Points:
  - Analysis:

- q2:
  - Question type: Free Response
  - Prompt: Two.
  - Response text: b
  - Possible points: 10
  - Points:
  - Analysis:

- q3:
  - Question type: Free Response
  - Prompt: Three.
  - Response text: c
  - Possible points: 10
  - Points:
  - Analysis:

- q4:
  - Question type: Free Response
  - Prompt: Four.
  - Response text: d
  - Possible points: 10
  - Points:
  - Analysis:
"""


@pytest.fixture
def cohort(tmp_path: Path) -> Path:
    submissions = tmp_path / "student-submissions"
    submissions.mkdir()
    for index in range(1, 7):
        (submissions / f"student{index}.submission.md").write_text(
            SUBMISSION.format(name=f"Student {index}", student_id=f"id{index:02d}")
        )
    return submissions


def _actors(submissions: Path) -> list[str]:
    return [claim.actor for claim in read_claims(submissions)]


def test_divide_by_question_writes_one_claim_each(cohort, capsys) -> None:
    run_command(cli.command, ["divide", "--submissions", str(cohort), "--graders", "alice,bob"])

    claims = read_claims(cohort)
    assert [claim.actor for claim in claims] == ["alice", "bob"]
    assert claims[0].work.question_ids == ("q1", "q2")
    assert claims[1].work.question_ids == ("q3", "q4")
    assert claims[0].work.student_ids == ()
    assert "q1-q2" in capsys.readouterr().out


def test_divide_by_student_uses_real_ids(cohort) -> None:
    run_command(
        cli.command,
        [
            "divide", "--submissions", str(cohort),
            "--graders", "alice,bob", "--by", "student",
        ]
    )
    claims = read_claims(cohort)
    assert claims[0].work.student_ids == ("id01", "id02", "id03")
    assert claims[1].work.student_ids == ("id04", "id05", "id06")


def test_divide_dry_run_writes_nothing(cohort, capsys) -> None:
    run_command(
        cli.command,
        [
            "divide", "--submissions", str(cohort),
            "--graders", "alice,bob", "--dry-run",
        ]
    )
    assert read_claims(cohort) == []
    assert "Dry run" in capsys.readouterr().out


def test_divide_refuses_to_double_book(cohort, capsys) -> None:
    run_command(cli.command, ["add", "--submissions", str(cohort), "--questions", "q1", "--actor", "zoe"])
    with pytest.raises(SystemExit) as exit_info:
        run_command(cli.command, ["divide", "--submissions", str(cohort), "--graders", "alice,bob"])

    assert exit_info.value.code == 2
    assert "zoe" in capsys.readouterr().err
    assert _actors(cohort) == ["zoe"]


def test_more_graders_than_questions_leaves_nobody_holding_everything(
    cohort, capsys
) -> None:
    run_command(
        cli.command,
        [
            "divide", "--submissions", str(cohort),
            "--graders", "a,b,c,d,e,f",
        ]
    )
    claims = read_claims(cohort)
    assert len(claims) == 4
    assert all(not claim.work.is_everything for claim in claims)
    assert "only 4 of 6 graders" in capsys.readouterr().err


def test_add_expands_a_question_range(cohort) -> None:
    run_command(
        cli.command,
        [
            "add", "--submissions", str(cohort),
            "--questions", "q1-q3", "--actor", "alice",
        ]
    )
    (claim,) = read_claims(cohort)
    assert claim.work.question_ids == ("q1", "q2", "q3")


def test_add_accepts_a_question_list(cohort) -> None:
    run_command(
        cli.command,
        [
            "add", "--submissions", str(cohort),
            "--questions", "q1,q4", "--actor", "alice",
        ]
    )
    (claim,) = read_claims(cohort)
    assert claim.work.question_ids == ("q1", "q4")


def test_student_range_is_positional_but_stored_as_ids(cohort) -> None:
    run_command(
        cli.command,
        [
            "add", "--submissions", str(cohort),
            "--student-range", "1-3", "--actor", "alice",
        ]
    )
    (claim,) = read_claims(cohort)
    assert claim.work.student_ids == ("id01", "id02", "id03")


def test_an_unknown_question_is_refused(cohort, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_command(
            cli.command,
            [
                "add", "--submissions", str(cohort),
                "--questions", "q9", "--actor", "alice",
            ]
        )
    assert exit_info.value.code == 1
    assert "not in this assignment" in capsys.readouterr().err
    assert read_claims(cohort) == []


def test_a_student_range_past_the_cohort_is_refused(cohort, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_command(
            cli.command,
            [
                "add", "--submissions", str(cohort),
                "--student-range", "9-12", "--actor", "alice",
            ]
        )
    assert exit_info.value.code == 1
    assert "class has 6 student(s)" in capsys.readouterr().err


def test_add_with_no_slice_refuses_to_claim_the_assignment(cohort, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_command(cli.command, ["add", "--submissions", str(cohort), "--actor", "alice"])
    assert exit_info.value.code == 1
    assert "entire assignment" in capsys.readouterr().err


def test_claiming_the_whole_assignment_is_possible_on_purpose(cohort) -> None:
    run_command(
        cli.command,
        ["add", "--submissions", str(cohort), "--actor", "alice", "--everything"]
    )
    (claim,) = read_claims(cohort)
    assert claim.work.is_everything


def test_add_reports_the_conflicting_holder(cohort, capsys) -> None:
    run_command(
        cli.command,
        ["add", "--submissions", str(cohort), "--questions", "q1-q2", "--actor", "alice"]
    )
    with pytest.raises(SystemExit) as exit_info:
        run_command(
            cli.command,
            [
                "add", "--submissions", str(cohort),
                "--questions", "q2-q3", "--actor", "bob",
            ]
        )
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "already claimed by alice" in err
    assert _actors(cohort) == ["alice"]


def test_list_shows_active_claims_only_by_default(cohort, capsys) -> None:
    run_command(cli.command, ["divide", "--submissions", str(cohort), "--graders", "alice,bob"])
    capsys.readouterr()
    claims = read_claims(cohort)
    run_command(cli.command, ["release", claims[0].claim_id, "--submissions", str(cohort)])
    capsys.readouterr()

    run_command(cli.command, ["list", "--submissions", str(cohort)])
    active = capsys.readouterr().out
    assert "bob" in active and "alice" not in active

    run_command(cli.command, ["list", "--submissions", str(cohort), "--all"])
    everything = capsys.readouterr().out
    assert "alice" in everything and "bob" in everything


def test_list_says_so_when_nothing_is_claimed(cohort, capsys) -> None:
    run_command(cli.command, ["list", "--submissions", str(cohort)])
    assert "Everyone sees the whole assignment" in capsys.readouterr().out


def test_status_reports_full_coverage(cohort, capsys) -> None:
    run_command(cli.command, ["divide", "--submissions", str(cohort), "--graders", "alice,bob"])
    capsys.readouterr()
    run_command(cli.command, ["status", "--submissions", str(cohort)])

    out = capsys.readouterr().out
    assert "24/24 answers claimed" in out
    assert "claimed exactly once" in out


def test_status_flags_a_deliberate_overlap(cohort, capsys) -> None:
    run_command(
        cli.command,
        ["add", "--submissions", str(cohort), "--questions", "q1", "--actor", "alice"]
    )
    run_command(
        cli.command,
        [
            "add", "--submissions", str(cohort),
            "--questions", "q1", "--actor", "bob", "--force",
        ]
    )
    capsys.readouterr()

    with pytest.raises(SystemExit) as exit_info:
        run_command(
            cli.command,
            ["status", "--submissions", str(cohort), "--fail-on-overlap"]
        )
    assert exit_info.value.code == 2
    out = capsys.readouterr().out
    assert "double-booked" in out
    assert "6 answer(s)" in out


def test_status_json_carries_the_numbers(cohort, capsys) -> None:
    import json

    run_command(cli.command, ["divide", "--submissions", str(cohort), "--graders", "alice,bob"])
    capsys.readouterr()
    run_command(cli.command, ["status", "--submissions", str(cohort), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["total_cells"] == 24
    assert payload["unclaimed_cells"] == 0
    assert payload["cells_by_actor"] == {"alice": 12, "bob": 12}
    assert payload["overlaps"] == []


def test_release_frees_the_work_for_someone_else(cohort, capsys) -> None:
    run_command(
        cli.command,
        ["add", "--submissions", str(cohort), "--questions", "q1", "--actor", "alice"]
    )
    (claim,) = read_claims(cohort)
    run_command(cli.command, ["release", claim.claim_id, "--submissions", str(cohort)])
    capsys.readouterr()

    run_command(
        cli.command,
        ["add", "--submissions", str(cohort), "--questions", "q1", "--actor", "bob"]
    )
    assert "bob" in _actors(cohort)


def test_complete_marks_the_slice_done(cohort, capsys) -> None:
    run_command(
        cli.command,
        ["add", "--submissions", str(cohort), "--questions", "q1", "--actor", "alice"]
    )
    (claim,) = read_claims(cohort)
    run_command(cli.command, ["complete", claim.claim_id, "--submissions", str(cohort)])

    assert "Completed" in capsys.readouterr().out
    assert read_claims(cohort)[0].status == "completed"


def test_closing_an_unknown_id_is_an_error(cohort, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_command(cli.command, ["release", "cl-nope", "--submissions", str(cohort)])
    assert exit_info.value.code == 1
    assert "no claim with id" in capsys.readouterr().err


def test_a_missing_submissions_path_is_an_error(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_command(cli.command, ["list", "--submissions", str(tmp_path / "nope")])
    assert exit_info.value.code == 1
    assert "not found" in capsys.readouterr().err
