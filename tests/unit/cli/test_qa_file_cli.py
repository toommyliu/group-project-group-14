from __future__ import annotations

import sys
from pathlib import Path

import fitz
import pytest

from opengrader.cli.qa_file import run_cli

REPO_ROOT = Path(__file__).resolve().parents[3]
SAMPLES_DIR = REPO_ROOT / "samples"


def _run_cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["qa-file", *args])
    run_cli()


def _extract_pdf_text(path: Path) -> str:
    with fitz.open(path) as document:
        return "\n".join(page.get_text() for page in document)


@pytest.mark.parametrize(
    "sample_name",
    ["qa-single.md", "qa-multi.md", "qa-multiple-choice.md"],
)
def test_validate_accepts_sample_qa_files(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    sample_name: str,
) -> None:
    sample_path = SAMPLES_DIR / sample_name

    _run_cli(monkeypatch, "validate", "--qa-file", str(sample_path))

    assert f"QA file is valid: {sample_path}" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("sample_name", "output_name", "expected_text"),
    [
        (
            "qa-single.md",
            "qa-single.pdf",
            ["Quiz 1: Shell/Linux Commands", "What is the capital of France?"],
        ),
        (
            "qa-multiple-choice.md",
            "qa-multiple-choice.pdf",
            ["Which data structure uses FIFO ordering?", "A)", "Queue"],
        ),
    ],
)
def test_generate_template_accepts_single_version_sample_qa_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    sample_name: str,
    output_name: str,
    expected_text: list[str],
) -> None:
    output_path = tmp_path / output_name

    _run_cli(
        monkeypatch,
        "generate-template",
        "--qa-file",
        str(SAMPLES_DIR / sample_name),
        "--output-pdf",
        str(output_path),
    )

    assert output_path.stat().st_size > 0
    extracted_text = _extract_pdf_text(output_path)
    for text in expected_text:
        assert text in extracted_text


def test_generate_template_accepts_all_versions_sample_qa_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "qa-multi.pdf"

    _run_cli(
        monkeypatch,
        "generate-template",
        "--qa-file",
        str(SAMPLES_DIR / "qa-multi.md"),
        "--output-pdf",
        str(output_path),
        "--all-versions",
    )

    expected_version_text = {
        "A": "Convert decimal 5 to binary [A].",
        "B": "Convert decimal 6 to binary [B].",
        "C": "Convert decimal 9 to binary [C].",
    }
    for version, expected_text in expected_version_text.items():
        versioned_output = tmp_path / f"qa-multi-v{version}.pdf"
        assert versioned_output.stat().st_size > 0
        assert expected_text in _extract_pdf_text(versioned_output)


def test_generate_bubble_sheet_writes_a_sheet_and_names_what_it_skipped(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    # The sample has one free-response subquestion, which no row of circles can
    # capture. Saying so *before* the success line is the point: an instructor
    # who discovers it after the exam has lost the answers.
    output_path = tmp_path / "sheet.pdf"

    _run_cli(
        monkeypatch,
        "generate-template",
        "--qa-file",
        str(SAMPLES_DIR / "qa-multiple-choice.md"),
        "--output-pdf",
        str(output_path),
        "--bubble-sheet",
    )

    out = capsys.readouterr().out
    assert "not multiple choice and were left off the sheet: q2b" in out
    assert "Wrote bubble sheet (3 questions" in out
    assert output_path.stat().st_size > 0


def test_generate_bubble_sheet_refuses_a_qa_file_with_no_multiple_choice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="no multiple-choice questions"):
        _run_cli(
            monkeypatch,
            "generate-template",
            "--qa-file",
            str(SAMPLES_DIR / "qa-single.md"),
            "--output-pdf",
            str(tmp_path / "sheet.pdf"),
            "--bubble-sheet",
        )


def test_generate_bubble_sheet_with_a_roster_makes_one_sheet_per_student(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    roster = tmp_path / "roster.tsv"
    roster.write_text("100001\tAda Lovelace\n100002\tAlan Turing\n")
    output_path = tmp_path / "batch.pdf"

    _run_cli(
        monkeypatch,
        "generate-template",
        "--qa-file",
        str(SAMPLES_DIR / "qa-multiple-choice.md"),
        "--output-pdf",
        str(output_path),
        "--bubble-sheet",
        "--roster",
        str(roster),
    )

    assert "bubble batch (2 students, master key on page 1" in capsys.readouterr().out
    with fitz.open(output_path) as document:
        assert len(document) == 3  # master key + one sheet each
        # The sheets print the id, never the name — that is what makes a
        # personalized stack safe to hand out.
        sheet_text = document[1].get_text() + document[2].get_text()
    assert "Ada Lovelace" not in sheet_text


def test_copies_prints_that_many_sheets(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "copies.pdf"
    _run_cli(
        monkeypatch,
        "generate-template",
        "--qa-file", str(SAMPLES_DIR / "qa-multiple-choice.md"),
        "--output-pdf", str(output_path),
        "--bubble-sheet",
        "--copies", "4",
    )
    assert "4 copies" in capsys.readouterr().out
    with fitz.open(output_path) as document:
        # No roster, so no master key — four sheets and nothing else.
        assert len(document) == 4


def test_shuffle_gives_each_sheet_its_own_order_and_form_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "scrambled.pdf"
    _run_cli(
        monkeypatch,
        "generate-template",
        "--qa-file", str(SAMPLES_DIR / "qa-multiple-choice.md"),
        "--output-pdf", str(output_path),
        "--bubble-sheet",
        "--copies", "6",
        "--shuffle",
    )
    out = capsys.readouterr().out
    assert "distinct question orders" in out
    # The form code must be printed, not only encoded: a QR that will not scan
    # would otherwise make the sheet ungradable.
    assert "form key" in out
    with fitz.open(output_path) as document:
        forms = {
            line.split("Form ")[1].split()[0]
            for page in document
            for line in page.get_text().splitlines()
            if "Form " in line
        }
    assert len(forms) == 6


def test_shuffle_without_a_batch_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="--roster or --copies"):
        _run_cli(
            monkeypatch,
            "generate-template",
            "--qa-file", str(SAMPLES_DIR / "qa-multiple-choice.md"),
            "--output-pdf", str(tmp_path / "x.pdf"),
            "--bubble-sheet",
            "--shuffle",
        )


def test_a_roster_and_copies_together_are_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    roster = tmp_path / "roster.tsv"
    roster.write_text("1\tA\n")
    with pytest.raises(ValueError, match="one or the other"):
        _run_cli(
            monkeypatch,
            "generate-template",
            "--qa-file", str(SAMPLES_DIR / "qa-multiple-choice.md"),
            "--output-pdf", str(tmp_path / "x.pdf"),
            "--bubble-sheet",
            "--roster", str(roster),
            "--copies", "5",
        )


def test_shuffling_a_written_template_is_refused_with_the_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A written template is read back by aligning scans to one blank layout,
    so a per-sheet order could never be recovered — refuse rather than print an
    exam that cannot be graded."""
    with pytest.raises(ValueError, match="needs --bubble-sheet"):
        _run_cli(
            monkeypatch,
            "generate-template",
            "--qa-file", str(SAMPLES_DIR / "qa-single.md"),
            "--output-pdf", str(tmp_path / "x.pdf"),
            "--shuffle",
            "--copies", "3",
        )
