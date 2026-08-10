"""Environment pre-flight: does this machine have what Opengrader needs?

Opengrader ships as a double-clickable desktop app that leans on things pip
cannot install — the Tesseract binary, an Ollama server with a pulled model, a
terminal that can draw images. When one of them is missing the failure surfaces
far away from the cause: extraction produces empty transcriptions, the answer
crop renders as coloured blocks, auto-grading refuses with a connection error.
Diagnosing that today means knowing which of a dozen commands to run.

This module answers the whole question at once, and — the part that matters for
a non-technical instructor — names the exact command that fixes each finding.

**The measurement/judgement split is deliberate**, matching
``core/submissions/scan_check.py``: a *probe* touches the outside world and
records what it found (``ToolProbe``, ``OllamaProbe``, ``ImportProbe``), and a
pure ``evaluate_*`` function turns that record into an ``EnvironmentCheck`` with
a status, an impact and a fix. The judgement half therefore unit-tests with no
Tesseract, no Ollama and no terminal, which is exactly the environment a CI
runner has.

Two rules keep the report honest:

* **A missing optional dependency is not a failure.** Ollama powers handwriting
  transcription and LLM grading; an instructor grading typed submissions with a
  rubric needs none of it. Reporting that as broken would train people to ignore
  the report — the same reasoning that keeps ``added`` files out of
  ``core/integrity.py``'s concerning statuses.
* **"Could not measure" is its own answer.** A probe that cannot run reports
  ``unknown``, never ``ready``, so the report can never be read as "checked, all
  good" for something that was never checked.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from opengrader.core.crypto import encryption_available

if TYPE_CHECKING:
    from opengrader.core.ai_config import LatencyMeasurement, ResolvedAI

# How a check turned out.
#   ready    - present and usable
#   degraded - absent or old, but only an optional capability is lost
#   broken   - a capability the core pipeline depends on does not work
#   unknown  - the probe could not run, so nothing is known either way
CheckStatus = Literal["ready", "degraded", "broken", "unknown"]

# Worst-first, so a report can be sorted and a single overall verdict derived.
_STATUS_ORDER: dict[CheckStatus, int] = {
    "broken": 0,
    "unknown": 1,
    "degraded": 2,
    "ready": 3,
}

# The lowest Tesseract release we are confident in. Older builds lack the
# orientation-detection (OSD) output that the scan pre-flight reads, so they
# would silently skip the rotation check rather than fail it.
MINIMUM_TESSERACT_VERSION = (4, 0)


@dataclass(frozen=True)
class EnvironmentCheck:
    """One finding: what was looked at, how it turned out, and what to do."""

    name: str
    status: CheckStatus
    # What was actually found — a version, a path, an error. This is the line an
    # instructor pastes into a bug report, so it records evidence, not a verdict.
    detail: str
    # What stops working while this is not ready. Empty for a passing check.
    impact: str = ""
    # The exact command to run. Empty when there is nothing to fix.
    fix: str = ""

    @property
    def needs_attention(self) -> bool:
        return self.status != "ready"


@dataclass(frozen=True)
class EnvironmentReport:
    """Every check, worst first."""

    checks: tuple[EnvironmentCheck, ...] = field(default_factory=tuple)

    @property
    def status(self) -> CheckStatus:
        """The worst status in the report — 'ready' only when everything is."""
        if not self.checks:
            return "unknown"
        return min(self.checks, key=lambda check: _STATUS_ORDER[check.status]).status

    @property
    def broken(self) -> tuple[EnvironmentCheck, ...]:
        return tuple(check for check in self.checks if check.status == "broken")

    @property
    def attention(self) -> tuple[EnvironmentCheck, ...]:
        """Everything that is not ready, worst first."""
        return tuple(
            sorted(
                (check for check in self.checks if check.needs_attention),
                key=lambda check: _STATUS_ORDER[check.status],
            )
        )


# --- measurements -----------------------------------------------------------
#
# Plain records of what a probe saw. Every field is optional, and `None` always
# means "not measurable", never "fine".


@dataclass(frozen=True)
class ToolProbe:
    """What was found out about one external program."""

    found: bool = False
    version: tuple[int, ...] | None = None
    version_text: str | None = None
    path: str | None = None
    # Why the probe could not answer, when it could not. Distinct from
    # `found=False`: "tesseract is not installed" and "asking tesseract for its
    # version raised" call for different fixes.
    error: str | None = None


@dataclass(frozen=True)
class ImportProbe:
    """Whether an installed Python package can actually be imported."""

    module: str
    importable: bool
    version_text: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class OllamaProbe:
    """What was found out about the local Ollama server."""

    host: str
    reachable: bool = False
    models: tuple[str, ...] = ()
    error: str | None = None


# --- install hints ----------------------------------------------------------


def install_hint(*, brew: str, apt: str, winget: str) -> str:
    """Return the install command for the platform we are actually running on.

    A fix an instructor can copy is the whole point of this module, and
    "install Tesseract" is not one. Mirrors the package names
    ``packaging/bootstrap.sh`` already uses so the two never disagree.
    """
    if sys.platform == "darwin":
        return f"brew install {brew}"
    if sys.platform.startswith("win"):
        return f"winget install {winget}"
    return f"sudo apt-get install {apt}  (or your distribution's equivalent)"


# --- judgement (pure) -------------------------------------------------------


def evaluate_python(version: tuple[int, ...]) -> EnvironmentCheck:
    """Judge the running interpreter against the pinned 3.12 requirement."""
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
    """Judge the Tesseract OCR binary.

    Broken rather than degraded when missing: every extraction mode except the
    born-digital text layer reads the page through Tesseract, so without it a
    scanned exam extracts to empty answers — the silent cohort-of-zeros failure
    the scan pre-flight exists to prevent.
    """
    fix = install_hint(brew="tesseract", apt="tesseract-ocr", winget="UB-Mannheim.TesseractOCR")
    if probe.error is not None and not probe.found:
        return EnvironmentCheck(
            name="Tesseract OCR",
            status="unknown",
            detail=f"could not be checked: {probe.error}",
            impact="Scan extraction, boundary detection and the scan pre-flight may not work.",
            fix=fix,
        )
    if not probe.found:
        return EnvironmentCheck(
            name="Tesseract OCR",
            status="broken",
            detail="not installed",
            impact="Scanned exams extract to empty answers; scan pre-flight cannot check rotation.",
            fix=fix,
        )
    where = f" at {probe.path}" if probe.path else ""
    if probe.version is not None and probe.version[:2] < MINIMUM_TESSERACT_VERSION:
        minimum = ".".join(str(part) for part in MINIMUM_TESSERACT_VERSION)
        return EnvironmentCheck(
            name="Tesseract OCR",
            status="degraded",
            detail=f"{probe.version_text}{where} — older than {minimum}",
            impact="Page-orientation detection (the scan pre-flight's rotation check) needs 4.0+.",
            fix=fix,
        )
    return EnvironmentCheck(
        name="Tesseract OCR",
        status="ready",
        detail=f"{probe.version_text or 'installed'}{where}",
    )


def evaluate_ollama(
    probe: OllamaProbe,
    *,
    vision_model: str,
    text_model: str | None = None,
) -> tuple[EnvironmentCheck, ...]:
    """Judge the Ollama server and the models the pipeline would ask it for.

    Everything here is **degraded, never broken**: Ollama powers handwriting
    transcription and LLM-assisted free-response grading, both of which are
    opt-in. An instructor grading with a rubric by hand needs none of it, and
    calling that a failure would teach them to ignore the whole report.
    """
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
    """Judge the Python packages the pipeline imports at runtime.

    Reported as one check rather than a dozen: they are installed together by a
    single ``uv sync``, so a dozen rows would all carry the same one-line fix and
    bury the findings that differ.
    """
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
    """Judge one deliberately optional package (e.g. the pix2tex maths reader)."""
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
    """Judge whether this install can encrypt packets and backups.

    Broken rather than degraded, unlike the other optional integrations: the
    library is a *base* dependency, so its absence means the install is
    incomplete — and the failure would only surface on the day someone tries to
    protect a copy before handing it over.
    """
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
    """Judge the Canvas export helper — optional, since export is opt-in."""
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
    """Judge the terminal's ability to draw answer crops.

    Degraded rather than broken, and this is the whole point of the 2026-08-01
    fallback work: grading still functions in any terminal, it just shows a
    coarse preview plus the `o` hotkey to open the crop in a desktop viewer.
    """
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
    """Judge the end-to-end smoke test (answer key -> template PDF)."""
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


def evaluate_ai_features(resolved: "ResolvedAI") -> EnvironmentCheck:
    """Report what the AI is set to do on this machine.

    A user whose scans extract to empty answers may simply have handwriting
    reading switched off, and that is currently invisible — this finding makes
    that choice explicit.
    """
    if not resolved.uses_ai:
        return EnvironmentCheck(
            name="AI features",
            status="ready",
            detail="nothing — the AI is switched off (a legitimate choice)",
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
        # Degraded: configuration errors found in resolved.problems()
        detail = "\n".join(problems)
        return EnvironmentCheck(
            name="AI features",
            status="degraded",
            detail=detail,
            impact="AI features are enabled but misconfigured.",
            fix=problems[0],
        )

    # Ready: AI is enabled and no configuration problems
    detail = ", ".join(features) if features else "nothing — the AI is switched off"
    return EnvironmentCheck(
        name="AI features",
        status="ready",
        detail=detail,
    )


def evaluate_ai_latency(measurement: "LatencyMeasurement | None") -> EnvironmentCheck:
    """Judge the AI response time against the configured timeout.

    Not measured at all (caller passed None) reports unknown and names the flag
    that times it. If measured, checks whether it is fast enough or recommends
    a higher limit.
    """
    from opengrader.core import ai_config

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
    if share is not None and share >= ai_config.TIMEOUT_WARN_FRACTION:
        # Measurement is ok but uses too much of the limit; recommend a higher one.
        recommended = ai_config.recommend_timeout(measurement.seconds or 0, measurement.limit)
        if recommended is not None:
            return EnvironmentCheck(
                name="AI response time",
                status="degraded",
                detail=measurement.describe(),
                impact="A longer answer or a second concurrent request risks timing out.",
                fix=f"Raise 'How long to wait for the AI' in AI setup to {recommended:.0f} s — a request that takes most of the limit will time out on a longer answer, and a timed-out read shows up as an empty answer, not as an error.",
            )

    # Fast or measurement is below the warn threshold; ready
    return EnvironmentCheck(
        name="AI response time",
        status="ready",
        detail=measurement.describe(),
    )


# --- probes (these touch the outside world) ---------------------------------


def probe_python() -> tuple[int, ...]:
    return sys.version_info[:3]


def probe_tesseract() -> ToolProbe:
    """Look for the Tesseract binary and ask it for its version."""
    path = shutil.which("tesseract")
    try:
        import pytesseract
    except Exception as exc:  # pragma: no cover - only when the install is broken
        return ToolProbe(found=path is not None, path=path, error=str(exc))

    try:
        raw = pytesseract.get_tesseract_version()
    except Exception as exc:
        # pytesseract raises when the binary is absent *or* unrunnable. A missing
        # binary is the ordinary case and is reported as such; anything else
        # keeps its message, since the two need different fixes.
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
    """Pull a numeric version out of a tool's version string.

    Tools report things like ``5.5.0`` or ``4.1.1-rc2-25-g9707``; only the
    leading dotted numbers are meaningful for a minimum-version comparison.
    """
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
    """Import a module and record whether it worked (never raises)."""
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
    """Answer "is this installed?" without paying to import it.

    ``pix2tex`` drags in torch, which is far too slow to import for a diagnostic
    line, so this checks whether the module *could* be found instead — the same
    ``find_spec`` trick ``core/ocr/math_ocr.py`` uses for the same reason.
    """
    from importlib.util import find_spec

    try:
        found = find_spec(module) is not None
    except Exception as exc:
        return ImportProbe(module=module, importable=False, error=str(exc))
    return ImportProbe(module=module, importable=found)


def probe_ollama(host: str, *, timeout_seconds: float = 3.0) -> OllamaProbe:
    """Ask the local Ollama server which models it has.

    The timeout is deliberately short: this is a diagnostic, and an instructor
    waiting two minutes to be told a server is not running has been failed twice.
    """
    from opengrader.core.llm.ollama_client import OllamaConfig, OllamaError, list_models

    config = OllamaConfig(host=host, timeout_seconds=timeout_seconds)
    try:
        models = list_models(config)
    except OllamaError as exc:
        return OllamaProbe(host=host, reachable=False, error=summarize_connection_error(str(exc)))
    except Exception as exc:  # pragma: no cover - defensive; a diagnostic must not crash
        return OllamaProbe(host=host, reachable=False, error=summarize_connection_error(str(exc)))
    return OllamaProbe(host=host, reachable=True, models=tuple(models))


def summarize_connection_error(message: str) -> str:
    """Compress a urllib traceback string into a phrase a person can act on.

    The raw message repeats the URL we already print and buries the one useful
    word ("refused", "timed out") inside nested exception text. A diagnostic
    whose findings are hard to read is a diagnostic nobody reads.
    """
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
    """Return (protocol, pixel_accurate, description) for this terminal."""
    from opengrader.tui.terminal_graphics import detect_graphics_capability

    capability = detect_graphics_capability()
    return capability.protocol, capability.pixel_accurate, capability.describe()


def run_smoke_test() -> tuple[bool, str]:
    """Turn a tiny answer key into a template PDF, in a temp directory.

    This is the one check that exercises the real pipeline rather than its
    ingredients: the QA parser, the PDF generator and the bundled fonts all have
    to work together for it to pass. Writes only into a temporary directory,
    which is removed either way.
    """
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
    """Measure the AI response time by calling measure_generate_latency.

    Imported lazily inside the function to avoid importing ai_config at module
    scope, matching how probe_ollama imports its client inside the function.
    """
    from opengrader.core import ai_config

    return ai_config.measure_generate_latency(resolved)


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
    """Run every probe and return the judged report.

    Ordered as a reader needs it: the interpreter and packages first (nothing
    works without them), then the external tools the pipeline calls, then the
    optional integrations, then the terminal, then AI-specific checks if
    requested.

    Args:
        vision_model: Vision model to check
        text_model: Text model to check (optional)
        ollama_host: Ollama server host
        check_graphics: Whether to check terminal graphics capability
        smoke_test: Whether to run the pipeline smoke test
        ai: ResolvedAI settings for AI feature checks (optional)
        time_ai: Whether to measure AI response time (only adds a check if True)
    """
    checks: list[EnvironmentCheck] = [evaluate_python(probe_python())]

    # The third-party packages every pipeline stage imports. Kept as an explicit
    # list rather than parsed out of pyproject.toml: a diagnostic should not
    # depend on the packaging metadata being readable in an installed app.
    runtime_modules = (
        "fitz",  # PyMuPDF: all PDF reading and writing
        "pypdf",  # page-level splitting and reordering
        "cv2",  # OpenCV: layout detection, QR and bubble reading
        "PIL",  # Pillow: image crops
        "numpy",
        "matplotlib",  # statistics charts and rendered maths
        "pytesseract",
        "textual",  # the grading TUI
        "segno",  # QR generation
    )
    checks.append(
        evaluate_imports(tuple(probe_import(module) for module in runtime_modules))
    )

    checks.append(evaluate_tesseract(probe_tesseract()))
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

    # Only add a latency check when explicitly requested; do not add an
    # unknown row to every ordinary report, which would be noise.
    if time_ai and ai is not None:
        measurement = probe_ai_latency(ai)
        checks.append(evaluate_ai_latency(measurement))

    return EnvironmentReport(checks=tuple(checks))
