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


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"})


def host_kind(host: str) -> Literal["local", "cloud"]:
    """Is this address on this machine (or this network), or is it out there?

    **The single most important function in this module.** A manifest is
    user-editable text that decides where student work is sent, so a provider
    must not be able to *declare* itself local: one line in a TOML file would
    otherwise switch off every safeguard §7.7 describes. The kind is therefore
    computed from the address the request will actually go to, and
    `ProviderSpec.kind` is a derived property rather than a constructor
    argument.

    Loopback, RFC1918, link-local and `.local` are local; everything else,
    including anything unparseable, is cloud. Unparseable resolving to *cloud*
    is deliberate: the failure that matters is treating a remote host as
    local, so an address we cannot read gets the careful answer, not the
    convenient one.
    """
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
    """What a service charges, so a pre-flight can state a number (L4).

    Optional, and declared by the provider rather than discovered: no API
    reports its own price list, and a wrong estimate shown confidently is
    worse than no estimate at all — so absent stays absent.
    """

    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0

    @property
    def stated(self) -> bool:
        return self.input_per_mtok > 0 or self.output_per_mtok > 0


@dataclass(frozen=True)
class ProviderSpec:
    """One AI service, as a record rather than a branch.

    Everything here is data a user could write in a TOML file, which is the
    requirement §7.2 exists to satisfy: a provider that must be added by
    editing this module is not a provider the user can choose. The optional
    `*_factory` callables are the exception, and only the built-in Ollama
    entry uses them — a provider that names a `dialect` instead gets the
    generic implementations above, which is how a manifest can supply no code
    at all and still work.

    `kind` is conspicuously absent from the fields: it is derived from the
    host (see `host_kind`), because a manifest that could declare itself local
    could switch off every safeguard in §7.7 with one line.
    """

    name: str  # Persisted provider IDs must not be renamed.
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
    # The transcription seam (core/ocr/vision_reader.VisionReader). Separate
    # from vision_config_factory because a provider's *client config* and the
    # *reader built over it* are not the same thing: a dialect-backed provider
    # has a reader and no Ollama-shaped config at all.
    vision_reader_factory: Callable[["ResolvedAI"], Any] | None = None
    # Can this service enumerate the models it will answer for? Ollama can
    # (/api/tags); most hosted APIs cannot, and a few that can charge for the
    # privilege. When False, ensure_ready() falls back to checking that a
    # credential resolves — the point is to fail *before* a long extraction
    # starts, and either check achieves that.
    supports_model_listing: bool = True
    list_models_factory: Callable[["ResolvedAI"], list[str]] | None = None
    text_generate_factory: Callable[..., Callable[[str], str]] | None = None
    # The wire shape this service speaks: one of core.llm.dialects.DIALECT_NAMES.
    # Empty means the spec brings its own factories instead, which is how the
    # built-in Ollama entry works and how a future non-HTTP provider could.
    dialect: str = ""
    # Where this provider came from, so a screen can say *you added this one*
    # and `doctor` can tell a shipped example from something hand-written.
    source: Literal["builtin", "user"] = "builtin"
    manifest_path: str = ""
    pricing: ProviderPricing | None = None
    # A command whose stdout is the API key — git's credential-helper idea, so
    # an instructor can use a keychain, `pass` or `op` without Opengrader ever
    # holding a secret. Parsed and displayed here; **not executed**, because
    # executing a command named by a user-editable file needs the L6 consent
    # screen, and that ships with the rest of §7.7 in Phase 5.
    credential_command: tuple[str, ...] = ()

    @property
    def kind(self) -> Literal["local", "cloud"]:
        """Derived from the host, never declared. See `host_kind`."""
        return host_kind(self.default_host)

    @property
    def sends_data_off_machine(self) -> bool:
        return self.kind != "local"

    @property
    def allows_identity_fields(self) -> bool:
        """May this provider be shown a student's name or nine-digit ID?

        False for anything off this machine, with no override anywhere — L5,
        and §7.8 Q3 on why it is a hard block rather than a setting. The pin
        itself lands with Phase 5; this is the fact it reads.
        """
        return self.kind == "local"


class AIUnavailableError(RuntimeError):
    """The configured AI provider cannot serve this run, and we know it now.

    Raised by ensure_ready() so a caller can fail (or degrade) before a long
    job starts, without having to name a provider's own error type. Each
    client keeps its own exception for wire-level failures; this one means
    "the pre-flight says don't bother".
    """


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


