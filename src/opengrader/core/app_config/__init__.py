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
    "AIUnavailableError",
    "ProviderSpec",
    "ProviderPricing",
    "ResolvedAI",
    "host_kind",
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
    "with_overrides",
    "for_provider",
    "available_models",
    "ensure_ready",
    "resolve_api_key",
    "load_settings",
    "save_settings",
    "settings_path",
    "text_config",
    "text_generate",
    "vision_config",
    "vision_reader",
    "manifest_problems",
    "reload_manifests",
    "manifests_dir",
    "dialect_names",
    "TIMEOUT_WARN_FRACTION",
    "TIMEOUT_HEADROOM_MULTIPLE",
    "LatencyMeasurement",
    "recommend_timeout",
    "measure_generate_latency",
    "parse_timeout_seconds",
]

DEFAULT_PROVIDER = "ollama"
DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_VISION_MODEL = "qwen3.5:9b"
DEFAULT_TEXT_MODEL = "qwen2.5vl:7b"
DEFAULT_TIMEOUT_SECONDS = 120.0

DEFAULT_MIN_CONFIDENCE = 0.7

DEFAULT_MATH_BACKEND = "vlm"

SETTINGS_FILENAME = "config.json"
SCHEMA_FILENAME = "config.schema.json"
SETTINGS_VERSION = 1


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"})


def host_kind(host: str) -> Literal["local", "cloud"]:
    """Treat unrecognized addresses as cloud so they cannot bypass cloud safeguards."""
    text = (host or "").strip()
    if not text:
        return "cloud"

    from urllib.parse import urlsplit

    candidate = text if "//" in text else f"//{text}"
    try:
        hostname = urlsplit(candidate).hostname
    except ValueError:
        return "cloud"
    if not hostname:
        return "cloud"

    name = hostname.strip().lower().rstrip(".")
    if name in _LOOPBACK_HOSTS or name.endswith(".local") or name.endswith(".localhost"):
        return "local"

    import ipaddress

    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return "cloud"
    if address.is_loopback or address.is_private or address.is_link_local:
        return "local"
    return "cloud"


@dataclass(frozen=True)
class ProviderPricing:
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0

    @property
    def stated(self) -> bool:
        return self.input_per_mtok > 0 or self.output_per_mtok > 0


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    label: str
    summary: str
    default_host: str
    default_vision_model: str
    default_text_model: str
    setup_hint: str
    api_key_env: str = ""
    requires_api_key: bool = False
    text_config_factory: Callable[["ResolvedAI"], Any] | None = None
    vision_config_factory: Callable[["ResolvedAI"], Any] | None = None
    vision_reader_factory: Callable[["ResolvedAI"], Any] | None = None
    supports_model_listing: bool = True
    list_models_factory: Callable[["ResolvedAI"], list[str]] | None = None
    text_generate_factory: Callable[..., Callable[[str], str]] | None = None
    dialect: str = ""
    source: Literal["builtin", "user"] = "builtin"
    manifest_path: str = ""
    pricing: ProviderPricing | None = None
    # Parsed from manifests but never executed.
    credential_command: tuple[str, ...] = ()

    @property
    def kind(self) -> Literal["local", "cloud"]:
        return host_kind(self.default_host)

    @property
    def sends_data_off_machine(self) -> bool:
        return self.kind != "local"

    @property
    def allows_identity_fields(self) -> bool:
        return self.kind == "local"


class AIUnavailableError(RuntimeError):
    """The selected provider failed its readiness check."""


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


def _ollama_vision_reader(resolved: "ResolvedAI") -> Any:
    from opengrader.core.ocr.vision_reader import OllamaVisionReader

    return OllamaVisionReader(config=_ollama_vision_config(resolved))


def _ollama_text_generate(
    resolved: "ResolvedAI", *, response_format: dict | None = None
) -> Callable[[str], str]:
    from opengrader.core.llm import generate

    config = _ollama_text_config(resolved)

    def _call(prompt: str) -> str:
        return generate(prompt=prompt, config=config, response_format=response_format)

    return _call


