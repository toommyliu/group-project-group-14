from collections.abc import Iterable
from pathlib import Path

import fitz

from opengrader.core.pages.features import PageFeatures, align, page_features
from opengrader.core.pdf_utils import render_page_to_image

MATCH_DPI = 100


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


def version_scores(
    scan: Path, pages: Iterable[int], references: dict[str, list[PageFeatures]]
) -> dict[int, dict[str, int]]:
    with fitz.open(scan) as document:
        scanned = _features(document, pages)
    return {
        number: {
            version: max(
                (((alignment := align(page, template)) and alignment.inliers) or 0 for template in templates),
                default=0,
            )
            for version, templates in references.items()
        }
        for number, page in scanned.items()
    }
