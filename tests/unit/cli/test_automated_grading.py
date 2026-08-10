from __future__ import annotations

import json
from pathlib import Path

import pytest

from opengrader.cli.automated_grading import grade_student_file, run_cli
from opengrader.core import ai_config
from opengrader.core.qa_file import load_qa_markdown
from opengrader.core.submissions import load_student_submission_markdown

SOLUTIONS_MD = """\
---
title: |
  Test Exam
instructions: Answer all questions.
questions_start_page: 1
---

- (10 points) Which data structure uses FIFO ordering?
  - **Choices**
    - A. Stack
    - B. Queue
    - C. Tree
    - D. Map
  - **Correct**
    - B
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)

- (10 points) Which number is prime?
  - **Choices**
    - A. 21
    - B. 25
    - C. 29
    - D. 35
  - **Correct**
    - C
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)

- (5 points) What is 2 + 2?
  - **Correct**
    - 4
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-2)
    - Incorrect (-5)

- (5 points) What color is the sky?
  - **Correct**
    - blue
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-2)
    - Incorrect (-5)

- (5 points) Name a sorting algorithm.
  - **Correct**
    - quicksort
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-2)
    - Incorrect (-5)
"""

SUBMISSION_MD = """\
---
student_name: "Test Student"
student_id: "123456789"
course_code: "CS146"
assignment_title: "Test Exam"
source_pdf: "student.pdf"
student_score: ""
---


- q1:
  - Question type: Multiple Choice
  - Prompt: Which data structure uses FIFO ordering?
  - Response text: b.
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:

- q2:
  - Question type: Multiple Choice
  - Prompt: Which number is prime?
  - Response text: A
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:

- q3:
  - Question type: Free Response
  - Prompt: What is 2 + 2?
  - Response text: 4
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:

- q4:
  - Question type: Free Response
  - Prompt: What color is the sky?
  - Response text: green
  - Possible points:
  - Points: 3.0
  - Rubric:
  - Analysis: Partially correct per manual review

- q5:
  - Question type: Free Response
  - Prompt: Name a sorting algorithm.
  - Response text:
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:
"""


def _grade(tmp_path: Path) -> dict[str, object]:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(SUBMISSION_MD)

    run_cli(["--solutions", str(solutions_path), "--input", str(submission_path)])

    document = load_student_submission_markdown(submission_path)
    return {question.question_id: question for question in document.questions}


def test_correct_multiple_choice_earns_full_points(tmp_path: Path) -> None:
    graded = _grade(tmp_path)

    # "b." matches choice label "B" despite case/punctuation differences.
    assert graded["q1"].points_awarded == 10.0
    assert graded["q1"].possible_points == 10.0
    assert graded["q1"].analysis == "Correct"


def test_incorrect_multiple_choice_earns_zero(tmp_path: Path) -> None:
    graded = _grade(tmp_path)

    assert graded["q2"].points_awarded == 0.0
    assert graded["q2"].possible_points == 10.0


def test_correct_free_response_earns_full_points(tmp_path: Path) -> None:
    graded = _grade(tmp_path)

    assert graded["q3"].points_awarded == 5.0
    assert graded["q3"].analysis == "Correct"


def test_incorrect_free_response_is_left_for_manual_grading(tmp_path: Path) -> None:
    graded = _grade(tmp_path)

    # Pre-existing manual grade and analysis must survive re-grading.
    assert graded["q4"].points_awarded == 3.0
    assert graded["q4"].analysis == "Partially correct per manual review"
    assert graded["q4"].possible_points == 5.0


def test_empty_response_is_skipped(tmp_path: Path) -> None:
    graded = _grade(tmp_path)

    assert graded["q5"].points_awarded is None
    assert graded["q5"].possible_points is None
    assert graded["q5"].analysis == ""


# --- Numeric and True/False auto-grading ------------------------------------

