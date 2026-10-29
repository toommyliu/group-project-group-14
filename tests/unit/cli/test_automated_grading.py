from __future__ import annotations

import json
from pathlib import Path

import pytest

from opengrader.cli import run_command
from opengrader.cli.automated_grading import grade_student_file, run_cli
from opengrader.core import app_config
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

    assert graded["q4"].points_awarded == 3.0
    assert graded["q4"].analysis == "Partially correct per manual review"
    assert graded["q4"].possible_points == 5.0


def test_empty_response_is_skipped(tmp_path: Path) -> None:
    graded = _grade(tmp_path)

    assert graded["q5"].points_awarded is None
    assert graded["q5"].possible_points is None
    assert graded["q5"].analysis == ""


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
    assert graded["q1"].points_awarded == 5.0
    assert graded["q1"].analysis == "Correct"


def test_numeric_outside_tolerance_left_for_manual(tmp_path: Path) -> None:
    graded = _grade_numeric_tf(tmp_path)
    assert graded["q2"].points_awarded is None
    assert graded["q2"].analysis == ""


def test_true_false_word_answer_grades(tmp_path: Path) -> None:
    graded = _grade_numeric_tf(tmp_path)
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
    assert graded["q2"].points_awarded is None


class _StubGenerator:
    def __init__(self, payload: dict | str) -> None:
        self.text = payload if isinstance(payload, str) else json.dumps(payload)
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.text


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
    assert graded["q4"].points_awarded is None
    assert graded["q5"].points_awarded is None


def test_llm_never_overwrites_existing_grade(tmp_path: Path) -> None:
    submission_path, qa_doc = _write_case(tmp_path)
    stub = _StubGenerator({"rubric_labels": ["Incorrect"], "confidence": 1.0})

    grade_student_file(submission_path, qa_doc, llm_generate=stub)

    graded = _reload(submission_path)
    assert graded["q4"].points_awarded == 3.0
    assert graded["q4"].analysis == "Partially correct per manual review"


def test_llm_does_not_call_model_for_exact_matches(tmp_path: Path) -> None:
    submission_path, qa_doc = _write_case(tmp_path, LLM_SUBMISSION_MD)
    stub = _StubGenerator({"rubric_labels": ["Partially Correct"], "confidence": 1.0})

    grade_student_file(submission_path, qa_doc, llm_generate=stub)

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

    assert _objective_correctness(entry, "A, C", _Resp) is True
    assert _objective_correctness(entry, "C and A", _Resp) is True
    assert _objective_correctness(entry, "AC", _Resp) is True
    assert _objective_correctness(entry, "A", _Resp) is None
    assert _objective_correctness(entry, "A, C, D", _Resp) is None
    assert _objective_correctness(entry, "A, B", _Resp) is None
    assert _objective_correctness(entry, "", _Resp) is None


def test_run_cli_reports_progress(tmp_path: Path) -> None:
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

    assert files == [("a.submission.md", 1, 2), ("b.submission.md", 2, 2)]
    assert ("a.submission.md", "q1") in questions
    assert ("a.submission.md", "q5") not in questions
    assert {name for name, _ in questions} == {"a.submission.md", "b.submission.md"}


def test_an_unknown_version_gets_no_answer_key_rather_than_the_first(tmp_path: Path) -> None:
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
        assignment_version="Z",
        source_pdf="s.pdf",
        questions=[],
    )

    # Grading a Version Z paper against Version A would look right and be
    # wrong: nothing is graded until someone picks its version.
    assert _solution_entries_for_submission(qa_doc, document) == {}


def _journal_entries(submissions_dir: Path):  # noqa: ANN202
    from opengrader.core.history import read_journal, resolve_history_dir

    return read_journal(resolve_history_dir(submissions_dir))


