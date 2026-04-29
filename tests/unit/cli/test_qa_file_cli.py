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
