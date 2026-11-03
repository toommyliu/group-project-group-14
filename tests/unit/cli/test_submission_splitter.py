from pathlib import Path

import fitz
import pytest
from pypdf import PdfReader

from opengrader.cli import submission_splitter
from opengrader.core import app_config
from opengrader.core.submissions.extra_pages import (
    read_extra_pages_map,
    resolve_extra_pages_path,
)


def _bundle(path: Path, pages: int) -> Path:
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((280, 770), str(index % 2 + 1))
    doc.save(str(path))
    doc.close()
    return path


def _no_ollama(monkeypatch) -> None:
    monkeypatch.setattr(
        submission_splitter, "_verified_vision_reader", lambda args, emit: None
    )


def test_extra_work_pages_writes_the_sidecar(tmp_path: Path, monkeypatch) -> None:
    _no_ollama(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 4)
    out = tmp_path / "split"

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(out),
            "--pages-per-submission", "2",
            "--extra-work-pages",
        ],
        status_callback=lambda _message: None,
    )

    assert read_extra_pages_map(out) == {
        "submission-0001": (),
        "submission-0002": (),
    }


def test_no_sidecar_without_the_flag(tmp_path: Path, monkeypatch) -> None:
    _no_ollama(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 4)
    out = tmp_path / "split"

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(out),
            "--pages-per-submission", "2",
            "--reorder",
        ],
        status_callback=lambda _message: None,
    )

    assert not resolve_extra_pages_path(out).exists()


@pytest.fixture(autouse=True)
def upright(monkeypatch):
    """Every-N cuts always check the scan; read every page as upright unless a
    test says otherwise, so no test waits on Tesseract's orientation pass."""
    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation",
        lambda _image: (0, 12.0),
    )


@pytest.fixture
def upside_down(monkeypatch):
    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation",
        lambda _image: (180, 12.0),
    )


def _messages(capture: list[str]):
    return "\n".join(capture)


def test_an_every_n_cut_turns_pages_upright_before_cutting(
    tmp_path: Path, monkeypatch, upside_down
) -> None:
    _no_ollama(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 4)
    out = tmp_path / "split"

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(out),
            "--pages-per-submission", "2",
        ],
        status_callback=lambda _message: None,
    )

    for name in ("submission-0001.pdf", "submission-0002.pdf"):
        rotations = [page.rotation for page in PdfReader(str(out / name)).pages]
        assert rotations == [180, 180], f"{name} was split from an uncorrected page"


def test_the_corrected_copy_hides_from_submission_discovery(
    tmp_path: Path, monkeypatch, upside_down
) -> None:
    _no_ollama(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 4)
    out = tmp_path / "split"

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(out),
            "--pages-per-submission", "2",
        ],
        status_callback=lambda _message: None,
    )

    assert sorted(path.name for path in out.glob("*.pdf")) == [
        "submission-0001.pdf",
        "submission-0002.pdf",
    ]
    assert (out / ".opengrader" / "scans-upright.pdf").exists()


def test_the_original_scan_is_never_modified(
    tmp_path: Path, monkeypatch, upside_down
) -> None:
    _no_ollama(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 4)
    before = bundle.read_bytes()

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(tmp_path / "split"),
            "--pages-per-submission", "2",
        ],
        status_callback=lambda _message: None,
    )

    assert bundle.read_bytes() == before


def test_the_page_count_check_uses_the_declared_submission_length(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _no_ollama(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 5)

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(tmp_path / "split"),
            "--pages-per-submission", "2",
        ],
        status_callback=lambda _message: None,
    )

    assert "not a whole number" in capsys.readouterr().err


def test_dry_run_writes_no_corrected_copy(
    tmp_path: Path, monkeypatch, upside_down
) -> None:
    _no_ollama(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 4)
    out = tmp_path / "split"
    messages: list[str] = []

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(out),
            "--pages-per-submission", "2",
            "--dry-run",
        ],
        status_callback=messages.append,
    )

    assert not out.exists()
    assert "would be rotated" in _messages(messages)


