from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from opengrader.core.qa_file.standard_rubric import (
    DEFAULT_QUESTION_POINTS,
    format_points,
    standard_rubric_lines,
)

_TITLE_RE = re.compile(r"^\s*Quiz\s+title\s*:\s*(.*)$", re.IGNORECASE)
# Outcome is an Opengrader extension to text2qti.
_OUTCOME_RE = re.compile(r"^\s*Outcomes?\s*:\s*(.*)$", re.IGNORECASE)
_DESCRIPTION_RE = re.compile(r"^\s*Quiz\s+description\s*:\s*(.*)$", re.IGNORECASE)
_POINTS_RE = re.compile(
    r"^\s*Points(?:\s+possible)?\s*:\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE
)
_GROUP_TITLE_RE = re.compile(r"^\s*Title\s*:\s*(.*)$")
# How a question may be numbered: "1." (text2qti's own), and Opengrader's
# additions "1)", "Q1.", "Q1)", "Question 1:", "Problem 1.". A bare "1:" is
# not one — that is how a time or a ratio starts.
_QUESTION_RE = re.compile(
    r"^\s*(?:(?:Q|Question|Problem)\s*(\d+)\s*[.):]|(\d+)\s*[.)])\s+(.+?)\s*$",
    re.IGNORECASE,
)
# A part of a question: "1a)", "1a.", "1(a)", "1.a)", "1.a.". The number is
# what makes it a part — a bare "a)" stays a multiple-choice option — and the
# closing mark is required, so "1. a quick question" is still question 1.
_PART_RE = re.compile(
    r"^\s*(?:(?:Q|Question|Problem)\s*)?(\d+)\s*"
    r"(?:\.?([a-zA-Z])[.)]|\(([a-zA-Z])\)[.:]?)\s+(.+?)\s*$",
    re.IGNORECASE,
)
# Points written on the question's own line, anywhere in it: "(10 points)",
# "(10 pts)", "[10 points]", "(2 marks)".
_INLINE_POINTS_RE = re.compile(
    r"\s*[\(\[]\s*(\d+(?:\.\d+)?)\s*(?:points?|pts?|marks?)\s*[\)\]]\s*",
    re.IGNORECASE,
)
_MC_OPTION_RE = re.compile(r"^\s*(\*?)([a-zA-Z])\)\s+(.+?)\s*$")
_CHECK_OPTION_RE = re.compile(r"^\s*\[([ *xX])\]\s+(.+?)\s*$")
_NUMERIC_RE = re.compile(r"^\s*=\s+(.+?)\s*$")
_SHORT_RE = re.compile(r"^\s*\*\s+(.+?)\s*$")
_FREE_RESPONSE_RE = re.compile(r"^\s*_{3,}\s*$")
# An Opengrader extension: the answer in plain words, for a file that is just
# questions and answers. One line per accepted answer.
_ANSWER_RE = re.compile(r"^\s*(?:Answers?|Ans|Solution)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_CHOICE_LETTERS_RE = re.compile(r"^[a-zA-Z]\)?(?:\s*(?:,|/|&|\band\b|\s)\s*[a-zA-Z]\)?)*$")
_CODE_FENCE_RE = re.compile(r"^\s*(```|~~~)")


@dataclass
class Choice:
    label: str
    text: str
    correct: bool


@dataclass
class Question:
    number: int
    # Stated points: the ones on the question's own line, else the `Points:`
    # line above it. None when the source gives neither.
    points: float | None
    prompt_lines: list[str] = field(default_factory=list)
    choices: list[Choice] = field(default_factory=list)
    short_answers: list[str] = field(default_factory=list)
    numeric_answer: str | None = None
    has_free_response: bool = False
    outcomes: list[str] = field(default_factory=list)
    # True when `points` was written on this question's own line.
    points_inline: bool = False
    # A part's letter as written ("a"); empty for a whole question.
    label: str = ""
    # "1a)", "1b)" … — themselves `Question`s, never nested further.
    parts: list["Question"] = field(default_factory=list)

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


