from __future__ import annotations

import pytest
from dataclasses import dataclass, field, replace

from opengrader.core import app_config
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
    evaluate_provider,
    evaluate_python,
    evaluate_smoke_test,
    evaluate_tesseract,
    install_hint,
    summarize_connection_error,
)


@dataclass(frozen=True)
class FakeProviderSpec:
    name: str = "ollama"
    label: str = "Ollama (local)"
    requires_api_key: bool = False
    api_key_env: str = ""
    credential_command: tuple[str, ...] = ()
    supports_model_listing: bool = True


@dataclass(frozen=True)
class FakeResolvedAI:
    read_handwriting: bool = False
    escalate_only: bool = False
    transcribe_math: bool = False
    grade_written: bool = False
    read_identity_locally: bool = True
    kind: str = "local"
    host: str = "http://127.0.0.1:11434"
    vision_model: str = "qwen2.5vl:7b"
    text_model: str = "llama3.1:8b"
    provider: FakeProviderSpec = field(default_factory=FakeProviderSpec)
    api_key: str | None = None
    cloud_allowed: bool = True
    cloud_consent_required: bool = False
    credential_consent_required: bool = False

    @property
    def uses_ai(self) -> bool:
        return self.read_handwriting or self.grade_written

    def problems(self) -> tuple[str, ...]:
        return ()


@dataclass(frozen=True)
class FakeLatencyMeasurement:
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


def test_python_312_is_ready() -> None:
    check = evaluate_python((3, 12, 4))
    assert check.status == "ready"
    assert "3.12.4" in check.detail


def test_other_python_versions_are_broken_and_name_the_fix() -> None:
    for version in ((3, 11, 9), (3, 13, 0)):
        check = evaluate_python(version)
        assert check.status == "broken"
        assert "uv python install 3.12" in check.fix


def test_missing_tesseract_is_broken() -> None:
    check = evaluate_tesseract(ToolProbe(found=False))
    assert check.status == "broken"
    assert check.fix


def test_recent_tesseract_is_ready() -> None:
    probe = ToolProbe(
        found=True, version=(5, 5, 2), version_text="5.5.2", path="/usr/local/bin/tesseract"
    )
    check = evaluate_tesseract(probe)
    assert check.status == "ready"
    assert "5.5.2" in check.detail
    assert "/usr/local/bin/tesseract" in check.detail


def test_old_tesseract_is_degraded_not_broken() -> None:
    probe = ToolProbe(found=True, version=(3, 5, 1), version_text="3.5.1")
    check = evaluate_tesseract(probe)
    assert check.status == "degraded"
    assert "orientation" in check.impact.lower()


def test_unrunnable_tesseract_is_unknown_never_ready() -> None:
    probe = ToolProbe(found=False, error="permission denied")
    check = evaluate_tesseract(probe)
    assert check.status == "unknown"
    assert "permission denied" in check.detail


def test_unreachable_ollama_is_only_degraded() -> None:
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


def test_ai_off_is_ready() -> None:
    resolved = FakeResolvedAI(read_handwriting=False, grade_written=False)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert check.status == "ready"
    assert "switched off" in check.detail.lower()
    assert not check.fix


def test_ai_on_and_clean_is_ready() -> None:
    resolved = FakeResolvedAI(read_handwriting=True, grade_written=True)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert check.status == "ready"
    assert "reading handwriting" in check.detail
    assert "suggesting grades" in check.detail


def test_ai_with_configuration_problems_is_degraded() -> None:
    class ResolvedWithProblems(FakeResolvedAI):
        def problems(self) -> tuple[str, ...]:
            return ("Unknown AI provider 'bogus' — falling back to 'ollama'.",)

    resolved = ResolvedWithProblems(read_handwriting=True)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert check.status == "degraded"
    assert "Unknown AI provider" in check.detail
    assert "Unknown AI provider" in check.fix


def test_escalation_mode_is_reported() -> None:
    resolved = FakeResolvedAI(read_handwriting=True, escalate_only=True)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert "escalation mode" in check.detail


def test_math_transcription_is_reported() -> None:
    resolved = FakeResolvedAI(read_handwriting=True, transcribe_math=True)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert "transcribing maths" in check.detail


def test_read_identity_locally_off_is_noted_even_with_ai_off() -> None:
    resolved = FakeResolvedAI(read_identity_locally=False)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert check.status == "ready"
    assert "Tesseract only" in check.detail


def test_read_identity_locally_off_is_noted_alongside_other_features() -> None:
    resolved = FakeResolvedAI(read_handwriting=True, read_identity_locally=False)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert "reading handwriting" in check.detail
    assert "Tesseract only" in check.detail


