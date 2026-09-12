from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QuestionSourceSegment:
    page_number: int
    bbox_norm: tuple[float, float, float, float]