def _split_inline_points(text: str) -> tuple[float | None, str]:
    """Take "(10 points)" out of a question's line, wherever it is written."""
    match = _INLINE_POINTS_RE.search(text)
    if match is None:
        return None, text.strip()
    rest = f"{text[: match.start()]} {text[match.end():]}"
    return float(match.group(1)), " ".join(rest.split())


def parse_text2qti(text: str) -> ParsedAssignment:
    title = ""
    description_lines: list[str] = []
    questions: list[Question] = []
    current_points: float | None = None
    current_outcomes: list[str] = []
    current: Question | None = None
    in_description = False
    in_code_block = False
    code_fence_marker: str | None = None
    # The answer a plain following line continues: (question, index into its
    # short answers), with -1 for an "Answer:" line that was left empty.
    open_answer: tuple[Question, int] | None = None

    def start(number: int, raw: str, *, label: str = "") -> Question:
        inline, prompt = _split_inline_points(raw)
        return Question(
            number=number,
            points=current_points if inline is None else inline,
            points_inline=inline is not None,
            prompt_lines=[prompt],
            outcomes=[] if label else list(current_outcomes),
            label=label,
        )

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        # Whatever this line is, the question's last part is what it adds to.
        target = None if current is None else (current.parts[-1] if current.parts else current)
        continued, open_answer = open_answer, None

        if target is not None and in_code_block:
            target.prompt_lines.append(line)
            if line.lstrip().startswith(code_fence_marker or ""):
                in_code_block = False
                code_fence_marker = None
            continue

        if target is not None:
            fence_match = _CODE_FENCE_RE.match(line)
            if fence_match is not None and not target.has_answer_block():
                in_code_block = True
                code_fence_marker = fence_match.group(1)
                target.prompt_lines.append(line)
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
            outcome_target = current.outcomes if current is not None else current_outcomes
            for outcome_name in names:
                if outcome_name not in outcome_target:
                    outcome_target.append(outcome_name)
            in_description = False
            continue

        part_match = _PART_RE.match(line)
        if part_match is not None:
            number = int(part_match.group(1))
            letter = (part_match.group(2) or part_match.group(3)).lower()
            if current is None or current.number != number:
                # "2a)" with no "2." line above it: the question has no text
                # of its own, only parts.
                if current is not None:
                    questions.append(current)
                current = Question(
                    number=number,
                    points=None,
                    prompt_lines=[""],
                    outcomes=list(current_outcomes),
                )
            current.parts.append(start(number, part_match.group(4), label=letter))
            in_description = False
            continue

        question_match = _QUESTION_RE.match(line)
        if question_match is not None:
            if current is not None:
                questions.append(current)
            current = start(
                int(question_match.group(1) or question_match.group(2)),
                question_match.group(3),
            )
            in_description = False
            continue

        if target is None:
            if in_description and line.strip():
                description_lines.append(line.strip())
            continue

        mc_match = _MC_OPTION_RE.match(line)
        if mc_match is not None:
            star, label, choice_text = mc_match.groups()
            target.choices.append(
                Choice(label=label, text=choice_text.strip(), correct=bool(star))
            )
            continue

        check_match = _CHECK_OPTION_RE.match(line)
        if check_match is not None:
            marker, choice_text = check_match.groups()
            label = chr(ord("a") + len(target.choices))
            target.choices.append(
                Choice(
                    label=label,
                    text=choice_text.strip(),
                    correct=marker.strip().lower() in {"*", "x"},
                )
            )
            continue

        numeric_match = _NUMERIC_RE.match(line)
        if numeric_match is not None:
            target.numeric_answer = numeric_match.group(1).strip()
            continue

        short_match = _SHORT_RE.match(line)
        if short_match is not None:
            target.short_answers.append(short_match.group(1).strip())
            continue

        answer_match = _ANSWER_RE.match(line)
        if answer_match is not None:
            index = _add_answer(target, answer_match.group(1))
            if index is not None:
                open_answer = (target, index)
            continue

        if _FREE_RESPONSE_RE.match(line):
            target.has_free_response = True
            continue

        if continued is not None and line.strip():
            # An answer that runs on: the line after "Answer:" belongs to it.
            # A blank line, or any marker above, ends it.
            answered, index = continued
            if index < 0:
                answered.has_free_response = False
                answered.short_answers.append(line.strip())
                index = len(answered.short_answers) - 1
            else:
                answered.short_answers[index] += f" {line.strip()}"
            open_answer = (answered, index)
            continue

        if not target.has_answer_block():
            target.prompt_lines.append(line)

    if current is not None:
        questions.append(current)

    description = " ".join(description_lines).strip()
    return ParsedAssignment(title=title, description=description, questions=questions)


