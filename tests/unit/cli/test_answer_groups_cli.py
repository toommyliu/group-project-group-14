import json

import pytest

from opengrader.cli import run_command
from opengrader.cli.answer_groups import command

_ANSWER = (
    "A binary search repeatedly halves the interval, comparing the target to "
    "the middle element and discarding the half that cannot contain it."
)
_ANSWER_REWORDED = _ANSWER.replace("cannot contain it.", "can not contain it.")
_OTHER = (
    "Depth-first search explores as far as possible along each branch before "
    "backtracking, using a stack to remember where to resume the traversal."
)


def _write_submission(path, *, student_id, response_text, points="") -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f'student_name: "Student {student_id}"',
                f'student_id: "{student_id}"',
                'course_code: "CS146"',
                'assignment_title: "Quiz 1"',
                'assignment_version: "A"',
                f'source_pdf: "{student_id}.pdf"',
                'student_score: ""',
                "---",
                "",
                "- q1:",
                "  - Question type: Free Response",
                "  - Prompt: ",
                f"  - Response text: {response_text}",
                "  - Possible points: 10",
                f"  - Points: {points}",
                "  - Rubric:",
                "  - Analysis: ",
                "",
            ]
        )
    )


@pytest.fixture()
def cohort(tmp_path):
    root = tmp_path / "subs"
    root.mkdir()
    for index, text in enumerate(
        [_ANSWER, _ANSWER, _ANSWER_REWORDED, _OTHER, ""], start=1
    ):
        _write_submission(root / f"s{index}.submission.md", student_id=f"00{index}", response_text=text)
    return root


def test_reports_the_grading_effort_a_cohort_actually_costs(cohort, capsys):
    run_command(command, ["--input", str(cohort)])

    out = capsys.readouterr().out
    assert "5 responses → 4 distinct answers → 3 after suggested merges" in out
    assert "Question 1" in out


def test_suggestions_list_the_answers_a_merge_would_join(cohort, capsys):
    run_command(command, ["--input", str(cohort), "--suggestions"])

    out = capsys.readouterr().out
    assert "1 proposed merge(s)" in out
    assert "grade this" in out
    assert "Nothing here is applied" in out


def test_a_strict_threshold_proposes_nothing(cohort, capsys):
    run_command(command, ["--input", str(cohort), "--threshold", "0.999", "--suggestions"])

    out = capsys.readouterr().out
    assert "No merges proposed at this threshold." in out


def test_json_output_carries_the_counts_and_the_proposals(cohort, capsys):
    run_command(command, ["--input", str(cohort), "--json"])

    payload = json.loads(capsys.readouterr().out)
    question = payload["questions"][0]
    assert question["question_id"] == "q1"
    assert question["responses"] == 5
    assert question["blank_responses"] == 1
    assert question["exact_groups"] == 4
    assert question["groups_after_merges"] == 3
    assert payload["totals"]["responses"] == 5

    suggestion = question["suggestions"][0]
    assert suggestion["size"] == 3
    assert sum(1 for answer in suggestion["answers"] if answer["representative"]) == 1


def test_a_single_question_can_be_isolated(cohort, capsys):
    run_command(command, ["--input", str(cohort), "--question", "q1"])

    assert "1 question(s)" in capsys.readouterr().out


def test_an_unknown_question_is_an_error(cohort, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run_command(command, ["--input", str(cohort), "--question", "q99"])

    assert exit_info.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_an_out_of_range_threshold_is_refused(cohort, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run_command(command, ["--input", str(cohort), "--threshold", "1.5"])

    assert exit_info.value.code == 1
    assert "between 0 and 1" in capsys.readouterr().err


def test_a_missing_input_path_is_an_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        run_command(command, ["--input", str(tmp_path / "nope")])

    assert exit_info.value.code == 1
    assert "not found" in capsys.readouterr().err


def test_the_report_never_writes_to_the_submissions(cohort, capsys):
    before = {path: path.read_bytes() for path in sorted(cohort.rglob("*"))}

    run_command(command, ["--input", str(cohort), "--suggestions"])

    assert {path: path.read_bytes() for path in sorted(cohort.rglob("*"))} == before
