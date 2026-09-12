from __future__ import annotations

import json
from pathlib import Path

OPENGRADER_DIR = ".opengrader"
EXTRA_PAGES_FILENAME = "extra-pages.json"


def resolve_extra_pages_path(directory: str | Path) -> Path:
    return Path(directory) / OPENGRADER_DIR / EXTRA_PAGES_FILENAME


def write_extra_pages_map(
    directory: str | Path,
    pages_by_stem: dict[str, tuple[int, ...] | list[int]],
) -> Path:
    path = resolve_extra_pages_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        stem: sorted({int(page) for page in pages if int(page) >= 1})
        for stem, pages in pages_by_stem.items()
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def read_extra_pages_map(directory: str | Path) -> dict[str, tuple[int, ...]]:
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
    """None means unrecorded; an empty list means checked with no extra pages."""
    path = Path(pdf_path)
    return read_extra_pages_map(path.parent).get(path.stem)
