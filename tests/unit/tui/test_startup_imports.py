import subprocess
import sys


def test_tui_startup_does_not_import_heavy_libraries():
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
