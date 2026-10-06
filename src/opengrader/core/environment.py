from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from opengrader.core.crypto import encryption_available

if TYPE_CHECKING:
    from opengrader.core.app_config import LatencyMeasurement, ResolvedAI

CheckStatus = Literal["ready", "degraded", "broken", "unknown"]

_STATUS_ORDER: dict[CheckStatus, int] = {
    "broken": 0,
    "unknown": 1,
    "degraded": 2,
    "ready": 3,
}

MINIMUM_TESSERACT_VERSION = (4, 0)
TESSERACT_CHECK_NAME = "Tesseract OCR"


@dataclass(frozen=True)
class EnvironmentCheck:
    name: str
    status: CheckStatus
    detail: str
    impact: str = ""
    fix: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.status != "ready"


@dataclass(frozen=True)
class EnvironmentReport:
    checks: tuple[EnvironmentCheck, ...] = field(default_factory=tuple)

    @property
    def status(self) -> CheckStatus:
        if not self.checks:
            return "unknown"
        return min(self.checks, key=lambda check: _STATUS_ORDER[check.status]).status

    @property
    def broken(self) -> tuple[EnvironmentCheck, ...]:
        return tuple(check for check in self.checks if check.status == "broken")

    @property
    def attention(self) -> tuple[EnvironmentCheck, ...]:
        return tuple(
            sorted(
                (check for check in self.checks if check.needs_attention),
                key=lambda check: _STATUS_ORDER[check.status],
            )
        )


@dataclass(frozen=True)
class ToolProbe:
    found: bool = False
    version: tuple[int, ...] | None = None
    version_text: str | None = None
    path: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ImportProbe:
    module: str
    importable: bool
    version_text: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class OllamaProbe:
    host: str
    reachable: bool = False
    models: tuple[str, ...] = ()
    error: str | None = None


def install_hint(*, brew: str, apt: str, winget: str) -> str:
    if sys.platform == "darwin":
        return f"brew install {brew}"
    if sys.platform.startswith("win"):
        return f"winget install {winget}"
    return f"sudo apt-get install {apt}  (or your distribution's equivalent)"


def evaluate_python(version: tuple[int, ...]) -> EnvironmentCheck:
    text = ".".join(str(part) for part in version[:3])
    if version[:2] == (3, 12):
        return EnvironmentCheck(
            name="Python", status="ready", detail=f"{text} (3.12 as required)"
        )
    return EnvironmentCheck(
        name="Python",
        status="broken",
        detail=f"{text} — Opengrader pins >=3.12,<3.13",
        impact="Dependencies are resolved for 3.12; another version may fail to import.",
        fix="uv python install 3.12 && uv sync",
    )


def evaluate_tesseract(probe: ToolProbe) -> EnvironmentCheck:
    fix = install_hint(brew="tesseract", apt="tesseract-ocr", winget="UB-Mannheim.TesseractOCR")
    if probe.error is not None and not probe.found:
        return EnvironmentCheck(
            name=TESSERACT_CHECK_NAME,
            status="unknown",
            detail=f"could not be checked: {probe.error}",
            impact="Scan extraction, boundary detection and the scan pre-flight may not work.",
            fix=fix,
        )
    if not probe.found:
        return EnvironmentCheck(
            name=TESSERACT_CHECK_NAME,
            status="broken",
            detail="not installed",
            impact="Scanned exams extract to empty answers; scan pre-flight cannot check rotation.",
            fix=fix,
        )
    where = f" at {probe.path}" if probe.path else ""
    if probe.version is not None and probe.version[:2] < MINIMUM_TESSERACT_VERSION:
        minimum = ".".join(str(part) for part in MINIMUM_TESSERACT_VERSION)
        return EnvironmentCheck(
            name=TESSERACT_CHECK_NAME,
            status="degraded",
            detail=f"{probe.version_text}{where} — older than {minimum}",
            impact="Page-orientation detection (the scan pre-flight's rotation check) needs 4.0+.",
            fix=fix,
        )
    return EnvironmentCheck(
        name=TESSERACT_CHECK_NAME,
        status="ready",
        detail=f"{probe.version_text or 'installed'}{where}",
    )