def _numbered_bundle(path: Path, numbers) -> Path:
    doc = fitz.open()
    for number in numbers:
        page = doc.new_page(width=612, height=792)
        page.insert_text((280, 770), f"Page {number}")
    doc.save(str(path))
    doc.close()
    return path


def _footers(pdf: Path) -> list[str]:
    doc = fitz.open(str(pdf))
    try:
        return [
            page.get_text().strip().splitlines()[-1].strip() for page in doc
        ]
    finally:
        doc.close()


def _record_reorder_calls(monkeypatch) -> list[Path]:
    from opengrader.core.submissions.reorder import ReorderResult

    seen: list[Path] = []

    def _stub(path, **_kwargs):
        seen.append(path)
        return ReorderResult(changed=False)

    monkeypatch.setattr(submission_splitter, "reorder_pdf_pages", _stub)
    return seen


def _ocr_page_numbers(monkeypatch, numbers) -> None:
    readings = iter(numbers)
    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_number",
        lambda *args, **kwargs: (next(readings, None), "ocr"),
    )


def test_an_every_n_cut_sorts_pages_when_the_numbers_are_exact(
    tmp_path: Path, monkeypatch
) -> None:
    _no_ollama(monkeypatch)
    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 2, 3, 1, 3, 2])
    out = tmp_path / "split"

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(out),
            "--pages-per-submission", "3",
        ],
        status_callback=lambda _message: None,
    )

    assert _footers(out / "submission-0001.pdf") == ["Page 1", "Page 2", "Page 3"]
    assert _footers(out / "submission-0002.pdf") == ["Page 1", "Page 2", "Page 3"]


