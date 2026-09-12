from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from opengrader.core.bubble_sheet import (
    DEFAULT_CHOICE_LABELS as DEFAULT_BUBBLE_CHOICE_LABELS,
)
from opengrader.core.pdf.templates.utils import default_subquestion_label
from opengrader.core.question_types import (
    FALSE_CHOICE_TEXT,
    FREE_RESPONSE,
    MULTIPLE_ANSWER,
    MULTIPLE_CHOICE,
    NUMERIC,
    TRUE_CHOICE_TEXT,
    QuestionType,
    is_boolean_answer,
    normalize_boolean,
)

from .numeric import format_numeric_spec, parse_numeric_answers
from .scoring import RubricOptionKind, RubricSelectionMode, rubric_scoring_mode

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.bubble_sheet import BubbleQuestion
    from opengrader.core.pdf.templates.utils import QuestionInput

_TYPE_TRUE_FALSE_ALIASES = {
    "true/false",
    "true false",
    "truefalse",
    "true_false",
    "t/f",
    "tf",
    "boolean",
    "bool",
}
_TYPE_NUMERIC_ALIASES = {"numeric", "number", "num"}
_TYPE_FREE_RESPONSE_ALIASES = {"free response", "free_response", "free-response", "free"}
_TYPE_MULTIPLE_CHOICE_ALIASES = {"multiple choice", "multiple_choice", "mc"}

_INLINE_POINTS_RE = re.compile(r"^\((\d+(?:\.\d+)?)\s*points?\)\s*(.*)$", re.IGNORECASE)
_FRONTMATTER_RE = re.compile(r"^---$")
_VERSION_HEADER_RE = re.compile(r"^\s*##\s+(.+?)\s*$")
_LIST_ITEM_RE = re.compile(r"^([ \t]*)-\s+(.*)$")
_CHOICE_ITEM_RE = re.compile(r"^(?P<label>[A-Za-z])[\)\.\:]\s*(?P<text>.+?)\s*$")
_RUBRIC_OPTION_RE = re.compile(
    r"^(?P<label>.+?)\s*(?:\(\s*(?P<paren>[+-]?\d+(?:\.\d+)?)\s*(?:points?)?\s*\)|:\s*(?P<colon>[+-]?\d+(?:\.\d+)?)\s*(?:points?)?)\s*$",
    re.IGNORECASE,
)
_RUBRIC_COMMENT_RE = re.compile(
    r"^\*\*\s*Comment\s*\*\*\s*(?P<text>.*)$",
    re.IGNORECASE | re.DOTALL,
)

_OUTCOME_SECTION_RE = re.compile(
    r"^\*\*\s*Outcomes?\s*\*\*\s*(?P<text>.*)$",
    re.IGNORECASE | re.DOTALL,
)


class Frontmatter(TypedDict, total=False):
    title: str
    instructions: str
    continued_page_header: str
    questions_start_page: int


class FrontmatterMarker(str, Enum):
    TITLE = "title"
    INSTRUCTIONS = "instructions"
    CONTINUED_PAGE_HEADER = "continued_page_header"
    QUESTIONS_START_PAGE = "questions_start_page"


@dataclass
class QARubricItem:
    label: str
    points_delta: float | None = None
    notes: list[str] = field(default_factory=list)
    subitems: list["QARubricItem"] = field(default_factory=list)
    default_comment: str | None = None


def _is_bonus(item: QARubricItem | QARubricFlatOption) -> bool:
    if isinstance(item, QARubricFlatOption):
        return item.kind == "bonus"
    return item.label == "Bonus"


@dataclass(frozen=True)
class QARubricFlatOption:
    label: str
    points_delta: float
    kind: RubricOptionKind
    selection_mode: RubricSelectionMode
    default_comment: str | None = None


@dataclass
class QAChoice:
    label: str
    text: str


@dataclass
class QASubquestion:
    id: str
    prompt: str
    points: int
    label: str | None = None
    answers: list[str] = field(default_factory=list)
    choices: list[QAChoice] = field(default_factory=list)
    correct_choice_label: str | None = None
    correct_choice_labels: tuple[str, ...] = ()
    rubric: list[QARubricItem] = field(default_factory=list)
    image_markdown: str | None = None
    outcomes: tuple[str, ...] = ()


@dataclass
class QAQuestion:
    id: str
    prompt: str
    points: int
    answers: list[str] = field(default_factory=list)
    choices: list[QAChoice] = field(default_factory=list)
    correct_choice_label: str | None = None
    correct_choice_labels: tuple[str, ...] = ()
    rubric: list[QARubricItem] = field(default_factory=list)
    image_markdown: str | None = None
    subquestions: list[QASubquestion] = field(default_factory=list)
    outcomes: tuple[str, ...] = ()

    @property
    def graded_outcomes(self) -> tuple[str, ...]:
        if not self.subquestions:
            return self.outcomes
        merged: list[str] = []
        for subquestion in self.subquestions:
            for outcome in subquestion.outcomes or self.outcomes:
                if outcome not in merged:
                    merged.append(outcome)
        return tuple(merged)

    def outcomes_for(self, question_id: str) -> tuple[str, ...]:
        if not self.subquestions:
            return self.outcomes if question_id == self.id else ()
        for subquestion in self.subquestions:
            if subquestion.id == question_id:
                return subquestion.outcomes or self.outcomes
        return ()


@dataclass
class QADocument:
    questions: list[QAQuestion]
    title: str | None = None
    instructions: str | None = None
    continued_page_header: str | None = None
    versions: list[str] = field(default_factory=list)
    version_questions: dict[str, list[QAQuestion]] = field(default_factory=dict)
    questions_start_page: int = 1


@dataclass
class _BulletItem:
    text: str
    indent: int
    line_no: int
    children: list["_BulletItem"] = field(default_factory=list)


def _indent_width(prefix: str) -> int:
    return len(prefix.expandtabs(4))


