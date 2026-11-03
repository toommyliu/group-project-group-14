from pathlib import Path

import fitz
import pytest
from pypdf import PdfReader

from opengrader.cli import run_command
from opengrader.cli import scan_check as cli
from opengrader.core.submissions.scan_check import OrientationUnavailableError


def _pdf(path: Path, pages: int = 2) -> Path:
    doc = fitz.open()
    body = ("The quick brown fox jumps over the lazy dog. " * 3 + "\n") * 12
    for _index in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(fitz.Rect(60, 80, 552, 700), body, fontsize=12)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def upside_down(monkeypatch):
    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation",
        lambda _image: (180, 12.0),
    )


@pytest.fixture
def upright(monkeypatch):
    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation",
        lambda _image: (0, 12.0),
    )


def test_a_clean_scan_reports_no_problems(tmp_path, upright, capsys) -> None:
    run_command(cli.command, ["--input", str(_pdf(tmp_path / "scan.pdf"))])
    assert "no problems found" in capsys.readouterr().out


def test_rotated_pages_are_reported_with_the_fix_hint(
    tmp_path, upside_down, capsys
) -> None:
    run_command(cli.command, ["--input", str(_pdf(tmp_path / "scan.pdf", pages=2))])
    out = capsys.readouterr().out
    assert "HIGH" in out
    assert "rotated 180" in out
    assert "--fix" in out


def test_fix_writes_an_upright_copy_beside_the_original(
    tmp_path, upside_down, capsys
) -> None:
    source = _pdf(tmp_path / "scan.pdf", pages=3)
    before = source.read_bytes()

    run_command(cli.command, ["--input", str(source), "--fix"])

    corrected = tmp_path / "scan-upright.pdf"
    assert corrected.exists()
    assert [page.rotation for page in PdfReader(str(corrected)).pages] == [180] * 3
    assert source.read_bytes() == before
    assert "Rotated 3 page(s)" in capsys.readouterr().out


def test_fix_writes_nothing_when_there_is_nothing_to_turn(
    tmp_path, upright, capsys
) -> None:
    run_command(cli.command, ["--input", str(_pdf(tmp_path / "scan.pdf")), "--fix"])
    assert not (tmp_path / "scan-upright.pdf").exists()
    assert "Nothing to rotate" in capsys.readouterr().out


def test_fix_across_a_directory_writes_into_the_output_directory(
    tmp_path, upside_down
) -> None:
    scans = tmp_path / "split"
    scans.mkdir()
    _pdf(scans / "submission-0001.pdf")
    _pdf(scans / "submission-0002.pdf")
    out = tmp_path / "upright"

    run_command(cli.command, ["--input", str(scans), "--fix", "--output", str(out)])

    assert sorted(path.name for path in out.glob("*.pdf")) == [
        "submission-0001.pdf",
        "submission-0002.pdf",
    ]


def test_fix_refuses_to_overwrite_the_input(tmp_path, upside_down) -> None:
    source = _pdf(tmp_path / "scan.pdf")
    with pytest.raises(SystemExit) as exit_info:
        run_command(cli.command, ["--input", str(source), "--fix", "--output", str(source)])
    assert exit_info.value.code == 1


def test_page_count_mismatch_is_reported_for_a_bundle(
    tmp_path, upright, capsys
) -> None:
    run_command(
        cli.command,
        [
            "--input", str(_pdf(tmp_path / "scan.pdf", pages=5)),
            "--pages-per-submission", "2",
        ]
    )
    assert "not a whole number" in capsys.readouterr().out


def test_a_split_directory_is_measured_against_the_template(
    tmp_path, upright, capsys
) -> None:
    scans = tmp_path / "split"
    scans.mkdir()
    _pdf(scans / "submission-0001.pdf", pages=3)
    template = _pdf(tmp_path / "blank.pdf", pages=4)

    run_command(cli.command, ["--input", str(scans), "--blank-template", str(template)])

    out = capsys.readouterr().out
    assert "3 pages, but a submission should have 4" in out


def test_pages_per_submission_means_exact_length_for_a_directory(
    tmp_path, upright, capsys
) -> None:
    scans = tmp_path / "split"
    scans.mkdir()
    _pdf(scans / "submission-0001.pdf", pages=3)

    run_command(cli.command, ["--input", str(scans), "--pages-per-submission", "4"])

    assert "3 pages, but a submission should have 4" in capsys.readouterr().out


def test_an_unavailable_orientation_check_is_stated_not_hidden(
    tmp_path, monkeypatch, capsys
) -> None:
    def _unavailable(_image):
        raise OrientationUnavailableError("osd.traineddata not found")

    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation", _unavailable
    )
    run_command(cli.command, ["--input", str(_pdf(tmp_path / "scan.pdf"))])

    out = capsys.readouterr().out
    assert "orientation could not be checked" in out
    assert "no problems found" in out


def test_unverified_pages_are_listed(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation",
        lambda _image: (_ for _ in ()).throw(ValueError("Too few characters")),
    )
    run_command(cli.command, ["--input", str(_pdf(tmp_path / "scan.pdf", pages=2))])
    assert "orientation unverified on page(s) 1, 2" in capsys.readouterr().out