def test_an_every_n_cut_never_sorts_on_an_ocr_reading(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _no_ollama(monkeypatch)
    _ocr_page_numbers(monkeypatch, [1, 3, 2])
    reordered = _record_reorder_calls(monkeypatch)
    messages: list[str] = []

    submission_splitter.run_cli(
        [
            "--input", str(_numbered_bundle(tmp_path / "scans.pdf", [1, 3, 2])),
            "--output", str(tmp_path / "split"),
            "--pages-per-submission", "3",
        ],
        status_callback=messages.append,
    )

    assert reordered == []
    text = _messages(messages)
    assert "Not reordering automatically" in text
    assert "--reorder" in text
    assert "out of order" in capsys.readouterr().err


def test_a_blank_template_makes_an_ocr_reading_actionable(
    tmp_path: Path, monkeypatch
) -> None:
    _no_ollama(monkeypatch)
    _ocr_page_numbers(monkeypatch, [1, 3, 2])
    reordered = _record_reorder_calls(monkeypatch)

    submission_splitter.run_cli(
        [
            "--input", str(_numbered_bundle(tmp_path / "scans.pdf", [1, 3, 2])),
            "--output", str(tmp_path / "split"),
            "--split", "every-n",
            "--pages-per-submission", "3",
            "--blank-template", str(_numbered_bundle(tmp_path / "blank.pdf", [1, 2, 3])),
        ],
        status_callback=lambda _message: None,
    )

    assert reordered, "a template should authorize the correction"


def test_sorting_costs_nothing_on_a_scan_that_is_in_order(
    tmp_path: Path, monkeypatch
) -> None:
    _no_ollama(monkeypatch)

    def _boom(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("nothing was out of order; no reorder should run")

    monkeypatch.setattr(submission_splitter, "reorder_pdf_pages", _boom)

    submission_splitter.run_cli(
        [
            "--input", str(_numbered_bundle(tmp_path / "scans.pdf", [1, 2, 3, 1, 2, 3])),
            "--output", str(tmp_path / "split"),
            "--split", "every-n",
            "--pages-per-submission", "3",
        ],
        status_callback=lambda _message: None,
    )


def _record_vlm_calls(monkeypatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(
        submission_splitter,
        "_verified_vision_reader",
        lambda args, emit: calls.append(1) or None,
    )
    return calls


def test_sorting_reads_page_numbers_with_ai_when_ai_setup_reads_handwriting(
    tmp_path: Path, monkeypatch
) -> None:
    app_config.save_settings(app_config.AISettings(read_handwriting=True))
    calls = _record_vlm_calls(monkeypatch)
    _record_reorder_calls(monkeypatch)

    submission_splitter.run_cli(
        [
            "--input", str(_bundle(tmp_path / "scans.pdf", 4)),
            "--output", str(tmp_path / "split"),
            "--pages-per-submission", "2",
            "--reorder",
        ],
        status_callback=lambda _message: None,
    )

    assert calls == [1]


def test_no_ai_reads_page_numbers_when_ai_setup_leaves_handwriting_off(
    tmp_path: Path, monkeypatch
) -> None:
    app_config.save_settings(app_config.AISettings())
    calls = _record_vlm_calls(monkeypatch)
    _record_reorder_calls(monkeypatch)

    submission_splitter.run_cli(
        [
            "--input", str(_bundle(tmp_path / "scans.pdf", 4)),
            "--output", str(tmp_path / "split"),
            "--pages-per-submission", "2",
            "--reorder",
        ],
        status_callback=lambda _message: None,
    )

    assert calls == []


def test_no_ai_reads_page_numbers_when_nothing_is_sorted(tmp_path: Path, monkeypatch) -> None:
    app_config.save_settings(app_config.AISettings(read_handwriting=True))
    calls = _record_vlm_calls(monkeypatch)

    submission_splitter.run_cli(
        [
            "--input", str(_bundle(tmp_path / "scans.pdf", 4)),
            "--output", str(tmp_path / "split"),
            "--split", "every-n",
            "--pages-per-submission", "2",
        ],
        status_callback=lambda _message: None,
    )

    assert calls == []


def test_page_matching_runs_no_scan_check_of_its_own(tmp_path: Path, monkeypatch) -> None:
    def _boom(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("page matching turns and orders pages itself")

    monkeypatch.setattr(submission_splitter, "analyze_scan_pdf", _boom)
    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 2, 1, 2])
    _scanned_catalog(monkeypatch, bundle, [1, 2, 1, 2])

    submission_splitter.run_cli(
        ["--input", str(bundle), "--output", str(tmp_path / "split")],
        status_callback=lambda _message: None,
    )

    assert (tmp_path / "split" / "submission-0002.pdf").exists()


def test_removed_flags_are_refused(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path / "scans.pdf", 2)
    for flag in ("--fixed", "--detect-boundaries", "--scan-check", "--fix-rotation", "--fix-order",
                 "--no-scan-orientation", "--no-scan-page-order", "--llm-page-numbers"):
        with pytest.raises(SystemExit) as refused:
            submission_splitter.run_cli(
                ["--input", str(bundle), "--output", str(tmp_path / "split"), flag],
                status_callback=lambda _message: None,
            )
        assert refused.value.code == 2, flag


def _scanned_catalog(monkeypatch, bundle: Path, sequence: list[int]) -> None:
    import numpy as np

    from opengrader.core.pages import catalog as catalog_module
    from opengrader.core.pages.catalog import PageCatalog, PageGroup, PageMatch, ScanPage
    from opengrader.core.pages.layout import PageLayout

    groups = {
        f"g{n}": PageGroup(id=f"g{n}", reference=ScanPage(bundle, 0), reference_turns=0,
                           layout=PageLayout(), page_number=n)
        for n in sorted(set(sequence))
    }
    matches = [PageMatch(ScanPage(bundle, index), f"g{n}", np.eye(3), 100) for index, n in enumerate(sequence)]
    monkeypatch.setattr(submission_splitter, "_is_scan", lambda _pdf: True)
    monkeypatch.setattr(
        catalog_module, "build_catalog",
        lambda pages, directory=None, **_kwargs: PageCatalog(groups=groups, matches=matches, directory=directory),
    )


def test_a_scan_that_looks_out_of_order_is_reported_and_left_as_scanned(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from opengrader.core.submissions.splitter import read_scan_warnings

    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 3, 2, 1, 2, 3])
    _scanned_catalog(monkeypatch, bundle, [1, 3, 2, 1, 2, 3])
    out = tmp_path / "split"
    messages: list[str] = []

    submission_splitter.run_cli(
        ["--input", str(bundle), "--output", str(out)], status_callback=messages.append
    )

    assert _footers(out / "submission-0001.pdf") == ["Page 1", "Page 3", "Page 2"]
    assert _footers(out / "submission-0002.pdf") == ["Page 1", "Page 2", "Page 3"]
    assert "left as scanned" in read_scan_warnings(out / "submission-0001.pdf")[0]
    assert not read_scan_warnings(out / "submission-0002.pdf")
    assert "--reorder" in _messages(messages)
    assert "left as scanned" in capsys.readouterr().err


def test_reorder_sorts_a_scan_that_looks_out_of_order(tmp_path: Path, monkeypatch) -> None:
    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 3, 2, 1, 2, 3])
    _scanned_catalog(monkeypatch, bundle, [1, 3, 2, 1, 2, 3])
    out = tmp_path / "split"

    submission_splitter.run_cli(
        ["--input", str(bundle), "--output", str(out), "--reorder"],
        status_callback=lambda _message: None,
    )

    assert _footers(out / "submission-0001.pdf") == ["Page 1", "Page 2", "Page 3"]


