"""The Phase 2 exit criterion (About/AI_README.md §7.9, the `VisionReader` row).

Before the seam went in, driving an extraction meant a running Ollama server,
a pulled model and a live network — the extraction tests were the slowest and
most environment-dependent in the suite (§7.4). This file is the proof that
is no longer true: a stub `VisionReader` returning fixed strings, holding no
socket and touching no HTTP, is enough to drive a complete extraction end to
end and land its fixed strings in the produced document and `.submission.md`.

Layer actually driven: `run_extraction_batch` -> `ExtractionJob` ->
`build_student_submission_document_unified` -> `_build_document_from_ocr_result`
(processor.py), and separately `student_file.build_student_submission_document`
directly for the one operation (`extract_title_metadata`) that only that
entry point calls. Both are real, unmocked code paths above the OCR/layout
boundary: real dataclass construction, real markdown rendering to disk, and
(for the batch test) the real single-job worker-count decision in
`run_extraction_batch`.

What is mocked, and why: `opengrader.core.ocr.extractor.process_submission_pdf`
— the Tesseract-backed layout scanner that locates identity fields and
question crops on the page image. That scanner is not part of the
`VisionReader` seam Phase 2 built; the existing OCR-layout tests
(tests/unit/ocr/test_layout_*.py, test_scanner_question_type.py) already
cover it, and the repo's own extraction tests
(tests/unit/core/submissions/test_student_file_subquestions.py) mock it the
same way to isolate the code that *does* belong to this seam. Tesseract
itself is present in this environment (tests/unit/core/submissions/test_batch.py's
`spawned_batch` fixture exercises it for real, via a text-layer PDF that
never reaches the vision reader at all) — it is not skipped here for lack of
the binary; it is out of scope for what this file is proving.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fitz
import pytest
from PIL import Image

from opengrader.core.ocr import extractor as ocr_extractor
from opengrader.core.ocr.extractor import (
    FieldExtractionResult,
    OcrConfig,
    OcrResult,
    QuestionExtractionResult,
    SubmissionResult,
)
from opengrader.core.ocr.vision_reader import TitleMetadataResult
from opengrader.core.submissions import student_file
from opengrader.core.submissions.batch import ExtractionJob, run_extraction_batch


@dataclass
class _StubVisionReader:
    """A `VisionReader` returning fixed strings and touching no network.

    Defined at module level, not inside a test function, because
    `ExtractionJob.vision_reader` (core/submissions/batch.py) crosses a
    `spawn`ed worker process boundary whenever a batch resolves more than one
    worker; only a module-level, picklable object survives that trip. A
    closure or a local class defined inside a test function would work under
    `workers=1` (this file's tests stay there — a single job never starts a
    pool) and fail only under the parallel path, which is exactly the
    failure mode a stub is supposed to make impossible to write by accident.
    """

    name_value: str = "Ada Lovelace"
    sid_value: str = "123456789"
    title_value: TitleMetadataResult = field(
        default_factory=lambda: TitleMetadataResult(
            course_code="CMPE30",
            assignment_title="Quiz 2",
            assignment_version="A",
            raw_title="CMPE 30 Quiz 2 (A)",
        )
    )
    qa_value: tuple[str, str] = ("What is a stack?", "A LIFO data structure.")
    handwriting_value: str = "isolated ink text"
    page_number_value: int | None = 1
    first_page_value: bool = True
    calls: list[tuple] = field(default_factory=list)

    def extract_field_text(self, *, image_path, field_kind):
        self.calls.append(("extract_field_text", image_path, field_kind))
        return self.name_value if field_kind == "name" else self.sid_value

    def extract_title_metadata(self, *, image_path):
        self.calls.append(("extract_title_metadata", image_path))
        return self.title_value

    def extract_question_prompt_and_answer(self, *, image_path, math_latex=False):
        self.calls.append(
            ("extract_question_prompt_and_answer", image_path, math_latex)
        )
        return self.qa_value

    def extract_handwriting_only(self, *, image_path, math_latex=False):
        self.calls.append(("extract_handwriting_only", image_path, math_latex))
        return self.handwriting_value

    def extract_page_number(self, *, image_path):
        self.calls.append(("extract_page_number", image_path))
        return self.page_number_value

    def page_has_identity_field(self, *, image_path):
        self.calls.append(("page_has_identity_field", image_path))
        return self.first_page_value


@pytest.fixture(autouse=True)
def _forbid_any_http(monkeypatch):
    """The single most important assertion in this file.

    `core/llm/ollama_transport.py` does `from urllib import error, request`
    and calls `request.urlopen(...)` at call time, so patching
    `urllib.request.urlopen` intercepts it regardless of how it is imported
    elsewhere. If anything in either pipeline under test — a default vision
    reader construction, a fallback path, a stray `ensure_model_available`
    call — ever opens a socket, this fails the test immediately instead of
    quietly hitting (or hanging on) a real network.
    """

    def _boom(*_args, **_kwargs):
        raise AssertionError("the seam leaked: something opened a socket")

    monkeypatch.setattr("urllib.request.urlopen", _boom)


def _scanned_pdf_with_no_text_layer(path: Path) -> None:
    """A valid, openable PDF with a blank page and no embedded text.

    `build_student_submission_document_unified` opens the PDF itself (for
    real — this isn't mocked) to decide between the text-layer route and the
    OCR route. A text-layer PDF (as `test_batch.py`'s fixtures use) would
    bypass the vision reader entirely, which is the opposite of what this
    file needs to prove.
    """

    doc = fitz.open()
    doc.new_page(width=612, height=792)
    doc.save(str(path))
    doc.close()


def _make_crop(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 20), color="white").save(path)


def test_a_complete_extraction_runs_through_a_stub_reader_with_no_network(
    tmp_path, monkeypatch
):
    """The exit criterion itself: `run_extraction_batch` end to end, with a
    stub `VisionReader` in place of Ollama, and the stub's fixed name,
    student ID and transcribed answer landing in both the returned
    `ExtractionOutcome` and the written `.submission.md`.
    """

    scans_dir = tmp_path / "scans"
    out_dir = tmp_path / "out"
    scans_dir.mkdir()
    pdf_path = scans_dir / "submission-0001.pdf"
    _scanned_pdf_with_no_text_layer(pdf_path)

    # process_submission_pdf normally writes crops under <output>/<stem>/...;
    # the processor computes every downstream image path relative to that
    # directory with no fallback, so the stand-in crops must live there too.
    submission_dir = out_dir / "submission-0001"
    name_crop = submission_dir / "identifiers" / "name.png"
    id_crop = submission_dir / "identifiers" / "id.png"
    question_crop = submission_dir / "responses" / "q1.png"
    for crop in (name_crop, id_crop, question_crop):
        _make_crop(crop)

    def _fake_process_submission_pdf(*, pdf_path, config, output_dir, **_kwargs):
        return SubmissionResult(
            filename=pdf_path.name,
            version_validated=True,
            fields=[
                FieldExtractionResult(
                    field_id="student_name",
                    ocr_result=OcrResult(
                        text="", confidence=0.0, bbox_px=(0, 0, 0, 0)
                    ),
                    crop_path=str(name_crop),
                ),
                FieldExtractionResult(
                    field_id="student_id",
                    ocr_result=OcrResult(
                        text="", confidence=0.0, bbox_px=(0, 0, 0, 0)
                    ),
                    crop_path=str(id_crop),
                ),
            ],
            questions=[
                QuestionExtractionResult(
                    question_id="q1",
                    page_number=1,
                    output_path=str(question_crop),
                )
            ],
        )

    monkeypatch.setattr(
        ocr_extractor, "process_submission_pdf", _fake_process_submission_pdf
    )

    stub_reader = _StubVisionReader()
    job = ExtractionJob(
        pdf_path=pdf_path,
        output_dir=out_dir,
        ocr_config=OcrConfig(),
        vision_reader=stub_reader,
    )

    report = run_extraction_batch([job], workers=1)

    assert [outcome.status for outcome in report.outcomes] == ["extracted"]
    outcome = report.outcomes[0]
    assert outcome.student_name == stub_reader.name_value
    assert outcome.student_id == stub_reader.sid_value

    assert outcome.markdown_path == submission_dir / "submission-0001.submission.md"
    markdown_text = outcome.markdown_path.read_text()
    assert stub_reader.name_value in markdown_text
    assert stub_reader.sid_value in markdown_text
    assert stub_reader.qa_value[1] in markdown_text  # the transcribed answer

    # The stub recorded exactly what the pipeline asked of it.
    called_methods = {call[0] for call in stub_reader.calls}
    assert called_methods == {
        "extract_field_text",
        "extract_question_prompt_and_answer",
    }
    field_kinds_asked = {
        call[2] for call in stub_reader.calls if call[0] == "extract_field_text"
    }
    assert field_kinds_asked == {"name", "sid"}
    qa_call = next(
        call
        for call in stub_reader.calls
        if call[0] == "extract_question_prompt_and_answer"
    )
    assert qa_call[1] == question_crop


def test_build_student_submission_document_reads_title_metadata_from_the_stub(
    tmp_path, monkeypatch
):
    """`run_extraction_batch`'s OCR route never calls `extract_title_metadata`
    (it leaves `assignment_title` blank and relies on a later step) — only
    `student_file.build_student_submission_document` does. This test drives
    that entry point directly so the title-metadata operation is proven to
    land end to end too, not just delegated correctly in isolation (that is
    tests/unit/ocr/test_vision_reader.py's job).
    """

    scans_dir = tmp_path / "scans"
    out_dir = tmp_path / "out"
    scans_dir.mkdir()
    pdf_path = scans_dir / "submission-0002.pdf"
    _scanned_pdf_with_no_text_layer(pdf_path)

    submission_dir = out_dir / pdf_path.stem
    name_crop = submission_dir / "identifiers" / "name.png"
    id_crop = submission_dir / "identifiers" / "id.png"
    title_crop = submission_dir / "identifiers" / "title_p1.png"
    question_crop = submission_dir / "responses" / "q1.png"
    for crop in (name_crop, id_crop, title_crop, question_crop):
        _make_crop(crop)

    def _fake_process_submission_pdf(*, pdf_path, config, output_dir, **_kwargs):
        return SubmissionResult(
            filename=pdf_path.name,
            version_validated=True,
            title_crop_path=str(title_crop),
            fields=[
                FieldExtractionResult(
                    field_id="student_name",
                    ocr_result=OcrResult(
                        text="", confidence=0.0, bbox_px=(0, 0, 0, 0)
                    ),
                    crop_path=str(name_crop),
                ),
                FieldExtractionResult(
                    field_id="student_id",
                    ocr_result=OcrResult(
                        text="", confidence=0.0, bbox_px=(0, 0, 0, 0)
                    ),
                    crop_path=str(id_crop),
                ),
            ],
            questions=[
                QuestionExtractionResult(
                    question_id="q1",
                    page_number=1,
                    output_path=str(question_crop),
                )
            ],
        )

    monkeypatch.setattr(
        ocr_extractor, "process_submission_pdf", _fake_process_submission_pdf
    )

    stub_reader = _StubVisionReader()

    result = student_file.build_student_submission_document(
        pdf_path=pdf_path,
        output_dir=out_dir,
        ocr_config=OcrConfig(),
        vision_reader=stub_reader,
    )

    document = result.document
    assert document.student_name == stub_reader.name_value
    assert document.student_id == stub_reader.sid_value
    assert document.course_code == stub_reader.title_value.course_code
    assert document.assignment_title == stub_reader.title_value.assignment_title
    assert document.assignment_version == stub_reader.title_value.assignment_version

    markdown_text = result.markdown_path.read_text()
    assert stub_reader.title_value.course_code in markdown_text
    assert stub_reader.title_value.assignment_title in markdown_text
    assert stub_reader.qa_value[1] in markdown_text

    called_methods = {call[0] for call in stub_reader.calls}
    assert "extract_title_metadata" in called_methods
    title_call = next(
        call for call in stub_reader.calls if call[0] == "extract_title_metadata"
    )
    assert title_call[1] == title_crop
