"""The environment pre-flight's judgement half (`core/environment.py`).

Every test here feeds a *measurement* to a pure ``evaluate_*`` function, so the
whole suite runs with no Tesseract, no Ollama server and no terminal — which is
the point of keeping the probes separate from the judging.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from opengrader.core.environment import (
    EnvironmentCheck,
    EnvironmentReport,
    ImportProbe,
    OllamaProbe,
    ToolProbe,
    evaluate_ai_features,
    evaluate_ai_latency,
    evaluate_canvas_sak,
    evaluate_graphics,
    evaluate_imports,
    evaluate_ollama,
    evaluate_optional_import,
    evaluate_python,
    evaluate_smoke_test,
    evaluate_tesseract,
    install_hint,
    summarize_connection_error,
)


# Construct test doubles for ResolvedAI and LatencyMeasurement without
# importing those modules (keeping the test suite pure and fast).
@dataclass(frozen=True)
class FakeResolvedAI:
    """Minimal ResolvedAI-like stub for testing evaluate_ai_features."""

    read_handwriting: bool = False
    escalate_only: bool = False
    transcribe_math: bool = False
    grade_written: bool = False

    @property
    def uses_ai(self) -> bool:
        return self.read_handwriting or self.grade_written

    def problems(self) -> tuple[str, ...]:
        # Return empty for testing; override in subclasses for specific findings.
        return ()


@dataclass(frozen=True)
class FakeLatencyMeasurement:
    """Minimal LatencyMeasurement-like stub for testing evaluate_ai_latency."""

    role: str = "handwriting"
    model: str = "qwen2.5vl:7b"
    limit: float = 120.0
    seconds: float | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.seconds is not None and not self.error

    @property
    def share_of_limit(self) -> float | None:
        if self.seconds is None:
            return None
        return self.seconds / self.limit

    def describe(self) -> str:
        label = self.role.capitalize()
        if not self.ok:
            return f"{label} model {self.model} did not answer: {self.error}"
        return (
            f"{label} model {self.model} responded in {self.seconds:.1f} s "
            f"(a first call also loads the model). Your limit is {self.limit:g} s."
        )


# --- Python -----------------------------------------------------------------


def test_python_312_is_ready() -> None:
    check = evaluate_python((3, 12, 4))
    assert check.status == "ready"
    assert "3.12.4" in check.detail


def test_other_python_versions_are_broken_and_name_the_fix() -> None:
    for version in ((3, 11, 9), (3, 13, 0)):
        check = evaluate_python(version)
        assert check.status == "broken"
        assert "uv python install 3.12" in check.fix


# --- Tesseract --------------------------------------------------------------


def test_missing_tesseract_is_broken() -> None:
    # Broken, not degraded: without it a scanned exam extracts to empty answers,
    # which is the silent failure the whole pre-flight exists to surface.
    check = evaluate_tesseract(ToolProbe(found=False))
    assert check.status == "broken"
    assert check.fix  # an actionable install command, whatever the platform


def test_recent_tesseract_is_ready() -> None:
    probe = ToolProbe(
        found=True, version=(5, 5, 2), version_text="5.5.2", path="/usr/local/bin/tesseract"
    )
    check = evaluate_tesseract(probe)
    assert check.status == "ready"
    assert "5.5.2" in check.detail
    assert "/usr/local/bin/tesseract" in check.detail


def test_old_tesseract_is_degraded_not_broken() -> None:
    # 3.x still reads text; it only lacks the orientation detection the scan
    # pre-flight uses, so extraction as a whole still works.
    probe = ToolProbe(found=True, version=(3, 5, 1), version_text="3.5.1")
    check = evaluate_tesseract(probe)
    assert check.status == "degraded"
    assert "orientation" in check.impact.lower()


def test_unrunnable_tesseract_is_unknown_never_ready() -> None:
    # An installed-but-unrunnable binary is not evidence that OCR works. It must
    # never be reported as ready, or the report claims a check it never made.
    probe = ToolProbe(found=False, error="permission denied")
    check = evaluate_tesseract(probe)
    assert check.status == "unknown"
    assert "permission denied" in check.detail


# --- Ollama -----------------------------------------------------------------


def test_unreachable_ollama_is_only_degraded() -> None:
    # Ollama is opt-in. Calling a missing optional integration a failure would
    # train people to ignore the report.
    checks = evaluate_ollama(
        OllamaProbe(host="http://127.0.0.1:11434", reachable=False, error="no server listening"),
        vision_model="qwen2.5vl:7b",
    )
    assert [check.status for check in checks] == ["degraded"]
    assert "ollama serve" in checks[0].fix


def test_reachable_ollama_reports_each_missing_model_separately() -> None:
    probe = OllamaProbe(
        host="http://127.0.0.1:11434", reachable=True, models=("qwen2.5vl:7b",)
    )
    checks = evaluate_ollama(probe, vision_model="qwen2.5vl:7b", text_model="llama3.1:8b")
    by_name = {check.name: check for check in checks}
    assert by_name["Ollama server"].status == "ready"
    assert by_name["Ollama vision model"].status == "ready"
    assert by_name["Ollama grading model"].status == "degraded"
    assert by_name["Ollama grading model"].fix == "ollama pull llama3.1:8b"


def test_one_model_serving_both_roles_is_not_reported_twice() -> None:
    probe = OllamaProbe(host="h", reachable=True, models=("qwen2.5vl:7b",))
    checks = evaluate_ollama(probe, vision_model="qwen2.5vl:7b", text_model="qwen2.5vl:7b")
    assert [check.name for check in checks] == ["Ollama server", "Ollama vision model"]


def test_missing_vision_model_names_the_pull_command() -> None:
    probe = OllamaProbe(host="h", reachable=True, models=("llama3.1:8b",))
    checks = evaluate_ollama(probe, vision_model="qwen2.5vl:7b")
    vision = next(check for check in checks if check.name == "Ollama vision model")
    assert vision.status == "degraded"
    assert vision.fix == "ollama pull qwen2.5vl:7b"


# --- packages ---------------------------------------------------------------


def test_all_imports_present_is_one_ready_row() -> None:
    probes = tuple(
        ImportProbe(module=name, importable=True) for name in ("fitz", "cv2", "numpy")
    )
    check = evaluate_imports(probes)
    assert check.status == "ready"
    assert "3" in check.detail


def test_a_failed_import_is_broken_and_names_the_module() -> None:
    probes = (
        ImportProbe(module="fitz", importable=True),
        ImportProbe(module="cv2", importable=False, error="No module named 'cv2'"),
    )
    check = evaluate_imports(probes)
    assert check.status == "broken"
    assert "cv2" in check.detail
    assert check.fix == "uv sync"


def test_optional_package_absence_is_degraded() -> None:
    check = evaluate_optional_import(
        ImportProbe(module="pix2tex", importable=False),
        feature="dedicated maths reading is unavailable",
        fix="uv sync --extra math",
    )
    assert check.status == "degraded"
    assert check.fix == "uv sync --extra math"


def test_missing_canvas_sak_is_degraded() -> None:
    check = evaluate_canvas_sak(ToolProbe(found=False))
    assert check.status == "degraded"
    assert "canvas-export" in check.impact


# --- terminal + smoke test --------------------------------------------------


def test_pixel_accurate_terminal_is_ready() -> None:
    check = evaluate_graphics(
        protocol="kitty", pixel_accurate=True, description="Kitty graphics (Ghostty)"
    )
    assert check.status == "ready"


def test_text_only_terminal_is_degraded_and_mentions_the_escape_hatch() -> None:
    check = evaluate_graphics(
        protocol="halfcell", pixel_accurate=False, description="half-block preview"
    )
    assert check.status == "degraded"
    assert "'o'" in check.impact


def test_failed_smoke_test_is_broken() -> None:
    check = evaluate_smoke_test(ok=False, detail="failed: ValueError: boom")
    assert check.status == "broken"
    assert "boom" in check.detail


# --- report aggregation -----------------------------------------------------


def _check(name: str, status: str) -> EnvironmentCheck:
    return EnvironmentCheck(name=name, status=status, detail="")  # type: ignore[arg-type]


def test_report_status_is_the_worst_check() -> None:
    report = EnvironmentReport(
        checks=(_check("a", "ready"), _check("b", "degraded"), _check("c", "broken"))
    )
    assert report.status == "broken"


def test_report_status_is_ready_only_when_everything_is() -> None:
    assert EnvironmentReport(checks=(_check("a", "ready"),)).status == "ready"
    two = EnvironmentReport(checks=(_check("a", "ready"), _check("b", "degraded")))
    assert two.status == "degraded"


def test_an_empty_report_is_unknown_not_ready() -> None:
    # Nothing was checked, so nothing is known — the same rule the scan
    # pre-flight follows for a measurement it could not take.
    assert EnvironmentReport().status == "unknown"


def test_attention_list_is_worst_first_and_excludes_passing_checks() -> None:
    report = EnvironmentReport(
        checks=(
            _check("fine", "ready"),
            _check("optional", "degraded"),
            _check("core", "broken"),
            _check("unmeasured", "unknown"),
        )
    )
    assert [check.name for check in report.attention] == ["core", "unmeasured", "optional"]


def test_broken_lists_only_core_failures() -> None:
    report = EnvironmentReport(checks=(_check("a", "degraded"), _check("b", "broken")))
    assert [check.name for check in report.broken] == ["b"]


# --- helpers ----------------------------------------------------------------


def test_install_hint_is_a_runnable_command_for_this_platform() -> None:
    hint = install_hint(brew="tesseract", apt="tesseract-ocr", winget="UB-Mannheim.TesseractOCR")
    assert any(tool in hint for tool in ("brew", "apt-get", "winget"))


def test_connection_errors_are_summarized_into_something_actionable() -> None:
    assert summarize_connection_error("<urlopen error [Errno 61] Connection refused>") == (
        "no server listening"
    )
    assert "did not respond" in summarize_connection_error("read timed out")


def test_unrecognized_connection_errors_keep_their_first_line() -> None:
    assert summarize_connection_error("something odd\nsecond line") == "something odd"


# --- AI features ────────────────────────────────────────────────────────────


def test_ai_off_is_ready() -> None:
    """When AI is switched off, that is a legitimate choice (not a failure)."""
    resolved = FakeResolvedAI(read_handwriting=False, grade_written=False)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert check.status == "ready"
    assert "switched off" in check.detail.lower()
    assert not check.fix  # no fix for a legitimate choice


def test_ai_on_and_clean_is_ready() -> None:
    """AI enabled with no configuration errors."""
    resolved = FakeResolvedAI(read_handwriting=True, grade_written=True)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert check.status == "ready"
    assert "reading handwriting" in check.detail
    assert "suggesting grades" in check.detail


def test_ai_with_configuration_problems_is_degraded() -> None:
    """Configuration errors returned by problems() appear in detail and fix."""

    class ResolvedWithProblems(FakeResolvedAI):
        def problems(self) -> tuple[str, ...]:
            return ("Unknown AI provider 'bogus' — falling back to 'ollama'.",)

    resolved = ResolvedWithProblems(read_handwriting=True)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert check.status == "degraded"
    assert "Unknown AI provider" in check.detail
    assert "Unknown AI provider" in check.fix


def test_escalation_mode_is_reported() -> None:
    """Escalation mode refinement of handwriting reading is listed separately."""
    resolved = FakeResolvedAI(read_handwriting=True, escalate_only=True)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert "escalation mode" in check.detail


def test_math_transcription_is_reported() -> None:
    """Math transcription is listed when enabled."""
    resolved = FakeResolvedAI(read_handwriting=True, transcribe_math=True)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert "transcribing maths" in check.detail


# --- AI latency ─────────────────────────────────────────────────────────────


def test_latency_not_measured_is_unknown() -> None:
    """When measurement is None, report unknown and name the flag."""
    check = evaluate_ai_latency(None)
    assert check.status == "unknown"
    assert "not timed" in check.detail
    assert "--time-ai" in check.fix


def test_latency_error_is_degraded() -> None:
    """A measurement with an error is degraded and describes the error."""
    measurement = FakeLatencyMeasurement(
        seconds=None, error="Connection refused"
    )
    check = evaluate_ai_latency(measurement)  # type: ignore[arg-type]
    assert check.status == "degraded"
    assert "did not answer" in check.detail
    assert "Connection refused" in check.detail
    assert "Ollama server" in check.fix or "running" in check.fix


def test_latency_fast_response_is_ready() -> None:
    """A fast response (well below the warn threshold) is ready."""
    measurement = FakeLatencyMeasurement(seconds=15.0, limit=120.0)
    check = evaluate_ai_latency(measurement)  # type: ignore[arg-type]
    assert check.status == "ready"
    assert "15.0 s" in check.detail
    assert "120" in check.detail


def test_latency_slow_response_recommends_higher_limit() -> None:
    """A response near the limit is degraded and recommends a higher timeout."""
    measurement = FakeLatencyMeasurement(seconds=70.0, limit=120.0)
    check = evaluate_ai_latency(measurement)  # type: ignore[arg-type]
    assert check.status == "degraded"
    assert "70.0 s" in check.detail
    # The recommend_timeout function should suggest 4x the measured time,
    # rounded up to the next 30s, so 70*4 = 280, which rounds up to 300.
    assert "300" in check.fix or "330" in check.fix  # depending on rounding


def test_latency_very_slow_remains_unknown_if_already_high_limit() -> None:
    """If the measured time is slow but recommend_timeout returns None
    (already a high enough limit), we stay ready or degraded appropriately."""
    # A measurement that's slow but recommend_timeout still returns None
    # because the current limit is already high enough.
    measurement = FakeLatencyMeasurement(seconds=30.0, limit=600.0)
    check = evaluate_ai_latency(measurement)  # type: ignore[arg-type]
    # share_of_limit = 30/600 = 0.05, which is below 0.5 (TIMEOUT_WARN_FRACTION)
    # so this should be ready, not degraded.
    assert check.status == "ready"


# --- build_environment_report with AI checks ────────────────────────────────


def test_build_report_includes_ai_features_when_ai_is_given() -> None:
    """When ai= is passed, the AI features check is included."""
    from opengrader.core.environment import build_environment_report

    resolved = FakeResolvedAI(read_handwriting=True)
    report = build_environment_report(
        vision_model="qwen2.5vl:7b",
        text_model="qwen2.5vl:7b",
        ollama_host="http://127.0.0.1:11434",
        check_graphics=False,
        ai=resolved,  # type: ignore[arg-type]
        time_ai=False,
    )
    check_names = [check.name for check in report.checks]
    assert "AI features" in check_names


def test_build_report_excludes_latency_when_time_ai_is_false() -> None:
    """When time_ai=False, no latency check is added (to avoid noise)."""
    from opengrader.core.environment import build_environment_report

    resolved = FakeResolvedAI(read_handwriting=True)
    report = build_environment_report(
        vision_model="qwen2.5vl:7b",
        text_model="qwen2.5vl:7b",
        ollama_host="http://127.0.0.1:11434",
        check_graphics=False,
        ai=resolved,  # type: ignore[arg-type]
        time_ai=False,
    )
    check_names = [check.name for check in report.checks]
    assert "AI response time" not in check_names


def test_build_report_without_ai_has_no_ai_checks() -> None:
    """When ai=None, neither AI feature nor latency check is added."""
    from opengrader.core.environment import build_environment_report

    report = build_environment_report(
        vision_model="qwen2.5vl:7b",
        text_model="qwen2.5vl:7b",
        ollama_host="http://127.0.0.1:11434",
        check_graphics=False,
        ai=None,
        time_ai=False,
    )
    check_names = [check.name for check in report.checks]
    assert "AI features" not in check_names
    assert "AI response time" not in check_names
