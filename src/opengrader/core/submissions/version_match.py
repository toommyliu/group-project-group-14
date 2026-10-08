from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import fitz

from opengrader.core.pages.features import PageFeatures, align, page_features
from opengrader.core.pdf_utils import render_page_to_image

MATCH_DPI = 100
MIN_SCORE = 25
MIN_LEAD = 1.4

TemplateScores = dict[int, dict[str, list[int]]]


def _features(document: fitz.Document, numbers: Iterable[int]) -> dict[int, PageFeatures]:
    return {
        number: page_features(
            render_page_to_image(document, number - 1, dpi=MATCH_DPI, cache=False)[0]
        )
        for number in numbers
    }


def template_features(templates: dict[str, Path]) -> dict[str, list[PageFeatures]]:
    references = {}
    for version, path in templates.items():
        with fitz.open(path) as document:
            references[version] = list(_features(document, range(1, len(document) + 1)).values())
    return references


def score_pages(
    scan: Path, pages: Iterable[int], references: dict[str, list[PageFeatures]]
) -> TemplateScores:
    with fitz.open(scan) as document:
        scanned = _features(document, pages)
    return {
        number: {
            version: [((alignment := align(page, template)) and alignment.inliers) or 0 for template in templates]
            for version, templates in references.items()
        }
        for number, page in scanned.items()
    }


def _leader[K: Hashable](totals: dict[K, int]) -> K | None:
    ranked = sorted(totals.items(), key=lambda item: -item[1])
    if not ranked:
        return None
    best, score = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0
    return best if score >= MIN_SCORE and score >= MIN_LEAD * second else None


def group_version(pages: list[int], scores: TemplateScores) -> str | None:
    totals: dict[str, int] = {}
    for page in pages:
        for version, by_template_page in scores.get(page, {}).items():
            totals[version] = totals.get(version, 0) + max(by_template_page, default=0)
    return _leader(totals)


def _page_version(page: int, scores: TemplateScores) -> str | None:
    return _leader({version: max(by_page, default=0) for version, by_page in scores.get(page, {}).items()})


def _printed_page(page: int, version: str, scores: TemplateScores) -> int | None:
    return _leader(dict(enumerate(scores.get(page, {}).get(version, []), start=1)))


def _pages(numbers: list[int]) -> str:
    return ", ".join(map(str, numbers[:-1])) + f" and {numbers[-1]}"


@dataclass
class GroupCheck:
    pages: list[int]
    version: str | None = None
    warnings: list[str] = field(default_factory=list)


def check_group(pages: list[int], scores: TemplateScores, *, reorder: bool) -> GroupCheck:
    version = group_version(pages, scores)
    if version is None:
        versions = {page: other for page in pages if (other := _page_version(page, scores))}
        if len(set(versions.values())) < 2:
            return GroupCheck(pages)
        described = ", ".join(f"scan page {page} is Version {other}" for page, other in versions.items())
        return GroupCheck(pages, warnings=[f"Pages come from different versions: {described}."])
    check = GroupCheck(pages, version)
    label = f"Version {version}"
    printed: dict[int, int] = {}
    for page in pages:
        other = _page_version(page, scores)
        if other not in (None, version):
            check.warnings.append(f"Scan page {page} looks like Version {other}, not {label}.")
        elif (number := _printed_page(page, version, scores)) is not None:
            printed[page] = number
    template_pages = max(len(scores.get(page, {}).get(version, [])) for page in pages)
    for number in range(1, template_pages + 1):
        copies = [page for page, printed_number in printed.items() if printed_number == number]
        if len(copies) > 1:
            both = "both" if len(copies) == 2 else "all"
            check.warnings.append(f"Scan pages {_pages(copies)} {both} look like page {number} of {label}.")
        elif not copies:
            check.warnings.append(f"No scan page looks like page {number} of {label}.")
    numbers = [printed[page] for page in pages if page in printed]
    if numbers != sorted(numbers):
        if reorder and not check.warnings:
            check.pages = sorted(pages, key=lambda page: printed.get(page, template_pages + 1))
            check.warnings.append(f"Pages were reordered to match {label}.")
        else:
            check.warnings.append(f"Scan pages are out of order for {label}.")
    return check
