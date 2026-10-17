from __future__ import annotations

from pathlib import Path

from opengrader.core.qa_file.check import check_answer_key

SAMPLES = Path(__file__).resolve().parents[4] / "samples"

_RUBRIC = "  - **Rubric**\n    - Correct (+0)\n    - Partially Correct (-2)\n    - Incorrect (-5)\n"


def test_the_sample_answer_keys_have_nothing_to_report() -> None:
    for name in ("cs146-quiz1", "cmpe30-quiz2"):
        result = check_answer_key((SAMPLES / name / "qa.md").read_text(encoding="utf-8"))
        assert result.ok and result.warnings == [], name


def test_a_key_that_does_not_load_says_where() -> None:
    result = check_answer_key("---\ntitle: x\n---\n\n- (5 points) Q\n  - **Rubric**\n    - Correct (+0)\n")

    assert not result.ok and result.errors[0].startswith("line 5:")


def test_likely_mistakes_are_warnings() -> None:
    key = (
        "---\ntitle: x\n---\n\n"
        "- (5 points) What is 2+2?\n  - **Correct**\n    - 4\n" + _RUBRIC + "\n"
        "- (5 points) What is 2+2?\n  - **Correct**\n    - 4\n" + _RUBRIC + "\n"
        "- (0 points) Name a prime.\n"
    )

    result = check_answer_key(key)

    assert result.ok
    text = "\n".join(result.warnings)
    assert "same text as q1" in text
    assert "worth 0 points" in text
    assert "no correct answer and no rubric" in text
