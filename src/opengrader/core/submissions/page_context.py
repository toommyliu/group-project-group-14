"""Whole-page context for a graded response (pure, no I/O).

A response crop shows only the rectangle the extractor decided was the answer
box. That is the right default — it is the fastest thing to grade — but it is a
*fragment*: a student who writes "continued on back", draws a diagram in the
margin, or finishes a derivation on a scratch sheet has work the crop does not
contain. Grading that fragment is a correctness bug, not a missing convenience.

This module answers one question for the grade screen: **which pages of the
student's scanned PDF should a grader be able to page through for this
question, and what should be highlighted on each?** It turns the per-question
``source_segments`` (page + normalized bbox, already persisted in the
``.submission.md``) plus the submission-level ``extra_pages`` list into an
ordered sequence of :class:`PageContextEntry`.

Ordering is deliberate:

1. the question's *own* pages first, lowest page number first, each carrying the
   bboxes to outline — this is the page the crop was cut from, so it is one
   keypress away and shows the answer in context;
2. then every extra page, lowest first. Extra pages are **submission-level**:
   nobody can know which question a scratch sheet belongs to, so every question
   can reach every extra page. That is the point — the grader decides.

The module is pure so the sequence can be unit-tested without a PDF; rendering
a page to pixels lives in ``core/pdf_utils.render_page_with_highlights``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from opengrader.core.source_location import QuestionSourceSegment

# "response" = a page this question's answer box was cropped from (the bboxes
# are highlighted). "extra" = a page no question crop covers — scratch/overflow
# work, offered to every question because it belongs to no single one.
PageKind = Literal["response", "extra"]


@dataclass(frozen=True)
class PageContextEntry:
    """One page a grader can page to from a question's response view."""

    # 1-based page number in the student's source PDF, matching the numbering
    # used by ``QuestionSourceSegment.page_number``.
    page_number: int
    kind: PageKind
    # Normalized (x, y, w, h) rectangles to outline on the rendered page —
    # the regions this question's crop was cut from. Empty for extra pages,
    # which belong to no question and so have nothing to point at.
    highlights: tuple[tuple[float, float, float, float], ...] = ()


def covered_pages(segments: Iterable[QuestionSourceSegment]) -> frozenset[int]:
    """Return the 1-based pages at least one response crop was taken from."""
    return frozenset(
        segment.page_number for segment in segments if segment.page_number >= 1
    )


def infer_extra_pages(
    total_pages: int,
    segments: Iterable[QuestionSourceSegment],
) -> tuple[int, ...]:
    """Pages of a scan that no question's crop covers, ascending.

    The fallback for submissions extracted before ``extra_pages`` was recorded
    (and for runs that never used ``submission-splitter --extra-work-pages``):
    any page the extractor never cut a crop from holds work a grader would
    otherwise never see. This catches the sharp case — a "continued on back"
    page — as well as blank continuation sheets, which cost nothing to offer.

    Returns ``()`` when nothing can be concluded: a page count we don't know
    (``total_pages <= 0``), or a submission with *no* segments at all, where
    every page would look uncovered and the answer would be pure noise.
    """
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
    """Build the ordered page sequence a question's response view can show.

    ``segments`` are one question's source segments; ``extra_pages`` is the
    submission-level list (frontmatter or inferred). Duplicate page numbers
    collapse into a single entry carrying every bbox on that page, and an extra
    page that is *also* one of this question's pages stays a ``"response"``
    entry — the highlight is more useful than the "extra" label.
    """
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
