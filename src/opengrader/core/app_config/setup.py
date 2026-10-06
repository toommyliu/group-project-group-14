"""What the guided "Set up a provider" flow knows, kept out of the screen.

The screen (`tui/ui/screens/provider_setup_screen.py`) only lays this out:
which services can be offered and what each one needs (`setup_choices`),
whether a provider answers and — in plain words — why not (`probe_provider`),
and which models it offers (`list_models`).

For a service that is not in the list there is a second way in: a prompt the
user pastes into whatever AI assistant they already use (`assistant_prompt`),
which interviews them and writes the provider file, and the function that
takes the assistant's reply back (`install_reply`). The prompt is generated
from the validator's own key lists, so the two cannot drift apart.

Everything a choice says is read from the shipped example manifests, so a new
example appears in the flow without a second list to keep in step.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from . import manifests as _manifests

__all__ = [
    "ProbeResult",
    "SetupChoice",
    "SUBSCRIPTION_NOTE",
    "assistant_prompt",
    "choice_for",
    "explain_failure",
    "extract_manifest",
    "install_reply",
    "is_advanced_example",
    "list_models",
    "local_models",
    "provider_status",
    "setup_choices",
    "probe_provider",
]

# Shown where the user is sent to get a key. People reasonably expect the
# subscription they already pay for to cover this, and it does not.
SUBSCRIPTION_NOTE = (
    "A chat subscription — ChatGPT Plus or Pro, Claude Pro or Max, Gemini "
    "Advanced — does not include a key. Keys come from the service's "
    "separate developer account, which is billed by how much you use. "
    "Anyone can open one with a personal email and a payment card; it does "
    "not have to belong to a school or a company."
)

_URL_RE = re.compile(r"https://[^\s,;)]+")

# Hosts under these names are reserved for documentation (RFC 2606/6761): an
# example that uses one is a template to fill in, not a service to call.
_PLACEHOLDER_SUFFIXES = (
    ".example",
    ".example.com",
    ".example.net",
    ".example.org",
    ".example.edu",
    ".invalid",
)


@dataclass(frozen=True)
class SetupChoice:
    """One service the guided setup can offer."""

    name: str
    label: str
    summary: str
    setup_hint: str
    host: str
    api_key_env: str
    # The page a key is created on, taken from the setup hint; "" if none.
    key_url: str
    # Signs in with a cloud account (AWS, Google Cloud) rather than a pasted
    # key. Offered, but marked, and the key step is skipped.
    advanced: bool
    # Already has a provider file on this computer.
    installed: bool
    # Why it cannot be used on this install; "" when it can.
    unavailable: str = ""

    @property
    def title(self) -> str:
        """The one-line name in the list."""
        if self.advanced:
            return f"{self.label} (advanced)"
        return self.label


def _key_url(hint: str) -> str:
    match = _URL_RE.search(hint or "")
    return match.group(0).rstrip(".") if match else ""


def _is_placeholder(host: str) -> bool:
    from .consent import hostname_of

    name = hostname_of(host or "").lower()
    return any(
        name == suffix.lstrip(".") or name.endswith(suffix)
        for suffix in _PLACEHOLDER_SUFFIXES
    )


def _missing_backend(spec: object) -> str:
    if getattr(spec, "dialect", "") != _manifests.PYDANTIC_AI_DIALECT:
        return ""
    if _manifests._pydantic_ai_available():
        return ""
    return (
        f"{getattr(spec, 'label', 'This service')} needs an optional add-on "
        "(pydantic-ai) that this copy of Opengrader was installed without, so "
        "it cannot be used here yet."
    )


def _choice(spec: object, *, installed: bool) -> SetupChoice:
    hint = getattr(spec, "setup_hint", "")
    env = getattr(spec, "api_key_env", "")
    return SetupChoice(
        name=spec.name,  # type: ignore[attr-defined]
        label=spec.label,  # type: ignore[attr-defined]
        summary=getattr(spec, "summary", ""),
        setup_hint=hint,
        host=getattr(spec, "default_host", ""),
        api_key_env=env,
        key_url=_key_url(hint),
        advanced=not env,
        installed=installed,
        unavailable=_missing_backend(spec),
    )


def choice_for(spec: object) -> SetupChoice:
    """The setup choice for a provider that is already on this computer."""
    return _choice(spec, installed=True)


def is_advanced_example(name: str) -> bool:
    """True for a shipped example that signs in with a cloud account, not a key."""
    try:
        spec = _manifests.parse_text(_manifests.example_text(name))
    except _manifests.ManifestError:
        return False
    return not spec.api_key_env


def setup_choices() -> tuple[SetupChoice, ...]:
    """Every service the guided setup offers: paste-a-key ones first, advanced last.

    Shipped examples, then any provider the user already added that is not
    one of them (so its key can be set or replaced the same way). An example
    whose host is a documentation placeholder is a template for a service
    that is not listed, and is left out: nobody can paste a key for
    "ai-gateway.example.edu".
    """
    from . import registered_providers

    registered = {
        spec.name: spec for spec in registered_providers() if spec.source == "user"
    }
    choices: list[SetupChoice] = []
    seen: set[str] = set()
    for example in _manifests.example_names():
        try:
            spec = _manifests.parse_text(_manifests.example_text(example))
        except _manifests.ManifestError:
            continue
        seen.add(spec.name)
        installed = registered.get(spec.name)
        if installed is not None:
            # The file on this computer wins: the user may have edited it.
            choices.append(_choice(installed, installed=True))
        elif not _is_placeholder(spec.default_host):
            choices.append(_choice(spec, installed=False))
    for name, spec in registered.items():
        if name not in seen:
            choices.append(_choice(spec, installed=True))
    # Stable sort: within each group the order above (alphabetical examples,
    # then the user's own) is kept.
    return tuple(sorted(choices, key=lambda choice: choice.advanced))


FailureKind = Literal[
    "", "key-missing", "key-rejected", "no-credit", "not-allowed", "model", "unreachable", "other"
]

# Checked in order; the first group with a match names the failure. Billing
# comes before the rejected-key group because a service with no credit often
# answers with words like "invalid request" or a 4xx that would match both.
_FAILURE_SIGNS: tuple[tuple[FailureKind, tuple[str, ...]], ...] = (
    (
        "no-credit",
        (
            "credit balance",
            "insufficient_quota",
            "insufficient quota",
            "exceeded your current quota",
            "billing",
            "payment",
            "quota",
            "429",
            "rate limit",
            "resource_exhausted",
        ),
    ),
    (
        "key-rejected",
        (
            "401",
            "unauthorized",
            "authentication",
            "invalid api key",
            "incorrect api key",
            "invalid x-api-key",
            "api key not valid",
            "api_key_invalid",
            "invalid_api_key",
        ),
    ),
    ("not-allowed", ("403", "forbidden", "permission")),
    ("model", ("404", "model_not_found", "not_found_error", "no model", "does not exist")),
    (
        "unreachable",
        (
            "refused",
            "timed out",
            "timeout",
            "name or service not known",
            "nodename nor servname",
            "getaddrinfo",
            "network is unreachable",
            "urlopen error",
            "certificate",
        ),
    ),
)

_FAILURE_TEXT: dict[str, str] = {
    "key-missing": "There is no key for this service yet.",
    "key-rejected": (
        "The service did not accept the key. Copy it again from the key page "
        "— all of it, with nothing before or after — and paste it here."
    ),
    "no-credit": (
        "The key was read, but the account behind it has no credit left or "
        "has reached its limit. Add a payment method or credit on the "
        "service's billing page, then try again."
    ),
    "not-allowed": (
        "The key is real, but it is not allowed to do this. Check on the "
        "service's site that the key has access to its models."
    ),
    "model": (
        "The service answered, but it does not offer the model this provider "
        "asks for."
    ),
    "unreachable": (
        "Opengrader could not reach the service. Check that this computer is "
        "online, then try again."
    ),
}


def explain_failure(message: str) -> tuple[FailureKind, str]:
    """A failed call's error text → what went wrong, in plain words ("" if unknown)."""
    lowered = (message or "").lower()
    for kind, signs in _FAILURE_SIGNS:
        if any(sign in lowered for sign in signs):
            return kind, _FAILURE_TEXT[kind]
    return ("other", "") if lowered.strip() else ("", "")


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    # What to show, most useful first. Never contains a key (errors are
    # redacted by the transport before they reach here).
    lines: tuple[str, ...]
    kind: FailureKind = ""