def test_read_identity_locally_on_by_default_adds_no_note() -> None:
    resolved = FakeResolvedAI(read_handwriting=True)
    check = evaluate_ai_features(resolved)  # type: ignore[arg-type]
    assert "Tesseract" not in check.detail


def test_latency_not_measured_is_unknown() -> None:
    check = evaluate_ai_latency(None)
    assert check.status == "unknown"
    assert "not timed" in check.detail
    assert "--time-ai" in check.fix


def test_latency_error_is_degraded() -> None:
    measurement = FakeLatencyMeasurement(
        seconds=None, error="Connection refused"
    )
    check = evaluate_ai_latency(measurement)  # type: ignore[arg-type]
    assert check.status == "degraded"
    assert "did not answer" in check.detail
    assert "Connection refused" in check.detail
    assert "Ollama server" in check.fix or "running" in check.fix


def test_latency_fast_response_is_ready() -> None:
    measurement = FakeLatencyMeasurement(seconds=15.0, limit=120.0)
    check = evaluate_ai_latency(measurement)  # type: ignore[arg-type]
    assert check.status == "ready"
    assert "15.0 s" in check.detail
    assert "120" in check.detail


def test_latency_slow_response_recommends_higher_limit() -> None:
    measurement = FakeLatencyMeasurement(seconds=70.0, limit=120.0)
    check = evaluate_ai_latency(measurement)  # type: ignore[arg-type]
    assert check.status == "degraded"
    assert "70.0 s" in check.detail
    assert "300" in check.fix or "330" in check.fix


def test_latency_very_slow_remains_unknown_if_already_high_limit() -> None:
    measurement = FakeLatencyMeasurement(seconds=30.0, limit=600.0)
    check = evaluate_ai_latency(measurement)  # type: ignore[arg-type]
    assert check.status == "ready"


def test_build_report_includes_ai_features_when_ai_is_given() -> None:
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


# ---------------------------------------------------------------------------
# evaluate_provider branches on kind, cloud never probes Ollama
# ---------------------------------------------------------------------------


def _cloud(**overrides) -> FakeResolvedAI:
    defaults = dict(
        kind="cloud",
        host="https://ai.example.edu/v1",
        provider=FakeProviderSpec(
            name="acme",
            label="Acme Grading Service",
            requires_api_key=True,
            api_key_env="ACME_API_KEY",
        ),
        api_key="sk-real-secret-do-not-leak",
        cloud_allowed=True,
        cloud_consent_required=False,
        credential_consent_required=False,
    )
    defaults.update(overrides)
    return FakeResolvedAI(**defaults)


def test_kill_switch_is_reported_clearly_not_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args, **_kwargs):
        raise AssertionError("must not touch the network under the kill switch")

    monkeypatch.setattr(app_config, "available_models", _boom)

    checks = evaluate_provider(_cloud(cloud_allowed=False))  # type: ignore[arg-type]
    assert len(checks) == 1
    check = checks[0]
    assert check.status == "degraded"
    assert check.status != "broken"
    assert "OPENGRADER_AI_ALLOW_CLOUD" in check.detail or "OPENGRADER_AI_ALLOW_CLOUD" in check.fix


def test_cloud_without_consent_is_a_consent_needed_check_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args, **_kwargs):
        raise AssertionError("must not probe an unconsented cloud provider")

    monkeypatch.setattr(app_config, "available_models", _boom)

    checks = evaluate_provider(_cloud(cloud_consent_required=True))  # type: ignore[arg-type]
    assert len(checks) == 1
    check = checks[0]
    assert check.status == "degraded"
    assert "consent" in check.detail.lower() or "consent" in check.fix.lower()
    assert "AI setup" in check.fix or "AI setup" in check.detail


def test_cloud_credential_command_not_approved_is_a_consent_needed_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args, **_kwargs):
        raise AssertionError("must not probe before the credential command is approved")

    monkeypatch.setattr(app_config, "available_models", _boom)

    checks = evaluate_provider(_cloud(credential_consent_required=True))  # type: ignore[arg-type]
    assert len(checks) == 1
    check = checks[0]
    assert check.status == "degraded"
    assert "approved" in check.detail.lower() or "approved" in check.fix.lower()


def test_cloud_without_a_key_names_the_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args, **_kwargs):
        raise AssertionError("must not probe without a key")

    monkeypatch.setattr(app_config, "available_models", _boom)

    checks = evaluate_provider(_cloud(api_key=None))  # type: ignore[arg-type]
    assert len(checks) == 1
    check = checks[0]
    assert check.status == "degraded"
    assert "ACME_API_KEY" in check.detail or "ACME_API_KEY" in check.fix