def test_a_batch_run_records_every_grade_it_writes(tmp_path: Path) -> None:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    (tmp_path / "a.submission.md").write_text(SUBMISSION_MD)

    run_cli(["--solutions", str(solutions_path), "--input", str(tmp_path)])

    entries = _journal_entries(tmp_path)
    assert entries, "a batch run must be journalled"
    graded = [e for e in entries if e.kind == "grade"]
    assert graded
    assert {e.actor for e in graded} == {"automated-grading"}
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

    run_command(grade_restore.command, [
        "apply", "--submissions", str(tmp_path),
        "--without-actor", "automated-grading", "--yes",
    ])

    restored = {
        q.question_id: q.points_awarded
        for q in load_student_submission_markdown(submission).questions
    }
    assert restored == before


def _cohort_submission(name: str, student_id: str, q4_answer: str) -> str:
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
    answers = ["green", "green", "green", "purple", "purple", "green"]
    solutions_path = _write_cohort(tmp_path, answers)
    stub = _StubGenerator(
        {"rubric_labels": ["Partially Correct"], "confidence": 0.9, "rationale": "ok"}
    )
    monkeypatch.setattr(app_config, "ensure_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "opengrader.core.llm.generate",
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


def test_identical_answers_receive_the_same_grade(tmp_path: Path, monkeypatch) -> None:
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
    monkeypatch.setattr(app_config, "ensure_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "opengrader.core.llm.generate",
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
    monkeypatch.setattr(app_config, "ensure_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "opengrader.core.llm.generate",
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
    monkeypatch.setattr(app_config, "ensure_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "opengrader.core.llm.generate",
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
    from opengrader.core.grading.llm_grader import GRADE_SUGGESTION_SCHEMA

    solutions_path = _write_cohort(tmp_path, ["green"])
    captured_kwargs: dict = {}

    def fake_generate(*, prompt, config, **kwargs):
        captured_kwargs.update(kwargs)
        return json.dumps(
            {"rubric_labels": ["Partially Correct"], "confidence": 0.9, "rationale": "ok"}
        )

    monkeypatch.setattr(app_config, "ensure_ready", lambda *_a, **_k: None)
    monkeypatch.setattr("opengrader.core.llm.generate", fake_generate)

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
    submission_path, qa_doc = _write_case(tmp_path, LLM_SUBMISSION_MD)
    stub = _StubGenerator(
        {"rubric_labels": ["Partially Correct"], "confidence": 0.9, "rationale": "ok"}
    )

    grade_student_file(submission_path, qa_doc, llm_generate=stub)

    assert stub.prompts
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


def test_llm_defaults_on_from_ai_setup(tmp_path: Path, monkeypatch) -> None:
    import opengrader.cli.automated_grading as ag

    app_config.save_settings(app_config.AISettings(grade_written=True))
    monkeypatch.setattr(app_config, "ensure_ready", lambda *_a, **_k: None)
    monkeypatch.setattr("opengrader.core.llm.generate", lambda **k: "{}")

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

    app_config.save_settings(app_config.AISettings())

    def _boom(*_a, **_k):  # pragma: no cover
        raise AssertionError("no model call should be attempted with --llm off")

    monkeypatch.setattr(app_config, "ensure_ready", _boom)

    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(SUBMISSION_MD)

    run_cli(["--solutions", str(solutions_path), "--input", str(submission_path)])


def test_explicit_no_llm_beats_ai_setup(tmp_path: Path, monkeypatch) -> None:
    import opengrader.cli.automated_grading as ag

    app_config.save_settings(app_config.AISettings(grade_written=True))

    def _boom(*_a, **_k):  # pragma: no cover
        raise AssertionError("--no-llm must turn AI grading off even with it saved on")

    monkeypatch.setattr(app_config, "ensure_ready", _boom)

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
    app_config.save_settings(
        app_config.AISettings(
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

    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert "never measured" in err
    assert "llm-eval" in err
    assert submission_path.read_text() == SUBMISSION_MD


def test_llm_does_not_refuse_once_calibration_is_measured(
    tmp_path: Path, monkeypatch
) -> None:
    import opengrader.cli.automated_grading as ag

    app_config.save_settings(
        app_config.AISettings(
            grade_written=True,
            require_calibration=True,
            min_confidence_measured=True,
        )
    )
    monkeypatch.setattr(app_config, "ensure_ready", lambda *_a, **_k: None)
    monkeypatch.setattr("opengrader.core.llm.generate", lambda **k: "{}")

    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(SUBMISSION_MD)

    run_cli(["--solutions", str(solutions_path), "--input", str(submission_path)])


_FREE_RESPONSE_QA = """---
title: T
---

- (5 points) Explain recursion.
  - **Correct**
    - A function that calls itself with a smaller input.
  - **Rubric**
    - Correct (+0)
{partial}    - Incorrect (-5)

- (5 points) What is 2 + 2?
  - **Correct**
    - 4
"""

_BARE_PARTIAL = "    - Partially Correct (-3)\n"


def _llm_run(tmp_path: Path, monkeypatch, partial: str) -> list[str]:
    import opengrader.cli.automated_grading as ag

    monkeypatch.setattr(app_config, "ensure_ready", lambda *_a, **_k: None)
    monkeypatch.setattr("opengrader.core.llm.generate", lambda **k: "{}")
    solutions_path = tmp_path / "qa.md"
    solutions_path.write_text(_FREE_RESPONSE_QA.format(partial=partial))
    (tmp_path / "student.submission.md").write_text(SUBMISSION_MD)
    messages: list[str] = []
    ag.run_cli(
        ["--solutions", str(solutions_path), "--input", str(tmp_path), "--llm"],
        status_callback=messages.append,
    )
    return messages


def test_llm_run_names_written_questions_without_partial_credit(tmp_path, monkeypatch) -> None:
    messages = _llm_run(tmp_path, monkeypatch, partial=_BARE_PARTIAL)
    note = next(m for m in messages if m.startswith("Note:"))
    # q2 is numeric, graded exactly, so it never needs drafted items.
    assert note.startswith("Note: q1 has no partial-credit rubric items yet")
    assert "q2" not in note.splitlines()[0]
    assert "opengrader draft-rubric --solutions" in note


def test_no_hint_once_partial_credit_items_exist(tmp_path, monkeypatch) -> None:
    messages = _llm_run(
        tmp_path,
        monkeypatch,
        partial="    - Partially Correct\n"
        "      - Mentions self-calls but no base case (-2)\n"
        "      - Describes iteration instead (-3)\n",
    )
    assert not any(m.startswith("Note:") for m in messages)


def test_no_hint_without_llm(tmp_path) -> None:
    import opengrader.cli.automated_grading as ag

    solutions_path = tmp_path / "qa.md"
    solutions_path.write_text(_FREE_RESPONSE_QA.format(partial=_BARE_PARTIAL))
    (tmp_path / "student.submission.md").write_text(SUBMISSION_MD)
    messages: list[str] = []
    ag.run_cli(
        ["--solutions", str(solutions_path), "--input", str(tmp_path)],
        status_callback=messages.append,
    )
    assert not any(m.startswith("Note:") for m in messages)


# Auto-grading again never scores over a grade a person gave
# (core/grading/ownership.py).

def _cohort_case(tmp_path: Path) -> tuple[Path, Path, Path]:
    solutions = tmp_path / "solutions.md"
    solutions.write_text(SOLUTIONS_MD)
    submissions = tmp_path / "submissions"
    (submissions / "s1").mkdir(parents=True)
    student = submissions / "s1" / "s1.submission.md"
    student.write_text(SUBMISSION_MD)
    return solutions, submissions, student


def _by_hand(student: Path, question_id: str, **fields) -> None:
    from opengrader.core.submissions import StudentSubmissionGradeUpdate, update_student_submission_file

    update_student_submission_file(student, StudentSubmissionGradeUpdate(question_id=question_id, **fields))


def _auto_grade(solutions: Path, submissions: Path, *extra: str) -> None:
    run_cli(["--solutions", str(solutions), "--input", str(submissions), *extra])


def test_partial_credit_given_by_hand_survives_grading_again(tmp_path: Path, capsys) -> None:
    solutions, submissions, student = _cohort_case(tmp_path)
    _auto_grade(solutions, submissions)
    assert _reload(student)["q1"].points_awarded == 10

    _by_hand(student, "q1", points_awarded=7, rubric_labels=())
    _auto_grade(solutions, submissions)

    assert _reload(student)["q1"].points_awarded == 7
    out = capsys.readouterr().out
    assert "Kept a grade a person gave: q1 (auto-grade gives 10/10)" in out
    assert "Kept 1 grade(s) a person gave" in out


def test_overwrite_grades_re_scores_them_as_before(tmp_path: Path) -> None:
    solutions, submissions, student = _cohort_case(tmp_path)
    _auto_grade(solutions, submissions)
    _by_hand(student, "q1", points_awarded=7, rubric_labels=())
    _auto_grade(solutions, submissions, "--overwrite-grades")
    assert _reload(student)["q1"].points_awarded == 10


def test_a_fixed_answer_key_still_re_scores_what_auto_grading_wrote(tmp_path: Path) -> None:
    solutions, submissions, student = _cohort_case(tmp_path)
    # q2's key wrongly says A, which this student chose.
    solutions.write_text(SOLUTIONS_MD.replace("    - C\n  - **Rubric**", "    - A\n  - **Rubric**", 1))
    _auto_grade(solutions, submissions)
    assert (_reload(student)["q2"].points_awarded, _reload(student)["q2"].analysis) == (10, "Correct")

    solutions.write_text(SOLUTIONS_MD)
    _auto_grade(solutions, submissions)
    # Re-scored, and its own "Correct" does not stay on a wrong answer.
    assert (_reload(student)["q2"].points_awarded, _reload(student)["q2"].analysis) == (0, "")


def test_a_comment_given_by_hand_stays_while_the_score_follows_the_key(tmp_path: Path) -> None:
    solutions, submissions, student = _cohort_case(tmp_path)
    solutions.write_text(SOLUTIONS_MD.replace("    - C\n  - **Rubric**", "    - A\n  - **Rubric**", 1))
    _auto_grade(solutions, submissions)
    _by_hand(student, "q2", analysis="Good reasoning, see me about 29.")

    solutions.write_text(SOLUTIONS_MD)
    _auto_grade(solutions, submissions)
    q2 = _reload(student)["q2"]
    assert (q2.points_awarded, q2.analysis) == (0, "Good reasoning, see me about 29.")


def test_a_folder_graded_before_the_record_falls_back_to_the_history(tmp_path: Path) -> None:
    from opengrader.core.grading.ownership import RECORD_FILENAME

    solutions, submissions, student = _cohort_case(tmp_path)
    solutions.write_text(SOLUTIONS_MD.replace("    - C\n  - **Rubric**", "    - A\n  - **Rubric**", 1))
    _auto_grade(solutions, submissions)
    (submissions / ".opengrader" / RECORD_FILENAME).unlink()

    solutions.write_text(SOLUTIONS_MD)
    _auto_grade(solutions, submissions)
    # The history says the auto-grader wrote q2's 10: still its to change.
    assert _reload(student)["q2"].points_awarded == 0


def test_a_grade_that_came_with_the_file_is_a_persons(tmp_path: Path) -> None:
    solutions, submissions, student = _cohort_case(tmp_path)
    # Graded before any auto-grading ran here, and never journalled.
    student.write_text(SUBMISSION_MD.replace("  - Response text: b.\n  - Possible points:\n  - Points:", "  - Response text: b.\n  - Possible points:\n  - Points: 7", 1))
    assert _reload(student)["q1"].points_awarded == 7

    _auto_grade(solutions, submissions)
    assert _reload(student)["q1"].points_awarded == 7


def test_a_dry_run_remembers_nothing(tmp_path: Path) -> None:
    from opengrader.core.grading.ownership import RECORD_FILENAME

    solutions, submissions, _student = _cohort_case(tmp_path)
    _auto_grade(solutions, submissions, "--dry-run")
    assert not (submissions / ".opengrader" / RECORD_FILENAME).exists()
