"""One place the AI is configured, and the seam a cloud provider plugs into.

Until now every tool that could use a model carried its own copy of the same
four questions — which provider, which host, which model, how confident is
confident enough — and asked them again in every setup-screen section. That is
the wrong shape twice over. For the *user* it is busywork: the answers are
properties of the machine, not of this particular split or this particular
extraction, so re-typing them per section is a chance to get them inconsistent
rather than a choice worth offering. For the *codebase* it meant the literal
``"qwen2.5vl:7b"`` and ``"http://127.0.0.1:11434"`` appeared in a dozen
argparse defaults, so "point Opengrader at a different model" was a dozen
edits.

This module is the single answer. It holds:

* :class:`AISettings` — what the user chose, persisted as JSON in their user
  config directory (not in an assignment folder: it describes the *machine*,
  and a folder handed to a TA must not carry a host only the teacher can
  reach, still less an API key).
* :class:`ProviderSpec` + the provider registry — *what kinds of AI exist*.
  One provider is registered today (local Ollama). The registry is the
  extension point described at the bottom of this docstring.
* :func:`resolve` — settings + environment + provider defaults folded into the
  concrete :class:`ResolvedAI` every caller actually wants.
* :func:`text_config` / :func:`vision_config` — bridges to the existing
  ``OllamaConfig`` / ``OllamaVisionConfig`` clients, so nothing downstream had
  to change to start reading these settings.

**Blank means "follow the default", and that is load-bearing.** Every model,
host and threshold field defaults to an empty string or ``None`` rather than to
today's literal. A settings file therefore records only what the user
deliberately changed, so upgrading Opengrader's recommended model moves an
unconfigured user forward instead of leaving them pinned to whatever was
current on the day they first opened the AI Setup screen. It is also what lets
the AI Setup screen show a real placeholder ("blank = qwen2.5vl:7b") rather
than pre-filling a value the user then can't tell apart from their own choice.

**Precedence, highest first:** an explicit CLI flag, then an ``OPENGRADER_AI_*``
environment variable, then the settings file, then the provider's own default.
The CLI layer owns the first of those (it can tell "the user typed
``--llm-model x``" from "argparse filled in the default"); everything below it
lives here. The environment tier sits above the file on purpose — it is how a
lab machine, a CI run or a test pins a model for one process without editing
the user's saved preferences.

**Nothing here reaches the network or imports a client.** The bridges import
their client module lazily inside the function, so ``core/ai_config`` stays
cheap enough for the TUI to import at startup (see
``tests/unit/tui/test_startup_imports.py``) and pure enough to unit-test with
no server anywhere.

Adding a cloud provider
-----------------------

The registry below is deliberately not a two-branch ``if provider ==
"ollama"``. A provider is a *record* — where it lives, which models it offers
for each of the two jobs, whether it needs a key and under which environment
variable, and how to build the two client configs — so adding one is three
edits in one place and **no change at all** to the CLIs, the setup screen or
the AI Setup screen, all of which iterate :func:`registered_providers` and
render whatever they find:

1. Write the transport. ``core/llm/ollama_transport.py`` is already
   domain-free (it takes a host, a model, a prompt and an ``error_factory``);
   a sibling ``core/llm/openai_transport.py`` speaking the Chat Completions
   shape is the same size. Both grading and vision go through one transport
   per provider — that consolidation was done in 2026-08-08 precisely so a new
   provider is written once, not twice.
2. Register a :class:`ProviderSpec` with ``kind="cloud"``, its ``api_key_env``
   set, and ``default_host`` pointing at the API base URL. ``requires_api_key``
   is what makes :func:`ResolvedAI.problems` report a missing key as a
   configuration error *before* a long extraction starts, and what makes the AI
   Setup screen show the key row at all.
3. Point the spec's ``text_config_factory`` / ``vision_config_factory`` at
   config objects your transport understands. They are typed loosely
   (``object``) because the two Ollama config dataclasses are already two
   different types; the contract is only that whatever comes out is what that
   provider's client accepts.

The two things that must **not** happen when that day comes: an API key must
never be written into :class:`AISettings` (only the *name* of the environment
variable holding it is stored — see :func:`resolve_api_key`), and the offline
default must not move. Opengrader is offline-first by design; a cloud provider
is an opt-in a teacher chooses with full knowledge that student handwriting
then leaves the machine, which is why :func:`ResolvedAI.sends_data_off_machine`
exists as a fact the UI can state plainly rather than as something buried in a
provider name.
"""

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

