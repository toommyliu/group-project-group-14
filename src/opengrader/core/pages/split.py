from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from opengrader.core.submissions.scan_notes import read_note_path, write_note

from .catalog import PageCatalog, PageMatch

SIDECAR_SUFFIX = ".pages.json"
# A break counts as class-wide, not one student's shuffle, at this many submissions and share.
_SHARED_MIN_SUBMISSIONS = 3
_SHARED_SHARE = 0.5
_TEMPLATE_INLIERS = 40
_TEMPLATE_RUNNER_UP = 0.6


@dataclass(frozen=True)
class ScanGroupPlan:
    pages: tuple[tuple[int, int], ...]
    warnings: tuple[str, ...]


def plan_scan(source: Path, directory: Path, expected_count: int | None = None) -> tuple[list[ScanGroupPlan], int | None]:
    from .catalog import build_catalog, scan_pages

    catalog = build_catalog(scan_pages([source]), directory, workers=4)
    plans = [
        ScanGroupPlan(
            tuple((m.page.index + 1, 90 * (m.alignment.quarter_turns if m.alignment is not None else 0)) for m in s.pages),
            tuple(s.warnings),
        )
        for s in plan_submissions(catalog, expected_count=expected_count)
    ]
    return plans, len(catalog.expected_pages) or None


@dataclass
class PlannedSubmission:
    name: str
    pages: list[PageMatch] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Where the scan runs backwards: (page read, page read right after it).
    order_breaks: tuple[tuple[int, int], ...] = ()
    order_shared: bool = False
    reordered: bool = False
    # What order was judged by, per page as scanned: template positions or printed numbers.
    order_keys: tuple[int | None, ...] = ()
    order_by_template: bool = False
    order_warning: str = ""

    def page_numbers(self, catalog: PageCatalog) -> list[int | None]:
        return [number_of(catalog, m) for m in self.pages]


def plan_submissions(
    catalog: PageCatalog, *, prefix: str = "submission", pad: int = 4, expected_count: int | None = None,
    reorder: bool = False, template_positions: Mapping[str, int] | None = None,
) -> list[PlannedSubmission]:
    # Pages stay in scan order unless reorder is set: extraction finds each page by its print,
    # so a misread number moving a correct page costs more than a shuffled one left alone.
    runs = _segments(catalog, expected_count)
    expected = catalog.expected_pages
    keyed = [_order_keys(catalog, run, template_positions) for run in runs]
    breaks = [_order_breaks([key for key in keys if key is not None]) for keys, _ in keyed]
    shared = _shared_breaks(breaks)
    planned: list[PlannedSubmission] = []
    for index, run in enumerate(runs, start=1):
        submission = PlannedSubmission(f"{prefix}-{index:0{pad}d}")
        keys, by_template = keyed[index - 1]
        submission.order_keys, submission.order_by_template = tuple(keys), by_template
        submission.order_breaks = breaks[index - 1]
        submission.order_shared = bool(submission.order_breaks) and all(b in shared for b in submission.order_breaks)
        submission.reordered = reorder and bool(submission.order_breaks)
        if submission.reordered:
            submission.pages = [run[i] for i in sorted_order(keys)]
        else:
            submission.pages = list(run)
        if submission.order_breaks:
            submission.order_warning = order_warning(
                keys, by_template=by_template, reordered=submission.reordered,
                shared=submission.order_shared, total=len(runs),
            )
            submission.warnings.append(submission.order_warning)
        numbers = [n for n in (number_of(catalog, m) for m in run) if n is not None]
        missing = [n for n in _expected_for(catalog, run, expected) if n not in numbers]
        if missing:
            listed = ", ".join(str(n) for n in missing)
            submission.warnings.append(f"Missing page(s) {listed}: this submission has {len(numbers)} of "
                                       f"{len(_expected_for(catalog, run, expected))} printed pages. Check the original scan.")
        repeated = sorted({n for n in numbers if numbers.count(n) > 1})
        if repeated:
            submission.warnings.append(f"Page(s) {', '.join(map(str, repeated))} appear more than once; check for a duplicate scan.")
        if expected_count and len(run) != expected_count and not missing:
            submission.warnings.append(f"{len(run)} page(s) scanned where {expected_count} are expected per submission; "
                                       "check for extra, duplicate or missing pages.")
        unknown = sum(1 for m in run if m.group is None)
        if unknown:
            where = "they are kept at the end" if submission.reordered else "they were left where they were scanned"
            submission.warnings.append(f"{unknown} page(s) did not match any printed page (blank, extra work, or another document); "
                                       f"{where}.")
        planned.append(submission)
    return planned


