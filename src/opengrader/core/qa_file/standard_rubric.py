"""The points a question gets when its source states none.

Create answer key reads questions from text, Word and PDF sources, and any of
them can leave a question without points. They all fall back to the same
standard, so an answer key never comes out worth nothing: full credit is
`DEFAULT_QUESTION_POINTS`, partial credit is half of it, no credit is zero.

Points a source does state always win; this only fills the gaps.
"""

from __future__ import annotations

DEFAULT_QUESTION_POINTS = 10.0


def format_points(value: float) -> str:
    """Points as an answer key writes them: `10`, not `10.0`; `2.5` kept."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def standard_rubric_lines(points: float, *, indent: str = "    ") -> list[str]:
    """The three rubric items of a drafted question, as deductions from full
    credit: nothing off, half off, everything off."""
    return [
        f"{indent}- Correct (+0)",
        f"{indent}- Partially Correct (-{format_points(points / 2)})",
        f"{indent}- Incorrect (-{format_points(points)})",
    ]
