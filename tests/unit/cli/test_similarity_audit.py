import json

import pytest

from opengrader.cli import run_command
from opengrader.cli.similarity_audit import command

_LONG_A = (
    "A binary search halves the search interval each step, comparing the target "
    "to the middle element and discarding the half that cannot contain it."
)
_LONG_B = (
    "Depth-first search explores as far as possible along each branch before "
    "backtracking, using a stack to remember where to resume the traversal."
)


def _write_submission(path, *, student_id, student_name, response_text) -> None:
    path.write_text(
        "\n".join(
            [
                "---",
                f'student_name: "{student_name}"',
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
                "  - Points: ",
                "  - Rubric:",
                "  - Analysis: ",
                "",
            ]
        )
    )


def test_flags_near_identical_answers_human_readable(tmp_path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_id="1", student_name="Ann", response_text=_LONG_A)
    _write_submission(subs / "b.submission.md", student_id="2", student_name="Bob", response_text=_LONG_A)

    run_command(command, ["--input", str(subs)])

    out = capsys.readouterr().out
    assert "similar-answer cluster" in out
    assert "NEAR-VERBATIM" in out
    assert "Ann" in out and "Bob" in out


def test_distinct_answers_report_nothing(tmp_path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_id="1", student_name="Ann", response_text=_LONG_A)
    _write_submission(subs / "b.submission.md", student_id="2", student_name="Bob", response_text=_LONG_B)

    run_command(command, ["--input", str(subs)])
    assert "No suspiciously-similar answers found" in capsys.readouterr().out


def test_json_output(tmp_path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_id="1", student_name="Ann", response_text=_LONG_A)
    _write_submission(subs / "b.submission.md", student_id="2", student_name="Bob", response_text=_LONG_A)

    run_command(command, ["--input", str(subs), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert len(payload) == 1
    assert payload[0]["severity"] == "high"
    assert payload[0]["max_score"] == 1.0
    assert payload[0]["member_count"] == 2


def test_fail_on_match_exits_nonzero(tmp_path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_id="1", student_name="Ann", response_text=_LONG_A)
    _write_submission(subs / "b.submission.md", student_id="2", student_name="Bob", response_text=_LONG_A)

    with pytest.raises(SystemExit) as exc:
        run_command(command, ["--input", str(subs), "--fail-on-match"])
    assert exc.value.code == 2


def test_invalid_threshold_rejected(tmp_path) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    _write_submission(subs / "a.submission.md", student_id="1", student_name="Ann", response_text=_LONG_A)

    with pytest.raises(SystemExit) as exc:
        run_command(command, ["--input", str(subs), "--threshold", "1.5"])
    assert exc.value.code == 1


_OPTIONS = ("A", "B", "C", "D")


def _write_mc_qa(path, questions: int) -> None:
    lines = ["---", "title: Pattern", "---", ""]
    for number in range(1, questions + 1):
        lines += [
            f"- (1 points) Question {number}?",
            "  - **Choices**",
            *[f"    - {option}) option {option}" for option in _OPTIONS],
            "  - **Correct**",
            "    - A",
            "",
        ]
    path.write_text("\n".join(lines))


def _write_mc_submission(path, *, student_id, student_name, answers) -> None:
    lines = [
        "---",
        f'student_name: "{student_name}"',
        f'student_id: "{student_id}"',
        'course_code: "CS146"',
        'assignment_title: "Quiz 1"',
        'assignment_version: "A"',
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


def _mc_cohort(tmp_path, *, questions=40, students=40, copier=True):
    import random

    rng = random.Random(11)
    subs = tmp_path / "subs"
    subs.mkdir()
    qa = tmp_path / "qa.md"
    _write_mc_qa(qa, questions)
    answers = {
        index: [
            "A" if rng.random() < 0.65 else rng.choice(_OPTIONS[1:])
            for _ in range(questions)
        ]
        for index in range(students)
    }
    if copier:
        answers[1] = list(answers[0])
    for index, row in answers.items():
        _write_mc_submission(
            subs / f"s{index}.submission.md",
            student_id=f"{index:03d}",
            student_name=f"Student {index:03d}",
            answers=row,
        )
    return subs, qa


def test_objective_half_is_off_without_solutions(tmp_path, capsys) -> None:
    subs, _qa = _mc_cohort(tmp_path)

    run_command(command, ["--input", str(subs)])

    out = capsys.readouterr().out
    assert "Objective answers" not in out


def test_flags_a_copied_objective_paper(tmp_path, capsys) -> None:
    subs, qa = _mc_cohort(tmp_path)

    run_command(command, ["--input", str(subs), "--solutions", str(qa)])

    out = capsys.readouterr().out
    assert "Student 000 ↔ Student 001" in out
    assert "identical wrong answers" in out
    assert "student pair(s) compared" in out


def test_reports_what_could_not_be_analyzed_before_any_verdict(tmp_path, capsys) -> None:
    subs = tmp_path / "subs"
    subs.mkdir()
    qa = tmp_path / "qa.md"
    _write_mc_qa(qa, 2)
    for index in range(6):
        _write_mc_submission(
            subs / f"s{index}.submission.md",
            student_id=str(index),
            student_name=f"S{index}",
            answers=["A", "A"],
        )

    run_command(command, ["--input", str(subs), "--solutions", str(qa)])

    out = capsys.readouterr().out
    assert "nobody answered it incorrectly" in out
    assert "Nothing to compare" in out
    assert "✓" not in out.split("Objective answers")[1]


def test_json_keeps_the_array_shape_without_solutions(tmp_path, capsys) -> None:
    subs, _qa = _mc_cohort(tmp_path, copier=False)

    run_command(command, ["--input", str(subs), "--json"])

    assert isinstance(json.loads(capsys.readouterr().out), list)


def test_json_becomes_an_object_with_solutions(tmp_path, capsys) -> None:
    subs, qa = _mc_cohort(tmp_path)

    run_command(command, ["--input", str(subs), "--solutions", str(qa), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"free_response", "objective"}
    objective = payload["objective"]
    assert objective["analyzed"] is True
    assert objective["pairs_compared"] > 0
    (flag,) = objective["flags"]
    assert {member["student_id"] for member in flag["members"]} == {"000", "001"}
    assert flag["shared_wrong"]


def test_fail_on_match_covers_the_objective_half(tmp_path) -> None:
    subs, qa = _mc_cohort(tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        run_command(command, ["--input", str(subs), "--solutions", str(qa), "--fail-on-match"])

    assert exit_info.value.code == 2


def test_pattern_threshold_can_be_tightened(tmp_path, capsys) -> None:
    subs, qa = _mc_cohort(tmp_path)

    run_command(
        command,
        [
            "--input", str(subs), "--solutions", str(qa),
            "--pattern-threshold", "20", "--json",
        ]
    )

    assert json.loads(capsys.readouterr().out)["objective"]["flags"] == []


def test_missing_solutions_file_is_reported(tmp_path, capsys) -> None:
    subs, _qa = _mc_cohort(tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        run_command(command, ["--input", str(subs), "--solutions", str(tmp_path / "nope.md")])

    assert exit_info.value.code == 1
    assert "solutions file not found" in capsys.readouterr().err
