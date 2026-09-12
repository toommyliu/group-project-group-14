from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Literal

__all__ = [
    "AISettings",
    "ProviderSpec",
    "ResolvedAI",
    "DEFAULT_PROVIDER",
    "DEFAULT_VISION_MODEL",
    "DEFAULT_TEXT_MODEL",
    "DEFAULT_HOST",
    "DEFAULT_TIMEOUT_SECONDS",
    "DEFAULT_MIN_CONFIDENCE",
    "get_provider",
    "registered_providers",
    "register_provider",
    "resolve",
    "resolve_api_key",
    "load_settings",
    "save_settings",
    "settings_path",
    "text_config",
    "vision_config",
    "TIMEOUT_WARN_FRACTION",
    "TIMEOUT_HEADROOM_MULTIPLE",
    "LatencyMeasurement",
    "recommend_timeout",
    "measure_generate_latency",
    "parse_timeout_seconds",
]

DEFAULT_PROVIDER = "ollama"
DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_VISION_MODEL = "qwen2.5vl:7b"
DEFAULT_TEXT_MODEL = "qwen2.5vl:7b"
DEFAULT_TIMEOUT_SECONDS = 120.0

DEFAULT_MIN_CONFIDENCE = 0.7

DEFAULT_MATH_BACKEND = "vlm"

ENV_PREFIX = "OPENGRADER_AI_"
ENV_PROVIDER = f"{ENV_PREFIX}PROVIDER"
ENV_HOST = f"{ENV_PREFIX}HOST"
ENV_VISION_MODEL = f"{ENV_PREFIX}VISION_MODEL"
ENV_TEXT_MODEL = f"{ENV_PREFIX}TEXT_MODEL"
ENV_MATH_BACKEND = f"{ENV_PREFIX}MATH_BACKEND"
ENV_TIMEOUT = f"{ENV_PREFIX}TIMEOUT"
ENV_MIN_CONFIDENCE = f"{ENV_PREFIX}MIN_CONFIDENCE"
ENV_AUTO_START = f"{ENV_PREFIX}AUTO_START"
ENV_READ_HANDWRITING = f"{ENV_PREFIX}READ_HANDWRITING"
ENV_ESCALATE_ONLY = f"{ENV_PREFIX}ESCALATE_ONLY"
ENV_TRANSCRIBE_MATH = f"{ENV_PREFIX}TRANSCRIBE_MATH"
ENV_GRADE_WRITTEN = f"{ENV_PREFIX}GRADE_WRITTEN"
ENV_AUTO_GRADE = f"{ENV_PREFIX}AUTO_GRADE"
ENV_REQUIRE_CALIBRATION = f"{ENV_PREFIX}REQUIRE_CALIBRATION"

ENV_CONFIG_PATH = "OPENGRADER_AI_CONFIG"

SETTINGS_FILENAME = "ai.json"
SETTINGS_VERSION = 1


@dataclass(frozen=True)
class ProviderSpec:
    name: str  # Persisted provider IDs must not be renamed.
    label: str
    kind: Literal["local", "cloud"]
    summary: str
    default_host: str
    default_vision_model: str
    default_text_model: str
    setup_hint: str
    api_key_env: str = ""
    requires_api_key: bool = False
    text_config_factory: Callable[["ResolvedAI"], Any] | None = None
    vision_config_factory: Callable[["ResolvedAI"], Any] | None = None

    @property
    def sends_data_off_machine(self) -> bool:
        return self.kind != "local"


def _ollama_text_config(resolved: "ResolvedAI") -> Any:
    from opengrader.core.llm import OllamaConfig

    return OllamaConfig(
        model=resolved.text_model,
        host=resolved.host,
        timeout_seconds=resolved.timeout_seconds,
    )


def _ollama_vision_config(resolved: "ResolvedAI") -> Any:
    from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig

    return OllamaVisionConfig(
        model=resolved.vision_model,
        host=resolved.host,
        timeout_seconds=resolved.timeout_seconds,
    )


