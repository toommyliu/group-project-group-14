from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from opengrader.cli import smart_submission_splitter
from opengrader.core.submissions.distribution import PageEvidence
from opengrader.tui.scan_review import ScanReviewApp


def _write_blank_pdf(path: Path, page_count: int) -> None:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    with path.open("wb") as file:
        writer.write(file)


def _evidence(*logical_pages: int | None) -> list[PageEvidence]:
    pages: list[PageEvidence] = []
    for index, logical_page in enumerate(logical_pages, start=1):
        pages.append(
            PageEvidence(
                physical_page=index,
                logical_page=logical_page,
                logical_page_confidence=100.0 if logical_page is not None else 0.0,
                is_first_page=(logical_page == 1),
                first_page_confidence=100.0 if logical_page == 1 else 0.0,
                evidence_reasons=(),
            )
        )
    return pages


def _run_cli(monkeypatch: pytest.MonkeyPatch, *args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["smart-submission-splitter", *args])
    smart_submission_splitter.run_cli()


def test_smart_submission_splitter_dry_run_writes_plan_but_no_pdfs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_pdf = tmp_path / "submissions.pdf"
    output_dir = tmp_path / "split"
    report_path = tmp_path / "plan.json"
    _write_blank_pdf(input_pdf, 3)

    def _fake_collect(*_args: object, **_kwargs: object) -> list[PageEvidence]:
        return _evidence(1, None, 3)

    monkeypatch.setattr(
        smart_submission_splitter,
        "collect_page_evidence_from_pdf",
        _fake_collect,
    )

    _run_cli(
        monkeypatch,
        "--input",
        str(input_pdf),
        "--output",
        str(output_dir),
        "--pages-per-submission",
        "3",
        "--report-json",
        str(report_path),
        "--dry-run",
    )

    assert report_path.exists()
    assert not list(output_dir.glob("*.pdf"))
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["ranges"][0]["logical_pages"] == [1, 2, 3]


def test_smart_submission_splitter_writes_split_pdfs_and_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_pdf = tmp_path / "submissions.pdf"
    output_dir = tmp_path / "split"
    report_path = tmp_path / "plan.json"
    _write_blank_pdf(input_pdf, 4)

    def _fake_collect(*_args: object, **_kwargs: object) -> list[PageEvidence]:
        return _evidence(1, 2, 1, 2)

    monkeypatch.setattr(
        smart_submission_splitter,
        "collect_page_evidence_from_pdf",
        _fake_collect,
    )

    _run_cli(
        monkeypatch,
        "--input",
        str(input_pdf),
        "--output",
        str(output_dir),
        "--pages-per-submission",
        "2",
        "--report-json",
        str(report_path),
    )

    first = output_dir / "submission-0001.pdf"
    second = output_dir / "submission-0002.pdf"
    assert first.exists()
    assert second.exists()
    assert len(PdfReader(str(first)).pages) == 2
    assert len(PdfReader(str(second)).pages) == 2

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert [page_range["name"] for page_range in payload["ranges"]] == [
        "submission-0001",
        "submission-0002",
    ]


def test_smart_submission_splitter_review_flag_opens_review_tui(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    input_pdf = tmp_path / "submissions.pdf"
    output_dir = tmp_path / "split"
    _write_blank_pdf(input_pdf, 2)

    def _fake_collect(*_args: object, **_kwargs: object) -> list[PageEvidence]:
        return _evidence(1, 2)

    review_calls: list[tuple[Path, int]] = []

    def _fake_run(self: ScanReviewApp) -> None:
        review_calls.append((self.input_pdf, len(self.plan.ranges)))

    monkeypatch.setattr(
        smart_submission_splitter,
        "collect_page_evidence_from_pdf",
        _fake_collect,
    )
    monkeypatch.setattr(ScanReviewApp, "run", _fake_run)

    _run_cli(
        monkeypatch,
        "--input",
        str(input_pdf),
        "--output",
        str(output_dir),
        "--pages-per-submission",
        "2",
        "--review",
        "--dry-run",
    )

    assert review_calls == [(input_pdf, 1)]
    assert not list(output_dir.glob("*.pdf"))