def test_cloud_without_model_listing_says_what_was_verified_not_a_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*_args, **_kwargs):
        raise AssertionError("a provider that cannot list models must not be probed")

    monkeypatch.setattr(app_config, "available_models", _boom)

    resolved = _cloud(provider=FakeProviderSpec(
        name="acme",
        label="Acme Grading Service",
        requires_api_key=True,
        api_key_env="ACME_API_KEY",
        supports_model_listing=False,
    ))
    checks = evaluate_provider(resolved)  # type: ignore[arg-type]
    assert len(checks) == 1
    check = checks[0]
    assert check.status == "ready"
    assert "does not list models" in check.detail
    assert "Connected" not in check.detail


def test_cloud_model_listing_failure_is_degraded_and_never_leaks_the_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail(_resolved):
        raise RuntimeError("<urlopen error [Errno 61] Connection refused>")

    monkeypatch.setattr(app_config, "available_models", _fail)

    resolved = _cloud()
    checks = evaluate_provider(resolved)  # type: ignore[arg-type]
    assert len(checks) == 1
    check = checks[0]
    assert check.status == "degraded"
    for text in (check.detail, check.impact, check.fix):
        assert resolved.api_key not in text


def test_cloud_ready_when_consented_keyed_and_listing_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_config, "available_models", lambda _resolved: ["model-a", "model-b"])

    checks = evaluate_provider(_cloud())  # type: ignore[arg-type]
    assert len(checks) == 1
    check = checks[0]
    assert check.status == "ready"
    assert "2" in check.detail


def test_evaluate_provider_never_calls_probe_ollama_for_a_cloud_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import opengrader.core.environment as environment

    def _boom(*_args, **_kwargs):
        raise AssertionError("evaluate_provider must not probe Ollama for a cloud provider")

    monkeypatch.setattr(environment, "probe_ollama", _boom)
    monkeypatch.setattr(app_config, "available_models", lambda _resolved: [])

    evaluate_provider(_cloud())  # type: ignore[arg-type]


def test_the_exit_criterion_a_ready_cloud_provider_reports_ready_without_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cloud provider, fully configured, with NO Ollama installed reports
    `ready`, not `broken` — and never asks whether Ollama is installed."""
    import opengrader.core.environment as environment
    from opengrader.core.environment import build_environment_report

    def _boom(*_args, **_kwargs):
        raise AssertionError("a cloud-configured doctor run must never probe Ollama")

    monkeypatch.setattr(environment, "probe_ollama", _boom)
    monkeypatch.setattr(environment, "probe_python", lambda: (3, 12, 4))
    monkeypatch.setattr(
        environment, "probe_import", lambda module: ImportProbe(module=module, importable=True)
    )
    monkeypatch.setattr(
        environment,
        "probe_tesseract",
        lambda: ToolProbe(found=True, version=(5, 5, 0), version_text="5.5.0", path="/usr/bin/tesseract"),
    )
    monkeypatch.setattr(
        environment,
        "probe_optional_import",
        lambda module: ImportProbe(module=module, importable=True),
    )
    monkeypatch.setattr(environment, "encryption_available", lambda: True)
    monkeypatch.setattr(
        environment, "probe_canvas_sak", lambda: ToolProbe(found=True, path="/usr/local/bin/canvas-sak")
    )
    monkeypatch.setattr(app_config, "available_models", lambda _resolved: ["model-a"])

    ai = _cloud()
    report = build_environment_report(
        vision_model="unused",
        text_model="unused",
        ollama_host="unused",
        check_graphics=False,
        smoke_test=False,
        ai=ai,  # type: ignore[arg-type]
        time_ai=False,
    )

    assert report.status == "ready"
    assert report.broken == ()


def test_local_provider_probes_the_ollama_host_argument_not_the_resolved_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: `build_environment_report`'s local path must keep using its
    own `ollama_host`/`vision_model`/`text_model` arguments (what doctor's
    `--ollama-host`/`--ollama-model`/`--llm-model` flags feed it), exactly as
    before the cloud branch was added — not fields read off the resolved AI."""
    import opengrader.core.environment as environment
    from opengrader.core.environment import build_environment_report

    seen: dict = {}

    def fake_probe_ollama(host: str, **kwargs) -> OllamaProbe:
        seen["host"] = host
        return OllamaProbe(host=host, reachable=True, models=())

    monkeypatch.setattr(environment, "probe_ollama", fake_probe_ollama)

    resolved = FakeResolvedAI(
        kind="local",
        host="http://this-should-not-be-used:11434",
        vision_model="resolved-vision-model",
        text_model="resolved-text-model",
    )

    report = build_environment_report(
        vision_model="argument-vision-model",
        text_model="argument-text-model",
        ollama_host="http://argument-host:11434",
        check_graphics=False,
        ai=resolved,  # type: ignore[arg-type]
        time_ai=False,
    )

    assert seen["host"] == "http://argument-host:11434"
    by_name = {check.name: check for check in report.checks}
    assert by_name["Ollama vision model"].fix == "ollama pull argument-vision-model"
