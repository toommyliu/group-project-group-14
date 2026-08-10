"""Lock in the lazy-import startup-latency fix.

``matplotlib``, ``cv2`` (OpenCV), and ``fitz`` (PyMuPDF) are heavy imports —
each costs tens to well over a hundred milliseconds cold — and an instructor
pays that cost on *every* TUI launch, whether or not the session ever touches
a chart, OCR, or PDF rendering. core/ocr, core/pdf, and tui/data modules defer
these imports to first use (see e.g. ``core/pdf/pdf_preview.py`` and
``core/ocr/extractor.py``) specifically so they never load during startup.

This test guards that property. It must run in a **fresh interpreter**
(``subprocess``) rather than importing directly, because by the time this
test file itself runs, pytest and earlier tests will already have imported
half of these libraries into the current process's ``sys.modules`` — that
would make the assertion pass even if a future change reintroduced a
top-level ``import cv2`` deep in the TUI's import chain.
"""

import subprocess
import sys


def test_tui_startup_does_not_import_heavy_libraries():
    """Importing the TUI entry module must not pull in cv2, fitz, or matplotlib."""

    probe = (
        "import opengrader.tui.main, sys; "
        "print('cv2' in sys.modules); "
        "print('fitz' in sys.modules); "
        "print('matplotlib' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr

    cv2_loaded, fitz_loaded, matplotlib_loaded = (
        line.strip() == "True" for line in result.stdout.splitlines()
    )
    assert not cv2_loaded, "cv2 (OpenCV) loaded during TUI startup — a lazy import regressed"
    assert not fitz_loaded, "fitz (PyMuPDF) loaded during TUI startup — a lazy import regressed"
    assert not matplotlib_loaded, "matplotlib loaded during TUI startup — a lazy import regressed"