# ── Built-in defaults ────────────────────────────────────────────────────────
#
# These are duplicated from the client dataclasses on purpose *not* to be a
# second source of truth but to be the *first* one: the clients keep their own
# field defaults so they still work when constructed directly (tests do this
# constantly), while these are what an unconfigured user gets. They are asserted
# equal in tests/unit/core/test_ai_config.py, so the pair can never drift.
DEFAULT_PROVIDER = "ollama"
DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_VISION_MODEL = "qwen2.5vl:7b"
DEFAULT_TEXT_MODEL = "qwen2.5vl:7b"
DEFAULT_TIMEOUT_SECONDS = 120.0

# The confidence an AI grade suggestion must clear before it is written as a
# pre-selected suggestion. Mirrors ``cli/automated_grading.DEFAULT_LLM_MIN_CONFIDENCE``
# (which imports it back from here, so there is exactly one number).
DEFAULT_MIN_CONFIDENCE = 0.7

# The maths reader. Deliberately a plain string here rather than math_ocr's
# ``MathBackendName`` Literal: importing that module for a type would drag its
# availability probing into every CLI's argument parser, and the value is
# validated against ``MATH_BACKEND_NAMES`` where it is actually used.
DEFAULT_MATH_BACKEND = "vlm"

# Environment overrides. One per settings field, all prefixed so a stray
# ``HOST`` or ``AI_MODEL`` in a user's shell can never be picked up by accident.
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
# Deliberately no ENV_MIN_CONFIDENCE_MEASURED: that field is provenance (did a
# human type the confidence, or did they accept llm-eval's recommendation?),
# not a knob, and an environment variable would let a process assert a fact
# about how a number was chosen rather than merely choose the number.

# Points at the settings file itself. Used by the test suite (so a test never
# reads or writes the developer's real preferences) and by anyone running two
# configurations side by side on one machine.
ENV_CONFIG_PATH = "OPENGRADER_AI_CONFIG"

# The on-disk file. A schema version is written from the first release so a
# later format change can migrate rather than guess.
SETTINGS_FILENAME = "ai.json"
SETTINGS_VERSION = 1


# ── Providers ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProviderSpec:
    """One kind of AI backend Opengrader knows how to talk to.

    Everything a UI needs to *offer* this provider and everything a caller
    needs to *build a client* for it lives here, so no consumer ever branches
    on the provider name.
    """

    name: str
    """Stable id written into the settings file. Never renamed once shipped."""

    label: str
    """What a teacher sees in the dropdown — plain words, not a package name."""

    kind: Literal["local", "cloud"]
    """``local`` = the model runs on this machine and nothing leaves it."""

    summary: str
    """One line under the dropdown: what choosing this actually means."""

    default_host: str
    """Server URL or API base. What a blank host field falls back to."""

    default_vision_model: str
    """Reads handwriting off an answer crop."""

    default_text_model: str
    """Grades a transcribed free response against the rubric."""

    setup_hint: str
    """The one command or step that makes this provider work. Shown when a
    connection test fails, so the fix is never a web search."""

    api_key_env: str = ""
    """Name of the environment variable holding the key. **Never the key.**"""

    requires_api_key: bool = False
    """When true, a missing key is a configuration error, not a runtime one."""

    text_config_factory: Callable[["ResolvedAI"], Any] | None = None
    vision_config_factory: Callable[["ResolvedAI"], Any] | None = None
    """How to turn resolved settings into whatever this provider's client
    accepts. Typed as ``Any`` because Ollama already uses two distinct config
    dataclasses for the two jobs; the only contract is that the client this
    provider ships with understands what comes out."""

    @property
    def sends_data_off_machine(self) -> bool:
        """True when using this provider means student work leaves the machine.

        A property of the provider rather than a note in the UI copy, so every
        surface that mentions a provider can state it the same way.
        """
        return self.kind != "local"


def _ollama_text_config(resolved: "ResolvedAI") -> Any:
    """Resolved settings → ``core.llm.OllamaConfig``.

    Imported inside the function: ``core/ai_config`` is imported by the TUI at
    startup and by every CLI's argument parser, and neither should pay for a
    client module it may never call.
    """
    from opengrader.core.llm import OllamaConfig

    return OllamaConfig(
        model=resolved.text_model,
        host=resolved.host,
        timeout_seconds=resolved.timeout_seconds,
    )


def _ollama_vision_config(resolved: "ResolvedAI") -> Any:
    """Resolved settings → ``core.ocr.ollama_vlm.OllamaVisionConfig``."""
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