def shared_order_note(planned: Sequence[PlannedSubmission]) -> str | None:
    affected = [s for s in planned if s.order_shared]
    if not affected:
        return None
    pairs = sorted({b for s in affected for b in s.order_breaks})
    listed = "; ".join(f"page {after} right after page {before}" for before, after in pairs)
    action = (
        "They were sorted because reordering is on; if the scan was in order, split again without it."
        if any(s.reordered for s in affected)
        else "Pages were left as scanned."
    )
    return (f"{len(affected)} of {len(planned)} submissions have the same pages out of sequence ({listed}). A whole class "
            f"rarely shuffles the same way, so a page number was probably misread and the scan is fine. {action}")


def template_positions(catalog: PageCatalog, template: Path, *, dpi: int = 150) -> dict[str, int]:
    # Group id -> 1-based template page, matched by print rather than by a page number read
    # off a scan. A group that matches no template page is left out.
    import fitz

    from opengrader.core.pdf_utils import render_page_to_image

    from .catalog import ensure_features
    from .features import align, gray_for_matching, page_features

    with fitz.open(str(template)) as doc:
        references = [
            page_features(gray_for_matching(render_page_to_image(doc, index, dpi=dpi, cache=False)[0]))
            for index in range(len(doc))
        ]
    positions: dict[str, int] = {}
    for group in catalog.groups.values():
        features = ensure_features(group).features
        scores = [
            (alignment.inliers if (alignment := align(features, reference)) is not None else 0, position)
            for position, reference in enumerate(references, start=1)
        ]
        ranked = sorted(scores, reverse=True)
        if not ranked or ranked[0][0] < _TEMPLATE_INLIERS:
            continue
        # Two template pages that look alike cannot say which one this is.
        if len(ranked) > 1 and ranked[1][0] > _TEMPLATE_RUNNER_UP * ranked[0][0]:
            continue
        positions[group.id] = ranked[0][1]
    return positions


def _order_keys(
    catalog: PageCatalog, run: list[PageMatch], positions: Mapping[str, int] | None
) -> tuple[list[int | None], bool]:
    keys, by_template = order_keys(catalog, [m.group for m in run], positions)
    if by_template:
        return keys, True
    # Each page's own number: its QR code's where it carries one.
    return [number_of(catalog, m) if m.group else None for m in run], False


def order_keys(
    catalog: PageCatalog, groups: Sequence[str | None], positions: Mapping[str, int] | None
) -> tuple[list[int | None], bool]:
    matched = [gid for gid in groups if gid is not None]
    if positions and matched and all(gid in positions for gid in matched):
        return [positions[gid] if gid else None for gid in groups], True
    return [catalog.groups[gid].page_number if gid else None for gid in groups], False


def sorted_order(keys: Sequence[int | None]) -> list[int]:
    return sorted(range(len(keys)), key=lambda i: (keys[i] is None, keys[i] or 0, i))


def order_breaks(keys: Sequence[int | None]) -> tuple[tuple[int, int], ...]:
    return _order_breaks([key for key in keys if key is not None])


