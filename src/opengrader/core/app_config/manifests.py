from __future__ import annotations

import importlib.util
import json
import re
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
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
    "set_manifest_models",
    "describe_provider",
    "example_names",
    "example_text",
    "examples_dir",
    "CONNECTION_TEST_PROMPT",
    "PYDANTIC_AI_DIALECT",
    "PydanticAIPrefixInfo",
    "PYDANTIC_AI_PREFIXES",
]

# Connection tests may reach a remote host; never include student data here.
CONNECTION_TEST_PROMPT = "Reply with the single word: ready"

# `dialect = "pydantic-ai"` selects a *backend*, not a wire dialect: unlike
# "ollama"/"openai-chat"/"anthropic-messages" (core/llm/dialects/, raw HTTP
# through the one transport — see transport.py), this one hands the
# request to the optional `pydantic-ai`
# package so Opengrader can reach services the raw dialects cannot
# authenticate to: AWS Bedrock (SigV4 / the AWS credential chain), Google
# Vertex AI (Application Default Credentials / a service account), Gemini's
# native API, and similar. It is an *additional* backend behind the same
# `ProviderSpec` seam, not a replacement for the transport — every other
# manifest keeps speaking raw HTTP. See `core/llm/pydantic_ai_client.py` and
# `core/ocr/pydantic_ai_reader.py` for the implementation, and this module's
# `_parse_pydantic_ai_manifest` for the validation described below.
PYDANTIC_AI_DIALECT = "pydantic-ai"


@dataclass(frozen=True)
class PydanticAIPrefixInfo:
    """One row of the pydantic-ai provider-prefix allowlist.

    `vision_model`/`text_model` for this backend are Pydantic AI model ids,
    `"prefix:model-name"` (e.g. `"anthropic:claude-haiku-4-5-20251001"`). The
    prefix is never trusted blind — it is looked up here, and an unknown
    prefix is a rejected manifest, the same posture the four dialects take
    with an unknown `dialect` string.
    """

    display_host: str
    """The service's real public hostname — becomes `default_host` (and so
    `kind`, via `host_kind()`) when the manifest omits `host`. Always a real
    cloud endpoint, so a manifest that says nothing about `host` is always
    `kind = "cloud"` for this backend, never `local` by omission."""

    honours_base_url: bool
    """Does this Pydantic AI provider class accept a caller-supplied base
    URL? If not, a manifest `host` for this prefix is rejected outright
    ("kind is derived, never declared" only holds if a `host` nobody reads
    cannot be used to *claim* a kind another provider does not actually
    deliver)."""

    api_key_required: bool
    """`False` only for the two prefixes that authenticate through a
    platform credential chain instead of a bearer key (`bedrock`: AWS;
    `google-cloud`: Google ADC/service account) — `api_key_env` stays
    optional for those, matching the existing "name the variable, never the
    key" rule when a key *is* supplied on top of the chain."""

    auth_note: str
    """Shown in `describe_provider` and used as the default `setup_hint`."""


