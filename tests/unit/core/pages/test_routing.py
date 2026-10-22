from pathlib import Path

import fitz

from opengrader.core.pages.catalog import PageCatalog
from opengrader.core.submissions import processor


def test_a_pdf_with_a_text_layer_is_read_from_its_text_even_with_a_catalog(tmp_path: Path):
    pdf = tmp_path / "typed.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Name: Ann Lee")
    page.insert_text((72, 120), "1. (10 pts) Define a heap.")
    doc.save(pdf)

    document, _ = processor.build_student_submission_document_unified(
        pdf_path=pdf, output_dir=tmp_path / "out", ocr_config=None, dpi=150, split_subquestions=False,
        vision_reader=None, skip_ollama_qa=True, save_layout_debug=False, blank_assignment_template=None,
        progress_callback=lambda _m: None, page_catalog=PageCatalog(groups={}, matches=[]),
    )

    assert document.student_name == "Ann Lee"


def _catalog_document(tmp_path: Path, monkeypatch, **options):
    from opengrader.core.pages import extract
    from opengrader.core.submissions.student_file import StudentSubmissionQuestion

    crops = extract.CatalogCrops(
        name_image="identifiers/name.png", id_image="identifiers/id.png", title_text="CS146: Quiz 1 (B)",
        questions=[StudentSubmissionQuestion("q1", "Define a heap.", 5.0, "responses/q1.png", "")],
    )
    monkeypatch.setattr(extract, "crop_submission", lambda *_a, **_k: crops)
    transcribed = []
    monkeypatch.setattr(processor, "transcribe_crop",
                        lambda path, **_k: (transcribed.append(path.name), ("a heap", 90.0, "vision-model", "local"))[1])

    class Reader:
        def extract_field_text(self, *, image_path, field_kind):
            return {"name": "Ann Lee", "sid": "123456789"}[field_kind]

    document = processor._build_document_from_page_catalog(
        tmp_path / "s.pdf", tmp_path, PageCatalog(groups={}, matches=[]), vision_reader=Reader(), **options)
    return document, transcribed


def test_names_ids_and_titles_are_always_read_but_answers_only_when_asked(tmp_path: Path, monkeypatch):
    document, transcribed = _catalog_document(tmp_path, monkeypatch)

    assert (document.student_name, document.student_id) == ("Ann Lee", "123456789")
    assert (document.assignment_title, document.assignment_version) == ("Quiz 1", "B")
    assert transcribed == [] and document.questions[0].response_text == ""
    assert document.questions[0].response_image_path == "responses/q1.png"


def test_answers_are_transcribed_for_automated_grading_when_asked(tmp_path: Path, monkeypatch):
    document, transcribed = _catalog_document(tmp_path, monkeypatch, llm_responses=True)

    assert transcribed == ["q1.png"] and document.questions[0].response_text == "a heap"


def _title_for(tmp_path: Path, monkeypatch, usual: str, read: str):
    from PIL import Image

    from opengrader.core.pages import extract, printed

    Image.new("RGB", (400, 40), "white").save(tmp_path / "title.png")
    monkeypatch.setattr(printed, "read_title", lambda _image: read)
    crops = extract.CatalogCrops(title_image="title.png", title_text=usual)
    title = processor._read_title(tmp_path, crops, lambda _m: None)
    return title.assignment_title, title.assignment_version


def test_each_copy_keeps_the_version_its_own_title_prints(tmp_path: Path, monkeypatch):
    assert _title_for(tmp_path, monkeypatch, "CS146: Quiz 1 (A)", "CS146: Quiz 1 (B)") == ("Quiz 1", "B")


def test_a_misread_title_falls_back_to_the_pages_usual_title(tmp_path: Path, monkeypatch):
    assert _title_for(tmp_path, monkeypatch, "CMPE 30 Quiz 2_5a (100 points)", "CMPE 30 Quiz 25 (100popins)") == ("Quiz 2", "5A")


def test_a_scan_cut_by_page_matching_ignores_a_qr_code_on_the_page(tmp_path: Path, monkeypatch):
    from opengrader.core.qr_codes import OpengraderCode, render_qr_png
    from opengrader.core.submissions.student_file import StudentSubmissionDocument

    pdf = tmp_path / "scan.pdf"
    doc = fitz.open()
    doc.new_page().insert_image(fitz.Rect(400, 40, 560, 200), stream=render_qr_png(OpengraderCode(student_id="999999999")))
    doc.save(pdf)
    monkeypatch.setattr(processor, "_is_scan", lambda _pdf: True)
    monkeypatch.setattr(processor, "_build_document_from_page_catalog", lambda *_a, **_k: StudentSubmissionDocument(
        student_name="Ann Lee", student_id="123456789", course_code=None, assignment_title="Quiz 1",
        assignment_version=None, source_pdf="scan.pdf", student_score="", questions=[]))

    document, _ = processor.build_student_submission_document_unified(
        pdf_path=pdf, output_dir=tmp_path / "out", ocr_config=None, dpi=150, split_subquestions=False,
        vision_reader=None, skip_ollama_qa=True, save_layout_debug=False, blank_assignment_template=None,
        progress_callback=lambda _m: None, page_catalog=PageCatalog(groups={}, matches=[]),
    )

    assert processor._read_qr_student_id(pdf) == "999999999"
    assert document.student_id == "123456789"
