from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from opengrader.core.submissions.review import DEFAULT_OCR_CONFIDENCE_FLOOR

# Persisted in Read by fields; renaming values breaks existing files.

SOURCE_TEXT_LAYER = "text-layer"
SOURCE_TESSERACT = "tesseract"
SOURCE_VISION_MODEL = "vision-model"
SOURCE_MATH_MODEL = "math-model"
SOURCE_BUBBLE_SHEET = "bubble-sheet"
SOURCE_HUMAN = "human"

KNOWN_READING_SOURCES: tuple[str, ...] = (
    SOURCE_TEXT_LAYER,
    SOURCE_TESSERACT,
    SOURCE_VISION_MODEL,
    SOURCE_MATH_MODEL,
    SOURCE_BUBBLE_SHEET,
    SOURCE_HUMAN,
)

MODEL_READING_SOURCES: frozenset[str] = frozenset(
    {SOURCE_VISION_MODEL, SOURCE_MATH_MODEL}
)

READING_SOURCE_LABELS: Mapping[str, str] = {
    SOURCE_TEXT_LAYER: "PDF text layer",
    SOURCE_TESSERACT: "Tesseract OCR",
    SOURCE_VISION_MODEL: "vision model",
    SOURCE_MATH_MODEL: "maths model",
    SOURCE_BUBBLE_SHEET: "bubble sheet",
    SOURCE_HUMAN: "grader",
}

UNRECORDED_SOURCE_LABEL = "unrecorded"


def describe_reading_source(source: str | None) -> str:
    if not source:
        return UNRECORDED_SOURCE_LABEL
    return READING_SOURCE_LABELS.get(source, source)


def is_model_reading(source: str | None) -> bool:
    return source in MODEL_READING_SOURCES


DEFAULT_ESCALATION_FLOOR: float = float(DEFAULT_OCR_CONFIDENCE_FLOOR)


def should_escalate(
    text: str,
    confidence: float | None,
    *,
    floor: float = DEFAULT_ESCALATION_FLOOR,
) -> bool:
    if not text.strip():
        return True
    if confidence is None:
        return False
    return confidence < floor


@dataclass(frozen=True)
class ReadingTally:
    counts: tuple[tuple[str, int], ...] = ()

    @property
    def total(self) -> int:
        return sum(count for _source, count in self.counts)

    def count_of(self, source: str) -> int:
        for name, count in self.counts:
            if name == source:
                return count
        return 0

    def merge(self, other: "ReadingTally") -> "ReadingTally":
        merged: dict[str, int] = {name: count for name, count in self.counts}
        for name, count in other.counts:
            merged[name] = merged.get(name, 0) + count
        return ReadingTally(counts=_ordered_counts(merged))

    def describe(self) -> str:
        if not self.counts:
            return "no responses transcribed"
        return ", ".join(
            f"{describe_reading_source(source)} {count}"
            for source, count in self.counts
        )


def _ordered_counts(counts: Mapping[str, int]) -> tuple[tuple[str, int], ...]:
    return tuple(
        sorted(
            ((source, count) for source, count in counts.items() if count),
            key=lambda item: (-item[1], item[0]),
        )
    )


def tally_reading_sources(sources: Iterable[str | None]) -> ReadingTally:
    counts: dict[str, int] = {}
    for source in sources:
        key = source or UNRECORDED_SOURCE_LABEL
        counts[key] = counts.get(key, 0) + 1
    return ReadingTally(counts=_ordered_counts(counts))