def _add_answer(question: Question, answer: str) -> int | None:
    """Record one `Answer:` line.

    Under a list of choices it names the correct ones by letter (`Answer: b`,
    `Answer: a, c`). Anywhere else it is one accepted answer, and each further
    line adds another: a second correct answer, or another way to write it.
    An empty `Answer:` is a blank for the student, graded by hand — unless the
    answer is written on the lines below it.

    Returns where the answer went in `short_answers` (-1 for an empty one), so
    a following plain line can continue it; None when it named choices.
    """
    if not answer:
        question.has_free_response = True
        return -1
    if question.choices and _CHOICE_LETTERS_RE.match(answer):
        letters = {letter.lower() for letter in re.findall(r"[a-zA-Z]", answer.replace("and", " "))}
        labels = {choice.label.lower() for choice in question.choices}
        if letters <= labels:
            for choice in question.choices:
                if choice.label.lower() in letters:
                    choice.correct = True
            return None
    if answer not in question.short_answers:
        question.short_answers.append(answer)
    return question.short_answers.index(answer)


@dataclass(frozen=True)
class PointsSummary:
    """What `render_qa` will make of an assignment's points."""

    # What is graded: a question without parts, or each part of one with them.
    graded: int
    # How many of those state no points and get the default.
    defaulted: int
    questions: int
    with_parts: int


def _whole_split(total: float, count: int) -> list[float] | None:
    """`total` over `count` parts in whole points, the first parts taking any
    remainder (10 over 3 is 4, 3, 3). None when that leaves a part with
    nothing — an answer key's points are whole numbers."""
    if count < 1 or not float(total).is_integer() or total < count:
        return None
    base, extra = divmod(int(total), count)
    return [float(base + (1 if index < extra else 0)) for index in range(count)]


def _part_points(question: Question, default_points: float) -> list[tuple[float, bool]]:
    """Each part's points, and whether they are the default.

    A part's own points come first. Then the question's, when written on its
    own line: what its parts have not claimed is shared out evenly among the
    rest. Then the `Points:` line above. Then the default.
    """
    unstated = [part for part in question.parts if not part.points_inline]
    shared: list[float] | None = None
    if question.points_inline and question.points is not None:
        claimed = sum(part.points or 0.0 for part in question.parts if part.points_inline)
        shared = _whole_split(question.points - claimed, len(unstated))

    resolved: list[tuple[float, bool]] = []
    for part in question.parts:
        if part.points_inline and part.points is not None:
            resolved.append((part.points, False))
        elif shared is not None:
            resolved.append((shared[unstated.index(part)], False))
        elif part.points is not None:
            resolved.append((part.points, False))
        else:
            resolved.append((default_points, True))
    return resolved


def summarize_points(parsed: ParsedAssignment) -> PointsSummary:
    graded = defaulted = with_parts = 0
    for question in parsed.questions:
        if question.parts:
            with_parts += 1
            # Whether a part is defaulted does not depend on the default's size.
            flags = [is_default for _points, is_default in _part_points(question, 1.0)]
        else:
            flags = [question.points is None]
        graded += len(flags)
        defaulted += sum(flags)
    return PointsSummary(
        graded=graded,
        defaulted=defaulted,
        questions=len(parsed.questions),
        with_parts=with_parts,
    )


