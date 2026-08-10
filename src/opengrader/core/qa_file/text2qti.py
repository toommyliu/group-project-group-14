"""Parse a text2qti-format source into a QA-format markdown answer key.

text2qti is a plain-text quiz-authoring format; this maps each question to a
QA-format bullet (prompt + points + correct answer(s) + a standard rubric) and
the file's title/description to the QA frontmatter. Pure text in, markdown out —
no PDF, OCR, or network — so the ``.md``/``.txt``/``.docx`` paths of the
``create-answer-key`` command all funnel through here.

``Quiz title:`` / ``Quiz description:`` are the literal text2qti keywords and are
matched verbatim; everything else speaks of "assignments".

Supported question types: short answer (``* answer``), numeric (``= value``),
multiple choice (``*a) correct`` / ``a) wrong``), multiple answer
(``[*] correct`` / ``[ ] wrong``), and essay/free response (``____``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ``Quiz title:`` / ``Quiz description:`` are text2qti format keywords — matched
# verbatim, not renamed.
_TITLE_RE = re.compile(r"^\s*Quiz\s+title\s*:\s*(.*)$", re.IGNORECASE)
# ``Outcome: CLO3`` tags a question with a learning outcome it measures. Not a
# text2qti keyword — an Opengrader extension, spelled as a plain ``Key: value``
# line so it reads like the ``Quiz title:`` / ``Points possible:`` directives
# already in this format rather than importing QA markdown's ``**Outcome**``
# bullet syntax into a file that has no bullets.
_OUTCOME_RE = re.compile(r"^\s*Outcomes?\s*:\s*(.*)$", re.IGNORECASE)
_DESCRIPTION_RE = re.compile(r"^\s*Quiz\s+description\s*:\s*(.*)$", re.IGNORECASE)
_POINTS_RE = re.compile(
    r"^\s*Points(?:\s+possible)?\s*:\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE
)
_GROUP_TITLE_RE = re.compile(r"^\s*Title\s*:\s*(.*)$")
_QUESTION_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
_MC_OPTION_RE = re.compile(r"^\s*(\*?)([a-zA-Z])\)\s+(.+?)\s*$")
_CHECK_OPTION_RE = re.compile(r"^\s*\[([ *xX])\]\s+(.+?)\s*$")
_NUMERIC_RE = re.compile(r"^\s*=\s+(.+?)\s*$")
_SHORT_RE = re.compile(r"^\s*\*\s+(.+?)\s*$")
_FREE_RESPONSE_RE = re.compile(r"^\s*_{3,}\s*$")
_CODE_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Choice:
    label: str
    text: str
    correct: bool


@dataclass
class Question:
    number: int
    points: float | None
    prompt_lines: list[str] = field(default_factory=list)
    choices: list[Choice] = field(default_factory=list)
    short_answers: list[str] = field(default_factory=list)
    numeric_answer: str | None = None
    has_free_response: bool = False
    outcomes: list[str] = field(default_factory=list)

    def has_answer_block(self) -> bool:
        return bool(
            self.choices
            or self.short_answers
            or self.numeric_answer is not None
            or self.has_free_response
        )

    def correct_lines(self) -> list[str]:
        if self.choices:
            return [choice.label.upper() for choice in self.choices if choice.correct]
        if self.numeric_answer is not None:
            return [self.numeric_answer]
        if self.short_answers:
            return list(self.short_answers)
        return []


@dataclass
class ParsedAssignment:
    title: str
    description: str
    questions: list[Question]


def parse_text2qti(text: str) -> ParsedAssignment:
    """Parse text2qti source text into title, description, and questions.

    Tracks fenced code blocks within question bodies so that ``*`` / ``=`` lines
    inside source-code examples are not mistaken for answer markers.
    """
    title = ""
    description_lines: list[str] = []
    questions: list[Question] = []
    current_points: float | None = None
    # Outcomes declared before any question apply to every question after them,
    # matching how ``Points possible:`` already works — "the next ten questions
    # all measure CLO2" is the common case, and repeating a line ten times is
    # how an instructor gets one of them wrong.
    current_outcomes: list[str] = []
    current: Question | None = None
    in_description = False
    in_code_block = False
    code_fence_marker: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        if current is not None and in_code_block:
            current.prompt_lines.append(line)
            if line.lstrip().startswith(code_fence_marker or ""):
                in_code_block = False
                code_fence_marker = None
            continue

        if current is not None:
            fence_match = _CODE_FENCE_RE.match(line)
            if fence_match is not None and not current.has_answer_block():
                in_code_block = True
                code_fence_marker = fence_match.group(1)
                current.prompt_lines.append(line)
                continue

        title_match = _TITLE_RE.match(line)
        if title_match is not None:
            title = title_match.group(1).strip()
            in_description = False
            continue

        description_match = _DESCRIPTION_RE.match(line)
        if description_match is not None:
            description_lines = [description_match.group(1).strip()]
            in_description = True
            continue

        if _GROUP_TITLE_RE.match(line) and current is None:
            in_description = False
            continue

        points_match = _POINTS_RE.match(line)
        if points_match is not None:
            current_points = float(points_match.group(1))
            in_description = False
            continue

        outcome_match = _OUTCOME_RE.match(line)
        if outcome_match is not None:
            names = [
                part.strip()
                for part in outcome_match.group(1).split(",")
                if part.strip()
            ]
            target = current.outcomes if current is not None else current_outcomes
            for outcome_name in names:
                if outcome_name not in target:
                    target.append(outcome_name)
            in_description = False
            continue

        question_match = _QUESTION_RE.match(line)
        if question_match is not None:
            if current is not None:
                questions.append(current)
            current = Question(
                number=int(question_match.group(1)),
                points=current_points,
                prompt_lines=[question_match.group(2)],
                outcomes=list(current_outcomes),
            )
            in_description = False
            continue

        if current is None:
            if in_description and line.strip():
                description_lines.append(line.strip())
            continue

        mc_match = _MC_OPTION_RE.match(line)
        if mc_match is not None:
            star, label, choice_text = mc_match.groups()
            current.choices.append(
                Choice(label=label, text=choice_text.strip(), correct=bool(star))
            )
            continue

        check_match = _CHECK_OPTION_RE.match(line)
        if check_match is not None:
            marker, choice_text = check_match.groups()
            label = chr(ord("a") + len(current.choices))
            current.choices.append(
                Choice(
                    label=label,
                    text=choice_text.strip(),
                    correct=marker.strip().lower() in {"*", "x"},
                )
            )
            continue

        numeric_match = _NUMERIC_RE.match(line)
        if numeric_match is not None:
            current.numeric_answer = numeric_match.group(1).strip()
            continue

        short_match = _SHORT_RE.match(line)
        if short_match is not None:
            current.short_answers.append(short_match.group(1).strip())
            continue

        if _FREE_RESPONSE_RE.match(line):
            current.has_free_response = True
            continue

        if not current.has_answer_block():
            current.prompt_lines.append(line)

    if current is not None:
        questions.append(current)

    description = " ".join(description_lines).strip()
    return ParsedAssignment(title=title, description=description, questions=questions)


def parse_text2qti_file(path: Path) -> ParsedAssignment:
    """Convenience wrapper: read a file and parse its text2qti content."""
    return parse_text2qti(Path(path).read_text())


def _format_points(value: float | None) -> str:
    if value is None:
        return "0"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def _format_yaml_field(key: str, value: str) -> list[str]:
    if not value:
        return []
    if "\n" in value:
        out = [f"{key}: |"]
        for piece in value.splitlines():
            out.append(f"  {piece}")
        return out
    if len(value) > 70 or ":" in value:
        return [f'{key}: "{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"']
    return [f"{key}: {value}"]


def render_qa(
    parsed: ParsedAssignment,
    *,
    title_override: str | None = None,
    instructions_override: str | None = None,
    questions_start_page: int = 1,
) -> str:
    """Render a parsed assignment as QA-format markdown."""
    title = title_override if title_override is not None else parsed.title
    instructions = (
        instructions_override
        if instructions_override is not None
        else parsed.description
    )

    lines: list[str] = ["---"]
    if title:
        lines.extend(_format_yaml_field("title", title))
    if instructions:
        lines.extend(_format_yaml_field("instructions", instructions))
    lines.append(f"questions_start_page: {questions_start_page}")
    lines.append("---")
    lines.append("")

    for question in parsed.questions:
        points_label = _format_points(question.points)
        prompt_first, *prompt_rest = question.prompt_lines or [""]
        lines.append(f"- ({points_label} points) {prompt_first.strip()}")
        for cont in prompt_rest:
            if cont.strip():
                lines.append(f"  {cont}")
            else:
                lines.append("")
        while lines and lines[-1] == "":
            lines.pop()

        if question.choices:
            lines.append("  - **Choices**")
            for choice in question.choices:
                lines.append(f"    - {choice.label.upper()}) {choice.text}")

        correct = question.correct_lines()
        if correct:
            lines.append("  - **Correct**")
            for entry in correct:
                lines.append(f"    - {entry}")
        elif question.has_free_response or not question.choices:
            lines.append("  - **Correct**")
            lines.append("    - (free response — manual grading)")

        points_value = question.points or 0.0
        partial_penalty = _format_points(points_value / 2)
        full_penalty = _format_points(points_value)
        lines.append("  - **Rubric**")
        lines.append("    - Correct (+0)")
        lines.append(f"    - Partially Correct (-{partial_penalty})")
        lines.append(f"    - Incorrect (-{full_penalty})")
        # After the rubric, matching where `markdown._render_question` puts them
        # so a generated file and a hand-edited one look the same. Emitted only
        # when tagged, so a source file that uses no outcomes produces exactly
        # the QA markdown it always did.
        for outcome in question.outcomes:
            lines.append(f"  - **Outcome** {outcome}")
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    lines.append("")
    return "\n".join(lines)