def _parse_frontmatter(lines: list[str]) -> tuple[Frontmatter, int]:
    if len(lines) < 2 or not _FRONTMATTER_RE.match(lines[0].strip()):
        return {}, 0

    i = 1
    frontmatter_lines: list[str] = []
    while i < len(lines):
        if _FRONTMATTER_RE.match(lines[i].strip()):
            i += 1
            break
        frontmatter_lines.append(lines[i])
        i += 1
    else:
        return {}, 0

    frontmatter: Frontmatter = {}
    key: str | None = None
    value_lines: list[str] = []
    literal_block = False

    def flush_key() -> None:
        nonlocal key, value_lines, literal_block
        if key is None:
            return

        if literal_block:
            normalized = [
                line[2:] if line.startswith("  ") else line for line in value_lines
            ]
            frontmatter[key] = "\n".join(line.rstrip() for line in normalized).rstrip()
        else:
            stripped = [line.strip() for line in value_lines if line.strip()]
            if stripped and all(line.startswith("- ") for line in stripped):
                frontmatter[key] = [line[2:].strip() for line in stripped]
            else:
                frontmatter[key] = "\n".join(stripped).rstrip()

        key = None
        value_lines = []
        literal_block = False

    for raw in frontmatter_lines:
        line = raw.rstrip("\n")

        if line == line.lstrip() and ":" in line:
            name, value = line.split(":", 1)
            name = name.strip()
            if name:
                flush_key()
                key = name
                value = value.strip()

                if value == "|":
                    literal_block = True
                    value_lines = []
                    continue

                if value:
                    frontmatter[key] = value
                    key = None
                    value_lines = []
                    literal_block = False
                    continue

                literal_block = False
                value_lines = []
                continue

        if key is not None:
            value_lines.append(line)

    flush_key()
    return frontmatter, i


def _render_frontmatter_field(lines: list[str], key: str, value: str) -> None:
    is_multiline = "\n" in value
    lines.append(f"{key}: {'|' if is_multiline else ''}")
    if is_multiline:
        for line in value.split("\n"):
            lines.append(f"  {line}")
    else:
        lines.append(f"  {value}")


def _render_frontmatter(document: QADocument) -> str:
    lines = ["---"]

    if document.title:
        _render_frontmatter_field(lines, FrontmatterMarker.TITLE.value, document.title)

    if document.instructions:
        _render_frontmatter_field(
            lines, FrontmatterMarker.INSTRUCTIONS.value, document.instructions
        )

    if document.continued_page_header:
        lines.append(
            f"{FrontmatterMarker.CONTINUED_PAGE_HEADER.value}: {document.continued_page_header}"
        )

    if document.questions_start_page is not None:
        lines.append(
            f"{FrontmatterMarker.QUESTIONS_START_PAGE.value}: {document.questions_start_page}"
        )

    lines.append("---")
    lines.append("")
    return "\n".join(lines)


