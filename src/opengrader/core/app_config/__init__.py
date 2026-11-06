"""Which AI provider Opengrader talks to, and on what terms — the provider seam.

Settings resolve flag > env > file > default, with ONE deliberate exception:
the kill switch `OPENGRADER_AI_ALLOW_CLOUD` (see `consent.py`) is the only
place the environment *forbids* rather than overrides, so that a department
can set it machine-wide without a per-user `config.json` defeating it.

Safeguards live here structurally, not in the screens:

* A cloud provider (kind derived from the ACTUAL request host) cannot produce a
  vision reader or a text generator without a matching cloud consent
  (`CloudConsentRequired`). There is no path to a saved cloud provider that
  does not pass through consent.
* Every reader `vision_reader()` hands out is wrapped in
  `ocr.vision_reader.GuardedVisionReader`, which never sends a student's name
  or ID to a cloud host — for a cloud provider those reads are redirected to
  a local Ollama reader instead — and writes one egress line per cloud call
  that is not an identity read.
* `credential_command` runs only after its own consent.

No client or SDK is imported at module scope: this module is imported at TUI
startup and by every CLI parser (tests/unit/tui/test_startup_imports.py).
API keys are read from the environment, from the operating system's own
password store (`keystore.py`: a key the user pasted into the setup screen),
or from a consented credential command. Opengrader never writes one to a file.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Literal

from . import consent as consent  # stdlib-only; re-exported as app_config.consent
from .consent import (
    ALLOW_CLOUD_ENV,
    ConsentRecord,
    cloud_allowed,
)

__all__ = [
    "AISettings",
    "AIUnavailableError",
    "CloudConsentRequired",
    "CloudDisabledError",
    "IdentityFieldBlocked",
    "ConsentRecord",
    "ALLOW_CLOUD_ENV",
    "cloud_allowed",
    "clear_credential_cache",
    "consent",
    "egress_statement",
    "data_flow_report",
    "GuardedGenerate",
    "CREDENTIAL_COMMAND_TIMEOUT_SECONDS",
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
    "api_key_source",
    "missing_key_help",
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
# The models a fresh install uses when AI setup names no others. To
# recommend a different model, change the name here: the setup hint, the
# help screen, `opengrader ai-readme --local` and the Ollama clients' own
# defaults all read these two lines.
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
    # Executed by resolve_api_key only after the user has approved this
    # exact command (credential consent), and only when api_key_env is unset.
    credential_command: tuple[str, ...] = ()

    @property
    def kind(self) -> Literal["local", "cloud"]:
        return host_kind(self.default_host)

    @property
    def sends_data_off_machine(self) -> bool:
        return self.kind != "local"


class AIUnavailableError(RuntimeError):
    """The selected provider failed its readiness check."""


class CloudConsentRequired(AIUnavailableError):
    """A cloud provider was asked for a client without a matching cloud consent."""


class CloudDisabledError(AIUnavailableError):
    """`OPENGRADER_AI_ALLOW_CLOUD=0` forbids the cloud provider that was asked for."""


def __getattr__(name: str) -> Any:
    # IdentityFieldBlocked lives with the guard that raises it; resolve lazily
    # so importing app_config never imports the OCR package.
    if name == "IdentityFieldBlocked":
        from opengrader.core.ocr.vision_reader import IdentityFieldBlocked

        return IdentityFieldBlocked
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
        "Opengrader does not start or download a local AI. Install Ollama, "
        f"run ollama serve, then ollama pull {DEFAULT_VISION_MODEL} for "
        f"handwriting and ollama pull {DEFAULT_TEXT_MODEL} for grading "
        "('opengrader ai-readme --local' has the steps). 'Check this "
        "computer' in Helper Tools reports exactly what is missing."
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
    """Load manifests on first use; invalid files do not prevent startup.

    While `OPENGRADER_AI_ALLOW_CLOUD` forbids cloud providers, user
    manifests are not read at all — and the "loaded" flag is left unset, so
    that lifting the switch later loads them then.
    """
    global _manifests_loaded, _manifest_problems
    if _manifests_loaded:
        return
    if not cloud_allowed():
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


def _selectable(spec: ProviderSpec, environ: dict[str, str] | None = None) -> bool:
    """With cloud forbidden, only built-in local providers can be chosen.

    Checked on every lookup, not only at load time, because the environment
    can change after the first load and the manifest cache must not defeat it.
    """
    if cloud_allowed(environ):
        return True
    return spec.source == "builtin" and spec.kind == "local"


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
    if not cloud_allowed():
        return ()
    return _manifest_problems


def manifests_dir() -> Path:
    return settings_path().parent / "providers"


def dialect_names() -> tuple[str, ...]:
    from opengrader.core.llm.dialects import DIALECT_NAMES

    return DIALECT_NAMES


def registered_providers(
    *, environ: dict[str, str] | None = None
) -> tuple[ProviderSpec, ...]:
    """Selectable providers, Ollama first. Filtered by the cloud kill switch."""
    _ensure_manifests_loaded()
    return tuple(spec for spec in _REGISTRY.values() if _selectable(spec, environ))


def get_provider(name: str, *, environ: dict[str, str] | None = None) -> ProviderSpec:
    _ensure_manifests_loaded()
    spec = _REGISTRY.get(name.strip())
    if spec is not None and _selectable(spec, environ):
        return spec
    return _REGISTRY[DEFAULT_PROVIDER]


@dataclass(frozen=True)
class AISettings:
    provider: str = DEFAULT_PROVIDER
    host: str = ""
    vision_model: str = ""
    text_model: str = ""
    math_backend: str = ""
    timeout_seconds: float | None = None
    min_confidence: float | None = None
    # (`auto_start`, which let a run start Ollama and download models, is
    # gone: Opengrader connects to a local AI the teacher runs. An old
    # config.json that still has it loads; the next save drops it.)
    read_handwriting: bool = False
    # None = never chosen: resolve() turns it ON for a cloud host (the largest
    # single cost reduction) and OFF for a local one. An explicit
    # True/False always wins.
    escalate_only: bool | None = None
    transcribe_math: bool = False
    grade_written: bool = False
    # (`auto_grade` moved to the pipeline card's own settings,
    # `pipeline_settings.MachinePipelineSettings.auto_grade`: it scores by the
    # answer key's rules, no AI. An old config.json that still has it loads;
    # the next save drops it.)
    require_calibration: bool = False
    min_confidence_measured: bool = False
    # Whether a LOCAL AI model may read a student's name or ID off a crop.
    # True (the default, so a fresh install behaves exactly as before this
    # setting existed) means a local model is tried before falling back to
    # Tesseract; False means those crops go straight to Tesseract (and, if
    # that fails, to the review queue) without any model ever seeing them.
    # This is about a LOCAL model only — a cloud model never reads a name or
    # ID regardless of this setting; see GuardedVisionReader.
    read_identity_locally: bool = True
    # Consent acknowledgements (cloud, credential). Never secret; managed
    # through app_config.consent's grant_*/revoke_* functions.
    consents: tuple[ConsentRecord, ...] = ()

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
            "read_handwriting",
            "escalate_only",
            "transcribe_math",
            "grade_written",
            "require_calibration",
            "min_confidence_measured",
            "read_identity_locally",
        ):
            value = getattr(self, name)
            if value != getattr(blank, name):
                body[name] = value
        if self.consents:
            body["consents"] = [record.to_dict() for record in self.consents]
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
            read_handwriting=_as_bool(data.get("read_handwriting"), False),
            escalate_only=_as_optional_bool(data.get("escalate_only")),
            transcribe_math=_as_bool(data.get("transcribe_math"), False),
            grade_written=_as_bool(data.get("grade_written"), False),
            require_calibration=_as_bool(data.get("require_calibration"), False),
            min_confidence_measured=_as_bool(
                data.get("min_confidence_measured"), False
            ),
            read_identity_locally=_as_bool(data.get("read_identity_locally"), True),
            consents=_as_consents(data.get("consents")),
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


def _as_optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return None


def _as_consents(value: Any) -> tuple[ConsentRecord, ...]:
    """Malformed entries are dropped: a broken record costs a consent, never a launch."""
    if not isinstance(value, list):
        return ()
    parsed = (ConsentRecord.from_dict(item) for item in value)
    return tuple(record for record in parsed if record is not None)


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
    # Kept out of repr: a ResolvedAI printed in a message or a log must not
    # carry the key with it.
    api_key: str | None = field(repr=False)
    read_handwriting: bool
    escalate_only: bool
    transcribe_math: bool
    grade_written: bool
    require_calibration: bool
    read_identity_locally: bool
    settings: AISettings = field(repr=False, default_factory=AISettings)
    # True for `for_provider()` resolutions: a probe may send the fixed
    # connection-test prompt without cloud consent (it carries no student
    # data) and nothing else. Never exempts a vision reader.
    probe: bool = False
    # The kill switch as seen by the `environ` resolve() was given. The live
    # process environment is consulted as well by every property below.
    cloud_forbidden: bool = False
    # Things resolve()/with_overrides() refused to do (e.g. a cloud host
    # override under the cloud kill switch); surfaced by problems().
    refusals: tuple[str, ...] = ()

    @property
    def kind(self) -> Literal["local", "cloud"]:
        """Classify the actual request host, including user overrides."""
        return host_kind(self.host)

    @property
    def sends_data_off_machine(self) -> bool:
        return self.kind != "local"

    @property
    def cloud_allowed(self) -> bool:
        """False while `OPENGRADER_AI_ALLOW_CLOUD` forbids cloud providers."""
        return not self.cloud_forbidden and cloud_allowed()

    @property
    def cloud_consent_given(self) -> bool:
        """True for a local host, or a cloud host with a matching, current consent."""
        if self.kind == "local":
            return True
        return self.cloud_allowed and consent.has_cloud_consent(self.settings, self)

    @property
    def cloud_consent_required(self) -> bool:
        """A cloud host with no matching consent: the TUI must show its consent modal."""
        return self.kind == "cloud" and not self.cloud_consent_given

    @property
    def credential_consent_required(self) -> bool:
        """The provider names a credential_command the user has not approved."""
        return bool(self.provider.credential_command) and not consent.has_credential_consent(
            self.settings, self.provider
        )

    @property
    def uses_ai(self) -> bool:
        return self.read_handwriting or self.grade_written

    def problems(self) -> tuple[str, ...]:
        found: list[str] = list(manifest_problems())
        chosen = self.settings.provider.strip()
        if chosen and chosen != self.provider.name:
            if not self.cloud_allowed:
                found.append(
                    f"{ALLOW_CLOUD_ENV}=0 is set on this machine, so cloud and "
                    f"user-added AI providers are turned off: {chosen!r} is not "
                    f"available and {self.provider.name!r} is used instead."
                )
            else:
                known = ", ".join(spec.name for spec in registered_providers())
                found.append(
                    f"Unknown AI provider {chosen!r} — falling back to "
                    f"{self.provider.name!r}. Known providers: {known}."
                )
        found.extend(self.refusals)
        if self.kind == "cloud" and not self.cloud_allowed:
            found.append(_cloud_disabled_message(self))
        elif self.cloud_consent_required:
            found.append(_consent_required_message(self))
        credential_problem = _credential_problem(self)
        if credential_problem:
            found.append(credential_problem)
        if (
            self.provider.requires_api_key
            and not self.api_key
            and not credential_problem
        ):
            found.append(
                f"{self.provider.label} needs an API key. "
                f"{missing_key_help(self.provider)} Then re-open this screen."
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


def _consent_required_message(resolved: ResolvedAI) -> str:
    status = consent.consent_status(resolved.settings, resolved, "cloud")
    reason = {
        "changed": (
            "its host, wire format, key variable or credential command changed "
            "since you agreed to send student work to it"
        ),
        "outdated": "the consent wording changed since you agreed to send student work to it",
    }.get(status, "you have not agreed to send student work to it")
    return (
        f"{resolved.provider.label} is a cloud service at "
        f"{consent.hostname_of(resolved.host)}, and {reason}. Nothing has been "
        "sent. Confirm it in Helper Tools → AI setup, or choose a local provider."
    )


def _cloud_disabled_message(resolved: ResolvedAI) -> str:
    return (
        f"{ALLOW_CLOUD_ENV}=0 is set on this machine, so cloud AI providers are "
        f"turned off: {resolved.provider.label} at "
        f"{consent.hostname_of(resolved.host)} cannot be used. Choose a local "
        "provider, or ask whoever manages this computer."
    )


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

CREDENTIAL_COMMAND_TIMEOUT_SECONDS = 15.0

# argv -> (key or None, redacted error). Per process: a credential command
# runs at most once per session, and a failure is not retried on every
# resolve() (a TUI resolves often) until clear_credential_cache().
_CREDENTIAL_CACHE: dict[tuple[str, ...], tuple[str | None, str]] = {}
_CREDENTIAL_LOCK = threading.Lock()


def clear_credential_cache() -> None:
    """Forget credential-command and key-store results, so the next resolve asks again."""
    with _CREDENTIAL_LOCK:
        _CREDENTIAL_CACHE.clear()
    from . import keystore

    keystore.clear_cache()


def _redact(text: str) -> str:
    try:
        from opengrader.core.llm.transport import redact

        return redact(text)
    except Exception:  # noqa: BLE001 - never let redaction failure leak the text
        return "[details withheld]"


def _register_secret(value: str) -> None:
    try:
        from opengrader.core.llm.transport import register_secret

        register_secret(value)
    except Exception:  # noqa: BLE001 - redaction is a safety net, not a gate
        pass


def _run_credential_command(provider: ProviderSpec) -> str | None:
    """Run a consented credential_command: no shell, short timeout, stdout stripped."""
    argv = tuple(provider.credential_command)
    with _CREDENTIAL_LOCK:
        cached = _CREDENTIAL_CACHE.get(argv)
    if cached is not None:
        return cached[0]

    import subprocess

    where = f"{provider.label}'s credential_command"
    value: str | None = None
    error = ""
    try:
        proc = subprocess.run(
            list(argv),
            shell=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=CREDENTIAL_COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError:
        error = f"{where} could not start: {argv[0]!r} was not found."
    except subprocess.TimeoutExpired:
        error = (
            f"{where} did not finish within {CREDENTIAL_COMMAND_TIMEOUT_SECONDS:g} s."
        )
    except (OSError, ValueError) as exc:
        error = f"{where} could not start: {exc}"
    else:
        output = (proc.stdout or "").strip()
        if output:
            _register_secret(output)
        if proc.returncode != 0:
            # Redacted before it is cut short: a cut key would no longer match
            # the registered secret, and its first half would survive.
            detail = _redact((proc.stderr or "").strip()).splitlines()
            error = f"{where} exited with status {proc.returncode}" + (
                f": {detail[0][:200]}" if detail else "."
            )
        elif not output:
            error = f"{where} printed nothing, so there is no key to use."
        else:
            value = output
    entry = (value, _redact(error) if error else "")
    with _CREDENTIAL_LOCK:
        _CREDENTIAL_CACHE[argv] = entry
    return value


def _credential_problem(resolved: ResolvedAI) -> str:
    provider = resolved.provider
    if not provider.credential_command or resolved.api_key:
        return ""
    if provider.api_key_env and os.environ.get(provider.api_key_env, "").strip():
        return ""
    if not consent.has_credential_consent(resolved.settings, provider):
        return (
            f"{provider.label} fetches its API key by running a command "
            "(credential_command), which will not run until you approve the exact "
            "command in Helper Tools → AI providers"
            + (
                f", or set {provider.api_key_env} instead."
                if provider.api_key_env
                else "."
            )
        )
    with _CREDENTIAL_LOCK:
        cached = _CREDENTIAL_CACHE.get(tuple(provider.credential_command))
    if cached is not None and cached[1]:
        return cached[1]
    return ""


def _stored_api_key(
    provider: ProviderSpec, environ: dict[str, str] | None = None
) -> str | None:
    """A key the user saved for this provider in the OS password store, if any.

    Only a provider that names an `api_key_env` is looked up: that is how a
    manifest says "I take a key", and it keeps the built-in local provider —
    resolved on every screen — from ever starting a keychain lookup.
    """
    if not provider.api_key_env or not _selectable(provider, environ):
        return None
    from . import keystore

    return keystore.lookup(keystore.account_for(provider))


def api_key_source(
    provider: ProviderSpec, *, environ: dict[str, str] | None = None
) -> Literal["environment", "keystore", ""]:
    """Where this provider's key would come from right now ("" = nowhere).

    A credential command is not reported here: whether it yields a key is
    only known by running it, which needs its own consent.
    """
    env = os.environ if environ is None else environ
    if provider.api_key_env and env.get(provider.api_key_env, "").strip():
        return "environment"
    if _stored_api_key(provider, environ):
        return "keystore"
    return ""


def missing_key_help(provider: ProviderSpec) -> str:
    """The one sentence every surface uses to say how to give a provider its key."""
    return (
        "Save one in Helper Tools → AI providers → Set up a provider, or set "
        f"the {provider.api_key_env} environment variable."
    )


def ollama_unreachable_help(host: str) -> str:
    """What every surface says when Ollama does not answer: the problem, then the fix.

    Short on purpose. The raw connection error ("[Errno 61] Connection
    refused") tells a teacher nothing, so it is not repeated here.
    """
    import shutil
    from urllib.parse import urlparse

    if urlparse(host).hostname not in {"127.0.0.1", "localhost", "::1"}:
        return (
            f"Ollama isn't answering at {host}. Check that it is running on "
            "that computer, then try again. You can also grade without AI."
        )
    installed = shutil.which("ollama") is not None or (
        sys.platform == "darwin" and Path("/Applications/Ollama.app").exists()
    )
    if not installed:
        return (
            "Ollama isn't installed. Get it from https://ollama.com, then try "
            "again. You can also grade without AI."
        )
    return (
        "Ollama isn't running. Open the Ollama app (or run 'ollama serve'), "
        "then try again. You can also grade without AI."
    )


def resolve_api_key(
    provider: ProviderSpec,
    *,
    environ: dict[str, str] | None = None,
    settings: AISettings | None = None,
) -> str | None:
    """The provider's key, registered for redaction. Never read from or written to disk.

    Order: the `api_key_env` variable if set and non-blank; then a key saved
    in the operating system's password store for this provider and host
    (`keystore.py`), unless the cloud kill switch forbids the provider;
    otherwise the provider's `credential_command`, but ONLY when `settings`
    holds a credential consent for exactly that argv, and the cloud kill
    switch does not forbid the provider. Without `settings`, no command ever
    runs. A failed command returns None and is reported, redacted, by
    `ResolvedAI.problems()` — never raised.
    """
    env = os.environ if environ is None else environ
    if provider.api_key_env:
        value = env.get(provider.api_key_env, "").strip()
        if value:
            _register_secret(value)
            return value
    stored = _stored_api_key(provider, environ)
    if stored:
        _register_secret(stored)
        return stored
    if (
        provider.credential_command
        and settings is not None
        and _selectable(provider, environ)
        and consent.has_credential_consent(settings, provider)
    ):
        return _run_credential_command(provider)
    return None


def resolve(
    settings: AISettings | None = None,
    *,
    environ: dict[str, str] | None = None,
    path: Path | None = None,
) -> ResolvedAI:
    env = os.environ if environ is None else environ
    base = settings if settings is not None else load_settings(path)
    allowed = cloud_allowed(env)

    provider = get_provider(base.provider, environ=env)
    host = base.host or provider.default_host
    refusals: list[str] = []
    if not allowed and host_kind(host) == "cloud":
        fallback = (
            provider.default_host
            if host_kind(provider.default_host) == "local"
            else DEFAULT_HOST
        )
        refusals.append(
            f"{ALLOW_CLOUD_ENV}=0 is set on this machine, so the saved host "
            f"{host} (not on this computer) is ignored and {fallback} is used."
        )
        host = fallback
    kind = host_kind(host)
    escalate = base.escalate_only if base.escalate_only is not None else kind == "cloud"
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
        api_key=resolve_api_key(provider, environ=env, settings=base),
        read_handwriting=base.read_handwriting,
        escalate_only=escalate and base.read_handwriting,
        transcribe_math=base.transcribe_math and base.read_handwriting,
        grade_written=base.grade_written,
        require_calibration=base.require_calibration,
        read_identity_locally=base.read_identity_locally,
        settings=base,
        cloud_forbidden=not allowed,
        refusals=tuple(refusals),
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


def _require_clearance(current: ResolvedAI, *, allow_probe: bool) -> bool:
    """Raise unless `current` may reach its host. Returns True for a restricted probe.

    Local host: always clear. Cloud host: forbidden by the kill switch → CloudDisabledError;
    consented → clear; an unconsented `for_provider()` probe where
    `allow_probe` → clear but restricted to the connection-test prompt;
    otherwise → CloudConsentRequired.
    """
    if current.kind != "cloud":
        return False
    if not current.cloud_allowed:
        raise CloudDisabledError(_cloud_disabled_message(current))
    if current.cloud_consent_given:
        return False
    if allow_probe and current.probe:
        return True
    raise CloudConsentRequired(_consent_required_message(current))


def _connection_test_prompt() -> str:
    from .manifests import CONNECTION_TEST_PROMPT

    return CONNECTION_TEST_PROMPT


def _record_call(*, provider: str, host: str, cloud: bool, category: str) -> None:
    """One egress line per cloud call; for a local call just clear the usage slot."""
    try:
        from opengrader.core.llm import usage

        input_tokens, output_tokens = usage.take_last()
    except Exception:  # noqa: BLE001 - accounting must never fail a call
        input_tokens = output_tokens = None
    if not cloud:
        return
    from opengrader.core import egress

    egress.record(
        provider=provider,
        host=host,
        category=category,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


@dataclass(frozen=True)
class GuardedGenerate:
    """The callable `text_generate()` returns: the provider's own, plus egress logging.

    `restricted` is set for an unconsented probe: then only the fixed
    connection-test prompt may be sent, and any other prompt raises
    CloudConsentRequired before anything leaves the machine.
    """

    call: Callable[..., str]
    provider: str
    host: str
    cloud: bool
    restricted: bool = False

    def __call__(self, prompt: str, *args: Any, **kwargs: Any) -> str:
        is_test = prompt == _connection_test_prompt()
        if self.restricted and not is_test:
            raise CloudConsentRequired(
                f"Only the connection test may be sent to {self.host} before you "
                "consent to sending student work there."
            )
        try:
            return self.call(prompt, *args, **kwargs)
        finally:
            _record_call(
                provider=self.provider,
                host=self.host,
                cloud=self.cloud,
                category="connection-test" if is_test else "graded-text",
            )


def text_config(resolved: ResolvedAI | None = None) -> Any:
    """A client config object. Performs no network call, so it is not consent-gated;
    every path that sends with it (text_generate, measure_generate_latency) is."""
    current = resolved if resolved is not None else resolve()
    return _factory(
        current, "text_config_factory", _dialect_text_config, "text client"
    )(current)


def text_generate(
    resolved: ResolvedAI | None = None, *, response_format: dict | None = None
) -> Callable[[str], str]:
    """A `GenerateFn` for grading. Raises CloudConsentRequired / CloudDisabledError
    for a cloud host without consent, or while the cloud kill switch is set."""
    current = resolved if resolved is not None else resolve()
    restricted = _require_clearance(current, allow_probe=True)
    factory = _factory(
        current, "text_generate_factory", _dialect_text_generate, "text client"
    )
    return GuardedGenerate(
        call=factory(current, response_format=response_format),
        provider=current.provider.name,
        host=current.host,
        cloud=current.kind == "cloud",
        restricted=restricted,
    )


def vision_config(resolved: ResolvedAI | None = None) -> Any:
    """A client config object; no network call, not consent-gated (see text_config)."""
    current = resolved if resolved is not None else resolve()
    return _factory(
        current, "vision_config_factory", _dialect_vision_config, "vision client"
    )(current)


def _local_identity_config() -> Any:
    """The plain config a cloud reader's guard uses to build a throwaway
    local Ollama reader for identity crops (name, ID, title, whole pages).

    Built from `for_provider("ollama")` — the same standard local resolution
    `doctor` and the AI Setup "Test" button use — never from the currently
    active cloud provider's own host or model. Constructing a config performs
    no network call; whether a local Ollama server is actually reachable is
    only discovered when a read is attempted.
    """
    return _ollama_vision_config(for_provider("ollama"))


def vision_reader(resolved: ResolvedAI | None = None) -> Any:
    """The provider's VisionReader, always wrapped in GuardedVisionReader.

    Raises CloudConsentRequired / CloudDisabledError for a cloud host without
    consent, or while the cloud kill switch is set. Probes are never exempt.
    A student's name or ID is never handed to a cloud provider's own reader:
    for a cloud host, the guard is given a local Ollama config to redirect
    those reads to instead (see `ocr.vision_reader.GuardedVisionReader`). The
    `read_identity_locally` setting is carried onto the guard as
    `identity_reads_allowed`; when it is False, `extract_field_text` (the
    name/ID crops) is blocked outright, on a local or a cloud provider alike.
    """
    from opengrader.core.ocr.vision_reader import GuardedVisionReader

    current = resolved if resolved is not None else resolve()
    _require_clearance(current, allow_probe=False)
    inner = _factory(
        current, "vision_reader_factory", _dialect_vision_reader, "vision reader"
    )(current)
    is_cloud = current.kind == "cloud"
    return GuardedVisionReader(
        inner=inner,
        provider=current.provider.name,
        host=current.host,
        cloud=is_cloud,
        local_identity_config=_local_identity_config() if is_cloud else None,
        identity_reads_allowed=current.read_identity_locally,
    )


def with_overrides(
    resolved: ResolvedAI | None = None,
    *,
    host: str | None = None,
    vision_model: str | None = None,
    text_model: str | None = None,
    timeout_seconds: float | None = None,
) -> ResolvedAI:
    """Apply explicit host, model, and timeout flags to resolved settings.

    While the cloud kill switch is set, a host override to a non-local host is refused:
    the current host is kept and the refusal is reported by problems().
    """
    current = resolved if resolved is not None else resolve()
    new_host = _override_text(host, current.host)
    refusals = current.refusals
    if new_host != current.host and host_kind(new_host) == "cloud" and not current.cloud_allowed:
        refusals = (
            *refusals,
            f"{ALLOW_CLOUD_ENV}=0 is set on this machine, so the host override "
            f"{new_host} (not on this computer) was refused; {current.host} is used.",
        )
        new_host = current.host
    escalate = current.escalate_only
    if current.settings.escalate_only is None:
        escalate = host_kind(new_host) == "cloud" and current.read_handwriting
    return replace(
        current,
        host=new_host,
        vision_model=_override_text(vision_model, current.vision_model),
        text_model=_override_text(text_model, current.text_model),
        timeout_seconds=(
            timeout_seconds
            if timeout_seconds is not None and timeout_seconds > 0
            else current.timeout_seconds
        ),
        escalate_only=escalate,
        refusals=refusals,
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
    """Resolve a named provider from its defaults, for a probe (doctor, Test).

    Ignores the saved provider choice, host, models and toggles, but carries
    the saved consent records so a probe knows what the user agreed to. The
    result has `probe=True`: `text_generate` may send the fixed
    connection-test prompt to an unconsented cloud host, and nothing else.
    """
    saved = load_settings()
    base = resolve(AISettings(provider=name, consents=saved.consents), environ=environ)
    return with_overrides(
        replace(base, probe=True),
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
    """Check consent, credentials and, when supported, model availability before a run.

    Raises CloudDisabledError / CloudConsentRequired (both AIUnavailableError)
    first, so a CLI that already catches AIUnavailableError refuses cleanly.
    A `for_provider()` probe is not refused for missing consent.
    """
    current = resolved if resolved is not None else resolve()
    provider = current.provider
    _require_clearance(current, allow_probe=True)

    if provider.requires_api_key and not current.api_key:
        credential_problem = _credential_problem(current)
        if credential_problem:
            raise AIUnavailableError(credential_problem)
        raise AIUnavailableError(
            f"{provider.label} needs an API key. {missing_key_help(provider)} "
            f"{provider.setup_hint}"
        )

    if not provider.supports_model_listing:
        return

    wanted = current.vision_model if purpose == "vision" else current.text_model
    try:
        models = available_models(current)
    except Exception as exc:  # provider client errors are provider-specific
        if provider.name == OLLAMA.name:
            raise AIUnavailableError(ollama_unreachable_help(current.host)) from exc
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
    if type(config).__name__ == "PydanticAIConfig":
        # Matched by name so the optional backend's module is imported only
        # when its config is actually in hand.
        from opengrader.core.llm.pydantic_ai_client import generate as sdk_generate

        return sdk_generate
    from opengrader.core.llm import generate as ollama_generate

    return ollama_generate


def measure_generate_latency(
    resolved: ResolvedAI,
    *,
    role: str = "handwriting",
    prompt: str = "Reply with the single word: ready",
    generate: Callable[..., str] | None = None,
) -> LatencyMeasurement:
    """Time one short call. Never raises.

    For a cloud host, the fixed connection-test prompt may be sent without
    cloud consent (it carries no student data); any other prompt needs
    consent. Nothing is sent while the cloud kill switch is set. Each cloud call writes
    one egress line.
    """
    model = resolved.vision_model if role == "handwriting" else resolved.text_model
    is_test = prompt == _connection_test_prompt()
    cloud = resolved.kind == "cloud"
    if cloud and not resolved.cloud_allowed:
        return LatencyMeasurement(
            role=role,
            model=model,
            limit=resolved.timeout_seconds,
            error=_cloud_disabled_message(resolved),
        )
    if cloud and not is_test and not resolved.cloud_consent_given:
        return LatencyMeasurement(
            role=role,
            model=model,
            limit=resolved.timeout_seconds,
            error=_consent_required_message(resolved),
        )
    try:
        cfg = text_config(resolved)
        if type(cfg).__name__ == "PydanticAIConfig":
            # Rebuilt for the model actually timed, not copied from the text
            # model's: where this backend sends a request depends on the
            # model id, and it is checked against the host when built.
            from .manifests import _pydantic_ai_config

            cfg = _pydantic_ai_config(resolved, model=model)
        else:
            cfg = replace(cfg, model=model)
    except Exception as exc:  # noqa: BLE001 - this function never raises
        return LatencyMeasurement(
            role=role, model=model, limit=resolved.timeout_seconds, error=str(exc)
        )
    call = generate if generate is not None else _generate_for(cfg)

    start = time.perf_counter()
    try:
        call(config=cfg, prompt=prompt, num_predict=8)
        elapsed = time.perf_counter() - start
    except Exception as exc:  # noqa: BLE001
        return LatencyMeasurement(
            role=role, model=model, limit=resolved.timeout_seconds, error=str(exc)
        )
    finally:
        _record_call(
            provider=resolved.provider.name,
            host=resolved.host,
            cloud=cloud,
            category="connection-test" if is_test else "graded-text",
        )
    return LatencyMeasurement(
        role=role, model=model, limit=resolved.timeout_seconds, seconds=elapsed
    )


def egress_statement(resolved: ResolvedAI | None = None) -> str:
    """The one sentence every surface uses. See consent.egress_statement."""
    return consent.egress_statement(resolved if resolved is not None else resolve())


def data_flow_report(resolved: ResolvedAI | None = None) -> "consent.DataFlowReport":
    """Which data would leave this machine, and to which host."""
    return consent.data_flow_report(resolved if resolved is not None else resolve())
