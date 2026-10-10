from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import Sequence

from opengrader.core.pdf_utils import POINTS_SPEC

from .outline import Outline
from .printed import FIELD_LINE_RE, TextLine

QUESTION_LABEL_RE = re.compile(r"^\s*[-*•]?\s*(\d{1,2})\s*[.,)]\s*" + POINTS_SPEC, re.IGNORECASE)
PART_LABEL_RE = re.compile(r"^\s*[-*•]?\s*\(?([a-h])\)\s*" + POINTS_SPEC, re.IGNORECASE)
_PLAIN_QUESTION_RE = re.compile(r"^\s*(\d{1,2})\s*[.)]\s+\S")
# How a question or a part may be printed when the answer key says what to
# look for (`outline_labels`): "3." "3)" "Q3." "Question 3:", parts "3a)"
# "3(a)" "3.a)", and — under their question — a bare "a)" "(a)" "a.".
_KEYED_PART_RE = re.compile(
    r"^\s*[-*•]?\s*(?:(?:Q|Question|Problem)\s*)?(\d{1,2})\s*"
    r"(?:\.?([a-h])[.)]|\(([a-h])\)[.:]?)\s*(.*)$",
    re.IGNORECASE,
)
_KEYED_QUESTION_RE = re.compile(
    r"^\s*[-*•]?\s*(?:(?:Q|Question|Problem)\s*(\d{1,2})\s*[.):]|(\d{1,2})\s*[.,)])\s*(.*)$",
    re.IGNORECASE,
)
_KEYED_BARE_PART_RE = re.compile(r"^\s*[-*•]?\s*(?:\(([a-h])\)|([a-h])[.)])\s+(.*)$", re.IGNORECASE)
_POINTS_ANYWHERE_RE = re.compile(POINTS_SPEC, re.IGNORECASE)
# How alike a printed line and a question's text must be for the line to be
# that question with no number in front of it at all.
_TEXT_ONLY_MATCH = 0.85
_TEXT_ONLY_MIN_CHARS = 12
# What one question of the key left unfound between two found ones costs.
_SKIP_COST = 0.3
_PAGE_NUMBER_RE = re.compile(r"^\s*(?:page\s*)?(\d{1,2})(?:\s*(?:of|/)\s*(\d{1,2}))?\s*$", re.IGNORECASE)
_PAGE_OF_RE = re.compile(r"\bpage\s*(\d{1,2})\s*(?:of|/)\s*(\d{1,2})\b", re.IGNORECASE)

LEFT, RIGHT = 0.01, 0.99
_PAD = 0.002
_EDGE_BAND = 0.12
MIN_CONTINUATION = 0.05


@dataclass(frozen=True)
class QuestionLabel:
    key: str
    number: int
    part: str | None
    points: float | None
    box: tuple[float, float, float, float]
    # The question's text as the answer key gives it, when the key is what
    # found this label; None reads it off the page (`_prompt`).
    prompt: str | None = None


@dataclass(frozen=True)
class PageLayout:
    labels: tuple[QuestionLabel, ...] = ()
    fields: dict[str, tuple[float, float, float, float]] = field(default_factory=dict)
    page_number: int | None = None
    page_count: int | None = None
    header_bottom: float = 0.04
    content_bottom: float = 0.965
    lines: tuple[TextLine, ...] = ()
    title: str = ""
    printed_only: bool = False
    # True when the answer key found the labels, not the page's own print.
    guided: bool = False

    @property
    def has_identity(self) -> bool:
        return "name" in self.fields or "id" in self.fields

    @property
    def top_band(self) -> tuple[float, float, float, float] | None:
        if not self.labels:
            return None
        top, bottom = self.header_bottom + 0.002, label_starts(self)[self.labels[0].key] - _PAD
        return (LEFT, top, RIGHT, bottom) if bottom - top >= MIN_CONTINUATION else None


@dataclass(frozen=True)
class Region:
    key: str
    box: tuple[float, float, float, float]
    points: float | None = None
    prompt: str = ""