def probe_provider(name: str) -> ProbeResult:
    """Send the fixed connection-test prompt to a provider. Never raises.

    The same check as the providers screen's Test button: no student data is
    in the prompt, so it may go to a cloud host that has no consent yet.
    """
    from . import (
        AIUnavailableError,
        ensure_ready,
        for_provider,
        measure_generate_latency,
    )

    resolved = for_provider(name)
    provider = resolved.provider
    if provider.requires_api_key and not resolved.api_key and not provider.credential_command:
        return ProbeResult(
            ok=False,
            lines=(f"{provider.label} has no key yet.", _FAILURE_TEXT["key-missing"]),
            kind="key-missing",
        )
    try:
        ensure_ready(resolved, purpose="text")
    except AIUnavailableError as exc:
        return _failed(str(exc), provider.setup_hint)
    measurement = measure_generate_latency(
        resolved, role="grading", prompt=_manifests.CONNECTION_TEST_PROMPT
    )
    if measurement.ok:
        return ProbeResult(
            ok=True,
            lines=(
                measurement.describe(),
                f"{provider.label} is reachable and answering.",
            ),
        )
    return _failed(measurement.describe(), provider.setup_hint, detail=measurement.error)


def _failed(shown: str, hint: str, *, detail: str = "") -> ProbeResult:
    from . import _redact

    kind, plain = explain_failure(detail or shown)
    lines = [plain] if plain else []
    # Errors are redacted where they are raised; once more here because this
    # text goes straight onto a screen and a key must never be on one.
    lines.append(_redact(shown))
    if hint and kind in ("", "other"):
        lines.append(hint)
    return ProbeResult(ok=False, lines=tuple(lines), kind=kind or "other")


