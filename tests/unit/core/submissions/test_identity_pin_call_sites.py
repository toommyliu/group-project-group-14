"""Identity pin call sites: a blocked identity read degrades to the local path, never a crash."""

from __future__ import annotations

from pathlib import Path

import fitz

from opengrader.core.ocr.vision_reader import GuardedVisionReader
from opengrader.core.submissions.reorder import reorder_pdf_pages


class _PageReader:
    def __init__(self) -> None:
        self.calls = 0

    def extract_page_number(self, *, image_path):
        self.calls += 1
        return 1


def _blank_scan(path: Path, pages: int) -> Path:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)
    doc.save(str(path))
    doc.close()
    return path


def test_reorder_survives_a_blocked_page_number_read(tmp_path) -> None:
    """reorder.py sends whole pages (the cover among them) as its last fallback.

    A cloud reader with no usable local Ollama fallback refuses that read;
    reorder must carry on with no number for the page, and the cloud service
    must never see the page.
    """
    inner = _PageReader()
    guard = GuardedVisionReader(
        inner=inner, provider="c", host="https://c.example", cloud=True
    )

    result = reorder_pdf_pages(_blank_scan(tmp_path / "s.pdf", 2), vision_reader=guard)

    assert inner.calls == 0
    assert result.changed is False