PYDANTIC_AI_PREFIXES: dict[str, PydanticAIPrefixInfo] = {
    "openai": PydanticAIPrefixInfo(
        display_host="https://api.openai.com",
        honours_base_url=True,
        api_key_required=True,
        auth_note="API key from platform.openai.com.",
    ),
    "anthropic": PydanticAIPrefixInfo(
        display_host="https://api.anthropic.com",
        honours_base_url=True,
        api_key_required=True,
        auth_note="API key from console.anthropic.com.",
    ),
    "groq": PydanticAIPrefixInfo(
        display_host="https://api.groq.com",
        honours_base_url=True,
        api_key_required=True,
        auth_note="API key from console.groq.com.",
    ),
    "mistral": PydanticAIPrefixInfo(
        display_host="https://api.mistral.ai",
        honours_base_url=True,
        api_key_required=True,
        auth_note="API key from console.mistral.ai.",
    ),
    "cohere": PydanticAIPrefixInfo(
        display_host="https://api.cohere.com",
        honours_base_url=False,
        api_key_required=True,
        auth_note=(
            "API key from dashboard.cohere.com. This backend cannot point "
            "Cohere at a custom host."
        ),
    ),
    "bedrock": PydanticAIPrefixInfo(
        display_host="https://bedrock-runtime.amazonaws.com",
        honours_base_url=True,
        api_key_required=False,
        auth_note=(
            "Uses the AWS credential chain (environment variables, "
            "~/.aws/credentials, an EC2/ECS instance role) unless "
            "api_key_env names a Bedrock API key."
        ),
    ),
    "google": PydanticAIPrefixInfo(
        display_host="https://generativelanguage.googleapis.com",
        honours_base_url=True,
        api_key_required=True,
        auth_note="Gemini Developer API key from aistudio.google.com.",
    ),
    "google-cloud": PydanticAIPrefixInfo(
        display_host="https://aiplatform.googleapis.com",
        honours_base_url=True,
        api_key_required=False,
        auth_note=(
            "Uses Application Default Credentials (`gcloud auth "
            "application-default login`) or GOOGLE_APPLICATION_CREDENTIALS "
            "unless api_key_env is set. This is Vertex AI, not the Gemini "
            "Developer API — use the \"google\" prefix for that."
        ),
    ),
}


def _pydantic_ai_available() -> bool:
    """Cheap: `find_spec` does not import the package, only locates it."""
    return importlib.util.find_spec("pydantic_ai") is not None


class ManifestError(ValueError):
    """Invalid provider manifest."""


class ManifestExistsError(ManifestError):
    """Provider manifest already exists."""


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

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_RESERVED_NAMES = frozenset({DEFAULT_PROVIDER})


def _where(path: Path | None) -> str:
    return f"{path}: " if path is not None else ""


def parse_manifest(
    data: dict[str, Any],
    *,
    path: Path | None = None,
    source: str = "user",
) -> ProviderSpec:
    """Validate a parsed TOML table without filesystem side effects."""
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

    dialect_hint = data.get("dialect")
    is_pydantic_ai = (
        isinstance(dialect_hint, str) and dialect_hint.strip() == PYDANTIC_AI_DIALECT
    )
    # `host` is the one required key this backend does not require: with no
    # host, `default_host` becomes the prefix's own public endpoint (below),
    # which is still a `host_kind() == "cloud"` value — never a silent local.
    required_keys = (
        tuple(key for key in _REQUIRED_KEYS if key != "host")
        if is_pydantic_ai
        else _REQUIRED_KEYS
    )
    missing = [key for key in required_keys if key not in data]
    if missing:
        raise ManifestError(f"{where}missing required key(s): {', '.join(missing)}.")

    name = _require_str(data, "name", where)
    if not _NAME_RE.match(name):
        raise ManifestError(
            f"{where}name {name!r} must match {_NAME_RE.pattern!r} — it is written into "
            "config.json and doubles as a filename, so it stays lowercase, alphanumeric, "
            "and only '.', '_' or '-' besides."
        )
    if name in _RESERVED_NAMES:
        raise ManifestError(
            f"{where}name {name!r} is a built-in provider's id and cannot be reused by "
            "a manifest. Pick a different name."
        )

    label = _require_str(data, "label", where)
    dialect = _require_str(data, "dialect", where)
    vision_model = _require_str(data, "vision_model", where)
    text_model = _require_str(data, "text_model", where)

    if dialect == PYDANTIC_AI_DIALECT:
        host = _optional_str(data, "host", where)
        return _parse_pydantic_ai_manifest(
            data,
            where=where,
            path=path,
            source=source,
            name=name,
            label=label,
            host=host,
            vision_model=vision_model,
            text_model=text_model,
        )

    host = _require_str(data, "host", where)

    from opengrader.core.llm.dialects import DIALECT_NAMES

    if dialect not in DIALECT_NAMES:
        known = ", ".join((*DIALECT_NAMES, PYDANTIC_AI_DIALECT))
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
    from . import host_kind

    if parsed.scheme != "https" and host_kind(host) == "cloud":
        # Over plain http, every answer image, the rubric and the API key
        # would cross the internet readable by anyone on the path.
        raise ManifestError(
            f"{where}host {host!r} is not on this computer or its local network, so it "
            "must use https://: over plain http:// student work and the API key would "
            "be sent unencrypted."
        )