def page_layout(
    lines: list[TextLine] | tuple[TextLine, ...],
    *,
    printed_only: bool,
    outlines: Sequence[Outline] = (),
) -> PageLayout:
    """`outlines` is the answer key's questions (one outline per version).
    They are used only where they find more than the page's own labels do, so
    a page Opengrader printed reads exactly as it does without them."""
    lines = sorted(lines, key=lambda line: (line.box[1], line.box[0]))
    fields, title = _fields(lines, printed_only=printed_only)
    labels = _labels(lines)
    guided = False
    if outlines:
        own = len(labels) if any(label.points is not None for label in labels) else 0
        keyed = max((outline_labels(lines, outline) for outline in outlines), key=_labels_score)
        if keyed and len(keyed) > own:
            labels, guided = keyed, True
    page_number, page_count = _page_number(lines)
    first_label_y = labels[0].box[1] if labels else 1.0
    last_label_y = labels[-1].box[1] if labels else 0.0

    footer = [line.box[1] for line in lines if line.box[1] > max(last_label_y, 1 - _EDGE_BAND) and is_page_marker(line.text)]
    content_bottom = min(footer, default=0.965) - _PAD
    header_lines = [
        line for line in lines
        if line.box[3] < first_label_y - 0.002
        and (line.box[3] < _EDGE_BAND or is_page_marker(line.text) or FIELD_LINE_RE.match(line.text))
    ]
    header_bottom = max((line.box[3] for line in header_lines), default=0.04)
    return PageLayout(
        labels=tuple(labels), fields=fields, page_number=page_number, page_count=page_count,
        header_bottom=header_bottom, content_bottom=content_bottom, lines=tuple(lines), title=title,
        printed_only=printed_only, guided=guided,
    )


def question_regions(layout: PageLayout, next_page: PageLayout | None = None) -> list[tuple[Region, tuple[float, float, float, float] | None]]:
    labels = list(layout.labels)
    starts = label_starts(layout)
    parents = {label.key for label in labels if any(other.number == label.number and other.part for other in labels) and not label.part}
    out: list[tuple[Region, tuple[float, float, float, float] | None]] = []
    pending_top: float | None = None
    for index, label in enumerate(labels):
        following = labels[index + 1] if index + 1 < len(labels) else None
        top = starts[label.key] - _PAD
        if label.key in parents:
            pending_top = top
            continue
        if pending_top is not None:
            top, pending_top = pending_top, None
        bottom = starts[following.key] - _PAD if following else layout.content_bottom
        box = (LEFT, max(0.0, top), RIGHT, max(top + 0.01, bottom))
        continuation = next_page.top_band if following is None and next_page is not None else None
        prompt = label.prompt if label.prompt is not None else _prompt(layout, box)
        out.append((Region(label.key, box, label.points, prompt), continuation))
    return out


