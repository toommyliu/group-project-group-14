from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import PageCatalog, PageMatch

SIDECAR_SUFFIX = ".pages.json"


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

    def page_numbers(self, catalog: PageCatalog) -> list[int | None]:
        return [catalog.groups[m.group].page_number if m.group else None for m in self.pages]


def plan_submissions(
    catalog: PageCatalog, *, prefix: str = "submission", pad: int = 4, expected_count: int | None = None,
) -> list[PlannedSubmission]:
    runs = _segments(catalog)
    expected = catalog.expected_pages
    planned: list[PlannedSubmission] = []
    for index, run in enumerate(runs, start=1):
        submission = PlannedSubmission(f"{prefix}-{index:0{pad}d}")
        numbered = [(number_of(catalog, m), position, m) for position, m in enumerate(run)]
        in_order = sorted(numbered, key=lambda item: (item[0] is None, item[0] or 0, item[1]))
        submission.pages = [m for _, _, m in in_order]
        numbers = [n for n, _, _ in numbered if n is not None]
        if numbers != sorted(numbers):
            submission.warnings.append("Pages were out of order in the scan; they have been put back in page order.")
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
            submission.warnings.append(f"{unknown} page(s) did not match any printed page (blank, extra work, or another document); "
                                       "they are kept at the end.")
        planned.append(submission)
    return planned


def number_of(catalog: PageCatalog, match: PageMatch) -> int | None:
    return catalog.groups[match.group].page_number if match.group else None


def _expected_for(catalog: PageCatalog, run: list[PageMatch], expected: list[int]) -> list[int]:
    counts = [catalog.groups[m.group].layout.page_count for m in run if m.group and catalog.groups[m.group].layout.page_count]
    if counts:
        return list(range(1, max(counts) + 1))
    return expected


def _segments(catalog: PageCatalog) -> list[list[PageMatch]]:
    matches = catalog.matches
    expected = catalog.expected_pages
    longest = 2 * max(1, len(expected)) + 2
    best = [0.0] + [float("inf")] * len(matches)
    cut = [0] * (len(matches) + 1)
    for end in range(1, len(matches) + 1):
        for start in range(max(0, end - longest), end):
            cost = _submission_cost([number_of(catalog, m) for m in matches[start:end]], expected)
            if best[start] + cost < best[end]:
                best[end], cut[end] = best[start] + cost, start
    runs, end = [], len(matches)
    while end > 0:
        runs.append(matches[cut[end]:end])
        end = cut[end]
    return runs[::-1]


def _submission_cost(numbers: list[int | None], expected: list[int]) -> float:
    known = [n for n in numbers if n is not None]
    if len(known) != len(set(known)):
        return float("inf")
    if not known:
        return 5.0
    missing = len([n for n in expected if n not in known])
    inversions = sum(1 for i, a in enumerate(known) for b in known[i + 1:] if a > b)
    return 1.0 + missing + 0.25 * inversions


def write_sidecar(pdf_path: Path, submission: PlannedSubmission, catalog: PageCatalog) -> Path:
    path = pdf_path.with_suffix(SIDECAR_SUFFIX)
    path.write_text(json.dumps({
        "pages": [{"group": m.group, "page_number": number_of(catalog, m)} for m in submission.pages],
    }, indent=1) + "\n")
    return path


def read_sidecar(pdf_path: Path) -> dict | None:
    path = pdf_path.with_suffix(SIDECAR_SUFFIX)
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
