from __future__ import annotations

import json
from pathlib import Path

import pytest

from opengrader.cli import llm_eval

SOLUTIONS = """---
title: Quiz
---

- (4 points) Explain the difference between a stack and a queue.
  - **Correct**
    - LIFO vs FIFO
  - **Rubric**
    - Correct (+0)
    - Partial (-2)
    - Incorrect (-4)
- (1 points) Which is a tree?
  - **Choices**
    - A) Heap
    - B) Queue
  - **Correct**
    - A
"""


def _submission(*, student_id: str, response: str, points: str, analysis: str = "") -> str:
    return (
        "---\n"
        f'name: "Student {student_id}"\n'
        f'student_id: "{student_id}"\n'
        "course_code: CS146\n"
        'assignment_title: "Quiz"\n'
        "assignment_version: null\n"
        f'source_pdf: "{student_id}.pdf"\n'
        'score: ""\n'
        "---\n\n\n"
        "- q1:\n"
        "  - Question type: Free Response\n"
        "  - Prompt: p\n"
        f"  - Response text: {response}\n"
        "  - Possible points: 4\n"
        f"  - Points: {points}\n"
        "  - Rubric: Correct\n"
        f"  - Analysis: {analysis}\n"
        "\n"
        "- q2:\n"
        "  - Question type: Multiple Choice\n"
        "  - Prompt: p\n"
        "  - Response text: A\n"
        "  - Possible points: 1\n"
        "  - Points: 1\n"
        "  - Rubric:\n"
        "  - Analysis:\n"
    )


def _class(
    tmp_path: Path, count: int = 8, *, points: str = "4", name: str = "subs"
) -> tuple[Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS)
    submissions = tmp_path / name
    submissions.mkdir()
    for index in range(count):
        (submissions / f"s{index}.submission.md").write_text(
            _submission(
                student_id=f"s{index}",
                response=f"answer number {index}",
                points=points,
            )
        )
    return solutions_path, submissions


def _factory(payloads: dict[str, dict]):  # noqa: ANN202

    def factory(model: str):  # noqa: ANN202
        body = json.dumps(payloads[model])
        return lambda _prompt: body

    return factory


def _agrees(confidence: float = 0.9) -> dict:
    return {"rubric_labels": ["Correct"], "confidence": confidence, "rationale": "ok"}


def _disagrees(confidence: float = 0.9) -> dict:
    return {
        "rubric_labels": ["Incorrect"],
        "confidence": confidence,
        "rationale": "missing the ordering",
    }