def _dialect_config(resolved: "ResolvedAI", *, model: str) -> Any:
    from opengrader.core.llm.dialect_client import DialectConfig

    return DialectConfig(
        dialect=resolved.provider.dialect,
        host=resolved.host,
        model=model,
        timeout_seconds=resolved.timeout_seconds,
        api_key=resolved.api_key,
        label=resolved.provider.label,
        max_attempts=1 if resolved.kind == "local" else 4,
    )


def _dialect_text_config(resolved: "ResolvedAI") -> Any:
    return _dialect_config(resolved, model=resolved.text_model)


def _dialect_vision_config(resolved: "ResolvedAI") -> Any:
    return _dialect_config(resolved, model=resolved.vision_model)


def _dialect_vision_reader(resolved: "ResolvedAI") -> Any:
    from opengrader.core.ocr.vision_reader import DialectVisionReader

    return DialectVisionReader(config=_dialect_vision_config(resolved))


def _dialect_list_models(resolved: "ResolvedAI") -> list[str]:
    from opengrader.core.llm.dialect_client import list_models

    return list_models(_dialect_text_config(resolved))


def _dialect_text_generate(
    resolved: "ResolvedAI", *, response_format: dict | None = None
) -> Callable[[str], str]:
    from opengrader.core.llm.dialect_client import generate

    config = _dialect_text_config(resolved)

    def _call(prompt: str) -> str:
        return generate(config=config, prompt=prompt, response_format=response_format)

    return _call


def _ollama_list_models(resolved: "ResolvedAI") -> list[str]:
    from opengrader.core.llm import list_models

    return list_models(_ollama_text_config(resolved))


OLLAMA = ProviderSpec(
    name="ollama",
    label="Ollama on this computer (offline)",
    summary=(
        "Models run on this machine. Nothing you grade — no handwriting, no "
        "names, no answers — ever leaves it."
    ),
    default_host=DEFAULT_HOST,
    default_vision_model=DEFAULT_VISION_MODEL,
    default_text_model=DEFAULT_TEXT_MODEL,
    setup_hint=(
        "Install Ollama, then pull qwen3.5:9b for handwriting and qwen2.5vl:7b for grading. Run ollama serve. "
        "'Check this computer' in Helper Tools reports exactly what is missing."
    ),
    text_config_factory=_ollama_text_config,
    vision_config_factory=_ollama_vision_config,
    vision_reader_factory=_ollama_vision_reader,
    supports_model_listing=True,
    list_models_factory=_ollama_list_models,
    text_generate_factory=_ollama_text_generate,
    dialect="ollama",
)

_REGISTRY: dict[str, ProviderSpec] = {OLLAMA.name: OLLAMA}

_manifests_loaded = False
_manifest_problems: tuple[str, ...] = ()


def register_provider(spec: ProviderSpec) -> None:
    _REGISTRY[spec.name] = spec


def _ensure_manifests_loaded() -> None:
    """Load manifests on first use; invalid files do not prevent startup."""
    global _manifests_loaded, _manifest_problems
    if _manifests_loaded:
        return
    _manifests_loaded = True
    try:
        from . import manifests as _manifests

        specs, problems = _manifests.load_all()
    except Exception as exc:  # noqa: BLE001 - bad manifests must not prevent startup
        _manifest_problems = (f"Could not read provider manifests: {exc}",)
        return
    skipped: list[str] = []
    for spec in specs:
        if spec.name in _REGISTRY:
            owner = _REGISTRY[spec.name].manifest_path or "a built-in provider"
            skipped.append(
                f"{spec.manifest_path}: name {spec.name!r} is already used by "
                f"{owner}. Give one of them a different name."
            )
            continue
        _REGISTRY[spec.name] = spec
    _manifest_problems = (*problems, *skipped)


def reload_manifests() -> tuple[str, ...]:
    global _manifests_loaded, _manifest_problems
    for name, spec in list(_REGISTRY.items()):
        if spec.source == "user":
            del _REGISTRY[name]
    _manifests_loaded = False
    _manifest_problems = ()
    _ensure_manifests_loaded()
    return _manifest_problems