# Insertion-ordered: the first registered provider is the one an unconfigured
# install uses, and the order here is the order the UI offers.
_REGISTRY: dict[str, ProviderSpec] = {OLLAMA.name: OLLAMA}


def register_provider(spec: ProviderSpec) -> None:
    """Add a provider to the registry.

    The whole extension point: every consumer reads the registry, so a
    provider registered at import time appears in the AI Setup dropdown, in
    ``doctor``'s report and in every CLI's resolution with no further edits.
    Re-registering the same name replaces it, which is what lets a test install
    a fake provider and restore the real one afterwards.
    """
    _REGISTRY[spec.name] = spec


def registered_providers() -> tuple[ProviderSpec, ...]:
    """Every provider this build can be pointed at, in offer order."""
    return tuple(_REGISTRY.values())


def get_provider(name: str) -> ProviderSpec:
    """Look a provider up by name, falling back to the default.

    An unknown name degrades rather than raising: a settings file written by a
    newer Opengrader (or hand-edited with a typo) must not stop the tool from
    starting — the misconfiguration surfaces through
    :meth:`ResolvedAI.problems`, where it can be reported next to its fix.
    """
    return _REGISTRY.get(name.strip()) or _REGISTRY[DEFAULT_PROVIDER]


# ── Settings ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AISettings:
    """What the user chose, exactly as it is written to disk.

    Every optional field's "unset" value is empty/``None``, meaning *follow the
    default* — see the module docstring. Frozen, so a screen edits by
    :func:`dataclasses.replace` and can never mutate a shared instance.
    """

    provider: str = DEFAULT_PROVIDER
    host: str = ""
    vision_model: str = ""
    text_model: str = ""
    math_backend: str = ""
    timeout_seconds: float | None = None
    min_confidence: float | None = None

    auto_start: bool = True
    """Start a local server and pull a missing model rather than stopping to
    ask. Meaningless for a cloud provider, and :class:`ResolvedAI` forces it
    off for one — there is nothing on this machine to start."""

    read_handwriting: bool = False
    """The vision model reads student handwriting: the extractor's
    ``--llm-responses``, the splitter's page-number VLM tier, and the answer
    key's scanned-PDF path all gate on this one switch."""

    escalate_only: bool = False
    """A refinement of :attr:`read_handwriting`: read every answer with the
    fast local OCR first and spend a model call only where it read badly,
    rather than transcribing everything with the model. Means nothing when
    :attr:`read_handwriting` is off."""

    transcribe_math: bool = False
    """Also a refinement of :attr:`read_handwriting`: transcribe handwritten
    maths as LaTeX rather than leaving it as OCR text."""

    grade_written: bool = False
    """The text model suggests rubric options for written answers."""

    auto_grade: bool = True
    """Run the auto-grade stage automatically once extraction finishes.
    Defaults to ``True`` because that is today's pipeline behaviour
    (``opengrader-run`` grades unless ``--no-auto-grade``); this field only
    makes that choice reachable from AI Setup rather than changing it."""

    require_calibration: bool = False
    """Refuse to write AI grade suggestions until the confidence gate has
    actually been measured, rather than merely available. The codebase builds
    ``llm-eval`` precisely so nobody has to guess ``--llm-min-confidence``;
    this is the switch that makes running it mandatory."""

    min_confidence_measured: bool = False
    """Provenance, not a knob: ``True`` when :attr:`AISettings.min_confidence`
    came from accepting ``llm-eval``'s recommendation, ``False`` when a human
    typed it. There is deliberately no environment variable for this field —
    a knob is a choice a deployment gets to make, but this is a fact about
    where a value came from, and letting the environment assert that fact
    would let a lie in."""

    def to_dict(self) -> dict[str, Any]:
        """The JSON body, omitting every field still at its default.

        A file that records only deliberate choices is one a human can read,
        and — more importantly — one whose silence keeps following Opengrader's
        defaults as they improve.
        """
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
        """Parse a settings body, ignoring anything unrecognized.

        Every field is coerced defensively and a bad value falls back to the
        default rather than raising: this file is user-editable and sits on the
        startup path of every tool, so a stray character in it must cost a
        setting, never a launch.
        """
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


# The public name for that same rule. The AI Setup screen validates its timeout
# box with it, and going through one function is what guarantees the screen
# accepts exactly what ``from_dict`` accepts: a second copy of "a number greater
# than zero, or nothing" is a second chance for the two to disagree about a
# value the user typed and the file then silently dropped.
parse_timeout_seconds = _as_positive_float


def _as_unit_float(value: Any) -> float | None:
    """A 0-1 confidence, or ``None`` when it isn't one."""
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