def list_models(name: str) -> tuple[tuple[str, ...], str]:
    """The models a provider says it offers, sorted, and "" — or (), and why not.

    Empty with no error means the service does not list models at all, which
    is normal for some of them and not a failure.
    """
    from . import available_models, for_provider

    try:
        models = available_models(for_provider(name))
    except Exception as exc:  # noqa: BLE001 - provider client errors are provider-specific
        return (), str(exc).splitlines()[0][:200] if str(exc) else "the request failed"
    return tuple(sorted({model for model in models if model})), ""


# A local server either answers at once or is not there; nobody should watch
# the provider list wait out the two-minute limit a real model call gets.
LOCAL_PROBE_TIMEOUT_SECONDS = 3.0


def local_models(name: str) -> tuple[tuple[str, ...], str] | None:
    """What a provider on this computer or local network has installed.

    Returns (models, "") or ((), why not) — and None for a cloud provider,
    without asking it anything: a request to a cloud host carries the API
    key, so it is only ever sent when the user presses Test.
    """
    from . import available_models, for_provider

    resolved = for_provider(name, timeout_seconds=LOCAL_PROBE_TIMEOUT_SECONDS)
    if resolved.sends_data_off_machine:
        return None
    try:
        models = available_models(resolved)
    except Exception as exc:  # noqa: BLE001 - provider client errors are provider-specific
        from opengrader.core.environment import summarize_connection_error

        return (), summarize_connection_error(str(exc) or "no answer")
    return tuple(sorted({model for model in models if model})), ""


def provider_status(
    spec: object,
    *,
    local: tuple[tuple[str, ...], str] | None = None,
    in_use: bool = False,
) -> str:
    """One short line for the provider list: where it runs and whether it is ready.

    `local` is `local_models()`'s answer for a local provider, or None while
    it has not been asked yet. Nothing here touches the network.
    """
    from . import api_key_source

    if not spec.sends_data_off_machine:  # type: ignore[attr-defined]
        if local is None:
            state = "checking…"
        elif local[1]:
            # Why not is in the details below the list; a row stays short.
            state = "not running"
        elif local[0]:
            count = len(local[0])
            state = f"{count} model{'' if count == 1 else 's'} installed"
        else:
            state = "running, no models installed yet"
        text = f"local · {state}"
    elif not getattr(spec, "api_key_env", ""):
        text = "cloud · signs in with a cloud account"
    else:
        source = api_key_source(spec)  # type: ignore[arg-type]
        state = {
            "keystore": "key saved",
            "environment": f"key set in {spec.api_key_env}",  # type: ignore[attr-defined]
        }.get(source, "no key yet")
        text = f"cloud · {state}"
    return f"{text} · chosen in AI setup" if in_use else text