OLLAMA = ProviderSpec(
    name="ollama",
    label="Ollama on this computer (offline)",
    kind="local",
    summary=(
        "Models run on this machine. Nothing you grade — no handwriting, no "
        "names, no answers — ever leaves it."
    ),
    default_host=DEFAULT_HOST,
    default_vision_model=DEFAULT_VISION_MODEL,
    default_text_model=DEFAULT_TEXT_MODEL,
    setup_hint=(
        "Install Ollama, then: ollama pull qwen2.5vl:7b && ollama serve. "
        "'Check this computer' in Helper Tools reports exactly what is missing."
    ),
    text_config_factory=_ollama_text_config,
    vision_config_factory=_ollama_vision_config,
)

_REGISTRY: dict[str, ProviderSpec] = {OLLAMA.name: OLLAMA}


def register_provider(spec: ProviderSpec) -> None:
    _REGISTRY[spec.name] = spec


def registered_providers() -> tuple[ProviderSpec, ...]:
    return tuple(_REGISTRY.values())


def get_provider(name: str) -> ProviderSpec:
    return _REGISTRY.get(name.strip()) or _REGISTRY[DEFAULT_PROVIDER]


@dataclass(frozen=True)
class AISettings:
    provider: str = DEFAULT_PROVIDER
    host: str = ""
    vision_model: str = ""
    text_model: str = ""
    math_backend: str = ""
    timeout_seconds: float | None = None
    min_confidence: float | None = None
    auto_start: bool = True
    read_handwriting: bool = False
    escalate_only: bool = False
    transcribe_math: bool = False
    grade_written: bool = False
    auto_grade: bool = True
    require_calibration: bool = False
    min_confidence_measured: bool = False

    def to_dict(self) -> dict[str, Any]:
        blank = AISettings()
        body: dict[str, Any] = {"version": SETTINGS_VERSION}
        for name in (
            "provider",
            "host",
            "vision_model",
            "text_model",
            "math_backend",
            "timeout_seconds",
            "min_confidence",
            "auto_start",
            "read_handwriting",
            "escalate_only",
            "transcribe_math",
            "grade_written",
            "auto_grade",
            "require_calibration",
            "min_confidence_measured",
        ):
            value = getattr(self, name)
            if value != getattr(blank, name):
                body[name] = value
        return body

    @classmethod
    def from_dict(cls, data: Any) -> "AISettings":
        if not isinstance(data, dict):
            return cls()
        return cls(
            provider=_as_text(data.get("provider"), DEFAULT_PROVIDER),
            host=_as_text(data.get("host"), ""),
            vision_model=_as_text(data.get("vision_model"), ""),
            text_model=_as_text(data.get("text_model"), ""),
            math_backend=_as_text(data.get("math_backend"), ""),
            timeout_seconds=_as_positive_float(data.get("timeout_seconds")),
            min_confidence=_as_unit_float(data.get("min_confidence")),
            auto_start=_as_bool(data.get("auto_start"), True),
            read_handwriting=_as_bool(data.get("read_handwriting"), False),
            escalate_only=_as_bool(data.get("escalate_only"), False),
            transcribe_math=_as_bool(data.get("transcribe_math"), False),
            grade_written=_as_bool(data.get("grade_written"), False),
            auto_grade=_as_bool(data.get("auto_grade"), True),
            require_calibration=_as_bool(data.get("require_calibration"), False),
            min_confidence_measured=_as_bool(
                data.get("min_confidence_measured"), False
            ),
        )


