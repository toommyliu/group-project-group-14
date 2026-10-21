from __future__ import annotations

from opengrader.core.submissions.scan_notes import (
    SCAN_NOTES_DIRNAME,
    SCAN_WARNINGS_SUFFIX,
    note_path,
    read_note_path,
    remove_note,
    write_note,
)
from opengrader.core.submissions.splitter import read_scan_warnings, write_scan_warnings


def test_notes_sit_in_their_own_folder_beside_the_pdfs(tmp_path) -> None:
    pdf = tmp_path / "submission-0001.pdf"
    write_scan_warnings(pdf, ("Missing page(s) 4.",))

    assert (tmp_path / SCAN_NOTES_DIRNAME / "submission-0001.scan.json").exists()
    assert not pdf.with_suffix(SCAN_WARNINGS_SUFFIX).exists()
    assert read_scan_warnings(pdf) == ("Missing page(s) 4.",)


def test_a_note_written_beside_the_pdf_is_still_read_and_moves_on_the_next_write(tmp_path) -> None:
    pdf = tmp_path / "submission-0001.pdf"
    legacy = pdf.with_suffix(SCAN_WARNINGS_SUFFIX)
    legacy.write_text('{"warnings": ["Missing page(s) 4."]}\n')

    assert read_note_path(pdf, SCAN_WARNINGS_SUFFIX) == legacy
    assert read_scan_warnings(pdf) == ("Missing page(s) 4.",)

    write_note(pdf, SCAN_WARNINGS_SUFFIX, '{"warnings": []}\n')
    assert not legacy.exists()
    assert read_note_path(pdf, SCAN_WARNINGS_SUFFIX) == note_path(pdf, SCAN_WARNINGS_SUFFIX)
    assert read_scan_warnings(pdf) == ()


def test_removing_a_note_clears_both_places(tmp_path) -> None:
    pdf = tmp_path / "submission-0001.pdf"
    write_note(pdf, SCAN_WARNINGS_SUFFIX, "{}\n")
    pdf.with_suffix(SCAN_WARNINGS_SUFFIX).write_text("{}\n")

    remove_note(pdf, SCAN_WARNINGS_SUFFIX)
    assert not note_path(pdf, SCAN_WARNINGS_SUFFIX).exists()
    assert not pdf.with_suffix(SCAN_WARNINGS_SUFFIX).exists()