NUMERIC_TF_SOLUTIONS_MD = """\
---
title: Numeric Exam
questions_start_page: 1
---

- (5 points) How many mL are in a liter?
  - **Correct**
    - 1000 ±10 units:mL

- (5 points) Value of pi to two places?
  - **Correct**
    - 3.14 ±0.01

- (2 points) The Earth is flat.
  - **Correct**
    - False

- (2 points) Water is H2O.
  - **Correct**
    - True
"""

NUMERIC_TF_SUBMISSION_MD = """\
---
student_name: "Test Student"
student_id: "123456789"
course_code: "CS146"
assignment_title: "Numeric Exam"
source_pdf: "student.pdf"
student_score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: How many mL are in a liter?
  - Response text: 1005 mL
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:

- q2:
  - Question type: Free Response
  - Prompt: Value of pi to two places?
  - Response text: 3.2
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:

- q3:
  - Question type: Multiple Choice
  - Prompt: The Earth is flat.
  - Response text: false
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:

- q4:
  - Question type: Multiple Choice
  - Prompt: Water is H2O.
  - Response text: T
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:
"""


def _grade_numeric_tf(tmp_path: Path) -> dict[str, object]:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(NUMERIC_TF_SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(NUMERIC_TF_SUBMISSION_MD)

    run_cli(["--solutions", str(solutions_path), "--input", str(submission_path)])

    document = load_student_submission_markdown(submission_path)
    return {question.question_id: question for question in document.questions}


def test_numeric_within_tolerance_earns_full_points(tmp_path: Path) -> None:
    graded = _grade_numeric_tf(tmp_path)
    # 1005 mL is inside 1000 ± 10, and the unit is ignored.
    assert graded["q1"].points_awarded == 5.0
    assert graded["q1"].analysis == "Correct"


def test_numeric_outside_tolerance_left_for_manual(tmp_path: Path) -> None:
    graded = _grade_numeric_tf(tmp_path)
    # 3.2 is outside 3.14 ± 0.01. Numeric is award-only: a miss is left for
    # manual review (not auto-zeroed), since numeric handwriting is error-prone.
    assert graded["q2"].points_awarded is None
    assert graded["q2"].analysis == ""


def test_true_false_word_answer_grades(tmp_path: Path) -> None:
    graded = _grade_numeric_tf(tmp_path)
    # "false" matches the correct False answer; "T" matches True.
    assert graded["q3"].points_awarded == 2.0
    assert graded["q4"].points_awarded == 2.0


def test_unreadable_numeric_left_for_manual(tmp_path: Path) -> None:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(NUMERIC_TF_SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(
        NUMERIC_TF_SUBMISSION_MD.replace("Response text: 3.2", "Response text: about pi")
    )
    run_cli(["--solutions", str(solutions_path), "--input", str(submission_path)])
    document = load_student_submission_markdown(submission_path)
    graded = {q.question_id: q for q in document.questions}
    # A non-numeric response is not marked wrong; it is left ungraded.
    assert graded["q2"].points_awarded is None


# --- LLM-assisted free-response grading -------------------------------------


class _StubGenerator:
    """Records prompts and returns a canned JSON payload per call."""

    def __init__(self, payload: dict | str) -> None:
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.text


# A submission with ungraded, incorrect free responses for the LLM path.
LLM_SUBMISSION_MD = """\
---
student_name: "Test Student"
student_id: "123456789"
course_code: "CS146"
assignment_title: "Test Exam"
source_pdf: "student.pdf"
student_score: ""
---


- q3:
  - Question type: Free Response
  - Prompt: What is 2 + 2?
  - Response text: 4
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:

- q4:
  - Question type: Free Response
  - Prompt: What color is the sky?
  - Response text: green
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:

- q5:
  - Question type: Free Response
  - Prompt: Name a sorting algorithm.
  - Response text: purple
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:
"""


def _write_case(tmp_path: Path, submission: str = SUBMISSION_MD) -> tuple[Path, object]:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(submission)
    return submission_path, load_qa_markdown(solutions_path)


def _reload(path: Path) -> dict[str, object]:
    document = load_student_submission_markdown(path)
    return {q.question_id: q for q in document.questions}


def test_llm_grades_incorrect_free_response(tmp_path: Path) -> None:
    submission_path, qa_doc = _write_case(tmp_path, LLM_SUBMISSION_MD)
    stub = _StubGenerator(
        {
            "rubric_labels": ["Partially Correct"],
            "confidence": 0.9,
            "rationale": "Right idea, wrong color.",
        }
    )

    grade_student_file(submission_path, qa_doc, llm_generate=stub)

    graded = _reload(submission_path)
    # q4 "green" vs "blue": AI suggested Partially Correct (-2) on a 5-pt item.
    assert graded["q4"].points_awarded == 3.0
    assert graded["q4"].rubric_label == "Partially Correct"
    assert graded["q4"].analysis.startswith("[AI · 90%]")
    assert "wrong color" in graded["q4"].analysis


def test_llm_low_confidence_is_left_for_manual(tmp_path: Path) -> None:
    submission_path, qa_doc = _write_case(tmp_path, LLM_SUBMISSION_MD)
    stub = _StubGenerator({"rubric_labels": ["Incorrect"], "confidence": 0.2})

    grade_student_file(
        submission_path, qa_doc, llm_generate=stub, llm_min_confidence=0.7
    )

    graded = _reload(submission_path)
    # Wrong, ungraded free responses stay untouched when confidence is low.
    assert graded["q4"].points_awarded is None
    assert graded["q5"].points_awarded is None


def test_llm_never_overwrites_existing_grade(tmp_path: Path) -> None:
    submission_path, qa_doc = _write_case(tmp_path)  # q4 pre-graded at 3.0
    stub = _StubGenerator({"rubric_labels": ["Incorrect"], "confidence": 1.0})

    grade_student_file(submission_path, qa_doc, llm_generate=stub)

    graded = _reload(submission_path)
    # q4 already had a manual grade (3.0) and analysis; AI must not touch it.
    assert graded["q4"].points_awarded == 3.0
    assert graded["q4"].analysis == "Partially correct per manual review"


def test_llm_does_not_call_model_for_exact_matches(tmp_path: Path) -> None:
    submission_path, qa_doc = _write_case(tmp_path, LLM_SUBMISSION_MD)
    stub = _StubGenerator({"rubric_labels": ["Partially Correct"], "confidence": 1.0})

    grade_student_file(submission_path, qa_doc, llm_generate=stub)

    # q3 ("4") matches exactly, so its prompt must never reach the LLM.
    assert all("2 + 2" not in prompt for prompt in stub.prompts)
    graded = _reload(submission_path)
    assert graded["q3"].analysis == "Correct"


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    submission_path, qa_doc = _write_case(tmp_path, LLM_SUBMISSION_MD)
    before = submission_path.read_text()
    stub = _StubGenerator({"rubric_labels": ["Incorrect"], "confidence": 1.0})

    grade_student_file(
        submission_path, qa_doc, llm_generate=stub, dry_run=True
    )

    assert submission_path.read_text() == before


def test_select_all_grades_all_or_nothing_award_only() -> None:
    """Select-all is graded on an exact set match; misses are left for review."""
    from opengrader.cli.automated_grading import (
        _make_solution_entry,
        _objective_correctness,
    )
    from opengrader.core.qa_file import parse_qa_markdown

    qa = """
- (3 points) Select every prime number.
  - **Choices**
    - A) 2
    - B) 4
    - C) 5
    - D) 9
  - **Correct**
    - A
    - C
"""
    question = parse_qa_markdown(qa).questions[0]
    entry = _make_solution_entry(
        3.0,
        question.answers,
        question.choices,
        question.correct_choice_label,
        question.correct_choice_labels,
    )

    class _Resp:
        question_type = "multiple_answer"

    # Exact set (any order / separators) -> full credit.
    assert _objective_correctness(entry, "A, C", _Resp) is True
    assert _objective_correctness(entry, "C and A", _Resp) is True
    assert _objective_correctness(entry, "AC", _Resp) is True
    # Subset, superset, or wrong set -> None (manual review, never auto-zeroed).
    assert _objective_correctness(entry, "A", _Resp) is None
    assert _objective_correctness(entry, "A, C, D", _Resp) is None
    assert _objective_correctness(entry, "A, B", _Resp) is None
    assert _objective_correctness(entry, "", _Resp) is None


def test_run_cli_reports_progress(tmp_path: Path) -> None:
    """run_cli fires file_start (with 1-based index/total) and question ticks."""
    from opengrader.cli.automated_grading import GradingProgress

    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    for name in ("a", "b"):
        (tmp_path / f"{name}.submission.md").write_text(SUBMISSION_MD)

    files: list[tuple[str, int, int]] = []
    questions: list[tuple[str, str]] = []
    progress = GradingProgress(
        on_file_start=lambda name, index, total: files.append((name, index, total)),
        on_question=lambda name, qid: questions.append((name, qid)),
    )

    run_cli(["--solutions", str(solutions_path), "--input", str(tmp_path)], progress=progress)

    # Two files, reported with a 1-based index and the cohort total.
    assert files == [("a.submission.md", 1, 2), ("b.submission.md", 2, 2)]
    # Each file ticks per graded (non-empty) question — q5 is blank, so skipped.
    assert ("a.submission.md", "q1") in questions
    assert ("a.submission.md", "q5") not in questions
    assert {name for name, _ in questions} == {"a.submission.md", "b.submission.md"}


def test_run_cli_auto_ollama_emits_pull_status(tmp_path: Path, monkeypatch) -> None:
    """--auto-ollama forwards server-start and model-pull status to the callback."""
    import opengrader.cli.automated_grading as ag

    monkeypatch.setattr(
        ag,
        "_start_ollama_server_if_needed",
        lambda config, emit: emit("Starting Ollama server…"),
    )
    monkeypatch.setattr(ag.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(ag, "ensure_model_available", lambda config: None)
    monkeypatch.setattr(ag, "generate", lambda **k: "{}")

    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(SUBMISSION_MD)

    messages: list[str] = []
    ag.run_cli(
        [
            "--solutions", str(solutions_path),
            "--input", str(submission_path),
            "--llm", "--auto-ollama",
        ],
        status_callback=messages.append,
    )

    assert any("Starting Ollama" in m for m in messages)
    assert any("Pulling model" in m for m in messages)


def test_unknown_version_falls_back_to_first_version(tmp_path: Path) -> None:
    """A submission version that matches no QA version uses the first version
    rather than raising, so one bad/misread string never blocks the run."""
    from opengrader.cli.automated_grading import _solution_entries_for_submission
    from opengrader.core.submissions import StudentSubmissionDocument

    qa_path = tmp_path / "qa.md"
    qa_path.write_text(
        "\n".join(
            [
                "---",
                "title: Quiz",
                "---",
                "",
                "## Version A",
                "",
                "- (1 points) PromptA",
                "  - **Correct**",
                "    - x",
                "",
                "## Version B",
                "",
                "- (1 points) PromptB",
                "  - **Correct**",
                "    - y",
                "",
            ]
        )
    )
    qa_doc = load_qa_markdown(qa_path)
    document = StudentSubmissionDocument(
        student_name="S",
        student_id="1",
        course_code="C",
        assignment_title="Quiz",
        assignment_version="Z",  # matches neither A nor B
        source_pdf="s.pdf",
        questions=[],
    )

    entries = _solution_entries_for_submission(qa_doc, document)
    # Fell back to version A (the first), not raised.
    assert any(entry.prompt == "PromptA" for entry in entries.values())
    assert not any(entry.prompt == "PromptB" for entry in entries.values())


# ── audit journalling (what makes a batch run reversible) ─────────────────


def _journal_entries(submissions_dir: Path):  # noqa: ANN202
    from opengrader.core.history import read_journal, resolve_history_dir

    return read_journal(resolve_history_dir(submissions_dir))


def test_a_batch_run_records_every_grade_it_writes(tmp_path: Path) -> None:
    # Without this the riskiest operation in the app — grading a whole cohort
    # unattended — is the one thing grade-restore cannot undo.
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    (tmp_path / "a.submission.md").write_text(SUBMISSION_MD)

    run_cli(["--solutions", str(solutions_path), "--input", str(tmp_path)])

    entries = _journal_entries(tmp_path)
    assert entries, "a batch run must be journalled"
    graded = [e for e in entries if e.kind == "grade"]
    assert graded
    # Recorded under the tool's own name, so undoing the batch cannot also undo
    # the human's hand grading.
    assert {e.actor for e in graded} == {"automated-grading"}
    # And each carries the before/after a restore needs.
    assert all("points" in e.old and "points" in e.new for e in graded)


def test_the_operator_is_named_even_though_the_actor_is_the_tool(tmp_path: Path) -> None:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    (tmp_path / "a.submission.md").write_text(SUBMISSION_MD)

    run_cli(["--solutions", str(solutions_path), "--input", str(tmp_path)])

    graded = [e for e in _journal_entries(tmp_path) if e.kind == "grade"]
    assert all("run by " in e.summary for e in graded)


def test_a_dry_run_journals_nothing(tmp_path: Path) -> None:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    (tmp_path / "a.submission.md").write_text(SUBMISSION_MD)

    run_cli([
        "--solutions", str(solutions_path), "--input", str(tmp_path), "--dry-run"
    ])

    assert _journal_entries(tmp_path) == []


def test_no_journal_opts_out(tmp_path: Path) -> None:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    (tmp_path / "a.submission.md").write_text(SUBMISSION_MD)

    run_cli([
        "--solutions", str(solutions_path), "--input", str(tmp_path), "--no-journal"
    ])

    assert _journal_entries(tmp_path) == []


def test_re_running_an_unchanged_cohort_records_no_new_grade_events(
    tmp_path: Path,
) -> None:
    # A journal full of non-events would make grade-restore propose restores
    # that change nothing.
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    (tmp_path / "a.submission.md").write_text(SUBMISSION_MD)

    args = ["--solutions", str(solutions_path), "--input", str(tmp_path)]
    run_cli(args)
    first = len(_journal_entries(tmp_path))
    run_cli(args)
    assert len(_journal_entries(tmp_path)) == first


def test_the_run_points_the_user_at_the_undo(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    (tmp_path / "a.submission.md").write_text(SUBMISSION_MD)

    run_cli(["--solutions", str(solutions_path), "--input", str(tmp_path)])

    out = capsys.readouterr().out
    assert "grade-restore apply" in out
    assert "--without-actor automated-grading" in out


def test_a_batch_run_is_undone_by_grade_restore(tmp_path: Path) -> None:
    # The end-to-end contract the two features form together: undoing the batch
    # clears exactly what the batch wrote, and leaves a human's existing grade
    # (q4, already 3.0 in the fixture) untouched.
    from opengrader.cli import grade_restore

    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission = tmp_path / "a.submission.md"
    submission.write_text(SUBMISSION_MD)

    before = {
        q.question_id: q.points_awarded
        for q in load_student_submission_markdown(submission).questions
    }
    assert before["q1"] is None and before["q4"] == 3.0

    run_cli(["--solutions", str(solutions_path), "--input", str(tmp_path)])
    graded = {
        q.question_id: q.points_awarded
        for q in load_student_submission_markdown(submission).questions
    }
    assert graded["q1"] is not None

    grade_restore.run_cli([
        "apply", "--submissions", str(tmp_path),
        "--without-actor", "automated-grading", "--yes",
    ])

    restored = {
        q.question_id: q.points_awarded
        for q in load_student_submission_markdown(submission).questions
    }
    assert restored == before


# ── grade the answer, not the student ────────────────────────────────────────


def _cohort_submission(name: str, student_id: str, q4_answer: str) -> str:
    """A minimal one-question submission, for cohort-level grouping tests."""
    return f"""\
---
student_name: "{name}"
student_id: "{student_id}"
course_code: "CS146"
assignment_title: "Test Exam"
source_pdf: "{student_id}.pdf"
student_score: ""
---

- q4:
  - Question type: Free Response
  - Prompt: What color is the sky?
  - Response text: {q4_answer}
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:
"""


def _write_cohort(tmp_path: Path, answers: list[str]) -> Path:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    subs = tmp_path / "student-submissions"
    subs.mkdir()
    for index, answer in enumerate(answers, start=1):
        (subs / f"s{index}.submission.md").write_text(
            _cohort_submission(f"Student {index}", f"10000{index}", answer)
        )
    return solutions_path


def test_identical_answers_cost_one_model_call_across_the_cohort(
    tmp_path: Path, monkeypatch
) -> None:
    # Ten students, three distinct answers: three decisions, not ten.
    answers = ["green", "green", "green", "purple", "purple", "green"]
    solutions_path = _write_cohort(tmp_path, answers)
    stub = _StubGenerator(
        {"rubric_labels": ["Partially Correct"], "confidence": 0.9, "rationale": "ok"}
    )
    monkeypatch.setattr(
        "opengrader.cli.automated_grading.ensure_model_available", lambda config: None
    )
    monkeypatch.setattr(
        "opengrader.cli.automated_grading.generate",
        lambda *, prompt, config, **_kwargs: stub(prompt),
    )

    run_cli(
        [
            "--solutions",
            str(solutions_path),
            "--input",
            str(tmp_path / "student-submissions"),
            "--llm",
            "--no-journal",
        ]
    )

    assert len(stub.prompts) == 2  # "green" and "purple"


def test_identical_answers_receive_the_same_grade(tmp_path: Path, monkeypatch) -> None:
    # The fairness property, and the reason this matters more than the speed:
    # a cohort's identical answers cannot diverge, because there is only one
    # decision. A model that answered differently the second time would
    # otherwise trip the project's own consistency audit.
    solutions_path = _write_cohort(tmp_path, ["green", "green"])
    replies = iter(
        [
            json.dumps(
                {"rubric_labels": ["Partially Correct"], "confidence": 0.9,
                 "rationale": "half right"}
            ),
            json.dumps(
                {"rubric_labels": ["Incorrect"], "confidence": 0.9, "rationale": "no"}
            ),
        ]
    )
    monkeypatch.setattr(
        "opengrader.cli.automated_grading.ensure_model_available", lambda config: None
    )
    monkeypatch.setattr(
        "opengrader.cli.automated_grading.generate",
        lambda *, prompt, config, **_kwargs: next(replies),
    )

    run_cli(
        [
            "--solutions",
            str(solutions_path),
            "--input",
            str(tmp_path / "student-submissions"),
            "--llm",
            "--no-journal",
        ]
    )

    subs = tmp_path / "student-submissions"
    first = _reload(subs / "s1.submission.md")["q4"]
    second = _reload(subs / "s2.submission.md")["q4"]
    assert first.points_awarded == second.points_awarded == 3.0
    assert first.rubric_labels == second.rubric_labels


def test_different_answers_are_still_graded_separately(
    tmp_path: Path, monkeypatch
) -> None:
    solutions_path = _write_cohort(tmp_path, ["green", "blue-ish"])
    stub = _StubGenerator(
        {"rubric_labels": ["Partially Correct"], "confidence": 0.9, "rationale": "ok"}
    )
    monkeypatch.setattr(
        "opengrader.cli.automated_grading.ensure_model_available", lambda config: None
    )
    monkeypatch.setattr(
        "opengrader.cli.automated_grading.generate",
        lambda *, prompt, config, **_kwargs: stub(prompt),
    )

    run_cli(
        [
            "--solutions",
            str(solutions_path),
            "--input",
            str(tmp_path / "student-submissions"),
            "--llm",
            "--no-journal",
        ]
    )

    assert len(stub.prompts) == 2


def test_model_name_is_recorded_in_the_grade(tmp_path: Path, monkeypatch) -> None:
    solutions_path = _write_cohort(tmp_path, ["green"])
    monkeypatch.setattr(
        "opengrader.cli.automated_grading.ensure_model_available", lambda config: None
    )
    monkeypatch.setattr(
        "opengrader.cli.automated_grading.generate",
        lambda *, prompt, config, **_kwargs: json.dumps(
            {"rubric_labels": ["Partially Correct"], "confidence": 0.9, "rationale": "ok"}
        ),
    )

    run_cli(
        [
            "--solutions",
            str(solutions_path),
            "--input",
            str(tmp_path / "student-submissions"),
            "--llm",
            "--llm-model",
            "llama3.1",
            "--no-journal",
        ]
    )

    graded = _reload(tmp_path / "student-submissions" / "s1.submission.md")
    assert "llama3.1" in graded["q4"].analysis


def test_llm_generate_lambda_passes_grade_suggestion_schema(
    tmp_path: Path, monkeypatch
) -> None:
    """The injected ``generate`` call site wires GRADE_SUGGESTION_SCHEMA through
    as Ollama's ``format`` so grading replies are structurally valid JSON."""
    from opengrader.core.grading.llm_grader import GRADE_SUGGESTION_SCHEMA

    solutions_path = _write_cohort(tmp_path, ["green"])
    captured_kwargs: dict = {}

    def fake_generate(*, prompt, config, **kwargs):
        captured_kwargs.update(kwargs)
        return json.dumps(
            {"rubric_labels": ["Partially Correct"], "confidence": 0.9, "rationale": "ok"}
        )

    monkeypatch.setattr(
        "opengrader.cli.automated_grading.ensure_model_available", lambda config: None
    )
    monkeypatch.setattr("opengrader.cli.automated_grading.generate", fake_generate)

    run_cli(
        [
            "--solutions",
            str(solutions_path),
            "--input",
            str(tmp_path / "student-submissions"),
            "--llm",
            "--no-journal",
        ]
    )

    assert captured_kwargs["response_format"] == GRADE_SUGGESTION_SCHEMA


# ── never grade a transcription the OCR doubts ───────────────────────────────


LOW_CONFIDENCE_SUBMISSION_MD = """\
---
student_name: "Test Student"
student_id: "123456789"
course_code: "CS146"
assignment_title: "Test Exam"
source_pdf: "student.pdf"
student_score: ""
---

- q4:
  - Question type: Free Response
  - Prompt: What color is the sky?
  - Response text: gr33n smudge
  - Confidence: 32
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:
"""


def test_weak_transcription_is_not_sent_to_the_model(tmp_path: Path) -> None:
    # The model reads the text and never the image, so below the floor it would
    # be grading a string nobody vouched for — confidently.
    submission_path, qa_doc = _write_case(tmp_path, LOW_CONFIDENCE_SUBMISSION_MD)
    stub = _StubGenerator({"rubric_labels": ["Incorrect"], "confidence": 1.0})

    grade_student_file(submission_path, qa_doc, llm_generate=stub)

    assert stub.prompts == []
    assert _reload(submission_path)["q4"].points_awarded is None


def test_weak_transcription_can_be_graded_when_the_floor_is_disabled(
    tmp_path: Path,
) -> None:
    submission_path, qa_doc = _write_case(tmp_path, LOW_CONFIDENCE_SUBMISSION_MD)
    stub = _StubGenerator(
        {"rubric_labels": ["Incorrect"], "confidence": 1.0, "rationale": "no"}
    )

    grade_student_file(
        submission_path, qa_doc, llm_generate=stub, min_transcription_confidence=0
    )

    assert len(stub.prompts) == 1
    assert _reload(submission_path)["q4"].points_awarded == 0.0


def test_unmeasured_confidence_is_still_graded(tmp_path: Path) -> None:
    # `None` means unmeasured, never "bad" — the convention the whole codebase
    # keeps. The VLM path reads handwriting and reports no number, so treating
    # None as untrusted would withdraw AI grading from nearly every real exam.
    submission_path, qa_doc = _write_case(tmp_path, LLM_SUBMISSION_MD)
    stub = _StubGenerator(
        {"rubric_labels": ["Partially Correct"], "confidence": 0.9, "rationale": "ok"}
    )

    grade_student_file(submission_path, qa_doc, llm_generate=stub)

    assert stub.prompts  # q4/q5 have no Confidence line and were still graded
    assert _reload(submission_path)["q4"].points_awarded == 3.0


def test_strong_transcription_is_graded(tmp_path: Path) -> None:
    submission = LOW_CONFIDENCE_SUBMISSION_MD.replace(
        "  - Confidence: 32", "  - Confidence: 95"
    )
    submission_path, qa_doc = _write_case(tmp_path, submission)
    stub = _StubGenerator(
        {"rubric_labels": ["Incorrect"], "confidence": 1.0, "rationale": "no"}
    )

    grade_student_file(submission_path, qa_doc, llm_generate=stub)

    assert len(stub.prompts) == 1


# --------------------------------------------------------------------------- #
# --llm follows AI Setup, and the calibration gate refuses before any grading
# work when `require_calibration` is on but never measured.
# --------------------------------------------------------------------------- #


def test_llm_defaults_on_from_ai_setup(tmp_path: Path, monkeypatch) -> None:
    import opengrader.cli.automated_grading as ag

    ai_config.save_settings(ai_config.AISettings(grade_written=True))
    monkeypatch.setattr(ag, "ensure_model_available", lambda config: None)
    monkeypatch.setattr(ag, "generate", lambda **k: "{}")

    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(SUBMISSION_MD)

    messages: list[str] = []
    run_cli(
        ["--solutions", str(solutions_path), "--input", str(submission_path)],
        status_callback=messages.append,
    )

    assert any("LLM free-response grading enabled" in m for m in messages)


def test_llm_defaults_off_when_ai_setup_leaves_it_off(tmp_path: Path, monkeypatch) -> None:
    import opengrader.cli.automated_grading as ag

    ai_config.save_settings(ai_config.AISettings())

    def _boom(*_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError("no model call should be attempted with --llm off")

    monkeypatch.setattr(ag, "ensure_model_available", _boom)

    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(SUBMISSION_MD)

    run_cli(["--solutions", str(solutions_path), "--input", str(submission_path)])


def test_explicit_no_llm_beats_ai_setup(tmp_path: Path, monkeypatch) -> None:
    """An explicit CLI flag must still win over a settings file: CLI > env > file."""
    import opengrader.cli.automated_grading as ag

    ai_config.save_settings(ai_config.AISettings(grade_written=True))

    def _boom(*_a, **_k):  # pragma: no cover - must never be reached
        raise AssertionError("--no-llm must turn AI grading off even with it saved on")

    monkeypatch.setattr(ag, "ensure_model_available", _boom)

    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(SUBMISSION_MD)

    run_cli(
        ["--solutions", str(solutions_path), "--input", str(submission_path), "--no-llm"]
    )


def test_llm_refuses_when_calibration_is_required_but_unmeasured(
    tmp_path: Path, capsys
) -> None:
    ai_config.save_settings(
        ai_config.AISettings(
            grade_written=True,
            require_calibration=True,
            min_confidence_measured=False,
        )
    )

    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(SUBMISSION_MD)

    with pytest.raises(SystemExit) as exit_info:
        run_cli(["--solutions", str(solutions_path), "--input", str(submission_path)])

    # 2, not 1: "refused, not crashed" — matching grade-check --fail-on-blocking
    # and claim --fail-on-overlap.
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "never measured" in err
    assert "llm-eval" in err
    # Nothing must have been graded: the file is untouched.
    assert submission_path.read_text() == SUBMISSION_MD


def test_llm_does_not_refuse_once_calibration_is_measured(
    tmp_path: Path, monkeypatch
) -> None:
    import opengrader.cli.automated_grading as ag

    ai_config.save_settings(
        ai_config.AISettings(
            grade_written=True,
            require_calibration=True,
            min_confidence_measured=True,
        )
    )
    monkeypatch.setattr(ag, "ensure_model_available", lambda config: None)
    monkeypatch.setattr(ag, "generate", lambda **k: "{}")

    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(SUBMISSION_MD)

    # No SystemExit: the gate must not fire once provenance says the
    # confidence floor was actually measured.
    run_cli(["--solutions", str(solutions_path), "--input", str(submission_path)])
