"""Phase 4's exit criterion: a provider nobody coded completes an extraction.

§7.9: "A hand-written manifest for a service not known to the codebase appears
in the AI Setup dropdown and completes an extraction." The dropdown half is in
`tests/unit/tui/test_provider_screen.py`. This file is the extraction half.

Nothing here names a provider. The test writes a TOML file, reloads the
registry, and asks `ai_config.vision_reader(resolve())` for a reader, the way
every CLI does. It then runs a real batch extraction with that reader. The
only fake is `urlopen`, which answers in the manifest's dialect. So the
request shape, auth header, endpoint and reply parsing are all real, and the
test checks all four as well as checking that the answer landed in the file.

The Tesseract layout scanner is replaced as in
`test_extraction_through_a_stub_reader.py`, for the reason given there: it is
not part of the provider seam.

Note for Phase 5: this provider is cloud, and today it is asked to read the
name and student-ID crops too. L5 will pin those to a local reader. When it
lands, the `identity_reads` assertion below is the one that must flip.
"""

from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest
from PIL import Image

from opengrader.core import ai_config
from opengrader.core.ocr import extractor as ocr_extractor
from opengrader.core.ocr.extractor import (
    FieldExtractionResult,
    OcrConfig,
    OcrResult,
    QuestionExtractionResult,
    SubmissionResult,
)
from opengrader.core.submissions.batch import ExtractionJob, run_extraction_batch

MANIFEST = """\
# Written by hand. The codebase has never heard of this service.
name         = "orbital-ocr"
label        = "Orbital OCR Service"
dialect      = "openai-chat"
host         = "https://api.orbital-ocr.example/v2"
vision_model = "orbital-vision-large"
text_model   = "orbital-text"
api_key_env  = "ORBITAL_KEY"
"""

KEY = "sk-orbital-0123456789abcdef"


class _Response:
    def __init__(self, reply: str) -> None:
        self.status = 200
        self._raw = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": reply}}]}
        ).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_exc) -> None:
        return None


def _reply_for(prompt: str) -> str:
    """Answer each vision operation in the JSON shape it asked for."""
    if "student's name" in prompt:
        return '{"value": "Grace Hopper"}'
    if "student ID" in prompt:
        return '{"value": "987654321"}'
    return '{"prompt": "Define recursion.", "answer": "A function that calls itself."}'


@pytest.fixture
def orbital(monkeypatch):
    directory = ai_config.manifests_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "orbital.toml").write_text(MANIFEST, encoding="utf-8")
    ai_config.reload_manifests()
    ai_config.save_settings(ai_config.AISettings(provider="orbital-ocr"))
    monkeypatch.setenv("ORBITAL_KEY", KEY)

    sent: list = []

    def _urlopen(req, timeout=None):
        sent.append(req)
        body = json.loads(req.data)
        prompt = body["messages"][0]["content"][0]["text"]
        return _Response(_reply_for(prompt))

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    try:
        yield sent
    finally:
        for path in directory.glob("*.toml"):
            path.unlink()
        ai_config.reload_manifests()


def _crop(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 20), color="white").save(path)


def test_a_hand_written_manifest_completes_an_extraction(
    orbital, tmp_path, monkeypatch
) -> None:
    resolved = ai_config.resolve()
    assert resolved.provider.name == "orbital-ocr"
    assert resolved.provider.source == "user"
    assert resolved.problems() == ()
    ai_config.ensure_ready(resolved)  # no listing, key present: passes offline

    scans = tmp_path / "scans"
    out = tmp_path / "out"
    scans.mkdir()
    pdf = scans / "submission-0001.pdf"
    doc = fitz.open()
    doc.new_page(width=612, height=792)  # no text layer: forces the vision path
    doc.save(str(pdf))
    doc.close()

    sub = out / "submission-0001"
    name_crop = sub / "identifiers" / "name.png"
    id_crop = sub / "identifiers" / "id.png"
    answer_crop = sub / "responses" / "q1.png"
    for crop in (name_crop, id_crop, answer_crop):
        _crop(crop)

    def _layout(*, pdf_path, config, output_dir, **_kwargs):
        blank = OcrResult(text="", confidence=0.0, bbox_px=(0, 0, 0, 0))
        return SubmissionResult(
            filename=pdf_path.name,
            version_validated=True,
            fields=[
                FieldExtractionResult(
                    field_id="student_name", ocr_result=blank, crop_path=str(name_crop)
                ),
                FieldExtractionResult(
                    field_id="student_id", ocr_result=blank, crop_path=str(id_crop)
                ),
            ],
            questions=[
                QuestionExtractionResult(
                    question_id="q1", page_number=1, output_path=str(answer_crop)
                )
            ],
        )

    monkeypatch.setattr(ocr_extractor, "process_submission_pdf", _layout)

    job = ExtractionJob(
        pdf_path=pdf,
        output_dir=out,
        ocr_config=OcrConfig(),
        vision_reader=ai_config.vision_reader(resolved),
    )
    report = run_extraction_batch([job], workers=1)

    outcome = report.outcomes[0]
    assert outcome.status == "extracted"
    assert outcome.student_name == "Grace Hopper"
    assert outcome.student_id == "987654321"
    assert "A function that calls itself." in outcome.markdown_path.read_text()

    # Every call went where the manifest said, in the shape its dialect speaks.
    assert orbital, "the reader never called the service"
    for req in orbital:
        assert req.full_url == "https://api.orbital-ocr.example/v2/chat/completions"
        assert req.get_header("Authorization") == f"Bearer {KEY}"
        body = json.loads(req.data)
        assert body["model"] == "orbital-vision-large"
        assert body["messages"][0]["content"][1]["type"] == "image_url"

    prompts = [json.loads(r.data)["messages"][0]["content"][0]["text"] for r in orbital]
    identity_reads = [p for p in prompts if "student's name" in p or "student ID" in p]
    assert len(identity_reads) == 2  # Phase 5 (L5) must drive this to zero.
