"""Where a split PDF's sidecar files live.

Each per-student PDF carries small JSON sidecars (scan warnings, which
template page each scanned page matched). They sit in a ``scan-notes/``
folder beside the PDFs, so the folder a teacher opens shows only the PDFs.

Runs before this layout wrote them next to the PDF (``submission-0001.scan.json``
beside ``submission-0001.pdf``). Those are still read, and a write moves the
note into ``scan-notes/`` and removes the old copy, so a folder tidies itself
as it is reviewed.
"""

from __future__ import annotations

from pathlib import Path

SCAN_NOTES_DIRNAME = "scan-notes"
SCAN_WARNINGS_SUFFIX = ".scan.json"


def note_path(pdf: Path, suffix: str) -> Path:
    return pdf.parent / SCAN_NOTES_DIRNAME / f"{pdf.stem}{suffix}"


def read_note_path(pdf: Path, suffix: str) -> Path:
    """The note to read: in scan-notes/, else an older one beside the PDF."""
    path = note_path(pdf, suffix)
    if path.exists():
        return path
    legacy = pdf.with_suffix(suffix)
    return legacy if legacy.exists() else path


def write_note(pdf: Path, suffix: str, text: str) -> Path:
    path = note_path(pdf, suffix)
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    pdf.with_suffix(suffix).unlink(missing_ok=True)
    return path


def remove_note(pdf: Path, suffix: str) -> None:
    note_path(pdf, suffix).unlink(missing_ok=True)
    pdf.with_suffix(suffix).unlink(missing_ok=True)