def test_a_break_shared_by_the_class_is_called_a_misread_number(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 2, 3] * 3)
    _scanned_catalog(monkeypatch, bundle, [1, 8, 3] * 3)
    out = tmp_path / "split"
    messages: list[str] = []

    submission_splitter.run_cli(
        ["--input", str(bundle), "--output", str(out)], status_callback=messages.append
    )

    assert _footers(out / "submission-0002.pdf") == ["Page 1", "Page 2", "Page 3"]
    assert "3 of 3 submissions" in capsys.readouterr().err
    assert "--reorder" not in _messages(messages)


def test_extra_work_pages_alone_no_longer_sorts_a_typed_bundle(tmp_path: Path, monkeypatch) -> None:
    _no_ollama(monkeypatch)
    seen = _record_reorder_calls(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 4)

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(tmp_path / "split"),
            "--pages-per-submission", "2",
            "--extra-work-pages",
        ],
        status_callback=lambda _message: None,
    )

    assert seen == []


def test_a_scan_left_as_scanned_is_recorded_for_review_before_extraction(tmp_path: Path, monkeypatch) -> None:
    from opengrader.core.pages.order_review import pending_findings

    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 3, 2, 1, 2, 3])
    _scanned_catalog(monkeypatch, bundle, [1, 3, 2, 1, 2, 3])
    out = tmp_path / "split"

    submission_splitter.run_cli(
        ["--input", str(bundle), "--output", str(out)], status_callback=lambda _message: None
    )

    (finding,) = pending_findings(out)
    assert (finding.name, finding.keys, finding.recommended) == ("submission-0001", (1, 3, 2), False)


def test_reorder_leaves_nothing_to_review(tmp_path: Path, monkeypatch) -> None:
    from opengrader.core.pages.order_review import pending_findings

    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 3, 2, 1, 2, 3])
    _scanned_catalog(monkeypatch, bundle, [1, 3, 2, 1, 2, 3])
    out = tmp_path / "split"

    submission_splitter.run_cli(
        ["--input", str(bundle), "--output", str(out), "--reorder"], status_callback=lambda _message: None
    )

    assert pending_findings(out) == []


def test_reorder_only_sorts_the_named_submission_and_leaves_the_rest(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    from opengrader.core.pages.order_review import pending_findings

    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 3, 2, 2, 1, 3])
    _scanned_catalog(monkeypatch, bundle, [1, 3, 2, 2, 1, 3])
    out = tmp_path / "split"

    submission_splitter.run_cli(
        ["--input", str(bundle), "--output", str(out), "--reorder-only", "submission-0002,submission-0007"],
        status_callback=lambda _message: None,
    )

    assert _footers(out / "submission-0001.pdf") == ["Page 1", "Page 3", "Page 2"]
    assert _footers(out / "submission-0002.pdf") == ["Page 1", "Page 2", "Page 3"]
    assert [finding.name for finding in pending_findings(out)] == ["submission-0001"]
    assert "submission-0007" in capsys.readouterr().err


