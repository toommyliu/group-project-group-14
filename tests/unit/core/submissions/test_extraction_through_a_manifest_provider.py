from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest
from PIL import Image

from opengrader.core import app_config, egress
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
    return '{"text": "A function that calls itself."}'


@pytest.fixture
def orbital(monkeypatch):
    directory = app_config.manifests_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "orbital.toml").write_text(MANIFEST, encoding="utf-8")
    app_config.reload_manifests()
    monkeypatch.setenv("ORBITAL_KEY", KEY)
    settings = app_config.AISettings(provider="orbital-ocr")
    # The user consented to sending student work to this exact host.
    settings = app_config.consent.grant_cloud_consent(
        settings, app_config.resolve(settings)
    )
    app_config.save_settings(settings)

    sent: list = []

    def _urlopen(req, timeout=None):
        sent.append(req)
        body = json.loads(req.data)
        prompt = body["messages"][-1]["content"][0]["text"]
        return _Response(_reply_for(prompt))

    monkeypatch.setattr("urllib.request.urlopen", _urlopen)
    try:
        yield sent
    finally:
        for path in directory.glob("*.toml"):
            path.unlink()
        app_config.reload_manifests()


def _crop(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 20), color="white").save(path)


def _run_extraction(resolved, tmp_path: Path, monkeypatch):
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
        vision_reader=app_config.vision_reader(resolved),
    )
    with egress.recording(out) as log_path:
        report = run_extraction_batch([job], workers=1)
    lines = (
        [json.loads(line) for line in log_path.read_text().splitlines()]
        if log_path.exists()
        else []
    )
    return report.outcomes[0], lines


def _prompts(sent) -> list[str]:
    return [json.loads(r.data)["messages"][-1]["content"][0]["text"] for r in sent]


def _is_identity(prompt: str) -> bool:
    return "student's name" in prompt or "student ID" in prompt


def test_a_hand_written_manifest_completes_an_extraction(
    orbital, tmp_path, monkeypatch
) -> None:
    resolved = app_config.resolve()
    assert resolved.provider.name == "orbital-ocr"
    assert resolved.provider.source == "user"
    assert resolved.problems() == ()
    app_config.ensure_ready(resolved)  # no listing, key present: passes offline

    # No local Ollama server is running in this test, so the identity route
    # this manifest's reader is never offered fails exactly like a real
    # missing server would, and the caller falls back to the local OCR value.
    from opengrader.core.ocr import ollama_vlm

    def _no_local_model(**_kwargs):
        raise ConnectionError("no Ollama server on this machine")

    monkeypatch.setattr(ollama_vlm, "extract_field_text", _no_local_model)

    outcome, egress_lines = _run_extraction(resolved, tmp_path, monkeypatch)

    assert outcome.status == "extracted"
    assert "A function that calls itself." in outcome.markdown_path.read_text()

    # Every call that reached the manifest's cloud service went where the
    # manifest said, in the shape its dialect speaks.
    assert orbital, "the reader never called the service"
    for req in orbital:
        assert req.full_url == "https://api.orbital-ocr.example/v2/chat/completions"
        assert req.get_header("Authorization") == f"Bearer {KEY}"
        body = json.loads(req.data)
        assert body["model"] == "orbital-vision-large"
        assert body["messages"][-1]["content"][1]["type"] == "image_url"

    # THE safeguard: the cloud service was never asked for a name or ID —
    # not now, not under any setting.
    assert [p for p in _prompts(orbital) if _is_identity(p)] == []
    assert outcome.student_name != "Grace Hopper"
    assert outcome.student_id != "987654321"

    # One egress line per call that actually reached the cloud service —
    # category only, never content or key.
    assert egress_lines and len(egress_lines) == len(orbital)
    assert {line["category"] for line in egress_lines} == {"answer-image"}
    assert all(line["host"] == resolved.host for line in egress_lines)
    raw = json.dumps(egress_lines)
    assert KEY not in raw and "function that calls itself" not in raw


def test_identity_crops_are_read_by_a_local_reader_not_the_cloud_service(
    orbital, tmp_path, monkeypatch
) -> None:
    """Student names and IDs are never sent to a cloud service, under any
    setting. When the active provider is a cloud host, they are read by a
    local Ollama model instead — the manifest's own reader never sees them."""
    from opengrader.core.ocr import ollama_vlm

    def _local_identity(*, image_path, field_kind, config):
        return "Grace Hopper" if field_kind == "name" else "987654321"

    monkeypatch.setattr(ollama_vlm, "extract_field_text", _local_identity)

    resolved = app_config.resolve()
    outcome, egress_lines = _run_extraction(resolved, tmp_path, monkeypatch)

    # The cloud service still never sees a name or ID...
    assert [p for p in _prompts(orbital) if _is_identity(p)] == []
    # ...yet the identity fields were filled in, by the local model.
    assert outcome.student_name == "Grace Hopper"
    assert outcome.student_id == "987654321"
    # And nothing about that local read appears in the cloud egress log.
    assert "identity-image" not in {line["category"] for line in egress_lines}