def test_it_reports_agreement_and_recommends_a_threshold(
    tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    solutions, submissions = _class(tmp_path)
    llm_eval.run_cli(
        ["--solutions", str(solutions), "--input", str(submissions), "--quiet"],
        generate_factory=_factory({"qwen2.5vl:7b": _agrees(0.88)}),
    )
    out = capsys.readouterr().out
    assert "Agreed with the human: 8/8" in out
    assert "--llm-min-confidence" in out
    assert "→ --llm-min-confidence 0.00" in out


def test_a_model_that_always_disagrees_gets_no_recommendation(
    tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    solutions, submissions = _class(tmp_path)
    llm_eval.run_cli(
        ["--solutions", str(solutions), "--input", str(submissions), "--quiet"],
        generate_factory=_factory({"qwen2.5vl:7b": _disagrees(0.95)}),
    )
    out = capsys.readouterr().out
    assert "no recommended threshold" in out
    assert "Grade this assignment's free responses by hand" in out


def test_objective_questions_are_reported_as_not_evaluated(
    tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    solutions, submissions = _class(tmp_path, count=3)
    llm_eval.run_cli(
        ["--solutions", str(solutions), "--input", str(submissions), "--quiet"],
        generate_factory=_factory({"qwen2.5vl:7b": _agrees()}),
    )
    out = capsys.readouterr().out
    assert "Evaluating 3 human-graded answer(s) per model." in out
    assert "3 graded by rule" in out


def test_it_never_writes_to_a_submission(tmp_path: Path) -> None:
    solutions, submissions = _class(tmp_path, count=3)
    before = {
        path.name: path.read_text() for path in submissions.glob("*.submission.md")
    }
    llm_eval.run_cli(
        ["--solutions", str(solutions), "--input", str(submissions), "--quiet"],
        generate_factory=_factory({"qwen2.5vl:7b": _disagrees()}),
    )
    after = {path.name: path.read_text() for path in submissions.glob("*.submission.md")}
    assert after == before
    assert not (submissions / ".opengrader").exists()


def test_several_models_are_measured_over_the_same_answers(
    tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    solutions, submissions = _class(tmp_path)
    llm_eval.run_cli(
        [
            "--solutions",
            str(solutions),
            "--input",
            str(submissions),
            "--llm-model",
            "good",
            "--llm-model",
            "bad",
            "--quiet",
        ],
        generate_factory=_factory({"good": _agrees(0.8), "bad": _disagrees(0.8)}),
    )
    out = capsys.readouterr().out
    assert "── bad" in out and "── good" in out
    assert "Best: good at --llm-min-confidence" in out


def test_limit_caps_the_number_of_model_calls(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    solutions, submissions = _class(tmp_path, count=8)
    calls = 0

    def factory(_model: str):  # noqa: ANN202
        def generate(_prompt: str) -> str:
            nonlocal calls
            calls += 1
            return json.dumps(_agrees())

        return generate

    llm_eval.run_cli(
        [
            "--solutions",
            str(solutions),
            "--input",
            str(submissions),
            "--limit",
            "3",
            "--quiet",
        ],
        generate_factory=factory,
    )
    assert calls == 3
    assert "Evaluating 3 human-graded answer(s) per model." in capsys.readouterr().out


def test_json_output_carries_the_recommendation_and_the_whole_curve(
    tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    solutions, submissions = _class(tmp_path)
    llm_eval.run_cli(
        ["--solutions", str(solutions), "--input", str(submissions), "--json", "--quiet"],
        generate_factory=_factory({"qwen2.5vl:7b": _agrees(0.9)}),
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["cases_per_model"] == 8
    assert payload["best_model"] == "qwen2.5vl:7b"
    model = payload["models"][0]
    assert model["overall"]["agreements"] == 8
    assert model["recommendation"]["llm_min_confidence"] == 0.0
    assert len(model["thresholds"]) == 21


def test_a_stricter_target_agreement_demands_a_stricter_threshold(
    tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS)
    submissions = tmp_path / "subs"
    submissions.mkdir()
    for index in range(10):
        (submissions / f"s{index}.submission.md").write_text(
            _submission(
                student_id=f"s{index}",
                response=f"answer {index}",
                points="4" if index % 2 == 0 else "0",
            )
        )
    llm_eval.run_cli(
        [
            "--solutions",
            str(solutions_path),
            "--input",
            str(submissions),
            "--target-agreement",
            "0.9",
            "--quiet",
        ],
        generate_factory=_factory({"qwen2.5vl:7b": _agrees(0.9)}),
    )
    out = capsys.readouterr().out
    assert "no recommended threshold" in out
    assert "the best is 50%" in out


def test_biggest_disagreements_are_printed_for_the_instructor_to_read(
    tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    solutions, submissions = _class(tmp_path, count=6, points="0")
    llm_eval.run_cli(
        ["--solutions", str(solutions), "--input", str(submissions), "--quiet"],
        generate_factory=_factory({"qwen2.5vl:7b": _agrees(0.9)}),
    )
    out = capsys.readouterr().out
    assert "Biggest disagreements" in out
    assert "human 0, model 4 of 4 (+4)" in out
    assert "leans generous" in out


def test_a_cohort_with_no_human_grades_explains_itself_and_exits_nonzero(
    tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS)
    submissions = tmp_path / "subs"
    submissions.mkdir()
    (submissions / "s0.submission.md").write_text(
        _submission(student_id="s0", response="something", points="")
    )

    with pytest.raises(SystemExit) as excinfo:
        llm_eval.run_cli(
            ["--solutions", str(solutions_path), "--input", str(submissions), "--quiet"],
            generate_factory=_factory({"qwen2.5vl:7b": _agrees()}),
        )
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "1 not yet graded by a human" in out
    assert "grading more of them by hand sharpens this report" in out


def test_a_missing_solutions_file_fails_fast(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as excinfo:
        llm_eval.run_cli(["--solutions", str(tmp_path / "nope.md"), "--input", str(tmp_path)])
    assert excinfo.value.code == 1


def test_a_directory_with_no_submissions_fails_fast(tmp_path: Path) -> None:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit) as excinfo:
        llm_eval.run_cli(["--solutions", str(solutions_path), "--input", str(empty)])
    assert excinfo.value.code == 1


def test_submissions_are_discovered_one_directory_down(tmp_path: Path) -> None:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS)
    submissions = tmp_path / "subs"
    (submissions / "s0").mkdir(parents=True)
    (submissions / "s0" / "s0.submission.md").write_text(
        _submission(student_id="s0", response="x", points="4")
    )
    assert len(llm_eval.discover_submission_paths(submissions)) == 1


def test_two_cohorts_are_pooled_into_one_sample(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    solutions, spring = _class(tmp_path / "2026-spring", count=4)
    _, fall = _class(tmp_path / "2025-fall", count=3)

    llm_eval.run_cli(
        [
            "--solutions",
            str(solutions),
            "--input",
            str(spring),
            "--input",
            str(fall),
            "--quiet",
        ],
        generate_factory=_factory({"qwen2.5vl:7b": _agrees(0.9)}),
    )
    out = capsys.readouterr().out
    assert "Evaluating 7 human-graded answer(s) per model" in out
    assert "pooled from 2 classes (2026-spring/subs, 2025-fall/subs)" in out
    assert "Agreed with the human: 7/7" in out


def test_a_repeated_folder_is_not_double_counted(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    solutions, submissions = _class(tmp_path, count=5)
    llm_eval.run_cli(
        [
            "--solutions",
            str(solutions),
            "--input",
            str(submissions),
            "--input",
            str(submissions),
            "--quiet",
        ],
        generate_factory=_factory({"qwen2.5vl:7b": _agrees()}),
    )
    assert "Evaluating 5 human-graded answer(s)" in capsys.readouterr().out


def test_pooled_disagreements_name_their_class(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    solutions, spring = _class(tmp_path / "2026-spring", count=3, points="0")
    _, fall = _class(tmp_path / "2025-fall", count=3, points="0")
    llm_eval.run_cli(
        [
            "--solutions",
            str(solutions),
            "--input",
            str(spring),
            "--input",
            str(fall),
            "--quiet",
        ],
        generate_factory=_factory({"qwen2.5vl:7b": _agrees(0.9)}),
    )
    out = capsys.readouterr().out
    assert "Biggest disagreements" in out
    assert "[2026-spring/subs]" in out


def test_a_single_cohort_report_carries_no_cohort_noise(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    solutions, submissions = _class(tmp_path, count=3, points="0")
    llm_eval.run_cli(
        ["--solutions", str(solutions), "--input", str(submissions), "--quiet"],
        generate_factory=_factory({"qwen2.5vl:7b": _agrees(0.9)}),
    )
    out = capsys.readouterr().out
    assert "pooled from" not in out
    assert "[subs]" not in out


def test_json_reports_the_cohorts_behind_the_sample(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    solutions, spring = _class(tmp_path / "spring", count=3)
    _, fall = _class(tmp_path / "fall", count=3)
    llm_eval.run_cli(
        [
            "--solutions",
            str(solutions),
            "--input",
            str(spring),
            "--input",
            str(fall),
            "--json",
            "--quiet",
        ],
        generate_factory=_factory({"qwen2.5vl:7b": _agrees(0.9)}),
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["classes"] == ["spring/subs", "fall/subs"]
    assert payload["cases_per_model"] == 6


def test_a_missing_cohort_folder_fails_fast(tmp_path: Path) -> None:
    solutions, submissions = _class(tmp_path, count=2)
    with pytest.raises(SystemExit) as excinfo:
        llm_eval.run_cli(
            [
                "--solutions",
                str(solutions),
                "--input",
                str(submissions),
                "--input",
                str(tmp_path / "nope"),
            ]
        )
    assert excinfo.value.code == 1


def test_the_limit_is_a_total_across_cohorts_not_per_folder(tmp_path: Path) -> None:
    solutions, spring = _class(tmp_path / "spring", count=6)
    _, fall = _class(tmp_path / "fall", count=6)
    calls = 0

    def factory(_model: str):  # noqa: ANN202
        def generate(_prompt: str) -> str:
            nonlocal calls
            calls += 1
            return json.dumps(_agrees())

        return generate

    llm_eval.run_cli(
        [
            "--solutions",
            str(solutions),
            "--input",
            str(spring),
            "--input",
            str(fall),
            "--limit",
            "4",
            "--quiet",
        ],
        generate_factory=factory,
    )
    assert calls == 4


def test_a_limited_run_samples_both_cohorts(tmp_path: Path) -> None:
    solutions, spring = _class(tmp_path / "spring", count=5)
    _, fall = _class(tmp_path / "fall", count=5)
    prompts: list[str] = []

    def factory(_model: str):  # noqa: ANN202
        def generate(prompt: str) -> str:
            prompts.append(prompt)
            return json.dumps(_agrees())

        return generate

    llm_eval.run_cli(
        [
            "--solutions",
            str(solutions),
            "--input",
            str(spring),
            "--input",
            str(fall),
            "--limit",
            "4",
            "--quiet",
        ],
        generate_factory=factory,
    )
    assert len(prompts) == 4
    assert sum("answer number 0" in p for p in prompts) == 2


def test_cohort_labels_shorten_to_the_folder_name_when_that_is_unique() -> None:
    labels = llm_eval.class_labels(
        [Path("/exams/spring-2026"), Path("/exams/fall-2025")]
    )
    assert labels == ["spring-2026", "fall-2025"]


def test_cohort_labels_walk_up_when_the_folder_names_collide() -> None:
    labels = llm_eval.class_labels(
        [
            Path("/exams/spring-2026/student-submissions"),
            Path("/exams/fall-2025/student-submissions"),
        ]
    )
    assert labels == [
        "spring-2026/student-submissions",
        "fall-2025/student-submissions",
    ]


def test_resolve_cohorts_drops_repeats_but_keeps_order(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    roots = llm_eval.resolve_classes([str(first), str(second), str(first)])
    assert [root.name for root in roots] == ["a", "b"]


def test_default_generate_factory_wires_grade_suggestion_schema(monkeypatch) -> None:
    from opengrader.core.grading.llm_grader import GRADE_SUGGESTION_SCHEMA

    captured_kwargs: dict = {}

    def fake_generate(*, prompt, config, **kwargs):
        captured_kwargs.update(kwargs)
        return "{}"

    monkeypatch.setattr(llm_eval, "ensure_model_available", lambda config: None)
    monkeypatch.setattr(llm_eval, "generate", fake_generate)

    factory = llm_eval._default_generate_factory(
        "http://127.0.0.1:11434", 30.0, auto_ollama=False
    )
    generate_fn = factory("qwen2.5vl:7b")
    generate_fn("some prompt")

    assert captured_kwargs["response_format"] == GRADE_SUGGESTION_SCHEMA