# ---------------------------------------------------------------------------
# A service that is not in the list: ask an AI assistant to write the file
# ---------------------------------------------------------------------------

# What each wire format is, in words an assistant can match a service to.
# Keyed by dialect name; `assistant_prompt` refuses to leave one out.
_DIALECT_NOTES: dict[str, str] = {
    "openai-chat": (
        'the OpenAI API and anything described as "OpenAI-compatible" (most '
        "university gateways, OpenRouter, Together, Groq, Mistral, LM Studio, "
        "vLLM). host is the base URL and normally ends in /v1; the program "
        "adds /chat/completions itself."
    ),
    "anthropic-messages": (
        "the Anthropic (Claude) API. host is the base URL and normally ends "
        "in /v1; the program adds /messages itself."
    ),
    "ollama": (
        "an Ollama server, usually on the user's own computer or network. "
        "host is the server address with no path, e.g. http://192.168.1.20:11434."
    ),
}

# What each key means. Keyed by manifest key; every key the validator accepts
# is either here or in _KEYS_NOT_OFFERED, and a test holds that true.
_KEY_NOTES: dict[str, str] = {
    "name": (
        'a short id: lowercase letters, digits, ".", "_" or "-", starting '
        'with a letter or digit, e.g. "campus-gateway". Not "ollama".'
    ),
    "label": "the name a person would recognise the service by.",
    "dialect": "how the service is spoken to. One of the values listed below.",
    "host": (
        "the service's API base URL. It must start with https:// unless the "
        "service runs on the user's own computer or local network."
    ),
    "vision_model": (
        "the model id used to read images of handwriting. It must be a model "
        "that accepts image input."
    ),
    "text_model": (
        "the model id used to suggest grades from text. It may be the same "
        "as vision_model."
    ),
    "api_key_env": (
        "the NAME of an environment variable for the key, e.g. "
        '"CAMPUS_AI_KEY": letters, digits and underscores only. Include it '
        "whenever the service needs a key. It is never the key itself."
    ),
    "summary": "one sentence describing the service.",
    "setup_hint": (
        "one sentence saying where a key is obtained. Include the https:// "
        "address of that page if you know it."
    ),
    "supports_model_listing": (
        'true only if the service has a "list models" endpoint (GET '
        "/models under the host); otherwise leave it out."
    ),
    "pricing": (
        "a [pricing] table with input_per_mtok and output_per_mtok, in US "
        "dollars per million tokens. Only if you are sure of the numbers."
    ),
}

# Accepted by the validator but deliberately not offered to an assistant:
# `credential_command` makes Opengrader run a program, which a file written by
# a chatbot must never be able to ask for (install_reply refuses it too), and
# `requires_api_key` already follows from api_key_env.
_KEYS_NOT_OFFERED = frozenset({"credential_command", "requires_api_key"})

_PROMPT_EXAMPLE = """\
name         = "campus-gateway"
label        = "Campus AI Gateway"
summary      = "The department's gateway in front of a self-hosted model."
dialect      = "openai-chat"
host         = "https://ai-gateway.example.edu/v1"
vision_model = "qwen2.5-vl-7b-instruct"
text_model   = "qwen2.5-vl-7b-instruct"
api_key_env  = "CAMPUS_AI_KEY"
setup_hint   = "Ask the department's AI gateway admin for a key."
"""