def _order_breaks(keys: Sequence[int]) -> tuple[tuple[int, int], ...]:
    return tuple((before, after) for before, after in zip(keys, keys[1:]) if after < before)


def _shared_breaks(breaks: Sequence[tuple[tuple[int, int], ...]]) -> set[tuple[int, int]]:
    counts = Counter(pair for found in breaks for pair in set(found))
    return {
        pair for pair, count in counts.items()
        if count >= _SHARED_MIN_SUBMISSIONS and count >= _SHARED_SHARE * len(breaks)
    }


def describe_order(keys: Sequence[int | None], by_template: bool) -> str:
    listed = ", ".join(str(key) for key in keys if key is not None)
    return f"matched to blank-template pages {listed}" if by_template else f"page numbers read as {listed}"


def order_warning(
    keys: Sequence[int | None], *, by_template: bool, reordered: bool = False, shared: bool = False, total: int = 1,
) -> str:
    read = describe_order(keys, by_template)
    if reordered:
        text = f"Pages looked out of order in the scan ({read}); they have been sorted" + (
            " into the template's order." if by_template else " by page number.")
        if shared:
            text += " Most submissions show the same break, so check that a page number was not misread."
        return text
    if shared:
        return (f"Pages look out of order ({read}), but most of the {total} submissions show the same break, so a page "
                "number was probably misread and the scan is fine. Pages were left as scanned.")
    return (f"Pages look out of order in the scan ({read}); they were left as scanned. Check them by eye; "
            "they can be sorted before extraction (--reorder when splitting).")


def unclear_starts(catalog: PageCatalog) -> str | None:
    """Why this scan can't show where each paper starts, or None when it can.

    The cuts are chosen from the page numbers read off the printed pages. With
    too few of them every way of cutting costs the same, and the planner then
    takes the longest pieces it allows — two students in one PDF. A guess like
    that is worse than asking for the page count, so the caller falls back to
    cutting every N pages, or stops. Only pages that matched a printed page
    are counted: blank backs and scratch sheets never make a scan unclear.
    """
    printed = [m for m in catalog.matches if m.group is not None]
    if not printed:
        return "no scanned page matched a printed page"
    if 1 not in catalog.expected_pages:
        return "no page was read as the first page of a paper"
    numbered = sum(1 for m in printed if number_of(catalog, m) is not None)
    if numbered * 2 < len(printed):
        return f"a page number was read on only {numbered} of {len(printed)} printed pages"
    return None


def number_of(catalog: PageCatalog, match: PageMatch) -> int | None:
    """The page's printed number: its own QR code's, which is exact, else its
    printed page's (read off the footer, or inferred from its neighbours)."""
    if match.code is not None and match.code.page is not None:
        return match.code.page
    return catalog.groups[match.group].page_number if match.group else None


def _expected_for(catalog: PageCatalog, run: list[PageMatch], expected: list[int]) -> list[int]:
    counts = [m.code.total_pages for m in run if m.code is not None and m.code.total_pages]
    counts += [catalog.groups[m.group].layout.page_count for m in run if m.group and catalog.groups[m.group].layout.page_count]
    if counts:
        return list(range(1, max(counts) + 1))
    return expected


# What it costs to put a page before a paper's first numbered page. A paper
# starts on a numbered page; a page with no number read in front of it is
# more likely the end of the paper scanned before (a blank back, a scratch
# sheet, a last page whose number went unread), so it goes there unless its
# own paper needs it. A printed page costs more than a blank or unmatched one:
# it may be another student's page.
_LEADING_PRINTED = 0.5
_LEADING_UNMATCHED = 0.1
# A run with no page number read and no length to measure it by: nothing says
# where it starts or ends, so it is kept as rare as before.
_UNANCHORED = 5.0


@dataclass(frozen=True)
class _CutPage:
    # Matched a printed page (blank backs and scratch sheets did not).
    printed: bool
    number: int | None
    # From the page's QR code: two students, or two versions, are never one paper.
    student: str | None = None
    version: str | None = None