def _parse_points(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise ValueError(f"invalid points value: {raw!r}") from exc


def _strip_inline_points(prompt: str) -> tuple[str, int | None]:
    text = prompt.strip()
    first_line, sep, rest = text.partition("\n")
    matched = _INLINE_POINTS_RE.match(first_line.strip())
    if matched is None:
        return text, None
    points = int(float(matched.group(1)))
    cleaned_first = matched.group(2).strip()
    cleaned = cleaned_first if not rest else (
        f"{cleaned_first}{sep}{rest}" if cleaned_first else rest
    )
    return cleaned, points


def _normalize_section_name(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("**") and cleaned.endswith("**") and len(cleaned) >= 4:
        cleaned = cleaned[2:-2].strip()
    return cleaned.rstrip(":").strip().lower()


def _parse_version_label(header_text: str) -> str:
    label = header_text.strip()
    matched = re.match(r"(?i)^version\s+(.+)$", label)
    if matched is not None:
        label = matched.group(1).strip()
    if not label:
        raise ValueError("empty version header")
    return label


def _split_version_sections(
    lines: list[str], *, start_line: int
) -> tuple[list[tuple[str | None, list[str], int]], bool]:
    sections: list[tuple[str | None, list[str], int]] = []
    current_label: str | None = None
    current_lines: list[str] = []
    current_start = start_line
    saw_header = False

    for offset, line in enumerate(lines):
        line_no = start_line + offset
        matched = _VERSION_HEADER_RE.match(line)
        if matched is None:
            current_lines.append(line)
            continue

        saw_header = True
        if current_label is not None or current_lines:
            sections.append((current_label, current_lines, current_start))

        current_label = _parse_version_label(matched.group(1))
        current_lines = []
        current_start = line_no + 1

    if current_label is not None or current_lines:
        sections.append((current_label, current_lines, current_start))

    if not saw_header:
        return [(None, lines, start_line)], False

    cleaned: list[tuple[str | None, list[str], int]] = []
    for label, section_lines, section_start in sections:
        if label is None:
            if any(line.strip() for line in section_lines):
                raise ValueError(
                    "content found before the first version header; "
                    "start versioned QA files with '## Version X'"
                )
            continue
        cleaned.append((label, section_lines, section_start))

    return cleaned, True


def _parse_bullet_items(lines: list[str], *, start_line: int) -> list[_BulletItem]:
    roots: list[_BulletItem] = []
    stack: list[_BulletItem] = []

    for offset, raw_line in enumerate(lines):
        line_no = start_line + offset

        if not raw_line.strip():
            continue

        matched = _LIST_ITEM_RE.match(raw_line)
        if matched is not None:
            indent = _indent_width(matched.group(1))
            text = matched.group(2).strip()
            item = _BulletItem(text=text, indent=indent, line_no=line_no)

            while stack and stack[-1].indent >= indent:
                stack.pop()

            if stack:
                stack[-1].children.append(item)
            else:
                roots.append(item)

            stack.append(item)
            continue

        if not stack:
            raise ValueError(
                f"expected list item ('- ...') at line {line_no}, got: {raw_line!r}"
            )

        current_indent = _indent_width(
            raw_line[: len(raw_line) - len(raw_line.lstrip(" \t"))]
        )
        if current_indent <= stack[-1].indent:
            raise ValueError(
                f"expected list item ('- ...') at line {line_no}, got: {raw_line!r}"
            )

        continuation = raw_line.strip()
        if continuation:
            if stack[-1].text:
                stack[-1].text += "\n" + continuation
            else:
                stack[-1].text = continuation

    return roots


def _section_values(item: _BulletItem) -> list[str]:
    values: list[str] = []
    for child in item.children:
        if child.text.strip():
            values.append(child.text.strip())
    return values


def _strip_bold(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("**") and cleaned.endswith("**") and len(cleaned) >= 4:
        cleaned = cleaned[2:-2].strip()
    return cleaned


def _normalize_choice_label(
    raw: str, *, context: str, allow_prefixed_choice: bool = False
) -> str:
    cleaned = _strip_bold(raw).strip()
    matched = re.fullmatch(r"([A-Za-z])", cleaned)
    if matched is not None:
        return matched.group(1).upper()

    if allow_prefixed_choice and _CHOICE_ITEM_RE.match(cleaned):
        raise ValueError(f"{context} must use only the option label, such as 'A'")

    raise ValueError(f"{context} must be a single option label, such as 'A'")


def _parse_choice_item(item: _BulletItem) -> QAChoice:
    matched = _CHOICE_ITEM_RE.match(_strip_bold(item.text))
    if matched is None:
        raise ValueError(
            "invalid choice at "
            f"line {item.line_no}: {item.text!r}. "
            "Use 'A) text', 'A. text', or 'A: text'."
        )
    if item.children:
        raise ValueError(
            f"choice at line {item.line_no} does not support nested bullets"
        )

    text = matched.group("text").strip()
    if not text:
        raise ValueError(f"choice at line {item.line_no} is missing option text")

    return QAChoice(label=matched.group("label").upper(), text=text)


def _parse_choices_section(item: _BulletItem) -> list[QAChoice]:
    if not item.children:
        raise ValueError(
            f"choices section at line {item.line_no} must contain at least one option"
        )
    return [_parse_choice_item(child) for child in item.children]


def _validate_multiple_choice(
    *,
    choices: list[QAChoice],
    correct_values: list[str],
    context: str,
) -> list[str]:
    if not choices:
        return []

    if len(choices) < 2:
        raise ValueError(f"{context} must define at least two choices")

    labels = [choice.label for choice in choices]
    if len(set(labels)) != len(labels):
        raise ValueError(f"{context} choices must use unique labels")

    expected_labels = [chr(ord("A") + i) for i in range(len(choices))]
    if labels != expected_labels:
        raise ValueError(
            f"{context} choices must use contiguous labels starting at A: {', '.join(expected_labels)}"
        )

    if not correct_values:
        raise ValueError(
            f"{context} multiple-choice questions must contain at least one correct option label"
        )

    correct_labels: list[str] = []
    for value in correct_values:
        correct_label = _normalize_choice_label(
            value,
            context=f"{context} correct answer",
            allow_prefixed_choice=True,
        )
        if correct_label not in labels:
            raise ValueError(
                f"{context} correct answer {correct_label!r} is not one of the defined choices"
            )
        if correct_label not in correct_labels:
            correct_labels.append(correct_label)

    return [label for label in labels if label in correct_labels]


def _split_correct_labels(
    correct_labels: list[str],
) -> tuple[str | None, tuple[str, ...]]:
    if len(correct_labels) <= 1:
        return (correct_labels[0] if correct_labels else None), ()
    return None, tuple(correct_labels)


def _normalize_type_hint(raw: str | None) -> str | None:
    if raw is None:
        return None
    normalized = " ".join(raw.strip().lower().split())
    if not normalized:
        return None
    if normalized in _TYPE_TRUE_FALSE_ALIASES:
        return "true_false"
    if normalized in _TYPE_NUMERIC_ALIASES:
        return NUMERIC
    if normalized in _TYPE_FREE_RESPONSE_ALIASES:
        return FREE_RESPONSE
    if normalized in _TYPE_MULTIPLE_CHOICE_ALIASES:
        return MULTIPLE_CHOICE
    raise ValueError(
        f"unknown question type {raw!r}; use True/False, Numeric, "
        "Multiple Choice, or Free Response"
    )


def _maybe_expand_true_false(
    *,
    type_hint: str | None,
    correct_values: list[str],
    choices: list[QAChoice],
    context: str,
) -> tuple[list[QAChoice], list[str]]:
    if choices:
        return choices, correct_values

    explicit_tf = type_hint == "true_false"
    inferred_tf = (
        type_hint is None
        and len(correct_values) == 1
        and is_boolean_answer(correct_values[0])
    )
    if not (explicit_tf or inferred_tf):
        return choices, correct_values

    truth = normalize_boolean(correct_values[0]) if len(correct_values) == 1 else None
    if truth is None:
        raise ValueError(
            f"{context} True/False questions need exactly one correct answer "
            "of True or False"
        )

    tf_choices = [
        QAChoice(label="A", text=TRUE_CHOICE_TEXT),
        QAChoice(label="B", text=FALSE_CHOICE_TEXT),
    ]
    return tf_choices, ["A" if truth else "B"]


def _parse_rubric_label_and_delta(
    text: str, *, line_no: int, require_delta: bool
) -> tuple[str, float | None]:
    matched = _RUBRIC_OPTION_RE.match(text.strip())
    if matched is not None:
        label = _strip_bold(matched.group("label") or "")
        if not label:
            raise ValueError(f"rubric option at line {line_no} is missing a label")

        delta_raw = matched.group("paren") or matched.group("colon")
        if delta_raw is None:
            raise ValueError(f"rubric option at line {line_no} is missing points")
        return label, float(delta_raw)

    label = _strip_bold(text)
    if not label:
        raise ValueError(f"rubric option at line {line_no} is missing a label")
    if require_delta:
        raise ValueError(
            "invalid rubric option at "
            f"line {line_no}: {text!r}. "
            "Use 'Label (+/-N)' or 'Label: +/-N'."
        )
    return label, None


def _canonical_rubric_category(label: str) -> str | None:
    normalized = " ".join(label.strip().lower().split())
    mapping = {
        "correct": "Correct",
        "partially correct": "Partially Correct",
        "partially incorrect": "Partially Correct",
        "partial": "Partially Correct",
        "incorrect": "Incorrect",
        "wrong": "Incorrect",
        "bonus": "Bonus",
        "extra credit": "Bonus",
    }
    return mapping.get(normalized)


def _extract_rubric_comment(child: _BulletItem) -> str | None:
    matched = _RUBRIC_COMMENT_RE.match(child.text.strip())
    if matched is None:
        return None
    parts = [matched.group("text").strip(), *_section_values(child)]
    comment = " ".join(part for part in parts if part).strip()
    return comment or None


def _parse_outcomes_section(child: _BulletItem) -> list[str]:
    matched = _OUTCOME_SECTION_RE.match(child.text.strip())
    values: list[str] = []
    if matched is not None and matched.group("text").strip():
        values.append(matched.group("text").strip())
    values.extend(_section_values(child))

    outcomes: list[str] = []
    for value in values:
        for part in value.split(","):
            name = part.strip()
            if name and name not in outcomes:
                outcomes.append(name)
    return outcomes


def _render_outcomes(lines: list[str], outcomes: tuple[str, ...], *, indent: int) -> None:
    for outcome in outcomes:
        if outcome.strip():
            _append_bullet(lines, indent=indent, text=f"**Outcome** {outcome.strip()}")


def _parse_rubric_subitem(item: _BulletItem) -> QARubricItem:
    label, points_delta = _parse_rubric_label_and_delta(
        item.text,
        line_no=item.line_no,
        require_delta=True,
    )
    default_comment: str | None = None
    notes: list[str] = []
    for child in item.children:
        if not child.text.strip():
            continue
        comment = _extract_rubric_comment(child)
        if comment is not None and default_comment is None:
            default_comment = comment
            continue
        notes.append(child.text.strip())
    return QARubricItem(
        label=label,
        points_delta=points_delta,
        notes=notes,
        default_comment=default_comment,
    )


def _parse_rubric_category(item: _BulletItem) -> QARubricItem:
    label, points_delta = _parse_rubric_label_and_delta(
        item.text,
        line_no=item.line_no,
        require_delta=False,
    )

    canonical = _canonical_rubric_category(label)
    if canonical is None:
        raise ValueError(
            "invalid rubric category at "
            f"line {item.line_no}: {item.text!r}. "
            "Top-level rubric categories must be Correct, Partially Correct, or Incorrect."
        )

    notes: list[str] = []
    subitems: list[QARubricItem] = []
    default_comment: str | None = None
    for child in item.children:
        if _RUBRIC_OPTION_RE.match(child.text.strip()):
            subitems.append(_parse_rubric_subitem(child))
            continue

        comment = _extract_rubric_comment(child)
        if comment is not None and default_comment is None:
            default_comment = comment
            continue

        if child.text.strip():
            notes.append(child.text.strip())
        notes.extend(_section_values(child))

    return QARubricItem(
        label=canonical,
        points_delta=points_delta,
        notes=notes,
        subitems=subitems,
        default_comment=default_comment,
    )


def _parse_rubric_section(item: _BulletItem) -> list[QARubricItem]:
    if not item.children:
        raise ValueError(
            f"rubric section at line {item.line_no} must contain at least one rubric option"
        )

    rubric = [_parse_rubric_category(child) for child in item.children]

    counts = {"Correct": 0, "Partially Correct": 0, "Incorrect": 0, "Bonus": 0}
    for rubric_item in rubric:
        counts[rubric_item.label] += 1

    duplicates = [
        label
        for label, count in counts.items()
        if count > 1 and label != "Bonus"
    ]
    if duplicates:
        raise ValueError(
            "rubric section must contain exactly one top-level "
            "Correct, Partially Correct, and Incorrect item; duplicates: "
            + ", ".join(duplicates)
        )

    missing = [
        label
        for label, count in counts.items()
        if count == 0 and label != "Bonus"
    ]
    if missing:
        raise ValueError(
            "rubric section must contain exactly one top-level "
            "Correct, Partially Correct, and Incorrect item; missing: "
            + ", ".join(missing)
        )

    return rubric


def _rubric_effective_deltas(
    rubric: list[QARubricItem], *, context: str
) -> list[float]:
    deltas: list[float] = []

    for item in rubric:
        if item.subitems:
            sub_deltas: list[float] = []
            for sub in item.subitems:
                if sub.points_delta is None:
                    raise ValueError(
                        f"{context} rubric subitem {sub.label!r} is missing points"
                    )
                sub_deltas.append(sub.points_delta)

            if not _is_bonus(item):
                if item.points_delta is not None:
                    total = sum(sub_deltas)
                    if abs(total - item.points_delta) > 1e-6:
                        raise ValueError(
                            f"{context} rubric item {item.label!r} has points "
                            f"{item.points_delta:+g} but subitems sum to {total:+g}"
                        )

                deltas.extend(sub_deltas)
            continue

        if not _is_bonus(item):
            if item.points_delta is None:
                raise ValueError(
                    f"{context} rubric item {item.label!r} must include points or weighted subitems"
                )
            deltas.append(item.points_delta)

    return deltas


def _validate_correct_and_incorrect_shortcuts(
    rubric: list[QARubricItem],
    *,
    points: int,
    mode: str,
    context: str,
) -> None:
    by_label = {item.label: item for item in rubric}
    correct = by_label.get("Correct")
    incorrect = by_label.get("Incorrect")
    if correct is None or correct.points_delta is None:
        return
    if incorrect is None or incorrect.points_delta is None:
        return

    if mode == "positive_awards":
        if abs(correct.points_delta - points) > 1e-6:
            raise ValueError(
                f"{context} Correct rubric item must award +{points:g} points"
            )
        if abs(incorrect.points_delta) > 1e-6:
            raise ValueError(f"{context} Incorrect rubric item must award +0 points")
        return

    if abs(correct.points_delta) > 1e-6:
        raise ValueError(f"{context} Correct rubric item must have +0 points")
    if abs(incorrect.points_delta + points) > 1e-6:
        raise ValueError(
            f"{context} Incorrect rubric item must deduct -{points:g} points"
        )


def _validate_nested_partial_exact_sum(
    rubric: list[QARubricItem],
    *,
    points: int,
    mode: str,
    context: str,
) -> None:
    standard_items = [item for item in rubric if not _is_bonus(item)]

    partial = next(
        (item for item in standard_items if item.label == "Partially Correct"), None
    )
    if partial is None or not partial.subitems:
        return

    total = sum(sub.points_delta or 0.0 for sub in partial.subitems)
    target = float(points if mode == "positive_awards" else -points)
    if abs(total - target) <= 1e-6:
        return

    raise ValueError(
        f"{context} checklist partial items must sum to {target:+g} points; "
        f"got {total:+g}"
    )


def _validate_rubric_points(
    rubric: list[QARubricItem], *, points: int, context: str
) -> None:
    if not rubric:
        return

    standard_rubric = [item for item in rubric if not _is_bonus(item)]
    deltas = _rubric_effective_deltas(standard_rubric, context=context)
    if not deltas:
        return

    mode = rubric_scoring_mode(deltas)
    _validate_correct_and_incorrect_shortcuts(
        standard_rubric,
        points=points,
        mode=mode,
        context=context,
    )
    _validate_nested_partial_exact_sum(
        standard_rubric,
        points=points,
        mode=mode,
        context=context,
    )


def _parse_subquestion_item(
    item: _BulletItem, *, parent_id: str, index: int
) -> QASubquestion:
    prompt, inline_points = _strip_inline_points(item.text)
    if not prompt:
        raise ValueError(f"subquestion at line {item.line_no} is missing prompt text")

    correct_values: list[str] = []
    choices: list[QAChoice] = []
    rubric: list[QARubricItem] = []
    image_markdown: str | None = None
    type_hint: str | None = None
    outcomes: list[str] = []
    unknown_nested: list[_BulletItem] = []

    for child in item.children:
        section_name = _normalize_section_name(child.text)

        if _OUTCOME_SECTION_RE.match(child.text.strip()) is not None:
            for outcome in _parse_outcomes_section(child):
                if outcome not in outcomes:
                    outcomes.append(outcome)
            continue

        if section_name in {"correct", "answer", "answers"}:
            correct_values.extend(_section_values(child))
            continue

        if section_name == "choices":
            choices.extend(_parse_choices_section(child))
            continue

        if section_name == "type":
            values = _section_values(child)
            type_hint = _normalize_type_hint(values[0] if values else None)
            continue

        if section_name == "image":
            values = _section_values(child)
            if values:
                image_markdown = "\n".join(values)
            continue

        if section_name == "rubric":
            rubric.extend(_parse_rubric_section(child))
            continue

        if section_name in {"expected", "partial", "wrong", "ungraded"}:
            continue

        unknown_nested.append(child)

    if unknown_nested:
        raise ValueError(
            f"nested subquestions are not supported (line {unknown_nested[0].line_no})"
        )

    label = default_subquestion_label(index)
    subquestion_id = f"{parent_id}{label}"
    points = inline_points or 0
    choices, correct_values = _maybe_expand_true_false(
        type_hint=type_hint,
        correct_values=correct_values,
        choices=choices,
        context=f"subquestion {subquestion_id!r}",
    )
    correct_choice_label, correct_choice_labels = _split_correct_labels(
        _validate_multiple_choice(
            choices=choices,
            correct_values=correct_values,
            context=f"subquestion {subquestion_id!r}",
        )
    )
    answers = [] if choices else correct_values
    _validate_rubric_points(
        rubric,
        points=points,
        context=f"subquestion {subquestion_id!r}",
    )

    return QASubquestion(
        id=subquestion_id,
        prompt=prompt,
        points=points,
        label=label,
        answers=answers,
        choices=choices,
        correct_choice_label=correct_choice_label,
        correct_choice_labels=correct_choice_labels,
        rubric=rubric,
        image_markdown=image_markdown,
        outcomes=tuple(outcomes),
    )


def _parse_question_item(item: _BulletItem, *, index: int) -> QAQuestion:
    prompt, inline_points = _strip_inline_points(item.text)
    if not prompt:
        raise ValueError(f"question at line {item.line_no} is missing prompt text")

    qid = f"q{index}"
    correct_values: list[str] = []
    choices: list[QAChoice] = []
    rubric: list[QARubricItem] = []
    image_markdown: str | None = None
    type_hint: str | None = None
    outcomes: list[str] = []
    subquestion_items: list[_BulletItem] = []

    for child in item.children:
        section_name = _normalize_section_name(child.text)

        if _OUTCOME_SECTION_RE.match(child.text.strip()) is not None:
            for outcome in _parse_outcomes_section(child):
                if outcome not in outcomes:
                    outcomes.append(outcome)
            continue

        if section_name in {"correct", "answer", "answers"}:
            correct_values.extend(_section_values(child))
            continue

        if section_name == "choices":
            choices.extend(_parse_choices_section(child))
            continue

        if section_name == "type":
            values = _section_values(child)
            type_hint = _normalize_type_hint(values[0] if values else None)
            continue

        if section_name == "image":
            values = _section_values(child)
            if values:
                image_markdown = "\n".join(values)
            continue

        if section_name == "rubric":
            rubric.extend(_parse_rubric_section(child))
            continue

        if section_name in {"expected", "partial", "wrong", "ungraded"}:
            continue

        subquestion_items.append(child)

    if choices and subquestion_items:
        raise ValueError(
            f"question {qid!r} mixes multiple-choice options and subquestions; choose one structure"
        )
    if correct_values and subquestion_items:
        raise ValueError(
            f"question {qid!r} mixes direct answers and subquestions; choose one structure"
        )

    subquestions = [
        _parse_subquestion_item(sub, parent_id=qid, index=sub_idx)
        for sub_idx, sub in enumerate(subquestion_items)
    ]

    points = inline_points or 0
    choices, correct_values = _maybe_expand_true_false(
        type_hint=type_hint,
        correct_values=correct_values,
        choices=choices,
        context=f"question {qid!r}",
    )
    correct_choice_label, correct_choice_labels = _split_correct_labels(
        _validate_multiple_choice(
            choices=choices,
            correct_values=correct_values,
            context=f"question {qid!r}",
        )
    )
    answers = [] if choices else correct_values
    _validate_rubric_points(rubric, points=points, context=f"question {qid!r}")

    return QAQuestion(
        id=qid,
        prompt=prompt,
        points=points,
        answers=answers,
        choices=choices,
        correct_choice_label=correct_choice_label,
        correct_choice_labels=correct_choice_labels,
        rubric=rubric,
        image_markdown=image_markdown,
        subquestions=subquestions,
        outcomes=tuple(outcomes),
    )


def _parse_questions_from_bullets(
    lines: list[str], *, start_line: int
) -> list[QAQuestion]:
    items = _parse_bullet_items(lines, start_line=start_line)
    return [_parse_question_item(item, index=i + 1) for i, item in enumerate(items)]


def _parse_frontmatter_questions_start_page(frontmatter: Frontmatter) -> int:
    raw_value = frontmatter.get(FrontmatterMarker.QUESTIONS_START_PAGE, 1)
    points = _parse_points(str(raw_value))
    if points is None or points not in (1, 2):
        raise ValueError("questions_start_page must be 1 or 2")
    return points


def _validate_unique_ids(
    questions: list[QAQuestion], *, context: str | None = None
) -> None:
    seen_ids: set[str] = set()
    prefix = f"{context}: " if context else ""

    for question in questions:
        if question.id in seen_ids:
            raise ValueError(
                f"{prefix}duplicate question/subquestion id detected: {question.id!r}"
            )
        seen_ids.add(question.id)

        for subquestion in question.subquestions:
            if subquestion.id in seen_ids:
                raise ValueError(
                    f"{prefix}duplicate question/subquestion id detected: {subquestion.id!r}"
                )
            seen_ids.add(subquestion.id)


def parse_qa_markdown(content: str) -> QADocument:
    lines = content.splitlines()
    frontmatter, i = _parse_frontmatter(lines)

    title = frontmatter.get(FrontmatterMarker.TITLE)
    instructions = frontmatter.get(FrontmatterMarker.INSTRUCTIONS)
    continued_page_header = frontmatter.get(FrontmatterMarker.CONTINUED_PAGE_HEADER)
    questions_start_page = _parse_frontmatter_questions_start_page(frontmatter)

    sections, has_version_headers = _split_version_sections(lines[i:], start_line=i + 1)

    if has_version_headers:
        version_questions: dict[str, list[QAQuestion]] = {}
        versions: list[str] = []

        for label, section_lines, section_start in sections:
            if label is None:
                continue
            if label in version_questions:
                raise ValueError(f"duplicate version section: {label!r}")

            parsed = _parse_questions_from_bullets(
                section_lines, start_line=section_start
            )
            _validate_unique_ids(parsed, context=f"version {label}")
            version_questions[label] = parsed
            versions.append(label)

        if not versions:
            raise ValueError("no version sections found")

        first = versions[0]
        return QADocument(
            questions=version_questions[first],
            title=title,
            instructions=instructions,
            continued_page_header=continued_page_header,
            versions=versions,
            version_questions=version_questions,
            questions_start_page=questions_start_page,
        )

    questions = _parse_questions_from_bullets(lines[i:], start_line=i + 1)
    _validate_unique_ids(questions)

    return QADocument(
        questions=questions,
        title=title,
        instructions=instructions,
        continued_page_header=continued_page_header,
        questions_start_page=questions_start_page,
    )


def load_qa_markdown(path: str | Path) -> QADocument:
    qa_path = Path(path)
    with open(qa_path) as f:
        return parse_qa_markdown(f.read())


def _render_prompt_with_points(prompt: str, points: int) -> str:
    text = prompt.strip()
    if not text:
        return f"({points} points)"
    return f"({points} points) {text}"


def _append_bullet(lines: list[str], *, indent: int, text: str) -> None:
    stripped = text.strip()
    if not stripped:
        return

    parts = stripped.splitlines()
    lines.append(f"{' ' * indent}- {parts[0].rstrip()}")
    for continuation in parts[1:]:
        lines.append(f"{' ' * (indent + 2)}{continuation.rstrip()}")


def _render_answers(lines: list[str], answers: list[str], *, indent: int) -> None:
    if not answers:
        return

    _append_bullet(lines, indent=indent, text="**Correct**")
    for answer in answers:
        _append_bullet(lines, indent=indent + 2, text=answer)


def _render_choices(lines: list[str], choices: list[QAChoice], *, indent: int) -> None:
    if not choices:
        return

    _append_bullet(lines, indent=indent, text="**Choices**")
    for choice in choices:
        _append_bullet(lines, indent=indent + 2, text=f"{choice.label}) {choice.text}")


def _render_correct_choice(
    lines: list[str],
    correct_choice_label: str | None,
    correct_choice_labels: tuple[str, ...] = (),
    *,
    indent: int,
) -> None:
    labels = (
        list(correct_choice_labels)
        if correct_choice_labels
        else ([correct_choice_label] if correct_choice_label else [])
    )
    if not labels:
        return

    _append_bullet(lines, indent=indent, text="**Correct**")
    for label in labels:
        _append_bullet(lines, indent=indent + 2, text=label)


def _render_points_delta(points_delta: float) -> str:
    rendered = f"{points_delta:g}"
    if not rendered.startswith("-"):
        rendered = f"+{rendered}"
    return rendered


def _render_rubric(
    lines: list[str], rubric_items: list[QARubricItem], *, indent: int
) -> None:
    if not rubric_items:
        return

    _append_bullet(lines, indent=indent, text="**Rubric**")
    for item in rubric_items:
        item_text = item.label
        if item.points_delta is not None:
            item_text = f"{item.label} ({_render_points_delta(item.points_delta)})"
        _append_bullet(lines, indent=indent + 2, text=item_text)

        _render_rubric_comment(lines, item.default_comment, indent=indent + 4)
        for note in item.notes:
            _append_bullet(lines, indent=indent + 4, text=note)

        for subitem in item.subitems:
            if subitem.points_delta is None:
                continue
            _append_bullet(
                lines,
                indent=indent + 4,
                text=f"{subitem.label} ({_render_points_delta(subitem.points_delta)})",
            )
            _render_rubric_comment(lines, subitem.default_comment, indent=indent + 6)
            for note in subitem.notes:
                _append_bullet(lines, indent=indent + 6, text=note)


def _render_rubric_comment(
    lines: list[str], comment: str | None, *, indent: int
) -> None:
    if comment is None or not comment.strip():
        return
    _append_bullet(lines, indent=indent, text=f"**Comment** {comment.strip()}")


def _render_image(lines: list[str], image_markdown: str | None, *, indent: int) -> None:
    if image_markdown is None or not image_markdown.strip():
        return

    _append_bullet(lines, indent=indent, text="**Image**")
    _append_bullet(lines, indent=indent + 2, text=image_markdown)


def _render_subquestion(
    lines: list[str], subquestion: QASubquestion, *, indent: int
) -> None:
    _append_bullet(
        lines,
        indent=indent,
        text=_render_prompt_with_points(subquestion.prompt, subquestion.points),
    )
    _render_image(lines, subquestion.image_markdown, indent=indent + 2)
    _render_choices(lines, subquestion.choices, indent=indent + 2)
    _render_correct_choice(
        lines,
        subquestion.correct_choice_label,
        subquestion.correct_choice_labels,
        indent=indent + 2,
    )
    _render_answers(lines, subquestion.answers, indent=indent + 2)
    _render_rubric(lines, subquestion.rubric, indent=indent + 2)
    _render_outcomes(lines, subquestion.outcomes, indent=indent + 2)


def _render_question(
    lines: list[str], question: QAQuestion, *, indent: int = 0
) -> None:
    _append_bullet(
        lines,
        indent=indent,
        text=_render_prompt_with_points(question.prompt, question.points),
    )
    _render_image(lines, question.image_markdown, indent=indent + 2)
    _render_choices(lines, question.choices, indent=indent + 2)
    _render_correct_choice(
        lines,
        question.correct_choice_label,
        question.correct_choice_labels,
        indent=indent + 2,
    )
    _render_answers(lines, question.answers, indent=indent + 2)
    _render_rubric(lines, question.rubric, indent=indent + 2)
    _render_outcomes(lines, question.outcomes, indent=indent + 2)

    for subquestion in question.subquestions:
        _render_subquestion(lines, subquestion, indent=indent + 2)


def _render_questions(lines: list[str], questions: list[QAQuestion]) -> None:
    for idx, question in enumerate(questions):
        _render_question(lines, question)
        if idx < len(questions) - 1:
            lines.append("")


def render_qa_markdown(document: QADocument) -> str:
    lines: list[str] = []
    lines.append(_render_frontmatter(document))

    if document.version_questions:
        version_order = document.versions or list(document.version_questions.keys())
        for i, version in enumerate(version_order):
            lines.append(f"## Version {version}")
            lines.append("")
            _render_questions(lines, document.version_questions.get(version, []))
            if i < len(version_order) - 1:
                lines.append("")
    else:
        _render_questions(lines, document.questions)

    return "\n".join(lines).rstrip() + "\n"


def _questions_for_version(
    document: QADocument, version: str | None
) -> list[QAQuestion]:
    if not document.version_questions:
        return document.questions

    selected = version
    if selected is None:
        selected = (
            document.versions[0]
            if document.versions
            else next(iter(document.version_questions))
        )

    if selected not in document.version_questions:
        available = ", ".join(document.version_questions.keys())
        raise ValueError(
            f"version {selected!r} not found in QA file. Available versions: {available}"
        )

    return document.version_questions[selected]


def find_qa_question(
    document: QADocument,
    question_id: str,
    *,
    version: str | None = None,
) -> QAQuestion | QASubquestion:
    for question in _questions_for_version(document, version):
        if question.id == question_id:
            return question
        for subquestion in question.subquestions:
            if subquestion.id == question_id:
                return subquestion
    raise ValueError(f"question not found in QA file: {question_id}")


def qa_question_type(question: QAQuestion | QASubquestion) -> QuestionType:
    if question.correct_choice_labels:
        return MULTIPLE_ANSWER
    if question.choices or question.correct_choice_label:
        return MULTIPLE_CHOICE
    if getattr(question, "subquestions", None):
        return FREE_RESPONSE
    if parse_numeric_answers(question.answers) is not None:
        return NUMERIC
    return FREE_RESPONSE


def _numeric_answer_hint(question: QAQuestion | QASubquestion) -> str | None:
    specs = parse_numeric_answers(question.answers)
    if specs is None:
        return None
    units = next((spec.units for spec in specs if spec.units), None)
    return f"Answer in {units}" if units else "Numeric answer"


def flatten_rubric_options(
    rubric_items: list[QARubricItem],
) -> list[QARubricFlatOption]:
    options = _flatten_rubric_options(rubric_items)
    base = [option for option in options if option.kind != "bonus"]
    bonus = [option for option in options if option.kind == "bonus"]
    return base + bonus


def qa_rubric_options_for_question(
    document: QADocument,
    question_id: str,
    *,
    version: str | None = None,
) -> list[QARubricFlatOption]:
    question = find_qa_question(document, question_id, version=version)
    return flatten_rubric_options(question.rubric)


def replace_qa_rubric_options(
    document: QADocument,
    question_id: str,
    options: list[QARubricFlatOption],
    *,
    version: str | None = None,
) -> None:
    question = find_qa_question(document, question_id, version=version)
    rubric = _rubric_from_flat_options(options, points=question.points)
    _validate_rubric_points(
        rubric,
        points=question.points,
        context=f"question {question_id!r}",
    )
    question.rubric = rubric


def _flatten_rubric_options(
    items: list[QARubricItem],
    *,
    parent_labels: tuple[str, ...] = (),
) -> list[QARubricFlatOption]:
    options: list[QARubricFlatOption] = []
    for item in items:
        if parent_labels == ("Partially Correct",) and item.label == "Partially Correct":
            labels = parent_labels
        else:
            labels = (*parent_labels, item.label)
        if item.subitems:
            options.extend(
                _flatten_rubric_options(
                    item.subitems,
                    parent_labels=labels,
                )
            )
            continue

        if item.points_delta is None:
            continue
        kind = _flat_option_kind(labels)
        selection_mode: RubricSelectionMode = (
            "checklist"
            if kind == "bonus"
            or (parent_labels and parent_labels[0] == "Partially Correct")
            else "exclusive"
        )
        options.append(
            QARubricFlatOption(
                label=" - ".join(labels),
                points_delta=item.points_delta,
                kind=kind,
                selection_mode=selection_mode,
                default_comment=item.default_comment,
            )
        )
    return options


def _flat_option_kind(labels: tuple[str, ...]) -> RubricOptionKind:
    if labels == ("Correct",):
        return "correct"
    if labels == ("Incorrect",):
        return "incorrect"
    if labels and labels[0] == "Bonus":
        return "bonus"
    return "partial"


def _rubric_from_flat_options(
    options: list[QARubricFlatOption],
    *,
    points: int,
) -> list[QARubricItem]:
    if not options:
        raise ValueError("rubric must include at least one option")

    labels = [option.label.strip() for option in options]
    if any(not label for label in labels):
        raise ValueError("rubric option labels cannot be blank")
    if len(set(labels)) != len(labels):
        raise ValueError("rubric option labels must be unique")

    correct_options = [
        option for option in options if option.label.strip() == "Correct"
    ]
    if len(correct_options) != 1:
        raise ValueError("rubric must contain exactly one Correct option")
    incorrect_options = [
        option for option in options if option.label.strip() == "Incorrect"
    ]
    if len(incorrect_options) != 1:
        raise ValueError("rubric must contain exactly one Incorrect option")

    partial_options = [
        option
        for option in options
        if option.kind == "partial"
    ]
    if not partial_options:
        raise ValueError("rubric must include at least one partial-credit option")

    bonus_options = [o for o in options if o.kind == "bonus"]

    rubric = [
        QARubricItem(
            label="Correct",
            points_delta=correct_options[0].points_delta,
            default_comment=correct_options[0].default_comment,
        ),
        _partial_rubric_item(partial_options),
    ]

    if bonus_options:
        subitems = [
            QARubricItem(
                label=_bonus_subitem_label(o.label),
                points_delta=o.points_delta,
                default_comment=o.default_comment,
            )
            for o in bonus_options
        ]
        rubric.append(QARubricItem(label="Bonus", subitems=subitems))

    rubric.append(
        QARubricItem(
            label="Incorrect",
            points_delta=incorrect_options[0].points_delta,
            default_comment=incorrect_options[0].default_comment,
        )
    )

    return rubric


def _partial_rubric_item(
    partial_options: list[QARubricFlatOption],
) -> QARubricItem:
    if len(partial_options) == 1:
        option = partial_options[0]
        label = option.label.strip()
        if (
            _canonical_rubric_category(label) == "Partially Correct"
            and option.selection_mode == "exclusive"
        ):
            return QARubricItem(
                label="Partially Correct",
                points_delta=option.points_delta,
                default_comment=option.default_comment,
            )

    subitems: list[QARubricItem] = []
    for option in partial_options:
        label = _partial_subitem_label(option.label)
        subitems.append(
            QARubricItem(
                label=label,
                points_delta=option.points_delta,
                default_comment=option.default_comment,
            )
        )
    return QARubricItem(label="Partially Correct", subitems=subitems)


def _partial_subitem_label(label: str) -> str:
    stripped = label.strip()
    prefix = "Partially Correct - "
    if stripped.startswith(prefix):
        stripped = stripped[len(prefix) :].strip()
    if not stripped:
        raise ValueError("partial-credit rubric option labels cannot be blank")
    if stripped in {"Correct", "Incorrect"}:
        raise ValueError(f"{stripped} cannot be used as a partial-credit label")
    return stripped


def qa_to_template_questions(
    document: QADocument, version: str | None = None
) -> list[QuestionInput]:
    questions: list[QuestionInput] = []
    for question in _questions_for_version(document, version):
        if not question.subquestions and not question.choices:
            hint = _numeric_answer_hint(question)
            if hint is None:
                questions.append((question.id, question.points, question.prompt))
            else:
                questions.append(
                    {
                        "id": question.id,
                        "points": question.points,
                        "prompt": question.prompt,
                        "answer_hint": hint,
                    }
                )
            continue

        question_input: dict[str, object] = {
            "id": question.id,
            "points": question.points,
            "prompt": question.prompt,
        }
        if question.choices:
            question_input["choices"] = [
                {"label": choice.label, "text": choice.text}
                for choice in question.choices
            ]
        if question.subquestions:
            question_input["subquestions"] = [
                {
                    "id": subquestion.id,
                    "label": subquestion.label,
                    "points": subquestion.points,
                    "prompt": subquestion.prompt,
                    **(
                        {
                            "choices": [
                                {"label": choice.label, "text": choice.text}
                                for choice in subquestion.choices
                            ]
                        }
                        if subquestion.choices
                        else {}
                    ),
                }
                for subquestion in question.subquestions
            ]

        questions.append(question_input)

    return questions


def qa_to_bubble_questions(
    document: QADocument, version: str | None = None
) -> list["BubbleQuestion"]:
    """Omits free-response, numeric, and select-all questions; callers must compare IDs
    to report omissions.
    """
    from opengrader.core.bubble_sheet import BubbleQuestion

    bubbles: list[BubbleQuestion] = []
    for question in _questions_for_version(document, version):
        candidates: list[QAQuestion | QASubquestion] = (
            list(question.subquestions) if question.subquestions else [question]
        )
        for item in candidates:
            if qa_question_type(item) != MULTIPLE_CHOICE:
                continue
            labels = tuple(choice.label for choice in item.choices)
            if not labels:
                labels = DEFAULT_BUBBLE_CHOICE_LABELS
            bubbles.append(
                BubbleQuestion(
                    question_id=item.id,
                    choice_labels=labels,
                    points=float(item.points),
                )
            )
    return bubbles


def save_qa_markdown(document: QADocument, path: str | Path) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(render_qa_markdown(document))


def _partial_subitem_label(label: str) -> str:
    stripped = label.strip()
    prefix = "Partially Correct - "
    if stripped.startswith(prefix):
        stripped = stripped[len(prefix) :].strip()
    if not stripped:
        raise ValueError("partial-credit rubric option labels cannot be blank")
    return stripped


def _bonus_subitem_label(label: str) -> str:
    stripped = label.strip()
    prefix = "Bonus - "
    if stripped.startswith(prefix):
        stripped = stripped[len(prefix) :].strip()
    if not stripped:
        raise ValueError("bonus rubric option labels cannot be blank")
    return stripped


def outcomes_by_question(document: "QADocument") -> dict[str, tuple[str, ...]]:
    merged: dict[str, tuple[str, ...]] = {}

    def add(question: QAQuestion) -> None:
        ids = (
            [sub.id for sub in question.subquestions]
            if question.subquestions
            else [question.id]
        )
        for graded_id in ids:
            outcomes = question.outcomes_for(graded_id)
            if not outcomes:
                continue
            existing = merged.get(graded_id, ())
            merged[graded_id] = existing + tuple(
                o for o in outcomes if o not in existing
            )

    for question in document.questions:
        add(question)
    for questions in document.version_questions.values():
        for question in questions:
            add(question)
    return merged


def possible_points_by_question(document: "QADocument") -> dict[str, float]:
    points: dict[str, float] = {}

    def add(question: QAQuestion) -> None:
        if question.subquestions:
            for sub in question.subquestions:
                points.setdefault(sub.id, float(sub.points))
            return
        points.setdefault(question.id, float(question.points))

    for question in document.questions:
        add(question)
    for questions in document.version_questions.values():
        for question in questions:
            add(question)
    return points