def assistant_prompt() -> str:
    """The text a user pastes into their own AI assistant to get a provider file.

    Plain ASCII on purpose: it travels through a clipboard and a chat box,
    and a curly quote or a long dash is one more thing that can come back
    changed inside the file.
    """
    from opengrader.core.llm.dialects import DIALECT_NAMES

    required = [key for key in _manifests._REQUIRED_KEYS if key not in _KEYS_NOT_OFFERED]
    optional = [key for key in _manifests._OPTIONAL_KEYS if key not in _KEYS_NOT_OFFERED]

    def rows(keys: list[str]) -> list[str]:
        return [f"  {key}: {_KEY_NOTES[key]}" for key in keys]

    lines = [
        "I use a grading program called Opengrader. It can send students' "
        "answers to an AI service, and for that it needs a small settings "
        'file (a "provider file") describing the service. Please help me '
        "write that file.",
        "",
        "I am not a technical person. Ask me simple questions, one at a "
        'time, and do not expect me to know words like "endpoint" or "API".',
        "",
        "Start by asking me:",
        "1. Which AI service I want to use: its name, or what my school or "
        "department calls it.",
        "2. Whether I was given any setup instructions for it, such as an "
        "email or a web page. If so, ask me to paste them here.",
        "Ask more only if you still cannot fill in a field below.",
        "",
        "If you are not sure of a value, say so plainly instead of guessing. "
        "A wrong address or model name simply will not work.",
        "",
        "IMPORTANT: never ask me for my API key or password, and stop me if "
        "I start to paste one here. The file must not contain a key. "
        "Opengrader asks me for the key itself, afterwards, and keeps it in "
        "my computer's password store.",
        "",
        "The file is in TOML format and may use only these keys.",
        "",
        "Required:",
        *rows(required),
        "",
        "Optional:",
        *rows(optional),
        "",
        "Values for dialect:",
        *[f'  "{name}": {_DIALECT_NOTES[name]}' for name in DIALECT_NAMES],
        "",
        "Do not use any other key. In particular do not write kind or "
        "credential_command, and never the key itself.",
        "",
        "An example of a correct file (the values are made up):",
        "",
        "```toml",
        _PROMPT_EXAMPLE.rstrip("\n"),
        "```",
        "",
        "When you have what you need, reply with the finished file inside "
        "ONE code block, with nothing else in that block, so that I can copy "
        "it. Below the block, list any value you were unsure of.",
    ]
    return "\n".join(lines) + "\n"


_FENCE_RE = re.compile(r"```[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)```", re.DOTALL)
_KEY_LINE_RE = re.compile(r"^\s*name\s*=", re.MULTILINE)


def extract_manifest(reply: str) -> str:
    """The provider file out of an assistant's reply, whatever surrounds it.

    A reply is usually a sentence, a fenced code block, and another sentence.
    The first fenced block that looks like a provider file wins; a reply with
    no fence is taken whole, since some people copy only the block's contents.
    Raises `ManifestError` when nothing in it looks like a provider file.
    """
    text = (reply or "").replace("\r\n", "\n").strip()
    if not text:
        raise _manifests.ManifestError(
            "The box is empty. Paste the file the assistant wrote."
        )
    blocks = [block.strip() for block in _FENCE_RE.findall(text)]
    for block in blocks:
        if _KEY_LINE_RE.search(block):
            return block + "\n"
    if not blocks and _KEY_LINE_RE.search(text):
        return text + "\n"
    raise _manifests.ManifestError(
        "That does not look like a provider file: there is no line starting "
        "with `name =`. Copy the whole code block from the assistant's reply."
    )


def install_reply(reply: str, *, replace: bool = False) -> tuple[object, object]:
    """Validate an assistant's reply and write it as a provider file.

    Returns (path, spec). Stricter than a file a person writes by hand,
    because nobody has read this one:

    * `credential_command` is refused. It makes Opengrader run a program to
      fetch the key, and a chatbot's answer must not be able to ask for that.
    * A documentation placeholder host (example.edu and the like) is refused:
      it means the assistant copied the example instead of answering.

    Raises `ManifestExistsError` when the name is taken and `replace` is
    false, and `ManifestError` for everything else.
    """
    text = extract_manifest(reply)
    spec = _manifests.parse_text(text)
    if spec.credential_command:
        raise _manifests.ManifestError(
            "This file contains credential_command, which would make "
            "Opengrader run a program on this computer. A file written by an "
            "assistant may not do that. Ask it to remove that line; "
            "Opengrader asks you for the key itself."
        )
    if _is_placeholder(spec.default_host):
        raise _manifests.ManifestError(
            f"The host in this file, {spec.default_host}, is only a "
            "placeholder from the example, not a real service. Ask the "
            "assistant for your service's actual address."
        )
    path, installed = _manifests.install_manifest(text, force=replace)
    return path, installed
