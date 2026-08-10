"""The math crop → LaTeX seam and its backends.

pix2tex itself is never imported here — the point of the seam is that a backend
is a ``Path -> str | None`` callable, so the wiring is testable with a two-line
fake and no gigabyte of torch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opengrader.core.ocr import math_ocr
from opengrader.core.ocr.math_ocr import (
    DEFAULT_MATH_BACKEND,
    MATH_BACKEND_NAMES,
    MathOcrError,
    available_backends,
    describe_backend,
    normalize_latex,
    resolve_math_transcriber,
)


# --------------------------------------------------------------------------- #
# normalize_latex — what makes a backend's output actually render
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"\frac{1}{2}", r"$\frac{1}{2}$"),      # bare LaTeX gets delimiters
        (r"$x^2$", r"$x^2$"),                     # already delimited, untouched
        (r"\[a+b\]", "$a+b$"),                   # display math unwrapped
        (r"\(a+b\)", "$a+b$"),
        ("  x + y  ", "$x + y$"),                 # trimmed
        ("x", ""),                                # too short to be an answer
        ("", ""),
        (None, ""),
    ],
)
def test_normalize_latex(raw, expected):
    assert normalize_latex(raw) == expected


def test_normalized_output_is_what_the_grade_screen_looks_for():
    """Without the delimiters the transcription is correct and never renders."""
    from opengrader.tui.latex_render import response_has_latex

    assert not response_has_latex(r"\frac{1}{2}")
    assert response_has_latex(normalize_latex(r"\frac{1}{2}"))


# --------------------------------------------------------------------------- #
# Backend description and resolution
# --------------------------------------------------------------------------- #


def test_the_vlm_backend_is_always_available():
    backend = describe_backend("vlm")
    assert backend.available
    assert "Ollama" in backend.detail


def test_every_backend_is_described():
    described = {backend.name for backend in available_backends()}
    assert described == set(MATH_BACKEND_NAMES)


def test_describing_pix2tex_does_not_import_it(monkeypatch):
    """Availability must cost a path lookup, not a torch import."""
    monkeypatch.setattr(math_ocr, "pix2tex_installed", lambda: False)
    backend = describe_backend("pix2tex")

    assert not backend.available
    assert "uv sync --extra math" in backend.detail


def test_the_default_backend_resolves_to_the_builtin_path():
    """``None`` means "keep using the vision model exactly as before"."""
    assert resolve_math_transcriber(DEFAULT_MATH_BACKEND) is None


def test_a_missing_pix2tex_refuses_loudly_when_strict(monkeypatch):
    monkeypatch.setattr(math_ocr, "pix2tex_installed", lambda: False)
    with pytest.raises(MathOcrError) as exc_info:
        resolve_math_transcriber("pix2tex")
    assert "uv sync --extra math" in str(exc_info.value)


def test_a_missing_pix2tex_degrades_quietly_when_not_strict(monkeypatch):
    """A worker mid-batch degrades; the CLI already refused at startup."""
    monkeypatch.setattr(math_ocr, "pix2tex_installed", lambda: False)
    assert resolve_math_transcriber("pix2tex", strict=False) is None


def test_an_installed_pix2tex_resolves_to_the_transcriber(monkeypatch):
    monkeypatch.setattr(math_ocr, "pix2tex_installed", lambda: True)
    assert resolve_math_transcriber("pix2tex") is math_ocr.transcribe_with_pix2tex


def test_an_unknown_backend_names_the_real_ones():
    with pytest.raises(MathOcrError) as exc_info:
        resolve_math_transcriber("mathpix")
    assert "pix2tex" in str(exc_info.value)


def test_pix2tex_returns_none_rather_than_raising_when_it_cannot_load(monkeypatch):
    """Never lose an answer: an unusable backend falls through, it doesn't fail."""
    def _boom():
        raise MathOcrError("no model")

    monkeypatch.setattr(math_ocr, "_load_pix2tex", _boom)
    assert math_ocr.transcribe_with_pix2tex(Path("nope.png")) is None


def test_pix2tex_returns_none_on_an_unreadable_crop(monkeypatch, tmp_path):
    monkeypatch.setattr(math_ocr, "_load_pix2tex", lambda: (lambda _image: "x^2"))
    assert math_ocr.transcribe_with_pix2tex(tmp_path / "missing.png") is None


def test_pix2tex_normalizes_what_the_model_returns(monkeypatch, tmp_path):
    from PIL import Image

    crop = tmp_path / "q1.png"
    Image.new("RGB", (40, 20), "white").save(crop)
    monkeypatch.setattr(
        math_ocr, "_load_pix2tex", lambda: (lambda _image: r"\int_0^1 x\,dx")
    )
    assert math_ocr.transcribe_with_pix2tex(crop) == r"$\int_0^1 x\,dx$"