def evaluate_ollama(
    probe: OllamaProbe,
    *,
    vision_model: str,
    text_model: str | None = None,
) -> tuple[EnvironmentCheck, ...]:
    if not probe.reachable:
        reason = probe.error or "no server listening"
        return (
            EnvironmentCheck(
                name="Ollama server",
                status="degraded",
                detail=f"not reachable at {probe.host} ({reason})",
                impact="Optional: handwriting transcription and LLM grading are unavailable.",
                fix="ollama serve   (install from https://ollama.com if missing)",
            ),
        )

    checks = [
        EnvironmentCheck(
            name="Ollama server",
            status="ready",
            detail=f"{len(probe.models)} model(s) installed at {probe.host}",
        )
    ]

    wanted: list[tuple[str, str, str]] = [
        (
            "Ollama vision model",
            vision_model,
            "Optional: handwriting/name/ID transcription falls back to Tesseract only.",
        )
    ]
    if text_model and text_model != vision_model:
        wanted.append(
            (
                "Ollama grading model",
                text_model,
                "Optional: `automated-grading --llm` cannot grade free responses.",
            )
        )

    for name, model, impact in wanted:
        if model in probe.models:
            checks.append(
                EnvironmentCheck(name=name, status="ready", detail=f"{model} installed")
            )
        else:
            checks.append(
                EnvironmentCheck(
                    name=name,
                    status="degraded",
                    detail=f"{model} not installed",
                    impact=impact,
                    fix=f"ollama pull {model}",
                )
            )
    return tuple(checks)


def evaluate_imports(probes: tuple[ImportProbe, ...]) -> EnvironmentCheck:
    missing = [probe for probe in probes if not probe.importable]
    if not missing:
        return EnvironmentCheck(
            name="Python packages",
            status="ready",
            detail=f"all {len(probes)} runtime packages import",
        )
    names = ", ".join(probe.module for probe in missing)
    return EnvironmentCheck(
        name="Python packages",
        status="broken",
        detail=f"cannot import: {names}",
        impact="The install is incomplete; most commands will fail at startup.",
        fix="uv sync",
    )


def evaluate_optional_import(
    probe: ImportProbe, *, feature: str, fix: str
) -> EnvironmentCheck:
    if probe.importable:
        return EnvironmentCheck(
            name=probe.module,
            status="ready",
            detail=probe.version_text or "installed",
        )
    return EnvironmentCheck(
        name=probe.module,
        status="degraded",
        detail="not installed (optional)",
        impact=f"Optional: {feature}",
        fix=fix,
    )


def evaluate_encryption(*, available: bool) -> EnvironmentCheck:
    if available:
        return EnvironmentCheck(
            name="Encryption",
            status="ready",
            detail="available for grader packets and backups",
        )
    return EnvironmentCheck(
        name="Encryption",
        status="broken",
        detail="the 'cryptography' package is missing",
        impact="Grader packets and backups cannot be protected with a passphrase.",
        fix="uv sync",
    )


def evaluate_canvas_sak(probe: ToolProbe) -> EnvironmentCheck:
    if probe.found:
        where = f" at {probe.path}" if probe.path else ""
        return EnvironmentCheck(
            name="canvas-sak", status="ready", detail=f"installed{where}"
        )
    return EnvironmentCheck(
        name="canvas-sak",
        status="degraded",
        detail="not on PATH (optional)",
        impact="Optional: `canvas-export` cannot push grades to Canvas.",
        fix="uv sync   (then run: canvas-sak help-me-setup)",
    )


def evaluate_graphics(
    *, protocol: str, pixel_accurate: bool, description: str
) -> EnvironmentCheck:
    if pixel_accurate:
        return EnvironmentCheck(
            name="Terminal graphics",
            status="ready",
            detail=f"{description} — answer crops render in the terminal",
        )
    return EnvironmentCheck(
        name="Terminal graphics",
        status="degraded",
        detail=f"{description} — low-fidelity preview only",
        impact="Answer crops show as coarse blocks; press 'o' to open one in an image viewer.",
        fix="Use a graphics-capable terminal (Ghostty, kitty, iTerm2, WezTerm), "
        "or force one with: tui --graphics kitty",
    )


def evaluate_smoke_test(*, ok: bool, detail: str) -> EnvironmentCheck:
    if ok:
        return EnvironmentCheck(
            name="Pipeline smoke test", status="ready", detail=detail
        )
    return EnvironmentCheck(
        name="Pipeline smoke test",
        status="broken",
        detail=detail,
        impact="Opengrader cannot turn an answer key into a printable template on this machine.",
        fix="uv sync   (then re-run: uv run doctor --smoke)",
    )