def label_starts(layout: PageLayout) -> dict[str, float]:
    heights = sorted(label.box[3] - label.box[1] for label in layout.labels)
    typical = heights[len(heights) // 2] if heights else 0.0
    return {label.key: max(label.box[1], (label.box[1] + label.box[3]) / 2 - 0.55 * typical) for label in layout.labels}


def _labels(lines: list[TextLine]) -> list[QuestionLabel]:
    labels: list[QuestionLabel] = []
    current: int | None = None
    for line in lines:
        if match := QUESTION_LABEL_RE.match(line.text):
            current = int(match.group(1))
            labels.append(QuestionLabel(f"q{current}", current, None, float(match.group(2)), line.box))
        elif (match := PART_LABEL_RE.match(line.text)) and current is not None:
            part = match.group(1).lower()
            labels.append(QuestionLabel(f"q{current}{part}", current, part, float(match.group(2)), line.box))
    if labels:
        return _dedupe(labels)
    return _plain_labels(lines)


def _plain_labels(lines: list[TextLine]) -> list[QuestionLabel]:
    candidates = [(int(m.group(1)), line) for line in lines if (m := _PLAIN_QUESTION_RE.match(line.text))]
    if len(candidates) < 2:
        return []
    column = min(line.box[0] for _, line in candidates)
    in_column = [(n, line) for n, line in candidates if line.box[0] - column < 0.03]
    labels, expected = [], None
    for number, line in in_column:
        if expected is None or number == expected:
            labels.append(QuestionLabel(f"q{number}", number, None, None, line.box))
            expected = number + 1
    return labels if len(labels) >= 2 else []


def _labels_score(labels: list[QuestionLabel]) -> tuple[int, int]:
    # More questions found wins; between versions that find as many, the one
    # whose parts were found too.
    return len(labels), sum(1 for label in labels if label.part)


def _alike(printed: str, expected: str) -> float:
    """How much a printed line reads like the start of a question's text."""
    a = re.sub(r"\W+", "", _POINTS_ANYWHERE_RE.sub("", printed).lower())
    b = re.sub(r"\W+", "", expected.lower())
    if not a or not b:
        return 0.0
    # A long question wraps: the line is only its beginning.
    return difflib.SequenceMatcher(None, a, b[: max(len(a), 1)]).ratio()


def _keyed_candidates(line: TextLine, outline: Outline) -> list[tuple[int, float, bool]]:
    """(index into the outline, how well it fits, needs its question above)
    for every question or part this line could be the printed start of."""
    text = line.text
    found: list[tuple[int, float, bool]] = []
    part = _KEYED_PART_RE.match(text)
    question = None if part else _KEYED_QUESTION_RE.match(text)
    bare = None if part or question else _KEYED_BARE_PART_RE.match(text)
    for index, item in enumerate(outline):
        if part and item.part == (part.group(2) or part.group(3)).lower() and item.number == int(part.group(1)):
            found.append((index, 1.0 + _alike(part.group(4), item.prompt), False))
        elif question and item.part is None and item.number == int(question.group(1) or question.group(2)):
            found.append((index, 1.0 + _alike(question.group(3), item.prompt), False))
        elif bare and item.part == (bare.group(1) or bare.group(2)).lower():
            # "a)" says which part, not of which question: it is one only
            # right under that question, or its earlier parts.
            found.append((index, 1.0 + _alike(bare.group(3), item.prompt), True))
        elif (
            not (part or question or bare)
            and len(re.sub(r"\W+", "", item.prompt)) >= _TEXT_ONLY_MIN_CHARS
            and _alike(text, item.prompt) >= _TEXT_ONLY_MATCH
        ):
            # No number printed at all, but the line is the question's text.
            found.append((index, 1.0 + _alike(text, item.prompt), False))
    return found


def outline_labels(lines: Sequence[TextLine], outline: Outline) -> list[QuestionLabel]:
    """The answer key's questions, found on a page.

    Lines are taken top to bottom and questions in the key's order: the
    result is the run of (line, question) pairs that fits the key best. So a
    numbered step a student wrote in an answer is not a question — it does
    not come in order, and it does not read like the question's text — and a
    question the page prints with no points, or lettered "3a)", still is.
    A page holds a stretch of the key, so starting anywhere costs nothing,
    but skipping a question inside the stretch does.
    """
    ordered = sorted(lines, key=lambda line: (line.box[1], line.box[0]))
    pairs = [
        (position, index, fit, needs_question)
        for position, line in enumerate(ordered)
        for index, fit, needs_question in _keyed_candidates(line, outline)
    ]
    best: list[float] = []
    link: list[int | None] = []
    for current, (position, index, fit, needs_question) in enumerate(pairs):
        score, previous = (float("-inf") if needs_question else fit), None
        for earlier in range(current):
            before_position, before_index, _fit, _needs = pairs[earlier]
            if before_position >= position or before_index >= index or best[earlier] == float("-inf"):
                continue
            if needs_question and outline[before_index].number != outline[index].number:
                continue
            skipped = sum(1 for item in outline[before_index + 1 : index] if not item.stem)
            candidate = best[earlier] + fit - _SKIP_COST * skipped
            if candidate > score:
                score, previous = candidate, earlier
        best.append(score)
        link.append(previous)

    if not pairs or max(best) == float("-inf"):
        return []
    at: int | None = max(range(len(pairs)), key=lambda i: best[i])
    run: list[tuple[int, int, float, bool]] = []
    while at is not None:
        run.append(pairs[at])
        at = link[at]
    run.reverse()
    # One number alone proves nothing unless the line also reads like the question.
    if len(run) == 1 and run[0][2] < 1.5:
        return []

    with_parts = {outline[index].number for _p, index, _f, _n in run if outline[index].part}
    labels = []
    for position, index, _fit, _needs in run:
        item, line = outline[index], ordered[position]
        if item.stem and item.number not in with_parts:
            # The stem with none of its parts on this page is not an answer.
            continue
        points = item.points
        if points is None and (printed := _POINTS_ANYWHERE_RE.search(line.text)):
            points = float(printed.group(1))
        labels.append(QuestionLabel(item.key, item.number, item.part, points, line.box, item.prompt))
    return labels


def _dedupe(labels: list[QuestionLabel]) -> list[QuestionLabel]:
    seen: set[str] = set()
    out = []
    for label in labels:
        if label.key not in seen:
            seen.add(label.key)
            out.append(label)
    return out


def _fields(lines: list[TextLine], *, printed_only: bool) -> tuple[dict[str, tuple[float, float, float, float]], str]:
    labels = [(line, m) for line in lines if (m := FIELD_LINE_RE.match(line.text))]
    fields: dict[str, tuple[float, float, float, float]] = {}
    for line, match in labels:
        kind = "name" if match.group(1).lower() == "name" else "id"
        if kind in fields:
            continue
        x0, y0, x1, y1 = line.box
        label_x1 = x0 + (x1 - x0) * match.end() / max(1, len(line.text))
        h = y1 - y0
        next_label = min(
            (other.box[0] for other, _ in labels if other is not line and other.box[0] > label_x1 and abs(other.center_y - line.center_y) < 1.5 * h),
            default=None,
        )
        right = next_label + 0.012 if next_label is not None else min(RIGHT, label_x1 + 0.3)
        fields[kind] = (max(0.0, x0 - 0.01), max(0.0, y0 - 1.2 * h), right, min(1.0, y1 + 0.7 * h))

    title_text = ""
    if fields:
        title = _title(lines, fields, printed_only=printed_only)
        if title is not None:
            fields["title"], title_text = title
    return fields, title_text


def _title(lines: list[TextLine], fields: dict, *, printed_only: bool) -> tuple[tuple[float, float, float, float], str] | None:
    first_label = next((line.box[1] for line in lines if QUESTION_LABEL_RE.match(line.text)), 1.0)
    field_rows = [box for kind, box in fields.items() if kind in ("name", "id")]

    def beside_field(line: TextLine) -> bool:
        return any(box[0] <= line.box[0] and box[1] <= line.center_y <= box[3] + 0.01 for box in field_rows)

    def in_paragraph(line: TextLine) -> bool:
        return _wide(line) and any(
            other is not line and _wide(other) and abs(other.center_y - line.center_y) < 1.6 * max(line.height, other.height)
            for other in lines
        )

    candidates = [
        line for line in lines
        if line.box[3] < first_label and not FIELD_LINE_RE.match(line.text)
        and len(re.sub(r"\W", "", line.text)) >= 4 and (printed_only or not beside_field(line))
        and not in_paragraph(line)
    ]
    if not candidates:
        return None
    tallest = max(line.height for line in candidates)
    title = min((line for line in candidates if line.height >= 0.8 * tallest), key=lambda line: line.box[1])
    x0, y0, x1, y1 = title.box
    right = min([1.0, x1 + 0.008] + [box[0] for box in field_rows if box[0] > x0 and box[1] <= title.center_y <= box[3]])
    return (max(0.0, x0 - 0.006), max(0.0, y0 - 0.004), right, min(1.0, y1 + 0.005)), title.text


def _wide(line: TextLine) -> bool:
    return line.box[2] - line.box[0] > 0.4


def _page_number(lines: list[TextLine]) -> tuple[int | None, int | None]:
    for line in lines:
        if (line.box[3] < _EDGE_BAND or line.box[1] > 1 - _EDGE_BAND) and (m := _PAGE_OF_RE.search(line.text)):
            return int(m.group(1)), int(m.group(2))
    for line in lines:
        if line.box[1] > 1 - _EDGE_BAND and (m := _PAGE_NUMBER_RE.match(line.text)):
            return int(m.group(1)), int(m.group(2)) if m.group(2) else None
    return None, None


def is_page_number(line: TextLine) -> bool:
    return is_page_marker(line.text) and (line.box[3] < _EDGE_BAND or line.box[1] > 1 - _EDGE_BAND)


def is_page_marker(text: str) -> bool:
    return bool(_PAGE_NUMBER_RE.match(text) or _PAGE_OF_RE.search(text))


def _prompt(layout: PageLayout, box: tuple[float, float, float, float]) -> str:
    heights = sorted(line.height for line in layout.lines)
    typical = heights[int(0.3 * len(heights))] if heights else 0.0
    gap = 3 * typical if layout.printed_only else typical
    kept: list[str] = []
    label = previous = None
    for line in (line for line in layout.lines if box[1] <= line.center_y <= box[3]):
        if QUESTION_LABEL_RE.match(line.text) or PART_LABEL_RE.match(line.text):
            label = previous = line
            kept.append(line.text)
        elif label is not None and _continues(line, label, previous, typical, gap):
            previous = line
            kept.append(line.text)
    if kept:
        for pattern in (QUESTION_LABEL_RE, PART_LABEL_RE):
            kept[0] = pattern.sub("", kept[0], count=1)
    return " ".join(part.strip() for part in kept if part.strip())


def _continues(line: TextLine, label: TextLine, previous: TextLine, typical: float, gap: float) -> bool:
    return (line.height <= 1.4 * typical and line.box[1] - previous.box[3] <= gap
            and label.box[0] - 0.01 <= line.box[0] <= label.box[0] + 0.08)
