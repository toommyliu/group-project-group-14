"""Provider manifests — how a user connects their own AI service (§7.5).

A manifest is a TOML file under `providers/<name>.toml`, beside `ai.json`
(`manifests_dir()`). `_ensure_manifests_loaded()` in the parent package reads
every file in that directory through `load_all()` below and folds whatever
parses into the provider registry with the *existing* `register_provider` —
no code change needed for a user-supplied provider to show up in the AI Setup
dropdown, in `doctor`, or in any CLI's resolution.

Four invariants make a user-editable format safe to load on every startup:

* **`kind` is derived, never declared.** §7.5 calls this "the single most
  important sentence in §7": a manifest that could say `kind = "local"` would
  let one line in a text file switch off every safeguard in §7.7, because
  every one of them (L5's identity-field pin, `--auto-ollama`, the L1 consent
  screen) reads `ProviderSpec.kind`. So `kind` is not a constructor argument
  at all — it is computed from `host` by `host_kind()` — and a manifest that
  contains a `kind` key is rejected outright, on principle, before it gets
  anywhere near that property.
* **A manifest is data, and only data.** The allowed keys name a URL, a
  dialect, two models and where a credential lives — nothing that could be
  code, a prompt, or a header. Any other top-level key is an error naming the
  offending key(s), so someone who tries to smuggle in `headers = {...}` or
  `prompt = "..."` learns immediately that this format does not take code.
  `credential_command` is the sole exception: it is parsed and stored here,
  but this module never runs it — running it needs its own consent (L6),
  which is not yet built.
* **A manifest never holds a secret.** `api_key_env` must look like an
  environment-variable name. That one rule is what stops someone pasting the
  key itself into the file, which would put it on disk and inside every
  backup archive and grader packet `core/integrity.py` and
  `core/handoff/packet.py` protect against exactly that.
* **An invalid manifest costs a provider, never a launch.** `load_all` never
  raises; every file is tried independently and a bad one becomes one string
  in the returned problem tuple, in the same spirit `load_settings` and
  `core/policy.py` already follow — a settings directory sits on the startup
  path of every command.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import DEFAULT_PROVIDER, ProviderPricing, ProviderSpec, manifests_dir

__all__ = [
    "ManifestError",
    "ManifestExistsError",
    "load_all",
    "load_file",
    "parse_manifest",
    "parse_text",
    "render_manifest",
    "install_manifest",
    "remove_manifest",
    "describe_provider",
    "example_names",
    "example_text",
    "examples_dir",
    "fingerprint",
    "CONNECTION_TEST_PROMPT",
]

# What a connection test sends. Fixed and free of student data on purpose:
# the test makes a real call to whatever host a provider names, and nothing
# that crosses that connection should be a name, an id, a rubric or an answer.
CONNECTION_TEST_PROMPT = "Reply with the single word: ready"


class ManifestError(ValueError):
    """One manifest is unusable. The message is written to be the fix."""


class ManifestExistsError(ManifestError):
    """A manifest with that name is already installed; nothing was written."""


_REQUIRED_KEYS: tuple[str, ...] = (
    "name",
    "label",
    "dialect",
    "host",
    "vision_model",
    "text_model",
)

_OPTIONAL_KEYS: tuple[str, ...] = (
    "api_key_env",
    "requires_api_key",
    "setup_hint",
    "summary",
    "supports_model_listing",
    "credential_command",
    "pricing",
)

_ALLOWED_KEYS = frozenset(_REQUIRED_KEYS) | frozenset(_OPTIONAL_KEYS)

_PRICING_KEYS = frozenset({"input_per_mtok", "output_per_mtok"})

# name is persisted into ai.json and doubles as the manifest's own filename
# stem, so it gets the conservative rule a filesystem and a JSON key both
# tolerate.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

# What stops someone pasting a key into this file: a plausible shell/POSIX
# environment-variable name, and nothing that could itself be a secret.
_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# The provider ids a manifest cannot claim, because the registry already has
# them under `register_provider`'s setdefault and a shadowing attempt is a
# mistake worth naming rather than silently losing. Today that is just
# Ollama; DEFAULT_PROVIDER is the source of truth rather than a repeated
# literal string.
_RESERVED_NAMES = frozenset({DEFAULT_PROVIDER})


def _where(path: Path | None) -> str:
    return f"{path}: " if path is not None else ""


def parse_manifest(
    data: dict[str, Any],
    *,
    path: Path | None = None,
    source: str = "user",
) -> ProviderSpec:
    """Turn one already-parsed TOML table into a `ProviderSpec`.

    Raises `ManifestError` for anything wrong with the data; never touches a
    filesystem or the registry, so it is also what `load_file` and the
    verification examples use directly.
    """
    where = _where(path)
    if not isinstance(data, dict):
        raise ManifestError(f"{where}a provider manifest must be a table of key = value pairs.")

    if "kind" in data:
        raise ManifestError(
            f"{where}a manifest cannot declare `kind` — it is derived from `host` by "
            "host_kind() (local for loopback/RFC1918/.local addresses, cloud for "
            "everything else), never trusted from a text file. Remove the `kind` key; "
            "Opengrader computes it for you."
        )

    unknown = sorted(set(data) - _ALLOWED_KEYS)
    if unknown:
        allowed = ", ".join(sorted(_ALLOWED_KEYS))
        raise ManifestError(
            f"{where}unknown key(s) {', '.join(unknown)!s} — a manifest is data, not "
            f"code, and takes only: {allowed}."
        )

    missing = [key for key in _REQUIRED_KEYS if key not in data]
    if missing:
        raise ManifestError(f"{where}missing required key(s): {', '.join(missing)}.")

    name = _require_str(data, "name", where)
    if not _NAME_RE.match(name):
        raise ManifestError(
            f"{where}name {name!r} must match {_NAME_RE.pattern!r} — it is written into "
            "ai.json and doubles as a filename, so it stays lowercase, alphanumeric, "
            "and only '.', '_' or '-' besides."
        )
    if name in _RESERVED_NAMES:
        raise ManifestError(
            f"{where}name {name!r} is a built-in provider's id and cannot be reused by "
            "a manifest. Pick a different name."
        )

    label = _require_str(data, "label", where)
    dialect = _require_str(data, "dialect", where)
    host = _require_str(data, "host", where)
    vision_model = _require_str(data, "vision_model", where)
    text_model = _require_str(data, "text_model", where)

    from opengrader.core.llm.dialects import DIALECT_NAMES

    if dialect not in DIALECT_NAMES:
        known = ", ".join(DIALECT_NAMES)
        raise ManifestError(f"{where}dialect {dialect!r} is not one of: {known}.")

    _validate_host(host, where)

    api_key_env = _optional_str(data, "api_key_env", where)
    if api_key_env and not _ENV_VAR_RE.match(api_key_env):
        raise ManifestError(
            f"{where}api_key_env {api_key_env!r} does not look like an environment "
            "variable name. This field names a variable to read a key from — it must "
            "never hold the key itself, so it has to match "
            f"{_ENV_VAR_RE.pattern!r}."
        )

    requires_api_key = _optional_bool(
        data, "requires_api_key", where, default=bool(api_key_env)
    )
    setup_hint = _optional_str(data, "setup_hint", where)
    summary = _optional_str(data, "summary", where)
    supports_model_listing = _optional_bool(
        data, "supports_model_listing", where, default=False
    )
    credential_command = _optional_str_list(data, "credential_command", where)
    pricing = _optional_pricing(data, where)

    return ProviderSpec(
        name=name,
        label=label,
        summary=summary,
        default_host=host,
        default_vision_model=vision_model,
        default_text_model=text_model,
        setup_hint=setup_hint,
        api_key_env=api_key_env,
        requires_api_key=requires_api_key,
        supports_model_listing=supports_model_listing,
        dialect=dialect,
        source=source,
        manifest_path=str(path) if path is not None else "",
        pricing=pricing,
        credential_command=credential_command,
    )


def _require_str(data: dict[str, Any], key: str, where: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{where}{key!r} must be a non-empty string.")
    return value.strip()


def _optional_str(data: dict[str, Any], key: str, where: str) -> str:
    if key not in data:
        return ""
    value = data[key]
    if not isinstance(value, str):
        raise ManifestError(f"{where}{key!r} must be a string.")
    return value.strip()


def _optional_bool(data: dict[str, Any], key: str, where: str, *, default: bool) -> bool:
    if key not in data:
        return default
    value = data[key]
    if not isinstance(value, bool):
        raise ManifestError(f"{where}{key!r} must be true or false.")
    return value


def _optional_str_list(data: dict[str, Any], key: str, where: str) -> tuple[str, ...]:
    if key not in data:
        return ()
    value = data[key]
    if not isinstance(value, list) or not value:
        raise ManifestError(f"{where}{key!r} must be a non-empty list of strings.")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ManifestError(f"{where}{key!r} must contain only non-empty strings.")
        result.append(item)
    return tuple(result)


def _optional_pricing(data: dict[str, Any], where: str) -> ProviderPricing | None:
    if "pricing" not in data:
        return None
    table = data["pricing"]
    if not isinstance(table, dict):
        raise ManifestError(f"{where}'pricing' must be a table.")
    unknown = sorted(set(table) - _PRICING_KEYS)
    if unknown:
        allowed = ", ".join(sorted(_PRICING_KEYS))
        raise ManifestError(
            f"{where}unknown key(s) in [pricing]: {', '.join(unknown)} — allowed: {allowed}."
        )
    input_per_mtok = _pricing_number(table, "input_per_mtok", where)
    output_per_mtok = _pricing_number(table, "output_per_mtok", where)
    return ProviderPricing(input_per_mtok=input_per_mtok, output_per_mtok=output_per_mtok)


def _pricing_number(table: dict[str, Any], key: str, where: str) -> float:
    if key not in table:
        return 0.0
    value = table[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ManifestError(f"{where}[pricing].{key} must be a number.")
    number = float(value)
    if number < 0:
        raise ManifestError(f"{where}[pricing].{key} must not be negative.")
    return number


def _validate_host(host: str, where: str) -> None:
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(host)
    except ValueError as exc:
        raise ManifestError(f"{where}host {host!r} could not be parsed: {exc}") from exc
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ManifestError(
            f"{where}host {host!r} must be a full http:// or https:// URL, including a "
            "hostname."
        )


def parse_text(text: str, *, path: Path | None = None) -> ProviderSpec:
    """Validate a manifest's TOML source. Raises `ManifestError`."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"{_where(path)}not valid TOML: {exc}") from exc
    return parse_manifest(data, path=path, source="user")


