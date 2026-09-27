"""Consent to send student work off this machine — records, wording, a kill switch.

This module is the one place two separate acknowledgements are defined:

* **cloud consent** — "student work may be sent to this host";
* **credential consent** — "this manifest may run this literal command line
  to fetch a key".

A student's name and student ID are never sent to a cloud host, under any
setting. There used to be a deliberately hard-to-reach override for that;
it has been removed outright, because a cloud model added no value it
couldn't get for free: matching a name/ID crop to the class roster already
happens on this computer, and anything that doesn't match goes to the review
queue regardless of what a cloud model might have guessed. For a cloud
provider, those reads are instead redirected to a local Ollama reader (see
`ocr.vision_reader.GuardedVisionReader`) — never sent anywhere off this
machine. An old `config.json` that still has a consent record for the removed
identity override loads fine; that record is simply not one of the kinds
recognised any more, so it is dropped silently (see `ConsentRecord.from_dict`).
Reading a name or ID with a LOCAL model is, separately, still a user choice —
`AISettings.read_identity_locally` (default on) — since that never touches a
cloud host either way; turning it off just leaves Tesseract as the only
reader for those two crops.

**Consent is fingerprinted, not named.** A record stores
`sha256(name, request host, dialect, api_key_env, credential_command)`, a
consent-text version and a UTC timestamp. A record covers exactly the
configuration it was given for: pointing a manifest at another host, changing
its dialect, key variable or credential command, or bumping one of the
`*_CONSENT_VERSION` constants below (because the wording changed) makes the
record stop matching, and the user is asked again. Nothing is deleted when that
happens; a stale record simply never matches. One record is kept per provider
and kind, so granting again for a new configuration replaces the old one.

**Everything here is pure.** Grant and revoke take an `AISettings` and return a
new one; the caller saves it with `app_config.save_settings`. Nothing here
reads the settings file, performs I/O or imports a client — `app_config` is
imported at TUI startup and this module is imported by it.

**Single-source wording.** Every surface that tells the user where data goes —
the consent modal, the banner, `doctor`, the run summary, the setup screen's
status lines, the printable data-flow report — takes its words from the
functions here (`cloud_consent_text`, `credential_consent_text`,
`egress_statement`, `data_flow_report`). A surface that composes its own
sentence about egress is a bug.

**The kill switch.** `OPENGRADER_AI_ALLOW_CLOUD=0` (or false/no/off) turns
cloud providers and user manifests off machine-wide. This is **the one place
the environment forbids rather than overrides**: the module-level precedence
in `app_config` (flag > env > file > default) is deliberately inverted here,
because a department that sets it must not be defeated by a per-user settings
file. Either the process environment or an `environ` mapping passed in can
forbid; neither can re-allow what the other forbids. (A machine may still have
the old, now-unused `OPENGRADER_AI_ALLOW_IDENTITY_TO_CLOUD` set from before
this override existed; nothing here reads it, so it is harmless either way.)
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal, Mapping

if TYPE_CHECKING:  # pragma: no cover
    from . import AISettings, ProviderSpec, ResolvedAI

__all__ = [
    "ALLOW_CLOUD_ENV",
    "CLOUD_CONSENT_VERSION",
    "CREDENTIAL_CONSENT_VERSION",
    "ConsentKind",
    "ConsentStatus",
    "ConsentRecord",
    "ConsentText",
    "DataFlowItem",
    "DataFlowReport",
    "cloud_allowed",
    "provider_fingerprint",
    "cloud_fingerprint",
    "credential_fingerprint",
    "consent_status",
    "has_cloud_consent",
    "grant_cloud_consent",
    "revoke_cloud_consent",
    "has_credential_consent",
    "grant_credential_consent",
    "revoke_credential_consent",
    "cloud_consent_text",
    "credential_consent_text",
    "egress_statement",
    "data_flow_report",
    "hostname_of",
    "format_argv",
]

ALLOW_CLOUD_ENV = "OPENGRADER_AI_ALLOW_CLOUD"

# Bump one of these whenever the matching wording below changes in substance:
# every record given under the old wording stops matching and the user is
# asked again.
CLOUD_CONSENT_VERSION = 1
CREDENTIAL_CONSENT_VERSION = 1

# "identity" is deliberately absent: that consent kind no longer exists (a
# name/ID is never sent to a cloud host, so there is nothing left to consent
# to), and dropping it from this set is what makes an old record of that kind
# fail to parse — see ConsentRecord.from_dict.
_KINDS = frozenset({"cloud", "credential"})


def _current_version(kind: str) -> int:
    # Read at call time, so a bumped constant takes effect everywhere at once.
    return {
        "cloud": CLOUD_CONSENT_VERSION,
        "credential": CREDENTIAL_CONSENT_VERSION,
    }[kind]

ConsentKind = Literal["cloud", "credential"]
ConsentStatus = Literal["not-needed", "granted", "missing", "changed", "outdated", "blocked"]

_FORBIDDING_VALUES = frozenset({"0", "false", "no", "off"})


# ---------------------------------------------------------------------------
# The kill switch
# ---------------------------------------------------------------------------


def _forbids(name: str, environ: Mapping[str, str] | None) -> bool:
    sources: list[Mapping[str, str]] = [os.environ]
    if environ is not None and environ is not os.environ:
        sources.append(environ)
    for source in sources:
        value = source.get(name)
        if isinstance(value, str) and value.strip().lower() in _FORBIDDING_VALUES:
            return True
    return False


def cloud_allowed(environ: Mapping[str, str] | None = None) -> bool:
    """False when `OPENGRADER_AI_ALLOW_CLOUD` is 0/false/no/off anywhere we look."""
    return not _forbids(ALLOW_CLOUD_ENV, environ)


# ---------------------------------------------------------------------------
# Records and fingerprints
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConsentRecord:
    """One acknowledgement, persisted in `config.json` under `consents`.

    Holds nothing secret: a provider name, a hash, a version and a timestamp.
    """

    provider: str
    kind: str  # one of ConsentKind
    fingerprint: str
    version: int
    granted_at: str  # ISO-8601, UTC

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "kind": self.kind,
            "fingerprint": self.fingerprint,
            "version": self.version,
            "granted_at": self.granted_at,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ConsentRecord | None":
        """Parse one stored record; anything malformed is dropped (returns None).

        This is also how an old record for the identity override — a consent
        kind that no longer exists — quietly disappears: its `kind` is not in
        `_KINDS`, so it is treated the same as any other malformed record and
        dropped rather than raising. The rest of `config.json` still loads.
        """
        if not isinstance(data, dict):
            return None
        provider = data.get("provider")
        kind = data.get("kind")
        fingerprint = data.get("fingerprint")
        version = data.get("version")
        granted_at = data.get("granted_at")
        if not (isinstance(provider, str) and provider.strip()):
            return None
        if kind not in _KINDS:
            return None
        if not (isinstance(fingerprint, str) and fingerprint.strip()):
            return None
        if isinstance(version, bool) or not isinstance(version, int):
            return None
        if not isinstance(granted_at, str):
            return None
        return cls(
            provider=provider.strip(),
            kind=kind,
            fingerprint=fingerprint.strip(),
            version=version,
            granted_at=granted_at,
        )


def provider_fingerprint(
    *,
    name: str,
    host: str,
    dialect: str,
    api_key_env: str,
    credential_command: tuple[str, ...] | list[str] = (),
) -> str:
    """sha256 over the five things that decide where data goes and how.

    Encoded as a JSON array so that no two different configurations can
    concatenate to the same string.
    """
    payload = json.dumps(
        [name, host, dialect, api_key_env, list(credential_command)],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cloud_fingerprint(resolved: "ResolvedAI") -> str:
    """The consent fingerprint of a resolved configuration — the ACTUAL request host."""
    spec = resolved.provider
    return provider_fingerprint(
        name=spec.name,
        host=resolved.host,
        dialect=spec.dialect,
        api_key_env=spec.api_key_env,
        credential_command=tuple(spec.credential_command),
    )


def credential_fingerprint(spec: "ProviderSpec") -> str:
    """The provider name, the literal argv that would run, and the host its key goes to.

    The host is included because the approval text names it ("the key it
    prints is sent to <host>"): a manifest edited to point somewhere else
    must not keep fetching a key on the strength of an approval that named
    a different destination.
    """
    payload = json.dumps(
        ["credential_command", spec.name, list(spec.credential_command), spec.default_host],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _now_text(now: datetime | None) -> str:
    moment = now if now is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _find(settings: "AISettings", provider: str, kind: str) -> ConsentRecord | None:
    for record in settings.consents:
        if record.provider == provider and record.kind == kind:
            return record
    return None


def _with_record(settings: "AISettings", record: ConsentRecord) -> "AISettings":
    kept = tuple(
        r
        for r in settings.consents
        if not (r.provider == record.provider and r.kind == record.kind)
    )
    return replace(settings, consents=(*kept, record))


def _without(settings: "AISettings", provider: str, kinds: tuple[str, ...]) -> "AISettings":
    kept = tuple(
        r for r in settings.consents if not (r.provider == provider and r.kind in kinds)
    )
    return replace(settings, consents=kept)


def _record_status(record: ConsentRecord | None, fingerprint: str, kind: str) -> ConsentStatus:
    if record is None:
        return "missing"
    if record.fingerprint != fingerprint:
        return "changed"
    if record.version != _current_version(kind):
        return "outdated"
    return "granted"


def consent_status(
    settings: "AISettings",
    resolved: "ResolvedAI",
    kind: ConsentKind,
    *,
    environ: Mapping[str, str] | None = None,
) -> ConsentStatus:
    """Why a consent is or is not in force — for a screen that must say which.

    `not-needed`: a local host, or no credential_command. `granted`: in
    force. `missing`: never given. `changed`: given for a different
    host/dialect/key variable/credential command. `outdated`: given under
    older wording. `blocked`: the kill switch forbids it.
    """
    spec = resolved.provider
    if kind == "credential":
        if not spec.credential_command:
            return "not-needed"
        if not cloud_allowed(environ) and spec.source == "user":
            return "blocked"
        return _record_status(
            _find(settings, spec.name, "credential"), credential_fingerprint(spec), "credential"
        )
    if resolved.kind == "local":
        return "not-needed"
    if not cloud_allowed(environ):
        return "blocked"
    fingerprint = cloud_fingerprint(resolved)
    return _record_status(_find(settings, spec.name, "cloud"), fingerprint, "cloud")


def has_cloud_consent(
    settings: "AISettings",
    resolved: "ResolvedAI",
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """True when a cloud consent matches this exact configuration (always True for local)."""
    return consent_status(settings, resolved, "cloud", environ=environ) in ("granted", "not-needed")


def grant_cloud_consent(
    settings: "AISettings",
    resolved: "ResolvedAI",
    *,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> "AISettings":
    """Record that the user read `cloud_consent_text(resolved)` and agreed.

    Raises ValueError for a local host (nothing to consent to) or when
    `OPENGRADER_AI_ALLOW_CLOUD` forbids cloud providers.
    """
    if resolved.kind == "local":
        raise ValueError(
            f"{resolved.provider.label} runs on this computer; there is nothing to consent to."
        )
    if not cloud_allowed(environ):
        raise ValueError(
            f"{ALLOW_CLOUD_ENV}=0 is set on this machine: cloud AI providers are turned "
            "off here, so consent to one cannot be recorded."
        )
    record = ConsentRecord(
        provider=resolved.provider.name,
        kind="cloud",
        fingerprint=cloud_fingerprint(resolved),
        version=CLOUD_CONSENT_VERSION,
        granted_at=_now_text(now),
    )
    return _with_record(settings, record)


def revoke_cloud_consent(settings: "AISettings", provider: str) -> "AISettings":
    """Forget the cloud consent for `provider`."""
    return _without(settings, provider, ("cloud",))


def has_credential_consent(
    settings: "AISettings",
    spec: "ProviderSpec",
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    """True when the user approved running exactly this provider's credential_command."""
    if not spec.credential_command:
        return False
    record = _find(settings, spec.name, "credential")
    return _record_status(record, credential_fingerprint(spec), "credential") == "granted"


def grant_credential_consent(
    settings: "AISettings",
    spec: "ProviderSpec",
    *,
    now: datetime | None = None,
) -> "AISettings":
    """Record approval to run `spec.credential_command` (shown by `credential_consent_text`)."""
    if not spec.credential_command:
        raise ValueError(f"{spec.label} names no credential_command.")
    record = ConsentRecord(
        provider=spec.name,
        kind="credential",
        fingerprint=credential_fingerprint(spec),
        version=CREDENTIAL_CONSENT_VERSION,
        granted_at=_now_text(now),
    )
    return _with_record(settings, record)


def revoke_credential_consent(settings: "AISettings", provider: str) -> "AISettings":
    return _without(settings, provider, ("credential",))


# ---------------------------------------------------------------------------
# Wording — the single source
# ---------------------------------------------------------------------------


def _on_this_computer(host: str) -> bool:
    import ipaddress

    name = hostname_of(host).strip("[]")
    if name in {"localhost", "0.0.0.0"} or name.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def _local_place(host: str) -> str:
    if _on_this_computer(host):
        return "Processed on this computer."
    return f"Processed on {hostname_of(host)}, on your local network."


def hostname_of(host: str) -> str:
    """The bare hostname a person recognises, from a URL or host string."""
    from urllib.parse import urlsplit

    text = (host or "").strip()
    candidate = text if "//" in text else f"//{text}"
    try:
        name = urlsplit(candidate).hostname
    except ValueError:
        name = None
    return (name or text).lower()


_WORK_LEAVING = (
    "Images of students' answers, including their handwriting",
    "Text transcribed from those answers",
    "Your rubric and answer-key text, when AI grading suggestions are on",
)


def format_argv(argv: tuple[str, ...] | list[str]) -> str:
    """The argv as it must be shown to the person approving it.

    Any argument that is empty, contains whitespace or a quote, or holds a
    character that is not plainly printable is shown JSON-quoted, so every
    control character appears as a visible escape. Printed raw, an escape
    sequence in a manifest's argument could make a terminal display a
    harmless-looking command in place of the one that would really run.
    """
    return " ".join(
        a
        if a and a.isprintable() and not any(ch.isspace() or ch in "\"'" for ch in a)
        else json.dumps(a)
        for a in argv
    )


@dataclass(frozen=True)
class ConsentText:
    """Structured consent wording; every surface renders this, none rewrites it."""

    kind: str  # ConsentKind
    version: int
    title: str
    paragraphs: tuple[str, ...]
    leaves_machine: tuple[str, ...]
    host: str
    hostname: str
    accept_label: str
    argv: tuple[str, ...] = ()

    def as_text(self) -> str:
        """A plain-text rendering for a terminal prompt."""
        lines = [self.title, ""]
        for paragraph in self.paragraphs:
            lines.extend([paragraph, ""])
        if self.leaves_machine:
            lines.append("What leaves this computer:")
            lines.extend(f"  - {item}" for item in self.leaves_machine)
            lines.append("")
        if self.argv:
            lines.append("Command that will run:")
            lines.append("  " + format_argv(self.argv))
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def cloud_consent_text(resolved: "ResolvedAI") -> ConsentText:
    """The consent modal: what leaves, where to, and that it cannot be recalled."""
    spec = resolved.provider
    hostname = hostname_of(resolved.host)
    identity_clause = (
        "Opengrader reads those on this computer, using a local model when the "
        "active provider is a cloud service, and leaves a field blank for review "
        "when neither can read it."
        if resolved.read_identity_locally
        else "reading them with a local AI model is turned off in AI setup, so "
        "Opengrader reads those on this computer with Tesseract only, and leaves "
        "a field blank for review when Tesseract can't read it either."
    )
    return ConsentText(
        kind="cloud",
        version=CLOUD_CONSENT_VERSION,
        title=f"Send student work to {hostname}?",
        paragraphs=(
            f"{spec.label} is a cloud service. Using it sends student work from "
            f"this computer to {hostname} ({resolved.host}), which is run by a "
            "third party, not by Opengrader.",
            "Once sent, Opengrader cannot recall it or control how long the service "
            "keeps it, who can read its logs, or whether it is used for training. "
            "Check your institution's rules before continuing.",
            "Student names and student IDs are never sent, under any setting: "
            f"{identity_clause}",
            "You will be asked again if this provider's host, wire format, key "
            "variable or credential command changes.",
        ),
        leaves_machine=(*_WORK_LEAVING,),
        host=resolved.host,
        hostname=hostname,
        accept_label=f"Send student work to {hostname}",
    )


def credential_consent_text(spec: "ProviderSpec") -> ConsentText:
    """The literal argv a manifest wants to run, shown before it ever runs."""
    host = spec.default_host
    hostname = hostname_of(host)
    return ConsentText(
        kind="credential",
        version=CREDENTIAL_CONSENT_VERSION,
        title=f"Run a command to fetch the API key for {spec.label}?",
        paragraphs=(
            "This provider's manifest asks Opengrader to run the command below on "
            "this computer and use what it prints as the API key. It is the only way a "
            "manifest can make anything run, so check it is the command you expect.",
            "It runs directly (no shell), at most once per session, with a short time "
            "limit. Its output is kept in memory only, scrubbed from every message, "
            "and never written to disk.",
            f"The key it prints is sent to {hostname} to sign in. If the manifest's "
            "command changes, you will be asked again.",
        ),
        leaves_machine=(),
        host=host,
        hostname=hostname,
        accept_label="Allow this command",
        argv=tuple(spec.credential_command),
    )


def egress_statement(resolved: "ResolvedAI") -> str:
    """The ONE sentence every surface uses to say where AI work goes."""
    spec = resolved.provider
    if resolved.kind == "local":
        if _on_this_computer(resolved.host):
            return "AI runs on this computer; no student work leaves it."
        return (
            f"AI runs on {hostname_of(resolved.host)}, a machine on your local "
            "network; no student work is sent to a cloud service."
        )
    hostname = hostname_of(resolved.host)
    what = (
        "Student work (answer images, handwriting, transcribed text, rubric text) "
        f"is sent to {hostname} ({spec.label}), a cloud service; names and IDs "
        "stay on this computer"
    )
    if not resolved.cloud_consent_given:
        return f"{what} — once you consent; nothing is sent until then."
    return f"{what}."


# ---------------------------------------------------------------------------
# The data-flow report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DataFlowItem:
    category: str  # an egress category (core/egress.CATEGORIES)
    description: str
    leaves_machine: bool
    reason: str


@dataclass(frozen=True)
class DataFlowReport:
    """Which categories of data would leave this machine, and to which host.

    Describes the configuration, not a run: a category marked as leaving is
    one that a run with this configuration may send.
    """

    provider: str
    label: str
    kind: str
    host: str
    hostname: str
    dialect: str
    statement: str
    cloud_allowed: bool
    cloud_consent: bool
    items: tuple[DataFlowItem, ...]
    notes: tuple[str, ...]

    @property
    def leaving(self) -> tuple[DataFlowItem, ...]:
        return tuple(item for item in self.items if item.leaves_machine)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "label": self.label,
            "kind": self.kind,
            "host": self.host,
            "hostname": self.hostname,
            "dialect": self.dialect,
            "statement": self.statement,
            "cloud_allowed": self.cloud_allowed,
            "cloud_consent": self.cloud_consent,
            "items": [
                {
                    "category": i.category,
                    "description": i.description,
                    "leaves_machine": i.leaves_machine,
                    "reason": i.reason,
                }
                for i in self.items
            ],
            "notes": list(self.notes),
        }

    def lines(self) -> list[str]:
        """Plain-text rendering, printable and handable to a department."""
        out = [
            "Opengrader data-flow report",
            f"Provider: {self.label} ({self.provider}), {self.kind}",
            f"Host: {self.host}",
            self.statement,
            "",
        ]
        for item in self.items:
            mark = "LEAVES THIS COMPUTER" if item.leaves_machine else "stays on this computer"
            out.append(f"[{mark}] {item.category}: {item.description}")
            out.append(f"    {item.reason}")
        if self.notes:
            out.append("")
            out.extend(self.notes)
        return out