def test_no_orientation_skips_the_slow_pass(tmp_path, monkeypatch, capsys) -> None:
    def _boom(_image):  # pragma: no cover
        raise AssertionError("orientation should not have been checked")

    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation", _boom
    )
    run_command(cli.command, ["--input", str(_pdf(tmp_path / "scan.pdf")), "--no-orientation"])
    assert "orientation check skipped" in capsys.readouterr().out


def _numbered_pdf(path: Path, numbers) -> Path:
    doc = fitz.open()
    body = ("The quick brown fox jumps over the lazy dog. " * 3 + "\n") * 10
    for number in numbers:
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(fitz.Rect(60, 80, 552, 640), body, fontsize=12)
        page.insert_text((290, 740), f"Page {number}")
    doc.save(str(path))
    doc.close()
    return path


def test_out_of_order_pages_are_reported_with_their_remedy(
    tmp_path, upright, capsys
) -> None:
    run_command(
        cli.command,
        [
            "--input", str(_numbered_pdf(tmp_path / "scan.pdf", [1, 3, 2])),
            "--pages-per-submission", "3",
        ]
    )
    out = capsys.readouterr().out
    assert "out of order" in out
    assert "--split every-n" in out


def test_page_order_says_when_it_could_not_run(tmp_path, upright, capsys) -> None:
    run_command(cli.command, ["--input", str(_numbered_pdf(tmp_path / "scan.pdf", [1, 3, 2]))])
    out = capsys.readouterr().out
    assert "page order not checked" in out
    assert "--pages-per-submission" in out


def test_a_split_directory_checks_order_against_each_file(
    tmp_path, upright, capsys
) -> None:
    split = tmp_path / "split"
    split.mkdir()
    _numbered_pdf(split / "submission-0001.pdf", [1, 2, 3])
    _numbered_pdf(split / "submission-0002.pdf", [1, 3, 2])

    run_command(cli.command, ["--input", str(split)])
    out = capsys.readouterr().out
    assert "submission-0002.pdf" in out
    assert "out of order" in out


def test_no_page_order_skips_the_check(tmp_path, upright, capsys) -> None:
    run_command(
        cli.command,
        [
            "--input", str(_numbered_pdf(tmp_path / "scan.pdf", [1, 3, 2])),
            "--pages-per-submission", "3",
            "--no-page-order",
        ]
    )
    out = capsys.readouterr().out
    assert "page order check skipped" in out
    assert "out of order" not in out


def test_json_output_carries_the_page_order_verdict(
    tmp_path, upright, capsys
) -> None:
    import json

    run_command(
        cli.command,
        [
            "--input", str(_numbered_pdf(tmp_path / "scan.pdf", [1, 3, 2])),
            "--pages-per-submission", "3",
            "--json",
        ]
    )
    order = json.loads(capsys.readouterr().out)["reports"][0]["page_order"]

    assert order["checked"] is True
    assert order["evidence"] == "exact"
    assert order["detected_page_numbers"] == [1, 3, 2]
    assert order["fix_is_safe"] is True
    assert order["out_of_order"] == [
        {
            "first_page": 1,
            "last_page": 3,
            "break_page": 3,
            "page_numbers": [1, 3, 2],
        }
    ]


def test_json_output_carries_the_rotations_and_issues(
    tmp_path, upside_down, capsys
) -> None:
    import json

    run_command(cli.command, ["--input", str(_pdf(tmp_path / "scan.pdf", pages=2)), "--json"])
    payload = json.loads(capsys.readouterr().out)

    report = payload["reports"][0]
    assert report["page_count"] == 2
    assert report["rotations"] == {"1": 180, "2": 180}
    assert {issue["kind"] for issue in report["issues"]} == {"rotated_page"}
    assert all(issue["fixable"] for issue in report["issues"])


def test_fail_on_high_exits_non_zero(tmp_path, upside_down) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_command(
            cli.command,
            ["--input", str(_pdf(tmp_path / "scan.pdf")), "--fail-on-high"]
        )
    assert exit_info.value.code == 2


def test_fail_on_high_is_quiet_on_a_clean_scan(tmp_path, upright) -> None:
    run_command(cli.command, ["--input", str(_pdf(tmp_path / "scan.pdf")), "--fail-on-high"])


def test_a_missing_input_is_an_error(tmp_path) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_command(cli.command, ["--input", str(tmp_path / "nope.pdf")])
    assert exit_info.value.code == 1


def test_a_non_pdf_input_is_an_error(tmp_path) -> None:
    notes = tmp_path / "notes.txt"
    notes.write_text("not a scan")
    with pytest.raises(SystemExit) as exit_info:
        run_command(cli.command, ["--input", str(notes)])
    assert exit_info.value.code == 1


def test_a_directory_with_no_pdfs_is_an_error(tmp_path) -> None:
    (tmp_path / "empty").mkdir()
    with pytest.raises(SystemExit) as exit_info:
        run_command(cli.command, ["--input", str(tmp_path / "empty")])
    assert exit_info.value.code == 1