def manifest_problems() -> tuple[str, ...]:
    _ensure_manifests_loaded()
    return _manifest_problems


def manifests_dir() -> Path:
    return settings_path().parent / "providers"


def dialect_names() -> tuple[str, ...]:
    from opengrader.core.llm.dialects import DIALECT_NAMES

    return DIALECT_NAMES


def registered_providers() -> tuple[ProviderSpec, ...]:
    _ensure_manifests_loaded()
    return tuple(_REGISTRY.values())


def get_provider(name: str) -> ProviderSpec:
    _ensure_manifests_loaded()
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
        body: dict[str, Any] = {
            "$schema": f"./{SCHEMA_FILENAME}",
            "version": SETTINGS_VERSION,
        }
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


def settings_path() -> Path:
    return _config_dir() / SETTINGS_FILENAME


def load_settings(
    path: Path | None = None,
) -> AISettings:
    target = path or settings_path()
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
) -> Path:
    from pydantic import TypeAdapter

    target = path or settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    schema = TypeAdapter(AISettings).json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["additionalProperties"] = False
    schema["properties"]["$schema"] = {"type": "string"}
    schema["properties"]["version"] = {"const": SETTINGS_VERSION}
    schema_path = target.with_name(SCHEMA_FILENAME)
    schema_text = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if not schema_path.is_file() or schema_path.read_text(encoding="utf-8") != schema_text:
        schema_path.write_text(schema_text, encoding="utf-8")
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
    def kind(self) -> Literal["local", "cloud"]:
        """Classify the actual request host, including user overrides."""
        return host_kind(self.host)

    @property
    def sends_data_off_machine(self) -> bool:
        return self.kind != "local"

    @property
    def allows_identity_fields(self) -> bool:
        return self.kind == "local"

    @property
    def uses_ai(self) -> bool:
        return self.read_handwriting or self.grade_written

    def problems(self) -> tuple[str, ...]:
        found: list[str] = list(manifest_problems())
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
    """Read a provider key from the environment and register it for redaction."""
    if not provider.api_key_env:
        return None
    env = os.environ if environ is None else environ
    value = env.get(provider.api_key_env, "").strip()
    if not value:
        return None
    try:
        from opengrader.core.llm.transport import register_secret

        register_secret(value)
    except Exception:  # noqa: BLE001 - redaction is a safety net, not a gate
        pass
    return value


def resolve(
    settings: AISettings | None = None,
    *,
    environ: dict[str, str] | None = None,
    path: Path | None = None,
) -> ResolvedAI:
    env = os.environ if environ is None else environ
    base = settings if settings is not None else load_settings(path)

    provider = get_provider(base.provider)
    host = base.host or provider.default_host
    return ResolvedAI(
        provider=provider,
        host=host,
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
        auto_start=base.auto_start and host_kind(host) == "local",
        api_key=resolve_api_key(provider, environ=env),
        read_handwriting=base.read_handwriting,
        escalate_only=base.escalate_only and base.read_handwriting,
        transcribe_math=base.transcribe_math and base.read_handwriting,
        grade_written=base.grade_written,
        auto_grade=base.auto_grade,
        require_calibration=base.require_calibration,
        settings=base,
    )


def _factory(
    current: ResolvedAI, attribute: str, dialect_default: Callable[..., Any], role: str
) -> Callable[..., Any]:
    explicit = getattr(current.provider, attribute)
    if explicit is not None:
        return explicit
    if current.provider.dialect:
        return dialect_default
    raise ValueError(
        f"provider {current.provider.name!r} has no {role} configured and names "
        "no dialect. A provider must bring either its own client or one of "
        "the shipped wire dialects."
    )


def text_config(resolved: ResolvedAI | None = None) -> Any:
    current = resolved if resolved is not None else resolve()
    return _factory(
        current, "text_config_factory", _dialect_text_config, "text client"
    )(current)


def text_generate(
    resolved: ResolvedAI | None = None, *, response_format: dict | None = None
) -> Callable[[str], str]:
    current = resolved if resolved is not None else resolve()
    factory = _factory(
        current, "text_generate_factory", _dialect_text_generate, "text client"
    )
    return factory(current, response_format=response_format)


