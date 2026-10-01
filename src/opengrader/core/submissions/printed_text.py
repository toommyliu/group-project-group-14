"""Remove the printed question from an OCR transcription of an answer crop.

Tesseract reads everything in a crop, so its text starts with the printed
question ("1. (10 pts) Convert decimal 5 to binary [A].") and, for multiple
choice, the printed options. The vision model splits prompt from answer
itself; Tesseract cannot, so callers that know the question pass its printed
text here. Matching is fuzzy because OCR garbles print too ("B 1" for "B) 1").
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from difflib import SequenceMatcher

# A printed segment is removed when a window of the transcription matches it
# at least this closely; short segments (a choice like "A) 0") must match exactly.
MIN_MATCH_RATIO = 0.8
_EXACT_MATCH_MAX_TOKENS = 3

_TOKEN_RE = re.compile(r"\S+")
_POINTS_MARKER_RE = re.compile(r"\(\s*\d+(?:\.\d+)?\s*(?:pts?|points?)\.?\s*\)", re.IGNORECASE)
_QUESTION_NUMBER_RE = re.compile(r"^\s*\d{1,3}\s*[.)]\s*")
_LEADING_PUNCTUATION_RE = re.compile(r"^[\s.,:;?!]+")


def _normalize(token: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", token.lower())


def _tokens(text: str) -> list[tuple[str, int, int]]:
    tokens: list[tuple[str, int, int]] = []
    for match in _TOKEN_RE.finditer(text):
        normalized = _normalize(match.group())
        if normalized:
            tokens.append((normalized, match.start(), match.end()))
    return tokens


def _best_window(
    tokens: list[tuple[str, int, int]], segment: list[str]
) -> tuple[int, int] | None:
    length = len(segment)
    exact = length <= _EXACT_MATCH_MAX_TOKENS
    words = [token for token, _, _ in tokens]
    best: tuple[float, int, int] | None = None
    target = " ".join(segment)
    sizes = [length] if exact else range(max(1, length - 2), length + 3)
    for size in sizes:
        for start in range(0, len(words) - size + 1):
            window = words[start : start + size]
            if exact:
                if window == segment:
                    return start, start + size
                continue
            # Character-level, so a misread letter ("decmal") costs little.
            ratio = SequenceMatcher(
                None, " ".join(window), target, autojunk=False
            ).ratio()
            if ratio >= MIN_MATCH_RATIO and (best is None or ratio > best[0]):
                best = (ratio, start, start + size)
    return (best[1], best[2]) if best else None


def strip_printed_text(text: str, printed: Sequence[str]) -> str:
    """Return `text` without the printed segments, or `text` unchanged if none match."""
    if not text.strip() or not printed:
        return text
    segments = sorted(
        {tuple(_normalize(t) for t in _TOKEN_RE.findall(s) if _normalize(t)) for s in printed},
        key=len,
        reverse=True,
    )
    cleaned = text
    removed = False
    for segment in segments:
        if not segment:
            continue
        tokens = _tokens(cleaned)
        window = _best_window(tokens, list(segment))
        if window is None:
            continue
        start = tokens[window[0]][1]
        end = tokens[window[1] - 1][2]
        cleaned = cleaned[:start] + " " + cleaned[end:]
        removed = True
    if not removed:
        return text
    cleaned = _POINTS_MARKER_RE.sub(" ", cleaned)
    cleaned = _QUESTION_NUMBER_RE.sub("", cleaned)
    cleaned = "\n".join(" ".join(line.split()) for line in cleaned.splitlines())
    cleaned = "\n".join(line for line in cleaned.splitlines() if line)
    return _LEADING_PUNCTUATION_RE.sub("", cleaned).strip()


def printed_question_text(question: object) -> tuple[str, ...]:
    """Every piece of text printed in a QA question's answer area.

    For a multipart question this is the parent's prompt plus every part's
    prompt and choices, since one crop can cover several parts.
    """
    parts = [question, *getattr(question, "subquestions", ())]
    printed: list[str] = []
    for part in parts:
        prompt = (getattr(part, "prompt", "") or "").strip()
        if prompt:
            printed.append(prompt)
        for choice in getattr(part, "choices", ()):
            printed.append(f"{choice.label}) {choice.text}".strip())
    return tuple(printed)


def response_without_printed_text(
    response_text: str, *, source: str | None, printed: Sequence[str]
) -> str:
    """The student's answer as graders and the AI should see it.

    Only Tesseract readings are cleaned: the vision model and a human already
    separate the question from the answer, and a student's own words may
    repeat part of the question.
    """
    # Imported here: transcription -> review -> grading.solutions imports this module.
    from opengrader.core.submissions.transcription import SOURCE_TESSERACT

    if source != SOURCE_TESSERACT:
        return response_text
    return strip_printed_text(response_text, printed)
