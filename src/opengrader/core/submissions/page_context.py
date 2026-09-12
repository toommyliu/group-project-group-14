from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from opengrader.core.source_location import QuestionSourceSegment

PageKind = Literal["response", "extra"]


@dataclass(frozen=True)
class PageContextEntry:
    # 1-based page number in the student's source PDF.
    page_number: int
    kind: PageKind
    # Normalized (x, y, w, h).
    highlights: tuple[tuple[float, float, float, float], ...] = ()


def covered_pages(segments: Iterable[QuestionSourceSegment]) -> frozenset[int]:
    return frozenset(
        segment.page_number for segment in segments if segment.page_number >= 1
    )


def infer_extra_pages(
    total_pages: int,
    segments: Iterable[QuestionSourceSegment],
) -> tuple[int, ...]:
    if total_pages <= 0:
        return ()
    covered = covered_pages(segments)
    if not covered:
        return ()
    return tuple(page for page in range(1, total_pages + 1) if page not in covered)


def build_page_context(
    segments: Iterable[QuestionSourceSegment],
    extra_pages: Iterable[int] = (),
) -> tuple[PageContextEntry, ...]:
    highlights_by_page: dict[int, list[tuple[float, float, float, float]]] = {}
    for segment in segments:
        if segment.page_number < 1:
            continue
        highlights_by_page.setdefault(segment.page_number, []).append(segment.bbox_norm)

    entries = [
        PageContextEntry(
            page_number=page,
            kind="response",
            highlights=tuple(highlights_by_page[page]),
        )
        for page in sorted(highlights_by_page)
    ]

    for page in sorted({int(page) for page in extra_pages if int(page) >= 1}):
        if page in highlights_by_page:
            continue
        entries.append(PageContextEntry(page_number=page, kind="extra"))

    return tuple(entries)