def _split_pydantic_ai_model(value: str, *, field: str, where: str) -> tuple[str, str]:
    if ":" not in value:
        raise ManifestError(
            f"{where}{field} {value!r} must be a Pydantic AI model id of the form "
            '"prefix:model-name" (e.g. "anthropic:claude-haiku-4-5-20251001") — the '
            "prefix selects which service this backend calls."
        )
    prefix, _, model_name = value.partition(":")
    if not prefix or not model_name:
        raise ManifestError(
            f"{where}{field} {value!r} must be a Pydantic AI model id of the form "
            '"prefix:model-name", with both halves non-empty.'
        )
    return prefix, model_name


def _parse_pydantic_ai_manifest(
    data: dict[str, Any],
    *,
    where: str,
    path: Path | None,
    source: str,
    name: str,
    label: str,
    host: str,
    vision_model: str,
    text_model: str,
) -> ProviderSpec:
    """Validate the `dialect = "pydantic-ai"` shape (see the module docstring
    above `PYDANTIC_AI_DIALECT`). Split out of `parse_manifest` because this
    backend's rules — prefix allowlist, same-prefix requirement, host only for
    a prefix that honours one — are its own, not a variation on the four wire
    dialects' rules.
    """
    vision_prefix, _ = _split_pydantic_ai_model(vision_model, field="vision_model", where=where)
    text_prefix, _ = _split_pydantic_ai_model(text_model, field="text_model", where=where)
    if vision_prefix != text_prefix:
        raise ManifestError(
            f"{where}vision_model and text_model must use the same Pydantic AI "
            f"provider prefix — got {vision_prefix!r} and {text_prefix!r}."
        )

    prefix = vision_prefix
    table = PYDANTIC_AI_PREFIXES.get(prefix)
    if table is None:
        known = ", ".join(sorted(PYDANTIC_AI_PREFIXES))
        raise ManifestError(
            f"{where}unknown Pydantic AI provider prefix {prefix!r} in vision_model / "
            f"text_model. Known prefixes: {known}."
        )

    # "kind is derived, never declared" holds here too: a `host` cannot
    # manufacture a `local` kind out of a prefix that
    # never actually sends there. Accepted only for a prefix whose Pydantic AI
    # provider class honours a caller-supplied base URL; everything else is
    # answered by the SDK's own endpoint (the AWS/Google credential chain
    # picks a region, for instance — there is no URL for a manifest to name).
    if host:
        if not table.honours_base_url:
            raise ManifestError(
                f"{where}provider prefix {prefix!r} does not accept a custom `host` — "
                "its SDK always chooses its own endpoint. Remove the `host` key."
            )
        _validate_host(host, where)
        default_host = host
    else:
        default_host = table.display_host

    api_key_env = _optional_str(data, "api_key_env", where)
    if api_key_env and not _ENV_VAR_RE.match(api_key_env):
        raise ManifestError(
            f"{where}api_key_env {api_key_env!r} does not look like an environment "
            "variable name. This field names a variable to read a key from — it must "
            "never hold the key itself, so it has to match "
            f"{_ENV_VAR_RE.pattern!r}."
        )
    if table.api_key_required and not api_key_env:
        raise ManifestError(
            f"{where}provider prefix {prefix!r} needs `api_key_env` — {table.auth_note}"
        )

    requires_api_key = _optional_bool(
        data, "requires_api_key", where, default=bool(api_key_env)
    )
    setup_hint = _optional_str(data, "setup_hint", where) or table.auth_note
    summary = _optional_str(data, "summary", where)

    if _optional_bool(data, "supports_model_listing", where, default=False):
        raise ManifestError(
            f'{where}dialect "{PYDANTIC_AI_DIALECT}" cannot set supports_model_listing '
            "= true — this backend does not enumerate models."
        )

    credential_command = _optional_str_list(data, "credential_command", where)
    pricing = _optional_pricing(data, where)

    return ProviderSpec(
        name=name,
        label=label,
        summary=summary,
        default_host=default_host,
        default_vision_model=vision_model,
        default_text_model=text_model,
        setup_hint=setup_hint,
        api_key_env=api_key_env,
        requires_api_key=requires_api_key,
        text_config_factory=_pydantic_ai_text_config,
        vision_config_factory=_pydantic_ai_vision_config,
        vision_reader_factory=_pydantic_ai_vision_reader,
        text_generate_factory=_pydantic_ai_text_generate,
        supports_model_listing=False,
        list_models_factory=None,
        dialect=PYDANTIC_AI_DIALECT,
        source=source,
        manifest_path=str(path) if path is not None else "",
        pricing=pricing,
        credential_command=credential_command,
    )


