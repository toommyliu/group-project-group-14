from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuestionSourceSegment:
    """Location of a question response region on a source PDF page."""

    page_number: int
    bbox_norm: tuple[float, float, float, float]