def evaluate_provider(resolved: "ResolvedAI") -> tuple[EnvironmentCheck, ...]:
    """Pre-flight for the configured AI provider.

    `build_environment_report` only calls this for a cloud-kind `ResolvedAI`;
    a local one keeps probing Ollama directly with the caller's own
    host/model arguments (see the branch there) so that path is unchanged
    byte-for-byte. This function therefore never touches Ollama.

    For a cloud provider, every guard below is checked BEFORE any network
    call — an unconsented, kill-switched or unkeyed provider must never be
    probed. Only once every guard passes does it ask the provider to list
    its models, or, for a provider that cannot list models, say only that a
    credential resolves (never that a connection was verified, because it
    was not).
    """
    from opengrader.core import app_config

    if resolved.kind != "cloud":
        # Defensive fallback for a direct call with a local ResolvedAI; the
        # real caller (build_environment_report) never takes this path.
        return evaluate_ollama(
            probe_ollama(resolved.host),
            vision_model=resolved.vision_model,
            text_model=resolved.text_model,
        )

    provider = resolved.provider
    name = "AI provider"
    consent_fix = f"opengrader-providers consent {provider.name}   (or Helper Tools → AI setup)"

    if not resolved.cloud_allowed:
        return (
            EnvironmentCheck(
                name=name,
                status="degraded",
                detail=(
                    f"{provider.label} is a cloud service, but "
                    f"{app_config.ALLOW_CLOUD_ENV}=0 forbids cloud AI providers on "
                    "this machine."
                ),
                impact="AI features fall back to the local provider; nothing is sent off this machine.",
                fix=f"Unset {app_config.ALLOW_CLOUD_ENV}, or choose a local provider in AI setup.",
            ),
        )

    if resolved.cloud_consent_required:
        return (
            EnvironmentCheck(
                name=name,
                status="degraded",
                detail=(
                    f"{provider.label} is a cloud service at "
                    f"{app_config.consent.hostname_of(resolved.host)}; you have not "
                    "consented to send student work to it. Nothing has been sent."
                ),
                impact="AI features are unavailable until consent is given.",
                fix=consent_fix,
            ),
        )

    if resolved.credential_consent_required:
        return (
            EnvironmentCheck(
                name=name,
                status="degraded",
                detail=f"{provider.label}'s credential command has not been approved.",
                impact="AI features are unavailable until the command is approved.",
                fix=consent_fix,
            ),
        )

    if provider.requires_api_key and not resolved.api_key:
        return (
            EnvironmentCheck(
                name=name,
                status="degraded",
                detail=f"{provider.label} needs an API key.",
                impact="AI features are unavailable until a key is set.",
                fix=f"{app_config.missing_key_help(provider)} Then re-run doctor.",
            ),
        )

    if not provider.supports_model_listing:
        return (
            EnvironmentCheck(
                name=name,
                status="ready",
                detail=(
                    f"{provider.label}: credential resolves; connection not tested "
                    "— this service does not list models."
                ),
            ),
        )

    try:
        models = app_config.available_models(resolved)
    except Exception as exc:  # provider client errors are provider-specific, and already redacted
        return (
            EnvironmentCheck(
                name=name,
                status="degraded",
                detail=f"{provider.label}: could not list models ({summarize_connection_error(str(exc))}).",
                impact="AI features may fail at runtime.",
                fix="Check the network connection and the API key, then re-run doctor.",
            ),
        )

    return (
        EnvironmentCheck(
            name=name,
            status="ready",
            detail=f"{provider.label}: connected, {len(models)} model(s) available.",
        ),
    )


