from __future__ import annotations

import re
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from dataclasses import dataclass, field
from pathlib import Path

"""
Generate a QA-format markdown file from a text2qti-format exam markdown file.

Maps each text2qti question to a QA-format bullet (question prompt + points +
correct answer(s) + standard rubric). Quiz-level metadata (title and
description) become the QA frontmatter title and instructions.

Supported text2qti question types:
  - Short answer (lines starting with ``* answer``)
  - Numeric (``= value``)
  - Multiple choice (``*a) correct`` / ``a) wrong``)
  - Multiple answer (``[*] correct`` / ``[ ] wrong``)
  - Essay / free response (``____`` marker)
"""

EPILOG = """
examples:
  uv run qa-from-text2qti --input exam.md --output qa.md
  uv run qa-from-text2qti --input exam.md --output qa.md --questions-start-page 2
  uv run qa-from-text2qti --input exam.md --output qa.md --title "CMPE 30 Final"
"""

_TITLE_RE = re.compile(r"^\s*Quiz\s+title\s*:\s*(.*)$", re.IGNORECASE)
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
class _Choice:
    label: str
    text: str
    correct: bool


@dataclass
class _Question:
    number: int
    points: float | None
    prompt_lines: list[str] = field(default_factory=list)
    choices: list[_Choice] = field(default_factory=list)
    short_answers: list[str] = field(default_factory=list)
    numeric_answer: str | None = None
    has_free_response: bool = False

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
class _ParsedExam:
    title: str
    description: str
    questions: list[_Question]


def _parse_text2qti(path: Path) -> _ParsedExam:
    """Parse a text2qti markdown file into title, description, and questions.

    Tracks fenced code blocks within question bodies so that ``*`` / ``=``
    lines that happen to appear inside source-code examples are not
    mistaken for short-answer or numeric answer markers.
    """
    title = ""
    description_lines: list[str] = []
    questions: list[_Question] = []
    current_points: float | None = None
    current: _Question | None = None
    in_description = False
    in_code_block = False
    code_fence_marker: str | None = None

    for raw_line in path.read_text().splitlines():
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

        question_match = _QUESTION_RE.match(line)
        if question_match is not None:
            if current is not None:
                questions.append(current)
            current = _Question(
                number=int(question_match.group(1)),
                points=current_points,
                prompt_lines=[question_match.group(2)],
            )
            in_description = False
            continue

        if current is None:
            if in_description and line.strip():
                description_lines.append(line.strip())
            continue

        mc_match = _MC_OPTION_RE.match(line)
        if mc_match is not None:
            star, label, text = mc_match.groups()
            current.choices.append(
                _Choice(label=label, text=text.strip(), correct=bool(star))
            )
            continue

        check_match = _CHECK_OPTION_RE.match(line)
        if check_match is not None:
            marker, text = check_match.groups()
            label = chr(ord("a") + len(current.choices))
            current.choices.append(
                _Choice(
                    label=label,
                    text=text.strip(),
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
    return _ParsedExam(title=title, description=description, questions=questions)


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


def _render_qa(
    parsed: _ParsedExam,
    *,
    title_override: str | None,
    instructions_override: str | None,
    questions_start_page: int,
) -> str:
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
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    lines.append("")
    return "\n".join(lines)


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Generate a QA-format markdown file from a text2qti-format exam "
            "markdown file."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, help="Path to the text2qti markdown file.")
    parser.add_argument("--output", required=True, help="Path to write the generated QA markdown file.")
    parser.add_argument(
        "--title",
        default=None,
        help="Override the QA file title (defaults to the text2qti 'Quiz title:').",
    )
    parser.add_argument(
        "--instructions",
        default=None,
        help=(
            "Override the QA file instructions "
            "(defaults to the text2qti 'Quiz description:')."
        ),
    )
    parser.add_argument(
        "--questions-start-page",
        type=int,
        default=1,
        help="Value to write for the 'questions_start_page' field (default: 1).",
    )
    return parser


def run_cli() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    try:
        parsed = _parse_text2qti(input_path)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not parsed.questions:
        print(f"error: no questions parsed from {input_path}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _render_qa(
            parsed,
            title_override=args.title,
            instructions_override=args.instructions,
            questions_start_page=args.questions_start_page,
        )
    )
    print(f"Wrote {output_path} ({len(parsed.questions)} questions)")


if __name__ == "__main__":
    run_cli()