# ── Where the file lives ─────────────────────────────────────────────────────


def _config_dir() -> Path:
    """The per-user config directory, matching what ``packaging/`` installs to.

    Deliberately hand-rolled rather than a new ``platformdirs`` dependency:
    three ``if``s against one more package in an offline-first tool that ships
    as a double-clickable app is the cheaper trade, and the paths here are the
    same ones ``packaging/install.sh`` already chose.
    """
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
    """Where the AI settings are read from and written to.

    ``OPENGRADER_AI_CONFIG`` overrides it outright — that is how the test
    suite keeps its hands off a developer's real preferences, and how one
    machine runs two configurations side by side.
    """
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
    """Read the saved settings, or the defaults when there are none.

    Absent, unreadable and malformed all degrade to the defaults for the same
    reason ``core/policy.py`` does: this sits on the startup path of every tool
    and a corrupt preferences file must never be able to stop grading.
    """
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
    """Write the settings and return where they went.

    Written whole rather than merged: the file is small, single-writer, and
    owned by one screen, so a read-modify-write cycle would add a race for no
    benefit. Errors are *not* swallowed here — unlike loading, a save that
    silently failed would leave the user believing they had configured
    something they hadn't.
    """
    target = path or settings_path(environ=environ)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(settings.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


# ── Resolution ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ResolvedAI:
    """Settings + environment + provider defaults, with nothing left blank.

    This is what callers want: every field is a usable value, so no consumer
    ever repeats an ``or DEFAULT_...`` fallback and no two of them can disagree
    about what "unset" meant.
    """

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
    """The raw choices this was resolved from, kept so a UI can tell a value
    the user typed from one that came from a default."""

    @property
    def sends_data_off_machine(self) -> bool:
        return self.provider.sends_data_off_machine

    @property
    def uses_ai(self) -> bool:
        """True when any model would be called during an ordinary run."""
        return self.read_handwriting or self.grade_written

    def problems(self) -> tuple[str, ...]:
        """Configuration errors that will bite before a model is ever called.

        Returned as plain sentences a CLI can print and a screen can show —
        each naming its own fix, the same rule ``core/environment.py`` follows.
        Deliberately *only* things checkable without touching the network:
        whether the server is actually up is ``doctor``'s job, not a
        configuration question.
        """
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
        # Reads self.settings.min_confidence_measured, not a resolved copy —
        # provenance is a fact about the saved choice, not something a
        # provider default or an env override could ever supply, the same
        # reason the unknown-provider check above reads self.settings.provider.
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
    """Read this provider's API key out of the environment, never off disk.

    The settings file records the *name* of the variable, so a key is never
    written by Opengrader, never lands in a backup archive, and never travels
    inside a grader packet. A local provider has no key and gets ``None``.
    """
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
    """Fold saved settings, environment overrides and provider defaults into one.

    Pass ``settings`` to resolve a value the user is editing but has not saved
    (the AI Setup screen's connection test does this); omit it to resolve
    whatever is on disk, which is what every CLI does.
    """
    env = os.environ if environ is None else environ
    base = settings if settings is not None else load_settings(path, environ=env)

    # The environment tier. Applied to the *settings*, not to the resolved
    # values, so an override behaves exactly as if the user had typed it into
    # the AI Setup screen — including "blank means default".
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
        # min_confidence_measured is not overridden here — see the comment by
        # its ENV_* declaration above: no environment variable exists for it.
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
        # A cloud provider has nothing on this machine to boot or pull, so the
        # flag is forced off rather than left to mean something it can't.
        auto_start=base.auto_start and provider.kind == "local",
        api_key=resolve_api_key(provider, environ=env),
        read_handwriting=base.read_handwriting,
        # A refinement of a switch that is off is off, so no consumer has to
        # remember to check both fields itself.
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


# ── Bridges to the clients ───────────────────────────────────────────────────


def text_config(resolved: ResolvedAI | None = None) -> Any:
    """The grading client's config for the configured provider.

    ``None`` resolves from disk, which is the common case: a caller that just
    wants "the text model the user chose" writes ``ai_config.text_config()``.
    """
    current = resolved if resolved is not None else resolve()
    factory = current.provider.text_config_factory
    if factory is None:  # pragma: no cover - only a half-registered provider
        raise ValueError(
            f"provider {current.provider.name!r} has no text client configured"
        )
    return factory(current)


def vision_config(resolved: ResolvedAI | None = None) -> Any:
    """The handwriting-reading client's config for the configured provider."""
    current = resolved if resolved is not None else resolve()
    factory = current.provider.vision_config_factory
    if factory is None:  # pragma: no cover - only a half-registered provider
        raise ValueError(
            f"provider {current.provider.name!r} has no vision client configured"
        )
    return factory(current)


# ── Timeout measurement ──────────────────────────────────────────────────────
#
# This is the one place ``timeout_seconds`` is resolved, so the thing that
# measures whether it is actually enough belongs beside it rather than in the
# AI Setup screen or in a CLI.

# A measurement below half the limit leaves so much headroom that recommending
# anything would be noise — the run wasn't close to timing out, so a new
# number would just be a number.
TIMEOUT_WARN_FRACTION = 0.5

# 4x, not 1x or 2x: the timed call is deliberately tiny (see
# ``measure_generate_latency``'s ``num_predict=8``) so it generates a token or
# two, while a real handwriting transcription generates a whole answer, and
# the first call of a run also pays for Ollama loading the model's weights —
# a cost later calls in the same run don't pay again. Recommending a bare
# multiple of the measured time would under-shoot both of those.
TIMEOUT_HEADROOM_MULTIPLE = 4.0


@dataclass(frozen=True)
class LatencyMeasurement:
    """How long one warm-up call to a model actually took, or why it didn't."""

    role: str
    """"handwriting" or "grading" — the JOB, matching the labels the AI Setup
    screen already uses, not the provider or model name."""

    model: str
    limit: float
    """The timeout this run would have used, for comparison in ``describe()``
    and ``share_of_limit``."""

    seconds: float | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        """True when the call actually completed and returned a duration."""
        return self.seconds is not None and not self.error

    @property
    def share_of_limit(self) -> float | None:
        """How much of the timeout the call used up, or ``None`` on failure."""
        if self.seconds is None:
            return None
        return self.seconds / self.limit

    def describe(self) -> str:
        """One plain sentence for a log or a report screen."""
        label = self.role.capitalize()
        if not self.ok:
            return f"{label} model {self.model} did not answer: {self.error}"
        return (
            f"{label} model {self.model} responded in {self.seconds:.1f} s "
            f"(a first call also loads the model). Your limit is {self.limit:g} s."
        )


def recommend_timeout(measured: float, limit: float) -> float | None:
    """A roomier limit, or ``None`` when the current one is already fine.

    Pure and side-effect-free so it is trivial to unit-test at its boundaries
    without a server anywhere.
    """
    if measured <= 0 or measured < TIMEOUT_WARN_FRACTION * limit:
        return None
    raw = measured * TIMEOUT_HEADROOM_MULTIPLE
    # Round up to the next multiple of 30 seconds — a number a human would
    # actually type into the settings screen, not a jittery float.
    rounded = 30.0 * -(-raw // 30.0)
    if rounded <= limit:
        # Not actually an increase over what is configured now, so
        # recommending it would be noise rather than help.
        return None
    return rounded


def measure_generate_latency(
    resolved: ResolvedAI,
    *,
    role: str = "handwriting",
    prompt: str = "Reply with the single word: ready",
    generate: Callable[..., str] | None = None,
) -> LatencyMeasurement:
    """Time one warm-up call to the model this run would actually use.

    Deliberately sends a *text* prompt to the configured model even for the
    "handwriting" role rather than inventing a throwaway image: the dominant
    cost being measured is Ollama loading that model's weights into memory,
    and a text-only prompt to the same model pays that cost in full — so this
    is an honest measurement, not an approximation, and it needs no image
    fixture to exist.
    """
    model = resolved.vision_model if role == "handwriting" else resolved.text_model
    # Imported lazily inside the function, not at module scope: the TUI
    # imports core.ai_config at startup (see
    # tests/unit/tui/test_startup_imports.py), and that import must never pay
    # for pulling in a network client it may never call.
    from opengrader.core.llm import generate as _default_generate

    call = generate if generate is not None else _default_generate
    cfg = replace(text_config(resolved), model=model)

    start = time.perf_counter()
    try:
        # Capped tiny so the measurement is dominated by model load and first
        # token, not by how long a full answer would take to generate.
        call(config=cfg, prompt=prompt, num_predict=8)
        elapsed = time.perf_counter() - start
    except Exception as exc:  # noqa: BLE001 - never raises, see docstring
        return LatencyMeasurement(
            role=role, model=model, limit=resolved.timeout_seconds, error=str(exc)
        )
    return LatencyMeasurement(
        role=role, model=model, limit=resolved.timeout_seconds, seconds=elapsed
    )