def _as_text(value: Any, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def _as_positive_float(value: Any) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


parse_timeout_seconds = _as_positive_float


def _as_unit_float(value: Any) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if 0.0 <= number <= 1.0 else None


def _as_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return fallback


def _config_dir() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Opengrader"
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / "Opengrader"
        return Path.home() / "AppData" / "Roaming" / "Opengrader"
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "opengrader"
    return Path.home() / ".config" / "opengrader"


def settings_path(*, environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get(ENV_CONFIG_PATH, "").strip()
    if override:
        return Path(override).expanduser()
    return _config_dir() / SETTINGS_FILENAME


def load_settings(
    path: Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> AISettings:
    target = path or settings_path(environ=environ)
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return AISettings()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return AISettings()
    return AISettings.from_dict(parsed)


def save_settings(
    settings: AISettings,
    path: Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> Path:
    target = path or settings_path(environ=environ)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(settings.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


@dataclass(frozen=True)
class ResolvedAI:
    provider: ProviderSpec
    host: str
    vision_model: str
    text_model: str
    math_backend: str
    timeout_seconds: float
    min_confidence: float
    auto_start: bool
    api_key: str | None
    read_handwriting: bool
    escalate_only: bool
    transcribe_math: bool
    grade_written: bool
    auto_grade: bool
    require_calibration: bool
    settings: AISettings = field(repr=False, default_factory=AISettings)

    @property
    def sends_data_off_machine(self) -> bool:
        return self.provider.sends_data_off_machine

    @property
    def uses_ai(self) -> bool:
        return self.read_handwriting or self.grade_written

    def problems(self) -> tuple[str, ...]:
        found: list[str] = []
        chosen = self.settings.provider.strip()
        if chosen and chosen != self.provider.name:
            known = ", ".join(spec.name for spec in registered_providers())
            found.append(
                f"Unknown AI provider {chosen!r} — falling back to "
                f"{self.provider.name!r}. Known providers: {known}."
            )
        if self.provider.requires_api_key and not self.api_key:
            found.append(
                f"{self.provider.label} needs an API key. Set the "
                f"{self.provider.api_key_env} environment variable, then "
                "re-open this screen."
            )
        if (
            self.grade_written
            and self.require_calibration
            and not self.settings.min_confidence_measured
        ):
            found.append(
                "AI grade suggestions are set to require a measured "
                f"confidence, but {self.min_confidence:g} was never measured. "
                "Run 'Calibrate AI grading' in Helper Tools (or the llm-eval "
                "command) against work you have already graded, then accept "
                "the recommendation."
            )
        return tuple(found)


def resolve_api_key(
    provider: ProviderSpec,
    *,
    environ: dict[str, str] | None = None,
) -> str | None:
    if not provider.api_key_env:
        return None
    env = os.environ if environ is None else environ
    value = env.get(provider.api_key_env, "").strip()
    return value or None


def resolve(
    settings: AISettings | None = None,
    *,
    environ: dict[str, str] | None = None,
    path: Path | None = None,
) -> ResolvedAI:
    env = os.environ if environ is None else environ
    base = settings if settings is not None else load_settings(path, environ=env)

    base = replace(
        base,
        provider=_env_text(env, ENV_PROVIDER, base.provider),
        host=_env_text(env, ENV_HOST, base.host),
        vision_model=_env_text(env, ENV_VISION_MODEL, base.vision_model),
        text_model=_env_text(env, ENV_TEXT_MODEL, base.text_model),
        math_backend=_env_text(env, ENV_MATH_BACKEND, base.math_backend),
        timeout_seconds=_env_float(env, ENV_TIMEOUT, base.timeout_seconds),
        min_confidence=_env_unit(env, ENV_MIN_CONFIDENCE, base.min_confidence),
        auto_start=_as_bool(env.get(ENV_AUTO_START), base.auto_start),
        read_handwriting=_as_bool(
            env.get(ENV_READ_HANDWRITING), base.read_handwriting
        ),
        escalate_only=_as_bool(env.get(ENV_ESCALATE_ONLY), base.escalate_only),
        transcribe_math=_as_bool(
            env.get(ENV_TRANSCRIBE_MATH), base.transcribe_math
        ),
        grade_written=_as_bool(env.get(ENV_GRADE_WRITTEN), base.grade_written),
        auto_grade=_as_bool(env.get(ENV_AUTO_GRADE), base.auto_grade),
        require_calibration=_as_bool(
            env.get(ENV_REQUIRE_CALIBRATION), base.require_calibration
        ),
    )

    provider = get_provider(base.provider)
    return ResolvedAI(
        provider=provider,
        host=base.host or provider.default_host,
        vision_model=base.vision_model or provider.default_vision_model,
        text_model=base.text_model or provider.default_text_model,
        math_backend=base.math_backend or DEFAULT_MATH_BACKEND,
        timeout_seconds=(
            base.timeout_seconds
            if base.timeout_seconds is not None
            else DEFAULT_TIMEOUT_SECONDS
        ),
        min_confidence=(
            base.min_confidence
            if base.min_confidence is not None
            else DEFAULT_MIN_CONFIDENCE
        ),
        auto_start=base.auto_start and provider.kind == "local",
        api_key=resolve_api_key(provider, environ=env),
        read_handwriting=base.read_handwriting,
        escalate_only=base.escalate_only and base.read_handwriting,
        transcribe_math=base.transcribe_math and base.read_handwriting,
        grade_written=base.grade_written,
        auto_grade=base.auto_grade,
        require_calibration=base.require_calibration,
        settings=base,
    )


def _env_text(env: dict[str, str], name: str, fallback: str) -> str:
    value = env.get(name, "")
    return value.strip() or fallback


def _env_float(env: dict[str, str], name: str, fallback: float | None) -> float | None:
    if name not in env:
        return fallback
    parsed = _as_positive_float(env.get(name))
    return parsed if parsed is not None else fallback


def _env_unit(env: dict[str, str], name: str, fallback: float | None) -> float | None:
    if name not in env:
        return fallback
    parsed = _as_unit_float(env.get(name))
    return parsed if parsed is not None else fallback


def text_config(resolved: ResolvedAI | None = None) -> Any:
    current = resolved if resolved is not None else resolve()
    factory = current.provider.text_config_factory
    if factory is None:  # pragma: no cover
        raise ValueError(
            f"provider {current.provider.name!r} has no text client configured"
        )
    return factory(current)


def vision_config(resolved: ResolvedAI | None = None) -> Any:
    current = resolved if resolved is not None else resolve()
    factory = current.provider.vision_config_factory
    if factory is None:  # pragma: no cover
        raise ValueError(
            f"provider {current.provider.name!r} has no vision client configured"
        )
    return factory(current)


TIMEOUT_WARN_FRACTION = 0.5

# Allow 4x the probe latency for full answers and cold model loads.
TIMEOUT_HEADROOM_MULTIPLE = 4.0


@dataclass(frozen=True)
class LatencyMeasurement:
    role: str
    model: str
    limit: float
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


def recommend_timeout(measured: float, limit: float) -> float | None:
    if measured <= 0 or measured < TIMEOUT_WARN_FRACTION * limit:
        return None
    raw = measured * TIMEOUT_HEADROOM_MULTIPLE
    rounded = 30.0 * -(-raw // 30.0)
    if rounded <= limit:
        return None
    return rounded


def measure_generate_latency(
    resolved: ResolvedAI,
    *,
    role: str = "handwriting",
    prompt: str = "Reply with the single word: ready",
    generate: Callable[..., str] | None = None,
) -> LatencyMeasurement:
    model = resolved.vision_model if role == "handwriting" else resolved.text_model
    from opengrader.core.llm import generate as _default_generate

    call = generate if generate is not None else _default_generate
    cfg = replace(text_config(resolved), model=model)

    start = time.perf_counter()
    try:
        call(config=cfg, prompt=prompt, num_predict=8)
        elapsed = time.perf_counter() - start
    except Exception as exc:  # noqa: BLE001
        return LatencyMeasurement(
            role=role, model=model, limit=resolved.timeout_seconds, error=str(exc)
        )
    return LatencyMeasurement(
        role=role, model=model, limit=resolved.timeout_seconds, seconds=elapsed
    )