def load_file(path: Path) -> ProviderSpec:
    """Read and validate one manifest file. Raises `ManifestError`."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManifestError(f"{path}: could not read the file: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError(f"{path}: not valid UTF-8: {exc}") from exc
    return parse_text(text, path=path)


def install_manifest(
    text: str,
    *,
    force: bool = False,
    directory: Path | None = None,
) -> tuple[Path, ProviderSpec]:
    """Validate a manifest's source and write it to `providers/<name>.toml`.

    The one place a manifest is written: `opengrader-providers add` and the
    TUI's provider screen both come through here, so "validate before a byte
    reaches disk" and "never overwrite unless asked" hold in one place. The
    text is written verbatim, comments included, because it is the user's
    file and not ours to reformat. The file is named after the manifest's
    `name`, not after wherever it came from, so the id written into `ai.json`
    and the file that defines it cannot drift apart.

    This only writes. Callers follow it with `ai_config.reload_manifests()`
    so the provider is live without a restart.
    """
    spec = parse_text(text)
    target_dir = directory if directory is not None else manifests_dir()
    dest = target_dir / f"{spec.name}.toml"
    if dest.exists() and not force:
        raise ManifestExistsError(f"{dest} already exists.")
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"could not write {dest}: {exc}") from exc
    return dest, spec


def remove_manifest(spec: ProviderSpec) -> Path:
    """Delete the file a user-added provider came from. Raises `ManifestError`.

    A built-in provider has no file and is refused rather than silently
    ignored: a caller that asked to remove Ollama has misunderstood something
    and should be told. Callers follow this with `ai_config.reload_manifests()`.
    """
    if spec.source != "user" or not spec.manifest_path:
        raise ManifestError(
            f"{spec.name!r} is a built-in provider and cannot be removed. Only a "
            "provider you added yourself can be."
        )
    path = Path(spec.manifest_path)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ManifestError(f"could not delete {path}: {exc}") from exc
    return path


def describe_provider(spec: ProviderSpec) -> list[tuple[str, str]]:
    """What a person should know about a provider, as (label, value) rows.

    Shared by `opengrader-providers list` and the TUI's provider screen so the
    two cannot word the one fact that matters differently. That fact is the
    first row: whether choosing this provider sends student work off this
    machine, read from `sends_data_off_machine` like every other surface
    that states it.

    The key row says only whether the variable is set, never any part of its
    value (L8).
    """
    if spec.sends_data_off_machine:
        where = f"CLOUD: data leaves this machine and is sent to {spec.default_host}"
    else:
        where = "LOCAL: nothing leaves this machine"

    if spec.api_key_env:
        from . import resolve_api_key

        state = "set" if resolve_api_key(spec) else "not set"
        key = f"{spec.api_key_env} ({state})"
    elif spec.credential_command:
        key = "no environment variable; uses credential_command"
    else:
        key = "not needed"

    rows = [
        ("kind", where),
        ("dialect", spec.dialect or "(built-in client, no dialect)"),
        ("host", spec.default_host),
        ("vision", spec.default_vision_model),
        ("text", spec.default_text_model),
        ("api key", key),
    ]
    if spec.credential_command:
        rows.append(
            (
                "credential_command",
                " ".join(spec.credential_command)
                + " (not run automatically: running it needs a consent step "
                "that does not exist yet)",
            )
        )
    rows.append(
        (
            "model listing",
            "yes" if spec.supports_model_listing else "no (checked by credential only)",
        )
    )
    if spec.source == "user":
        rows.append(("source", f"user manifest: {spec.manifest_path}"))
    else:
        rows.append(("source", "built-in"))
    return rows


def load_all(
    *,
    directory: Path | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[list[ProviderSpec], tuple[str, ...]]:
    """Every `*.toml` in `directory` (default: `manifests_dir()`), sorted by name.

    Never raises. A directory that does not exist, or cannot be listed, is
    indistinguishable from an empty one — that is L0's "a fresh install has
    no cloud provider registered at all" holding even when the directory was
    never created. A file that fails to parse costs that one provider; it is
    reported as a problem string and every other file is still tried.
    """
    target = directory if directory is not None else manifests_dir(environ=environ)
    try:
        entries = sorted(
            (p for p in target.iterdir() if p.is_file() and p.suffix == ".toml"),
            key=lambda p: p.name,
        )
    except OSError:
        return [], ()

    specs: list[ProviderSpec] = []
    problems: list[str] = []
    for entry in entries:
        try:
            specs.append(load_file(entry))
        except ManifestError as exc:
            problems.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - a bad file costs a provider, never a launch
            problems.append(f"{entry}: {exc}")
    return specs, tuple(problems)


def fingerprint(spec: ProviderSpec) -> str:
    """`sha256` of the fields L2 (§7.7) re-prompts consent over.

    Consent (L1) is given to a specific service; if editing a manifest could
    silently repoint it at a different host, dialect, credential variable or
    credential command, consent given once would cover whatever the file
    later became. L2 exists to close that: `AISettings` stores this digest
    alongside the consent, and a change to it means "prompt again."

    Fields are joined with `\\x00`, a byte no legitimate field value here
    contains, so `("a", "bc")` and `("ab", "c")` never collide — a plain `+`
    concatenation would conflate them.
    """
    parts = (spec.name, spec.default_host, spec.dialect, spec.api_key_env, *spec.credential_command)
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def render_manifest(
    *,
    name: str,
    label: str,
    dialect: str,
    host: str,
    vision_model: str,
    text_model: str,
    api_key_env: str = "",
    setup_hint: str = "",
    summary: str = "",
    supports_model_listing: bool = False,
    credential_command: Sequence[str] = (),
    pricing: ProviderPricing | None = None,
) -> str:
    """Render a manifest a human could have typed — for `opengrader-providers add`.

    Validates through `parse_manifest` before formatting a single line, so
    this never hands a caller TOML that would fail to load back in.
    """
    data: dict[str, Any] = {
        "name": name,
        "label": label,
        "dialect": dialect,
        "host": host,
        "vision_model": vision_model,
        "text_model": text_model,
    }
    if api_key_env:
        data["api_key_env"] = api_key_env
    if setup_hint:
        data["setup_hint"] = setup_hint
    if summary:
        data["summary"] = summary
    if supports_model_listing:
        data["supports_model_listing"] = True
    if credential_command:
        data["credential_command"] = list(credential_command)
    if pricing is not None and pricing.stated:
        data["pricing"] = {
            "input_per_mtok": pricing.input_per_mtok,
            "output_per_mtok": pricing.output_per_mtok,
        }
    parse_manifest(data, source="user")  # fail loudly, not on the next load

    lines = [
        f"name         = {json.dumps(name)}",
        f"label        = {json.dumps(label)}",
        f"dialect      = {json.dumps(dialect)}",
        f"host         = {json.dumps(host)}",
        f"vision_model = {json.dumps(vision_model)}",
        f"text_model   = {json.dumps(text_model)}",
    ]
    if api_key_env:
        lines.append(f"api_key_env  = {json.dumps(api_key_env)}")
    if setup_hint:
        lines.append(f"setup_hint   = {json.dumps(setup_hint)}")
    if summary:
        lines.append(f"summary      = {json.dumps(summary)}")
    if supports_model_listing:
        lines.append("supports_model_listing = true")
    if credential_command:
        lines.append(f"credential_command = {json.dumps(list(credential_command))}")
    text = "\n".join(lines) + "\n"
    if pricing is not None and pricing.stated:
        text += "\n[pricing]\n"
        text += f"input_per_mtok  = {pricing.input_per_mtok}\n"
        text += f"output_per_mtok = {pricing.output_per_mtok}\n"
    return text


# --- Shipped examples --------------------------------------------------
#
# These live in examples/ beside this file, NOT in providers/, and load_all()
# only ever reads providers/. That split is deliberate, not an oversight to
# "fix" by pointing load_all at this directory too: L0 (§7.7) requires that a
# fresh install register no cloud provider at all, pinned by a test asserting
# registered_providers()[0].kind == "local" against an empty config
# directory. §7.5's "ship built-in providers as manifests too, so the
# dropdown is not empty" is satisfied by a user *copying one in* — today via
# `example_text()`, and by `opengrader-providers add --example NAME`, which
# reads example_names()/example_text() and writes the result into
# providers/. Until a human (or that command) does that, an example is just a
# file. Auto-loading it would put a cloud provider one keystroke away from
# every fresh install, which is exactly what L0 exists to prevent.


def examples_dir() -> Path:
    return Path(__file__).parent / "examples"


def example_names() -> tuple[str, ...]:
    """The shipped examples' names, sorted — what `--example` accepts."""
    try:
        entries = examples_dir().iterdir()
    except OSError:
        return ()
    return tuple(sorted(p.stem for p in entries if p.is_file() and p.suffix == ".toml"))


def example_text(name: str) -> str:
    """One example's raw TOML source, to write out or to show a user.

    The name is matched against the listing rather than joined onto a path:
    this is reached from a command line, and a name is not a route to an
    arbitrary file just because it happens to be a filename stem.
    """
    if name not in example_names():
        known = ", ".join(example_names())
        raise ManifestError(f"no example named {name!r}. Known examples: {known}.")
    path = examples_dir() / f"{name}.toml"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        known = ", ".join(example_names())
        raise ManifestError(f"no example named {name!r}. Known examples: {known}.") from exc