def data_flow_report(resolved: "ResolvedAI") -> DataFlowReport:
    spec = resolved.provider
    hostname = hostname_of(resolved.host)
    local = resolved.kind == "local"
    consent = resolved.cloud_consent_given
    allowed = resolved.cloud_allowed

    def gate(active: bool, off_reason: str, on_reason: str) -> tuple[bool, str]:
        if local:
            return False, _local_place(resolved.host)
        if not allowed:
            return False, f"Blocked on this machine by {ALLOW_CLOUD_ENV}=0."
        if not active:
            return False, off_reason
        if not consent:
            return False, (
                f"Would be sent to {hostname} once you consent; nothing is sent before that."
            )
        return True, on_reason

    answers_on = resolved.read_handwriting
    answers_reason = (
        "Only answers the offline OCR could not read confidently are sent "
        "(escalate-only is on)."
        if resolved.escalate_only
        else "Every answer crop is sent when handwriting reading runs."
    )
    answer_leaves, answer_why = gate(
        answers_on, "Handwriting reading is off, so no answer images are sent.",
        f"Sent to {hostname}. {answers_reason}",
    )
    graded_leaves, graded_why = gate(
        resolved.grade_written,
        "AI grading suggestions are off, so no answer text or rubric is sent.",
        f"Sent to {hostname} for each written answer graded with AI suggestions.",
    )
    if local:
        id_reason = _local_place(resolved.host)
    else:
        id_reason = (
            "Never sent to a cloud service, under any setting: read by a local Ollama "
            "model when one is available, by Tesseract otherwise, and left blank for "
            "review when neither can read it."
        )
    # "identity-image" (the Name/Student ID crops) gets its own wording,
    # separate from title-image/page-image above: it is the only category
    # `read_identity_locally` governs, since it is the only one whose output
    # is a student's identity. When the setting is off, no model — local or
    # cloud — ever reads a name or ID; Tesseract (or the review queue) is
    # what is left.
    if not resolved.read_identity_locally:
        identity_reason = (
            "Reading names and IDs with a local AI model is turned off in AI setup: "
            "read by Tesseract only, and left blank for review when Tesseract can't "
            "read it either. Never sent to a cloud service either way."
        )
    else:
        identity_reason = id_reason
    items = (
        DataFlowItem(
            "answer-image",
            "Images of answer regions: handwriting and marked choices (also answer-key "
            "pages when an answer key is extracted from a PDF)",
            answer_leaves,
            answer_why,
        ),
        DataFlowItem(
            "graded-text",
            "Transcribed answer text together with the rubric and question text",
            graded_leaves,
            graded_why,
        ),
        DataFlowItem(
            "identity-image",
            "Images of the student Name and Student ID boxes",
            False,
            identity_reason,
        ),
        DataFlowItem(
            "title-image",
            "Image of the assignment title on the cover page",
            False,
            id_reason,
        ),
        DataFlowItem(
            "page-image",
            "Whole scanned pages, to read a page number or find the cover page (may show "
            "a name and ID)",
            False,
            id_reason,
        ),
        DataFlowItem(
            "connection-test",
            "A fixed test prompt with no student data, only when you press Test",
            not local and allowed,
            "Sent only on request."
            if not local and allowed
            else _local_place(resolved.host)
            if local
            else f"Blocked on this machine by {ALLOW_CLOUD_ENV}=0.",
        ),
    )
    notes: list[str] = []
    if not local:
        notes.append(
            f"Every call to {hostname} is recorded (category, count, host, time — never "
            "content) in .opengrader/cloud-egress.jsonl beside the submissions."
        )
        if spec.api_key_env or spec.credential_command:
            notes.append(
                f"The API key is sent to {hostname} to sign in. Opengrader never writes "
                "it to disk."
            )
    return DataFlowReport(
        provider=spec.name,
        label=spec.label,
        kind=resolved.kind,
        host=resolved.host,
        hostname=hostname,
        dialect=spec.dialect,
        statement=egress_statement(resolved),
        cloud_allowed=allowed,
        cloud_consent=consent,
        items=items,
        notes=tuple(notes),
    )