# --- Providers that are nothing but a dialect and a host --------------------
#
# A manifest cannot supply code (§7.5), so every user-defined provider shares
# these four functions and differs only in the record they read. That is the
# whole bet of §7.2 made mechanical: the service is data, the shape is code,
# and there is exactly one copy of the code.


def _dialect_config(resolved: "ResolvedAI", *, model: str) -> Any:
    from opengrader.core.llm.dialect_client import DialectConfig

    return DialectConfig(
        dialect=resolved.provider.dialect,
        host=resolved.host,
        model=model,
        timeout_seconds=resolved.timeout_seconds,
        api_key=resolved.api_key,
        label=resolved.provider.label,
        # A server on this machine gets today's behaviour — fail at once. A
        # hosted one gets the backoff it needs to ride out a rate limit.
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
        "Install Ollama, then: ollama pull qwen2.5vl:7b && ollama serve. "
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

# Ollama stays first, and a fresh install has nothing else in here (L0).
_REGISTRY: dict[str, ProviderSpec] = {OLLAMA.name: OLLAMA}

_manifests_loaded = False
_manifest_problems: tuple[str, ...] = ()


def register_provider(spec: ProviderSpec) -> None:
    _REGISTRY[spec.name] = spec


def _ensure_manifests_loaded() -> None:
    """Fold the user's provider manifests into the registry, once per process.

    Lazy rather than at import: this module is imported by the TUI at startup
    and by every CLI's argument parser, and reading a directory as a side
    effect of an import is the kind of thing that makes a test suite depend on
    whose laptop it runs on. The first question anyone asks the registry pays
    for it instead.

    A manifest that will not parse costs that one provider and nothing else —
    the same degradation rule `load_settings` and `core/policy.py` already
    follow. The reason is on the startup path of every command: a stray
    character in a settings directory must cost a setting, never a launch.
    """
    global _manifests_loaded, _manifest_problems
    if _manifests_loaded:
        return
    _manifests_loaded = True
    try:
        from . import manifests as _manifests

        specs, problems = _manifests.load_all()
    except Exception as exc:  # noqa: BLE001 - see the docstring; never a launch
        _manifest_problems = (f"Could not read provider manifests: {exc}",)
        return
    skipped: list[str] = []
    for spec in specs:
        if spec.name in _REGISTRY:
            # Two files claiming one id: the first (by filename) wins, and the
            # other is reported rather than silently dropped — otherwise an
            # edit to the losing file would appear to do nothing at all.
            owner = _REGISTRY[spec.name].manifest_path or "a built-in provider"
            skipped.append(
                f"{spec.manifest_path}: name {spec.name!r} is already used by "
                f"{owner}. Give one of them a different name."
            )
            continue
        _REGISTRY[spec.name] = spec
    _manifest_problems = (*problems, *skipped)


def reload_manifests() -> tuple[str, ...]:
    """Drop every user-defined provider and read the directory again.

    For the tests, and for `opengrader-providers` after it writes a file — a
    user who has just added a manifest should not have to restart to see
    whether it took.
    """
    global _manifests_loaded, _manifest_problems
    for name, spec in list(_REGISTRY.items()):
        if spec.source == "user":
            del _REGISTRY[name]
    _manifests_loaded = False
    _manifest_problems = ()
    _ensure_manifests_loaded()
    return _manifest_problems


def manifest_problems() -> tuple[str, ...]:
    """Why a manifest was skipped — surfaced by `problems()` and by `doctor`."""
    _ensure_manifests_loaded()
    return _manifest_problems


def manifests_dir(*, environ: dict[str, str] | None = None) -> Path:
    """`providers/` beside `ai.json`, wherever that turned out to be."""
    return settings_path(environ=environ).parent / "providers"


def dialect_names() -> tuple[str, ...]:
    """The wire dialects a manifest may name, for a CLI choice list or a form.

    Here so that code above the provider boundary can offer the list without
    importing `core.llm.dialects`, which would be naming a dialect (§7.3).
    """
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
    def kind(self) -> Literal["local", "cloud"]:
        """Derived from the *resolved* host, not from the provider's default.

        Deliberately not `self.provider.kind`: a host can be overridden by a
        flag, an environment variable or the settings file, and pointing the
        local provider at a public address is a real way to send a cohort's
        handwriting off this machine. The safeguards read this, so it has to
        follow the address the request is going to.
        """
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
    """Read the key out of the environment. Never write one anywhere.

    The rule this module has always held: the settings file stores the
    variable's *name*, so a key cannot travel inside a backup archive
    (`core/integrity.py`) or a grader packet (`core/handoff/packet.py`).
    Nothing here softens that.

    What is new is the second line of defence: a key we resolve is handed to
    the transport's redaction registry, so that if it does reach an error
    message — a connection-test log, `run-ledger.jsonl`, an exception `doctor`
    prints — it is scrubbed on the way out. Storing it correctly and never
    printing it are different guarantees, and L8 asks for both.
    """
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


def _factory(
    current: ResolvedAI, attribute: str, dialect_default: Callable[..., Any], role: str
) -> Callable[..., Any]:
    """The provider's own factory, or the generic dialect one, or an error.

    A manifest supplies no code (§7.5), so a user-defined provider arrives with
    every factory unset and a dialect name instead. Resolving that here rather
    than filling four closures into each spec keeps `ProviderSpec` a record
    that can be built from a TOML file — which is the whole point of it being
    one.
    """
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
    """A `GenerateFn` for the configured provider — the text seam, as a callable.

    `core/grading/llm_grader.py` has always taken an injected
    `GenerateFn: Callable[[str], str]` and never named a client (finding F3, the
    seam that already worked). This is the factory that finally lets every
    caller hand it one without naming a client either: before it, each CLI
    closed over an `OllamaConfig` and called Ollama's `generate` by name, which
    is F1 surviving in the one place the bridges did not reach.
    """
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
    """The configured provider's VisionReader — the seam, not a client.

    This is what every consumer above the provider boundary should ask for.
    vision_config() below it stays, because a provider's reader is built over
    its client config, but nothing outside core/ocr and core/llm needs to know
    that a client config exists at all.
    """
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
    """Fold explicit flag values onto an already-resolved configuration.

    This is the *flag* tier of the flag > env > file > default precedence this
    module documents. Every AI-capable CLI takes its argparse defaults from
    resolve() and then has to let an explicitly typed flag win; before this
    function existed each one did that by constructing a provider's client
    config by hand, which is why the registry could only ever decide *which
    model string to type in*, never *which client to build*.

    A None or blank value means "not supplied, keep what resolve() decided",
    so a caller can forward argparse results unconditionally without first
    working out which of them the user actually typed.

    Note that this deliberately does not re-run resolve(): the provider, the
    API key and every feature toggle are already settled by the time a flag is
    applied, and a flag may not change which provider is in use.
    """
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
    """Resolve against one *named* provider, ignoring what the user configured.

    For probes that ask about a specific service rather than about the active
    one — `doctor`'s "is the local Ollama server up?" is the motivating case,
    and it must keep giving a straight answer about Ollama on a machine whose
    configured provider is something else entirely.

    Reads neither the settings file nor the `OPENGRADER_AI_*` variables, so
    the answer depends only on the arguments and the provider's own defaults.
    That matters for a probe: a diagnostic that silently picks up ambient
    configuration is a diagnostic you cannot trust when it disagrees with you.

    The one exception is the API key, read from `environ` (default: the
    process environment) through the provider's own `api_key_env`. A key has
    nowhere else to live, since `resolve_api_key` never persists one, so a
    probe that ignored the environment could never test a provider that
    needs one.

    Prefer this to building a ResolvedAI by hand — every field this fills in is
    a field a caller would otherwise have to keep in sync with the dataclass.
    """
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
    """Every model the configured provider says it will answer for.

    Empty when the provider cannot enumerate its models — which is not an
    error, just an absence: see supports_model_listing.
    """
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
    """Fail now, rather than part-way through a cohort.

    The property worth keeping from Ollama's ensure_model_available is not
    that it lists models — it is that a misconfigured run stops before it has
    half-processed a class. So this checks whatever the provider can actually
    be asked:

      * a provider that enumerates models is asked whether it has this one;
      * a provider that does not is asked only for a credential, because a
        missing key is the failure that is both most likely and cheapest to
        detect.

    Raises AIUnavailableError, naming what to do about it. Callers that can
    degrade (Tesseract-only extraction, say) should catch it and carry on;
    callers that cannot should exit 2.
    """
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
    """The raw `generate(config=, prompt=, num_predict=)` that speaks `config`.

    `text_config()` hands back whichever client config the provider builds:
    an `OllamaConfig` for the built-in entry, a `DialectConfig` for anything a
    manifest declared. Calling Ollama's `generate` on the second would send an
    Ollama-shaped body to `<host>/api/generate` on someone else's service, so
    the config's own type picks the function, not an assumption.
    """
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