def evaluate_ai_features(resolved: "ResolvedAI") -> EnvironmentCheck:
    # Reading a name/ID with a local model is independent of the toggles
    # below (it happens whenever a vision reader exists, not only when
    # handwriting reading or AI grading are on), so it is noted here even
    # when nothing else about the AI setup is worth reporting.
    identity_note = (
        ""
        if resolved.read_identity_locally
        else " (student names/IDs are read by Tesseract only; local AI reading of them is turned off)"
    )

    if not resolved.uses_ai:
        return EnvironmentCheck(
            name="AI features",
            status="ready",
            detail="nothing — the AI is switched off (a legitimate choice)" + identity_note,
        )

    features: list[str] = []
    if resolved.read_handwriting:
        features.append("reading handwriting")
    if resolved.escalate_only:
        features.append("using escalation mode for handwriting")
    if resolved.transcribe_math:
        features.append("transcribing maths as LaTeX")
    if resolved.grade_written:
        features.append("suggesting grades for written answers")

    problems = resolved.problems()
    if problems:
        detail = "\n".join(problems)
        return EnvironmentCheck(
            name="AI features",
            status="degraded",
            detail=detail,
            impact="AI features are enabled but misconfigured.",
            fix=problems[0],
        )

    detail = ", ".join(features) if features else "nothing — the AI is switched off"
    return EnvironmentCheck(
        name="AI features",
        status="ready",
        detail=detail + identity_note,
    )


def evaluate_ai_latency(measurement: "LatencyMeasurement | None") -> EnvironmentCheck:
    from opengrader.core import app_config

    if measurement is None:
        return EnvironmentCheck(
            name="AI response time",
            status="unknown",
            detail="not timed",
            impact="Response-time checks require a measurement.",
            fix="Run 'doctor' again with the --time-ai flag to measure the handwriting model.",
        )

    if not measurement.ok:
        return EnvironmentCheck(
            name="AI response time",
            status="degraded",
            detail=measurement.describe(),
            impact="AI features may time out or fail during grading.",
            fix="Run 'doctor' again with the Ollama server running, or check the model name in AI setup.",
        )

    share = measurement.share_of_limit
    if share is not None and share >= app_config.TIMEOUT_WARN_FRACTION:
        recommended = app_config.recommend_timeout(measurement.seconds or 0, measurement.limit)
        if recommended is not None:
            return EnvironmentCheck(
                name="AI response time",
                status="degraded",
                detail=measurement.describe(),
                impact="A longer answer or a second concurrent request risks timing out.",
                fix=f"Raise 'How long to wait for the AI' in AI setup to {recommended:.0f} s — a request that takes most of the limit will time out on a longer answer, and a timed-out read shows up as an empty answer, not as an error.",
            )

    return EnvironmentCheck(
        name="AI response time",
        status="ready",
        detail=measurement.describe(),
    )


def probe_python() -> tuple[int, ...]:
    return sys.version_info[:3]


def probe_tesseract() -> ToolProbe:
    path = shutil.which("tesseract")
    try:
        import pytesseract
    except Exception as exc:  # pragma: no cover
        return ToolProbe(found=path is not None, path=path, error=str(exc))

    try:
        raw = pytesseract.get_tesseract_version()
    except Exception as exc:
        if path is None:
            return ToolProbe(found=False)
        return ToolProbe(found=True, path=path, error=str(exc))

    version_text = str(raw)
    return ToolProbe(
        found=True,
        version=_parse_version(version_text),
        version_text=version_text,
        path=path,
    )


def _parse_version(text: str) -> tuple[int, ...] | None:
    parts: list[int] = []
    for chunk in text.strip().split("."):
        digits = ""
        for character in chunk:
            if not character.isdigit():
                break
            digits += character
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else None


def probe_import(module: str) -> ImportProbe:
    import importlib

    try:
        imported = importlib.import_module(module)
    except Exception as exc:
        return ImportProbe(module=module, importable=False, error=str(exc))
    version = getattr(imported, "__version__", None)
    return ImportProbe(
        module=module,
        importable=True,
        version_text=str(version) if version else None,
    )


def probe_optional_import(module: str) -> ImportProbe:
    from importlib.util import find_spec

    try:
        found = find_spec(module) is not None
    except Exception as exc:
        return ImportProbe(module=module, importable=False, error=str(exc))
    return ImportProbe(module=module, importable=found)


def probe_ollama(host: str, *, timeout_seconds: float = 3.0) -> OllamaProbe:
    from opengrader.core import app_config

    # This is explicitly the Ollama probe (doctor's "is the local server up"
    # check), so it resolves against that provider by name rather than
    # whatever the user has configured — a cloud-configured install should
    # still get a straight answer about the local server.
    resolved = app_config.for_provider(
        "ollama", host=host, timeout_seconds=timeout_seconds
    )
    try:
        models = app_config.available_models(resolved)
    except Exception as exc:  # provider client errors are provider-specific
        return OllamaProbe(host=host, reachable=False, error=summarize_connection_error(str(exc)))
    return OllamaProbe(host=host, reachable=True, models=tuple(models))