def _segments(catalog: PageCatalog, expected_count: int | None = None) -> list[list[PageMatch]]:
    """Cut the scan into papers at the lowest total `_submission_cost`.

    The length of a paper is what its QR codes print, else `expected_count`
    when given (by a person, or a blank template's length), else the highest
    page number printed.
    """
    matches = catalog.matches
    expected = catalog.expected_pages
    length = catalog.qr_total_pages or expected_count or (max(expected) if expected else None)
    longest = 2 * max(1, len(expected), length or 0) + 2
    pages = [
        _CutPage(
            m.group is not None, number_of(catalog, m),
            m.code.student_id if m.code is not None else None,
            m.code.version if m.code is not None else None,
        )
        for m in matches
    ]
    best = [0.0] + [float("inf")] * len(matches)
    cut = [0] * (len(matches) + 1)
    for end in range(1, len(matches) + 1):
        for start in range(max(0, end - longest), end):
            cost = _submission_cost(pages[start:end], expected, length)
            if best[start] + cost < best[end]:
                best[end], cut[end] = best[start] + cost, start
    runs, end = [], len(matches)
    while end > 0:
        runs.append(matches[cut[end]:end])
        end = cut[end]
    return runs[::-1]


def _submission_cost(pages: Sequence[_CutPage], expected: Sequence[int], length: int | None) -> float:
    """What it costs to call these scanned pages one paper.

    Every paper costs 1, so pages that fit together stay together. A page
    number read twice can't be one paper, nor can QR codes naming two
    students or two versions. Each expected page missing costs 1,
    but a printed page whose number went unread may be the missing one. Pages
    beyond the paper's length cost 1 each — the printed pages only, so blank
    backs and scratch sheets never make a paper too long — and pages in the
    wrong order 0.25 a pair. Pages before the first numbered one cost
    `_LEADING_*`, so a paper starts on a numbered page when it can.
    """
    known = [page.number for page in pages if page.number is not None]
    if len(known) != len(set(known)):
        return float("inf")
    if len({p.student for p in pages if p.student}) > 1 or len({p.version for p in pages if p.version}) > 1:
        return float("inf")
    unread = sum(1 for page in pages if page.printed and page.number is None)
    leading = 0.0
    for page in pages:
        if page.number is not None:
            break
        leading += _LEADING_PRINTED if page.printed else _LEADING_UNMATCHED
    excess = max(0, len(known) + unread - length) if length else 0
    if not known:
        if length is None:
            return _UNANCHORED
        # Nothing read says where this paper starts; only its length can.
        return 1.0 + abs(unread - length) + leading
    missing = max(0, len([n for n in expected if n not in known]) - unread)
    inversions = sum(1 for i, a in enumerate(known) for b in known[i + 1:] if a > b)
    return 1.0 + missing + excess + 0.25 * inversions + leading


def write_sidecar(pdf_path: Path, submission: PlannedSubmission, catalog: PageCatalog) -> Path:
    return write_note(pdf_path, SIDECAR_SUFFIX, json.dumps({
        "pages": [{"group": m.group, "page_number": number_of(catalog, m)} for m in submission.pages],
    }, indent=1) + "\n")


def read_sidecar(pdf_path: Path) -> dict | None:
    path = read_note_path(pdf_path, SIDECAR_SUFFIX)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def write_split_pdf(submission: PlannedSubmission, output: Path) -> Path:
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    readers: dict[Path, PdfReader] = {}
    for match in submission.pages:
        reader = readers.setdefault(match.page.pdf, PdfReader(str(match.page.pdf)))
        writer.add_page(reader.pages[match.page.index])
        turns = match.alignment.quarter_turns if match.alignment is not None else 0
        if turns:
            writer.pages[-1].rotate(90 * turns)
    with open(output, "wb") as handle:
        writer.write(handle)
    return output