# ---------------------------------------------------------------------------
# Factories wired onto the `ProviderSpec` this backend builds. Set
# explicitly here — rather than left `None` to fall through to
# `app_config`'s dialect defaults — because `dialect = "pydantic-ai"` is not
# one of the three wire dialects `_dialect_text_config` et al. know how to
# speak; without these, `app_config._factory()` would hand a "pydantic-ai"
# dialect name to `core.llm.dialect_client.get_dialect`, which does not
# recognise it. `core/app_config/__init__.py` is not touched by this module.
# ---------------------------------------------------------------------------


def _pydantic_ai_base_url(resolved: Any, *, model: str) -> str | None:
    """`None` unless the manifest named a real custom endpoint.

    Keyed off `model` (the actual vision/text model id about to be requested)
    rather than the provider spec's own default, so a `with_overrides`/CLI
    override to a different prefix is not silently pointed at the wrong
    prefix's endpoint. `resolved.host` is always populated (from the
    manifest's `host`, or from `default_host` — this table's own canonical
    hostname — when the manifest left it out). Only the first case is a base
    URL the SDK should be told about; the second is just what
    `describe_provider`/the dropdown show, and passing it on would fight the
    SDK's own (often more specific, e.g. versioned) default endpoint for no
    reason.
    """
    prefix, _, _ = model.partition(":")
    table = PYDANTIC_AI_PREFIXES.get(prefix)
    if table is None or not table.honours_base_url:
        return None
    if resolved.host == table.display_host:
        return None
    return resolved.host


def _check_pydantic_ai_endpoint(resolved: Any, *, model: str) -> None:
    """Refuse a model whose requests would go somewhere other than `resolved.host`.

    Everything that decides whether a call needs cloud consent, whether the
    guard redirects name/ID reads to a local model, and what the consent text
    and egress log name as the destination is computed from `resolved.host`.
    For this backend the SDK only goes to that host when the model id's
    prefix accepts a base URL; otherwise it goes to the prefix's own public
    endpoint. The model id can differ from the manifest's (a model saved in
    AI setup, or a `--ollama-model`/`--llm-model` flag), so a prefix that
    ignores base URLs, or one this build does not know, could send student
    work to a service nobody consented to while the host still reads as
    local. Such a model is refused before any client is built.
    """
    from . import AIUnavailableError, host_kind

    prefix, _, _ = model.partition(":")
    table = PYDANTIC_AI_PREFIXES.get(prefix)
    label = getattr(resolved.provider, "label", "") or "This provider"
    if table is None:
        known = ", ".join(sorted(PYDANTIC_AI_PREFIXES))
        raise AIUnavailableError(
            f"{label}: model {model!r} has no known Pydantic AI provider prefix "
            f"({known}), so where it would send data cannot be known. Nothing was sent."
        )
    if table.honours_base_url:
        return
    same_endpoint = resolved.host.strip().rstrip("/") == table.display_host.rstrip("/")
    if not same_endpoint:
        raise AIUnavailableError(
            f"{label}: model {model!r} always goes to {table.display_host} (its "
            f"SDK takes no custom host), not to the configured host "
            f"{resolved.host} ({host_kind(resolved.host)}). Nothing was sent; use a "
            "model from this provider's own prefix."
        )