def summarize_connection_error(message: str) -> str:
    lowered = message.lower()
    if "refused" in lowered:
        return "no server listening"
    if "timed out" in lowered or "timeout" in lowered:
        return "server did not respond in time"
    if "name or service not known" in lowered or "nodename nor servname" in lowered:
        return "host name could not be resolved"
    return message.splitlines()[0][:120]


def probe_canvas_sak() -> ToolProbe:
    path = shutil.which("canvas-sak")
    return ToolProbe(found=path is not None, path=path)


def probe_graphics() -> tuple[str, bool, str]:
    from opengrader.tui.terminal_graphics import detect_graphics_capability

    capability = detect_graphics_capability()
    return capability.protocol, capability.pixel_accurate, capability.describe()


def run_smoke_test() -> tuple[bool, str]:
    import tempfile
    from pathlib import Path

    qa_source = (
        "---\ntitle: Doctor Smoke Test\n---\n\n"
        "- (2 points) What is 2 + 2?\n"
        "  - **Correct** 4\n"
    )
    try:
        from opengrader.core.pdf.templates.generate import generate_template_pdf
        from opengrader.core.qa_file.markdown import parse_qa_markdown, qa_to_template_questions

        document = parse_qa_markdown(qa_source)
        questions = qa_to_template_questions(document)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "smoke-template.pdf"
            generate_template_pdf(
                output_path=output,
                version="A",
                questions=questions,
                title=document.title or "Doctor Smoke Test",
            )
            if not output.exists() or output.stat().st_size == 0:
                return False, "template PDF was not written"
            size = output.stat().st_size
        return True, f"answer key -> template PDF ({size} bytes) OK"
    except Exception as exc:
        return False, f"failed: {type(exc).__name__}: {exc}"


def probe_ai_latency(resolved: "ResolvedAI") -> "LatencyMeasurement | None":
    from opengrader.core import app_config

    return app_config.measure_generate_latency(resolved)


def build_environment_report(
    *,
    vision_model: str,
    text_model: str | None,
    ollama_host: str,
    check_graphics: bool = True,
    smoke_test: bool = False,
    ai: "ResolvedAI | None" = None,
    time_ai: bool = False,
) -> EnvironmentReport:
    checks: list[EnvironmentCheck] = [evaluate_python(probe_python())]

    runtime_modules = (
        "fitz",
        "pypdf",
        "cv2",
        "PIL",
        "numpy",
        "matplotlib",
        "pytesseract",
        "textual",
        "segno",
    )
    checks.append(
        evaluate_imports(tuple(probe_import(module) for module in runtime_modules))
    )

    checks.append(evaluate_tesseract(probe_tesseract()))

    # A cloud provider is checked on its own terms and never probed as if it
    # were Ollama, so a cloud-only machine without Ollama can still be ready.
    # A local provider, or no AI configured at all, probes Ollama with the
    # caller's own host/model arguments.
    if ai is not None and ai.kind == "cloud":
        checks.extend(evaluate_provider(ai))
    else:
        checks.extend(
            evaluate_ollama(
                probe_ollama(ollama_host),
                vision_model=vision_model,
                text_model=text_model,
            )
        )
    checks.append(
        evaluate_optional_import(
            probe_optional_import("pix2tex"),
            feature="`--math-backend pix2tex` is unavailable; handwritten maths "
            "still reads through the Ollama vision model.",
            fix="uv sync --extra math",
        )
    )
    checks.append(evaluate_encryption(available=encryption_available()))
    checks.append(evaluate_canvas_sak(probe_canvas_sak()))

    if check_graphics:
        protocol, pixel_accurate, description = probe_graphics()
        checks.append(
            evaluate_graphics(
                protocol=protocol,
                pixel_accurate=pixel_accurate,
                description=description,
            )
        )

    if smoke_test:
        ok, detail = run_smoke_test()
        checks.append(evaluate_smoke_test(ok=ok, detail=detail))

    if ai is not None:
        checks.append(evaluate_ai_features(ai))

    if time_ai and ai is not None:
        measurement = probe_ai_latency(ai)
        checks.append(evaluate_ai_latency(measurement))

    return EnvironmentReport(checks=tuple(checks))