def _unreadable_catalog(monkeypatch, bundle: Path, pages: int, length: int) -> None:
    """A scan whose printed pages are told apart but carry no page number read."""
    import numpy as np

    from opengrader.core.pages import catalog as catalog_module
    from opengrader.core.pages.catalog import PageCatalog, PageGroup, PageMatch, ScanPage
    from opengrader.core.pages.layout import PageLayout

    groups = {
        f"g{n}": PageGroup(id=f"g{n}", reference=ScanPage(bundle, 0), reference_turns=0,
                           layout=PageLayout(), page_number=None)
        for n in range(length)
    }
    matches = [PageMatch(ScanPage(bundle, index), f"g{index % length}", np.eye(3), 100)
               for index in range(pages)]
    monkeypatch.setattr(submission_splitter, "_is_scan", lambda _pdf: True)
    monkeypatch.setattr(
        catalog_module, "build_catalog",
        lambda pages, directory=None, **_kwargs: PageCatalog(groups=groups, matches=matches, directory=directory),
    )


def test_a_scan_without_readable_page_numbers_is_cut_every_n_pages(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 2, 3] * 3)
    _unreadable_catalog(monkeypatch, bundle, pages=9, length=3)
    out = tmp_path / "split"
    messages: list[str] = []

    submission_splitter.run_cli(
        ["--input", str(bundle), "--output", str(out), "--pages-per-submission", "3"],
        status_callback=messages.append,
    )

    written = sorted(out.glob("*.pdf"))
    assert [len(PdfReader(str(pdf)).pages) for pdf in written] == [3, 3, 3]
    assert all(_footers(pdf) == ["Page 1", "Page 2", "Page 3"] for pdf in written)
    assert "cut every 3 pages instead" in _messages(messages)
    assert "Can't tell where each paper starts" in capsys.readouterr().err


def test_a_scan_without_readable_page_numbers_or_a_page_count_stops(
    tmp_path: Path, monkeypatch
) -> None:
    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 2, 3] * 3)
    _unreadable_catalog(monkeypatch, bundle, pages=9, length=3)
    out = tmp_path / "split"

    with pytest.raises(SystemExit) as stopped:
        submission_splitter.run_cli(
            ["--input", str(bundle), "--output", str(out)], status_callback=lambda _message: None
        )

    assert "Can't tell where each student's paper starts" in str(stopped.value.code)
    assert "--pages-per-submission" in str(stopped.value.code)
    assert not list(out.glob("*.pdf"))


def test_a_blank_template_is_the_fallback_cut_size_when_no_page_count_is_given(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 2, 3] * 3)
    template = _numbered_bundle(tmp_path / "blank.pdf", [1, 2, 3])
    _unreadable_catalog(monkeypatch, bundle, pages=9, length=3)
    out = tmp_path / "split"

    submission_splitter.run_cli(
        ["--input", str(bundle), "--output", str(out), "--blank-template", str(template)],
        status_callback=lambda _message: None,
    )

    assert [len(PdfReader(str(pdf)).pages) for pdf in sorted(out.glob("*.pdf"))] == [3, 3, 3]
    assert "cut every 3 pages instead" in capsys.readouterr().err


def test_every_n_takes_its_page_count_from_the_blank_template(tmp_path: Path) -> None:
    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 2, 3] * 2)
    template = _numbered_bundle(tmp_path / "blank.pdf", [1, 2, 3])
    out = tmp_path / "split"
    messages: list[str] = []

    submission_splitter.run_cli(
        ["--input", str(bundle), "--output", str(out), "--split", "every-n", "--blank-template", str(template)],
        status_callback=messages.append,
    )

    assert [len(PdfReader(str(pdf)).pages) for pdf in sorted(out.glob("*.pdf"))] == [3, 3]
    assert "the length of the blank template" in _messages(messages)


def test_every_n_without_a_page_count_or_template_is_refused(tmp_path: Path) -> None:
    bundle = _numbered_bundle(tmp_path / "scans.pdf", [1, 2, 3])

    with pytest.raises(SystemExit) as stopped:
        submission_splitter.run_cli(
            ["--input", str(bundle), "--output", str(tmp_path / "split"), "--split", "every-n"],
            status_callback=lambda _message: None,
        )

    assert stopped.value.code == 2
