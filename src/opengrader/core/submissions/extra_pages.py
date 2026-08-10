"""The splitter→extractor hand-off for extra student-work pages.

``submission-splitter --extra-work-pages`` already knows something no later
stage can re-derive exactly: which pages of a student's scan could **not** be
matched to a template page, i.e. the scratch/overflow sheets it pushed to the
end. That knowledge used to die with the process — the split PDF looked no
different from any other, so by grading time the pages were invisible.

This module is the small file that carries it forward: a JSON map from the
split PDF's **stem** (what decides the extractor's output folder, so moving the
directory doesn't invalidate it) to the 1-based page numbers of its extra work
pages, written into ``<split-dir>/.opengrader/extra-pages.json`` — the same
dot-dir convention as ``policy.json`` / ``curve.json`` / the resume ledger, so
it travels with the folder and stays out of submission discovery.

Absence is meaningful and distinct from emptiness:

* **no file / no entry** (``read_extra_pages_for`` returns ``None``) — the
  splitter never looked. The extractor falls back to inferring extra pages from
  page coverage (``page_context.infer_extra_pages``).
* **an empty list** (``()``) — the splitter looked and this student had none.
  Authoritative; no inference.

A malformed or unreadable file degrades to "never looked" rather than raising:
losing this hint costs a fallback, never a failed extraction.
"""

from __future__ import annotations

import json
from pathlib import Path

OPENGRADER_DIR = ".opengrader"
EXTRA_PAGES_FILENAME = "extra-pages.json"


def resolve_extra_pages_path(directory: str | Path) -> Path:
    """Path of the extra-work-pages map for a directory of split PDFs."""
    return Path(directory) / OPENGRADER_DIR / EXTRA_PAGES_FILENAME


def write_extra_pages_map(
    directory: str | Path,
    pages_by_stem: dict[str, tuple[int, ...] | list[int]],
) -> Path:
    """Write the stem → extra-page-numbers map, creating the dot-dir."""
    path = resolve_extra_pages_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        stem: sorted({int(page) for page in pages if int(page) >= 1})
        for stem, pages in pages_by_stem.items()
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def read_extra_pages_map(directory: str | Path) -> dict[str, tuple[int, ...]]:
    """Read the stem → extra-page-numbers map; ``{}`` when absent or malformed."""
    path = resolve_extra_pages_path(directory)
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}

    result: dict[str, tuple[int, ...]] = {}
    for stem, pages in raw.items():
        if not isinstance(stem, str) or not isinstance(pages, list):
            continue
        cleaned = sorted(
            {int(page) for page in pages if isinstance(page, int) and page >= 1}
        )
        result[stem] = tuple(cleaned)
    return result


def read_extra_pages_for(pdf_path: str | Path) -> tuple[int, ...] | None:
    """Extra work pages recorded for one split PDF, or ``None`` if unrecorded.

    ``None`` means "the splitter never recorded anything for this file" and is
    the caller's cue to infer; ``()`` means "recorded: this student has none".
    """
    path = Path(pdf_path)
    return read_extra_pages_map(path.parent).get(path.stem)