def _pydantic_ai_config(resolved: Any, *, model: str) -> Any:
    from opengrader.core.llm.pydantic_ai_client import PydanticAIConfig

    _check_pydantic_ai_endpoint(resolved, model=model)
    return PydanticAIConfig(
        model=model,
        api_key=resolved.api_key,
        base_url=_pydantic_ai_base_url(resolved, model=model),
        timeout_seconds=resolved.timeout_seconds,
        max_attempts=1 if resolved.kind == "local" else 4,
        label=resolved.provider.label,
    )


def _pydantic_ai_text_config(resolved: Any) -> Any:
    return _pydantic_ai_config(resolved, model=resolved.text_model)


def _pydantic_ai_vision_config(resolved: Any) -> Any:
    return _pydantic_ai_config(resolved, model=resolved.vision_model)


def _pydantic_ai_vision_reader(resolved: Any) -> Any:
    from opengrader.core.ocr.pydantic_ai_reader import PydanticAIVisionReader

    return PydanticAIVisionReader(config=_pydantic_ai_vision_config(resolved))


def _pydantic_ai_text_generate(
    resolved: Any, *, response_format: dict | None = None
):
    from opengrader.core.llm import pydantic_ai_client

    config = _pydantic_ai_text_config(resolved)

    def _call(prompt: str) -> str:
        return pydantic_ai_client.generate(
            config=config, prompt=prompt, response_schema=response_format
        )

    return _call


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
    """Validate and write a manifest without overwriting unless forced."""
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


# One top-level `vision_model = "..."` / `text_model = "..."` line: what is
# before the value, the quoted value, and whatever follows it (a comment).
_MODEL_LINE_RE = re.compile(
    r"""^(?P<lead>\s*(?P<key>vision_model|text_model)\s*=\s*)"""
    r"""(?P<value>"(?:[^"\\]|\\.)*"|'[^']*')(?P<tail>.*)$"""
)


def set_manifest_models(
    spec: ProviderSpec, *, vision_model: str, text_model: str
) -> ProviderSpec:
    """Change which models a provider file asks for, and nothing else in it.

    A surgical rewrite of the two lines: the file's comments, spacing and
    every other key are left exactly as they were, because a user may have
    written them. The result is validated before it replaces the file, so a
    value the format would reject never reaches the disk.
    """
    if spec.source != "user" or not spec.manifest_path:
        raise ManifestError(
            f"{spec.name!r} is a built-in provider; its models are chosen in AI setup."
        )
    path = Path(spec.manifest_path)
    try:
        original = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ManifestError(f"{path}: could not read the file: {exc}") from exc

    wanted = {"vision_model": vision_model.strip(), "text_model": text_model.strip()}
    replaced: set[str] = set()
    lines = original.splitlines(keepends=True)
    for index, line in enumerate(lines):
        body = line.rstrip("\r\n")
        if body.lstrip().startswith("["):
            # A [table] header: every key after it belongs to that table.
            break
        match = _MODEL_LINE_RE.match(body)
        if match is None or match.group("key") in replaced:
            continue
        key = match.group("key")
        ending = line[len(body):]
        lines[index] = (
            f"{match.group('lead')}{json.dumps(wanted[key])}{match.group('tail')}{ending}"
        )
        replaced.add(key)
    missing = sorted(set(wanted) - replaced)
    if missing:
        raise ManifestError(
            f"{path}: could not find the {' and '.join(missing)} line to change. "
            "Edit the file by hand, then press 'Re-read provider files'."
        )
    text = "".join(lines)
    updated = parse_text(text, path=path)
    if (
        updated.default_vision_model != wanted["vision_model"]
        or updated.default_text_model != wanted["text_model"]
    ):
        raise ManifestError(f"{path}: the models could not be changed safely.")
    try:
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        raise ManifestError(f"could not write {path}: {exc}") from exc
    return updated