def vision_config(resolved: ResolvedAI | None = None) -> Any:
    current = resolved if resolved is not None else resolve()
    return _factory(
        current, "vision_config_factory", _dialect_vision_config, "vision client"
    )(current)


def vision_reader(resolved: ResolvedAI | None = None) -> Any:
    current = resolved if resolved is not None else resolve()
    return _factory(
        current, "vision_reader_factory", _dialect_vision_reader, "vision reader"
    )(current)


def with_overrides(
    resolved: ResolvedAI | None = None,
    *,
    host: str | None = None,
    vision_model: str | None = None,
    text_model: str | None = None,
    timeout_seconds: float | None = None,
) -> ResolvedAI:
    """Apply explicit host, model, and timeout flags to resolved settings."""
    current = resolved if resolved is not None else resolve()
    return replace(
        current,
        host=_override_text(host, current.host),
        vision_model=_override_text(vision_model, current.vision_model),
        text_model=_override_text(text_model, current.text_model),
        timeout_seconds=(
            timeout_seconds
            if timeout_seconds is not None and timeout_seconds > 0
            else current.timeout_seconds
        ),
    )


def _override_text(value: str | None, fallback: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback


def for_provider(
    name: str,
    *,
    host: str | None = None,
    vision_model: str | None = None,
    text_model: str | None = None,
    timeout_seconds: float | None = None,
    environ: dict[str, str] | None = None,
) -> ResolvedAI:
    """Resolve a named provider from its defaults and credential environment."""
    base = resolve(AISettings(provider=name), environ={})
    base = replace(base, api_key=resolve_api_key(base.provider, environ=environ))
    return with_overrides(
        base,
        host=host,
        vision_model=vision_model,
        text_model=text_model,
        timeout_seconds=timeout_seconds,
    )


def available_models(resolved: ResolvedAI | None = None) -> list[str]:
    current = resolved if resolved is not None else resolve()
    if not current.provider.supports_model_listing:
        return []
    factory = current.provider.list_models_factory
    if factory is None:
        if not current.provider.dialect:
            return []
        factory = _dialect_list_models
    return factory(current)


def ensure_ready(
    resolved: ResolvedAI | None = None,
    *,
    purpose: Literal["vision", "text"] = "vision",
) -> None:
    """Check credentials and, when supported, model availability before a run."""
    current = resolved if resolved is not None else resolve()
    provider = current.provider

    if provider.requires_api_key and not current.api_key:
        raise AIUnavailableError(
            f"{provider.label} needs an API key. Set the "
            f"{provider.api_key_env} environment variable. {provider.setup_hint}"
        )

    if not provider.supports_model_listing:
        return

    wanted = current.vision_model if purpose == "vision" else current.text_model
    try:
        models = available_models(current)
    except Exception as exc:  # provider client errors are provider-specific
        raise AIUnavailableError(
            f"Could not reach {provider.label} at {current.host}: {exc}"
        ) from exc

    if not models or wanted in models:
        # An empty list means the service answered but told us nothing useful;
        # that is not evidence the model is missing, so do not block on it.
        return

    available_text = ", ".join(models)
    raise AIUnavailableError(
        f"{provider.label} has no model {wanted!r} at {current.host}. "
        f"Available: {available_text}. {provider.setup_hint}"
    )


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


def _generate_for(config: Any) -> Callable[..., str]:
    from opengrader.core.llm.dialect_client import DialectConfig

    if isinstance(config, DialectConfig):
        from opengrader.core.llm.dialect_client import generate as dialect_generate

        return dialect_generate
    from opengrader.core.llm import generate as ollama_generate

    return ollama_generate


def measure_generate_latency(
    resolved: ResolvedAI,
    *,
    role: str = "handwriting",
    prompt: str = "Reply with the single word: ready",
    generate: Callable[..., str] | None = None,
) -> LatencyMeasurement:
    model = resolved.vision_model if role == "handwriting" else resolved.text_model
    cfg = replace(text_config(resolved), model=model)
    call = generate if generate is not None else _generate_for(cfg)

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
