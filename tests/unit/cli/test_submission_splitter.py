from pathlib import Path

import fitz
import pytest
from pypdf import PdfReader

from opengrader.cli import submission_splitter
from opengrader.core import ai_config
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


@pytest.fixture
def upside_down(monkeypatch):
    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation",
        lambda _image: (180, 12.0),
    )


def _messages(capture: list[str]):
    return "\n".join(capture)


def test_fix_rotation_turns_pages_before_they_are_split(
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
            "--fix-rotation",
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
            "--fix-rotation",
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
            "--fix-rotation",
        ],
        status_callback=lambda _message: None,
    )

    assert bundle.read_bytes() == before


def test_scan_check_reports_without_fixing(
    tmp_path: Path, monkeypatch, capsys, upside_down
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
            "--scan-check",
        ],
        status_callback=messages.append,
    )

    assert "--fix-rotation" in _messages(messages)
    assert not (out / ".opengrader" / "scans-upright.pdf").exists()
    assert (out / "submission-0001.pdf").exists()
    assert "rotated 180" in capsys.readouterr().err


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
            "--scan-check",
            "--no-scan-orientation",
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
            "--fix-rotation",
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


def test_fix_order_sorts_pages_when_the_numbers_are_exact(
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
            "--fix-order",
            "--no-scan-orientation",
        ],
        status_callback=lambda _message: None,
    )

    assert _footers(out / "submission-0001.pdf") == ["Page 1", "Page 2", "Page 3"]
    assert _footers(out / "submission-0002.pdf") == ["Page 1", "Page 2", "Page 3"]


def test_fix_order_refuses_to_act_on_an_ocr_reading(
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
            "--fix-order",
            "--no-scan-orientation",
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
            "--pages-per-submission", "3",
            "--blank-template", str(_numbered_bundle(tmp_path / "blank.pdf", [1, 2, 3])),
            "--fix-order",
            "--no-scan-orientation",
        ],
        status_callback=lambda _message: None,
    )

    assert reordered, "a template should authorize the correction"


def test_fix_order_costs_nothing_on_a_scan_that_is_in_order(
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
            "--pages-per-submission", "3",
            "--fix-order",
            "--no-scan-orientation",
        ],
        status_callback=lambda _message: None,
    )


def test_scan_check_alone_reports_the_order_without_touching_it(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    _no_ollama(monkeypatch)

    def _boom(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("--scan-check must not reorder anything")

    monkeypatch.setattr(submission_splitter, "reorder_pdf_pages", _boom)
    messages: list[str] = []

    submission_splitter.run_cli(
        [
            "--input", str(_numbered_bundle(tmp_path / "scans.pdf", [1, 3, 2])),
            "--output", str(tmp_path / "split"),
            "--pages-per-submission", "3",
            "--scan-check",
            "--no-scan-orientation",
        ],
        status_callback=messages.append,
    )

    assert "--fix-order" in _messages(messages)
    assert "out of order" in capsys.readouterr().err


def _record_vlm_calls(monkeypatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(
        submission_splitter,
        "_verified_vision_reader",
        lambda args, emit: calls.append(1) or None,
    )
    return calls


def test_llm_page_numbers_off_skips_vlm_even_during_boundary_detection(
    tmp_path: Path, monkeypatch
) -> None:
    calls = _record_vlm_calls(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 4)

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(tmp_path / "split"),
            "--detect-boundaries",
            "--no-llm-page-numbers",
        ],
        status_callback=lambda _message: None,
    )

    assert calls == []


def test_llm_page_numbers_on_uses_vlm_during_boundary_detection(
    tmp_path: Path, monkeypatch
) -> None:
    calls = _record_vlm_calls(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 4)

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(tmp_path / "split"),
            "--detect-boundaries",
            "--llm-page-numbers",
        ],
        status_callback=lambda _message: None,
    )

    assert calls == [1]


def test_llm_page_numbers_defaults_from_ai_setup(tmp_path: Path, monkeypatch) -> None:
    ai_config.save_settings(ai_config.AISettings(read_handwriting=True))
    calls = _record_vlm_calls(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 4)

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(tmp_path / "split"),
            "--detect-boundaries",
        ],
        status_callback=lambda _message: None,
    )

    assert calls == [1]


def test_llm_page_numbers_defaults_off_when_ai_setup_leaves_it_off(
    tmp_path: Path, monkeypatch
) -> None:
    ai_config.save_settings(ai_config.AISettings())
    calls = _record_vlm_calls(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 4)

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(tmp_path / "split"),
            "--detect-boundaries",
        ],
        status_callback=lambda _message: None,
    )

    assert calls == []


def test_no_preflight_runs_unless_asked(tmp_path: Path, monkeypatch) -> None:
    def _boom(_image):  # pragma: no cover
        raise AssertionError("the scan pre-flight should not have run")

    monkeypatch.setattr(
        "opengrader.core.submissions.scan_check.detect_page_orientation", _boom
    )
    _no_ollama(monkeypatch)
    bundle = _bundle(tmp_path / "scans.pdf", 4)

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(tmp_path / "split"),
            "--pages-per-submission", "2",
        ],
        status_callback=lambda _message: None,
    )