def describe_provider(spec: ProviderSpec) -> list[tuple[str, str]]:
    if spec.sends_data_off_machine:
        where = f"CLOUD: data leaves this machine and is sent to {spec.default_host}"
    else:
        where = "LOCAL: nothing leaves this machine"

    if spec.api_key_env:
        from . import api_key_source, keystore

        source = api_key_source(spec)
        if source == "keystore":
            key = f"saved in {keystore.status().name}"
        else:
            state = "set" if source == "environment" else "not set"
            key = f"{spec.api_key_env} ({state})"
    elif spec.credential_command:
        key = "no environment variable; uses credential_command"
    elif spec.dialect == PYDANTIC_AI_DIALECT:
        key = "not set as an env var — see the auth note below"
    else:
        key = "not needed"

    is_pydantic_ai = spec.dialect == PYDANTIC_AI_DIALECT
    dialect_label = (
        "Pydantic AI (optional extra) — a backend, not a wire dialect"
        if is_pydantic_ai
        else (spec.dialect or "(built-in client, no dialect)")
    )

    rows = [
        ("kind", where),
        ("dialect", dialect_label),
        ("host", spec.default_host),
        ("vision", spec.default_vision_model),
        ("text", spec.default_text_model),
        ("api key", key),
    ]
    if is_pydantic_ai:
        prefix, _, _ = spec.default_vision_model.partition(":")
        info = PYDANTIC_AI_PREFIXES.get(prefix)
        if info is not None:
            rows.append(("auth", info.auth_note))
        if not _pydantic_ai_available():
            rows.append(
                (
                    "status",
                    "pydantic-ai is not installed — install with "
                    "`uv sync --extra pydantic-ai`",
                )
            )
    if spec.credential_command:
        rows.append(
            (
                "credential_command",
                " ".join(spec.credential_command)
                + " (runs only after you approve the exact command line — "
                "Helper Tools → AI providers)",
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
) -> tuple[list[ProviderSpec], tuple[str, ...]]:
    """Load valid manifests and report errors without stopping startup."""
    target = directory if directory is not None else manifests_dir()
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
            spec = load_file(entry)
        except ManifestError as exc:
            problems.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001 - a bad file costs a provider, never a launch
            problems.append(f"{entry}: {exc}")
            continue
        # A manifest naming this backend is well-formed data even
        # when the optional package is not installed on this machine — it
        # costs this one provider, named clearly, never the launch.
        if spec.dialect == PYDANTIC_AI_DIALECT and not _pydantic_ai_available():
            problems.append(
                f"{entry}: {spec.name!r} uses the optional \"pydantic-ai\" backend, "
                "which is not installed. Install it with `uv sync --extra "
                "pydantic-ai`, then restart Opengrader."
            )
            continue
        specs.append(spec)
    return specs, tuple(problems)


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
    """Validate fields and render a provider manifest."""
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
    parse_manifest(data, source="user")

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


def examples_dir() -> Path:
    return Path(__file__).parent / "examples"


def example_names() -> tuple[str, ...]:
    try:
        entries = examples_dir().iterdir()
    except OSError:
        return ()
    return tuple(sorted(p.stem for p in entries if p.is_file() and p.suffix == ".toml"))


def example_text(name: str) -> str:
    if name not in example_names():
        known = ", ".join(example_names())
        raise ManifestError(f"no example named {name!r}. Known examples: {known}.")
    path = examples_dir() / f"{name}.toml"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        known = ", ".join(example_names())
        raise ManifestError(f"no example named {name!r}. Known examples: {known}.") from exc