def test_pix2tex_shrugging_reads_as_nothing(monkeypatch, tmp_path):
    from PIL import Image

    crop = tmp_path / "q1.png"
    Image.new("RGB", (40, 20), "white").save(crop)
    monkeypatch.setattr(math_ocr, "_load_pix2tex", lambda: (lambda _image: "x"))
    assert math_ocr.transcribe_with_pix2tex(crop) is None


# --------------------------------------------------------------------------- #
# The extraction wiring
# --------------------------------------------------------------------------- #


def _question(question_id: str = "q1", *, image: str | None = "responses/q1.png"):
    from opengrader.core.submissions.student_file import StudentSubmissionQuestion

    return StudentSubmissionQuestion(
        question_id=question_id,
        prompt="",
        possible_points=10,
        response_image_path=image,
        response_text="",
    )


def _fill(questions, transcriber, **kwargs):
    from opengrader.core.submissions.processor import (
        ExtractionProgress,
        _fill_responses_with_vlm,
    )

    return _fill_responses_with_vlm(
        questions,
        Path("/nowhere"),
        None,  # type: ignore[arg-type] — never reached when the backend answers
        pdf_name="sub-0001.pdf",
        progress=ExtractionProgress(),
        progress_callback=lambda _message: None,
        math_latex=True,
        math_transcriber=transcriber,
        **kwargs,
    )


def test_a_math_backend_transcription_is_used_and_left_unverified():
    filled = _fill([_question()], lambda _path: r"$x^2$")

    assert filled[0].response_text == r"$x^2$"
    # A model reading carries no calibrated confidence, like the VLM's.
    assert filled[0].response_confidence is None


def test_a_backend_that_reads_nothing_falls_through(monkeypatch):
    """The whole safety property: a backend choice can't cost an answer."""
    from opengrader.core.submissions import processor

    monkeypatch.setattr(
        processor,
        "_ocr_image_with_confidence",
        lambda _path, psm=6: ("fallback text", 72.0),
    )
    filled = _fill([_question()], lambda _path: None)

    assert filled[0].response_text == "fallback text"
    assert filled[0].response_confidence == 72.0


def test_a_question_with_no_crop_is_untouched():
    filled = _fill([_question(image=None)], lambda _path: r"$x^2$")
    assert filled[0].response_text == ""


def test_the_backend_name_crosses_the_process_boundary_not_a_callable():
    """`batch.ExtractionJob` must stay picklable — see core/submissions/batch.py."""
    import pickle

    from opengrader.core.submissions.batch import ExtractionJob

    job = ExtractionJob(
        pdf_path=Path("a.pdf"),
        output_dir=Path("out"),
        ocr_config=None,
        dpi=300,
        llm_math=True,
        math_backend="pix2tex",
    )
    assert pickle.loads(pickle.dumps(job)).math_backend == "pix2tex"


# --------------------------------------------------------------------------- #
# The CLI's gate
# --------------------------------------------------------------------------- #


def test_the_cli_refuses_a_missing_backend_before_rendering_a_page(
    monkeypatch, tmp_path, capsys
):
    """Fail at second zero, not after an overnight batch has used the other one."""
    from opengrader.cli import submission_extractor

    monkeypatch.setattr(math_ocr, "pix2tex_installed", lambda: False)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4\n")

    with pytest.raises(SystemExit) as exit_info:
        submission_extractor.run_cli(
            [
                "--input", str(tmp_path / "a.pdf"),
                "--output", str(tmp_path / "out"),
                "--llm-math",
                "--math-backend", "pix2tex",
            ]
        )
    assert exit_info.value.code == 1
    assert "uv sync --extra math" in capsys.readouterr().err


def test_a_backend_without_llm_math_says_it_does_nothing(
    monkeypatch, tmp_path, capsys
):
    """Silently ignoring it would leave the instructor believing it ran."""
    from opengrader.cli import submission_extractor

    monkeypatch.setattr(math_ocr, "pix2tex_installed", lambda: True)
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4\n")

    with pytest.raises(SystemExit):
        submission_extractor.run_cli(
            [
                "--input", str(tmp_path / "a.pdf"),
                "--output", str(tmp_path / "out"),
                "--math-backend", "pix2tex",
            ]
        )
    assert "does nothing without --llm-math" in capsys.readouterr().err


def test_the_backend_invalidates_a_resumed_run():
    """A different reader produces a different transcription of the same crop."""
    from opengrader.core.submissions.manifest import settings_fingerprint

    base = {"dpi": 300, "llm_math": True, "math_backend": "vlm"}
    swapped = {**base, "math_backend": "pix2tex"}
    assert settings_fingerprint(base) != settings_fingerprint(swapped)