def questions_without_points(parsed: ParsedAssignment) -> int:
    """How many questions (or parts) `render_qa` will give the default points."""
    return summarize_points(parsed).defaulted


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


def _without_unanswered_choices(question: Question) -> Question:
    """Choices with none marked correct are not a valid multiple-choice
    question; in a draft they are kept as the question's text instead."""
    if not question.choices or any(choice.correct for choice in question.choices):
        return question
    listed = [f"{choice.label.upper()}) {choice.text}" for choice in question.choices]
    return replace(question, prompt_lines=[*question.prompt_lines, *listed], choices=[])


def _render_answer_block(
    question: Question, points: float, indent: str, *, blank_answers: bool = False
) -> list[str]:
    """A question's (or a part's) choices, correct answers and rubric."""
    lines: list[str] = []
    if question.choices:
        lines.append(f"{indent}- **Choices**")
        for choice in question.choices:
            lines.append(f"{indent}  - {choice.label.upper()}) {choice.text}")

    correct = question.correct_lines()
    if correct:
        lines.append(f"{indent}- **Correct**")
        for entry in correct:
            lines.append(f"{indent}  - {entry}")
    elif blank_answers:
        # A draft: the answer is the teacher's to fill in.
        pass
    elif question.has_free_response or not question.choices:
        lines.append(f"{indent}- **Correct**")
        lines.append(f"{indent}  - (free response — manual grading)")

    lines.append(f"{indent}- **Rubric**")
    lines.extend(standard_rubric_lines(points, indent=f"{indent}  "))
    return lines


def _render_prompt(question: Question, points: float, bullet_indent: str) -> list[str]:
    prompt_first, *prompt_rest = question.prompt_lines or [""]
    lines = [f"{bullet_indent}- ({format_points(points)} points) {prompt_first.strip()}".rstrip()]
    for cont in prompt_rest:
        if cont.strip():
            lines.append(f"{bullet_indent}  {cont}")
        else:
            lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def render_question(
    question: Question,
    *,
    default_points: float = DEFAULT_QUESTION_POINTS,
    blank_answers: bool = False,
) -> list[str]:
    """One question as the lines of its answer-key bullet, parts and all.

    `blank_answers` writes a draft: a question with no answer gets no
    **Correct** section at all (instead of the manual-grading note), for the
    teacher to add.
    """
    lines: list[str] = []
    if question.parts:
        part_points = [points for points, _ in _part_points(question, default_points)]
        # A question with parts is worth what its parts add up to.
        lines.extend(_render_prompt(question, sum(part_points), ""))
        for part, points in zip(question.parts, part_points):
            if blank_answers:
                part = _without_unanswered_choices(part)
            lines.extend(_render_prompt(part, points, "  "))
            lines.extend(_render_answer_block(part, points, "    ", blank_answers=blank_answers))
    else:
        if blank_answers:
            question = _without_unanswered_choices(question)
        points_value = default_points if question.points is None else question.points
        lines.extend(_render_prompt(question, points_value, ""))
        lines.extend(_render_answer_block(question, points_value, "  ", blank_answers=blank_answers))
    for outcome in question.outcomes:
        lines.append(f"  - **Outcome** {outcome}")
    return lines


def render_qa(
    parsed: ParsedAssignment,
    *,
    title_override: str | None = None,
    instructions_override: str | None = None,
    questions_start_page: int = 1,
    default_points: float = DEFAULT_QUESTION_POINTS,
    blank_answers: bool = False,
) -> str:
    """`default_points` is what a question (or a part) is worth when the
    source states no points for it. A stated `Points: 0` is kept as zero.
    `blank_answers`: see `render_question`."""
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
        lines.extend(
            render_question(question, default_points=default_points, blank_answers=blank_answers)
        )
        lines.append("")

    while lines and lines[-1] == "":
        lines.pop()
    lines.append("")
    return "\n".join(lines)
