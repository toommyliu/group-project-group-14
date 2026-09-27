"""The VisionReader seam, its implementations, and the guard every reader wears.

Two rules bind every implementation: a reader resolves the function it
delegates to through the *module* at call time (tests monkeypatch those
functions after construction), and a reader is picklable
(`batch.ExtractionJob` carries one into a spawned worker).

`GuardedVisionReader` is what `app_config.vision_reader()` hands out for
EVERY provider — dialect, pydantic-ai, or the built-in Ollama pointed at a
remote host — so the safeguards below cannot be skipped by a provider that
brings its own reader:

* **The identity pin.** A student's name and student ID are never sent to a
  cloud host. There is no setting, flag, or consent that changes this:
  matching a name/ID crop to the class roster already happens on this
  computer (`core/submissions/processor.py`'s roster matching), and anything
  that does not match goes to the review queue anyway, so a cloud model would
  add no validation the roster match does not already provide — only risk.
  For a cloud provider, the operations that could show a student's identity
  are instead run against a local Ollama reader, if one is usable, and raise
  `IdentityFieldBlocked` otherwise: `extract_field_text` (name/ID crops),
  `extract_title_metadata` (the cover-page title crop), `page_has_identity_field`
  (a whole page, asked whether it is the cover page — so it may be the cover
  page), and `extract_page_number`: `reorder.py` sends it WHOLE pages at 150
  dpi, in unknown order, as the last fallback after the text layer, QR code,
  template layout and Tesseract have failed — page 1 among them. There is no
  way to know a page is not the cover page before reading it, so it is
  treated as identity-bearing. Every call site catches the exception and
  falls back to the local path (Tesseract, or no page number / no header),
  and an unreadable name or ID lands in the review queue.
* **The local opt-out.** Reading a name or ID with a LOCAL model is,
  separately, a user choice: `AISettings.read_identity_locally` (default
  True, so nothing changes on an existing install). It governs exactly
  `extract_field_text` — the only one of the four operations above whose
  *output* is a student's identity; the other three return a course/assignment
  title, a yes/no, or a page number, and are unaffected by it. When it is
  False, `extract_field_text` raises `IdentityFieldBlocked` immediately, on a
  local or a cloud provider alike, without calling any model (and, on a cloud
  provider, without even building the local-Ollama route) — the caller's
  Tesseract/review fallback takes over exactly as if no model had been
  configured at all.
* **The egress log.** For a cloud host, each attempted non-identity call
  writes one line to `core/egress` — category, provider, host, token counts
  from `core/llm/usage` — never content. A failed attempt is logged too:
  whether the image reached the host before the failure is unknowable, so the
  log errs toward saying it might have. For a local host, or for an identity
  operation routed to the local Ollama reader, the usage slot is cleared and
  nothing is written — the cloud host was never contacted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.llm.dialect_client import DialectConfig

    from .ollama_vlm import OllamaVisionConfig

__all__ = [
    "FieldKind",
    "TitleMetadataResult",
    "VisionReader",
    "OllamaVisionReader",
    "DialectVisionReader",
    "GuardedVisionReader",
    "CloudCallBlocked",
    "IdentityFieldBlocked",
]

FieldKind = Literal["name", "sid"]


@dataclass(frozen=True)
class TitleMetadataResult:
    course_code: str | None
    assignment_title: str
    assignment_version: str | None
    raw_title: str


@runtime_checkable
class VisionReader(Protocol):

    def extract_field_text(
        self, *, image_path: str | Path, field_kind: FieldKind
    ) -> str:
        """Read a student name or nine-digit ID off an identity crop."""
        ...

    def extract_title_metadata(self, *, image_path: str | Path) -> TitleMetadataResult:
        """Read course, assignment and version off a title crop."""
        ...

    def extract_question_prompt_and_answer(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> tuple[str, str]:
        """Split a response crop into (printed prompt, handwritten answer)."""
        ...

    def extract_handwriting_only(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> str:
        """Transcribe only student handwriting or marked choices in a response crop."""
        ...

    def extract_page_number(self, *, image_path: str | Path) -> int | None:
        """Read the page number printed at the foot of a scanned page."""
        ...

    def page_has_identity_field(self, *, image_path: str | Path) -> bool:
        """Is this the cover page — does it carry a Name or Student ID field?"""
        ...


@dataclass(frozen=True)
class OllamaVisionReader:

    config: OllamaVisionConfig

    def extract_field_text(
        self, *, image_path: str | Path, field_kind: FieldKind
    ) -> str:
        from . import ollama_vlm

        return ollama_vlm.extract_field_text(
            image_path=image_path, field_kind=field_kind, config=self.config
        )

    def extract_title_metadata(self, *, image_path: str | Path) -> TitleMetadataResult:
        from . import ollama_vlm

        return ollama_vlm.extract_title_metadata(
            image_path=image_path, config=self.config
        )

    def extract_question_prompt_and_answer(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> tuple[str, str]:
        from . import ollama_vlm

        return ollama_vlm.extract_question_prompt_and_answer(
            image_path=image_path, config=self.config, math_latex=math_latex
        )

    def extract_handwriting_only(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> str:
        from . import ollama_vlm

        return ollama_vlm.extract_handwriting_only(
            image_path=image_path, config=self.config, math_latex=math_latex
        )

    def extract_page_number(self, *, image_path: str | Path) -> int | None:
        from . import ollama_vlm

        return ollama_vlm.extract_page_number(
            image_path=image_path, config=self.config
        )

    def page_has_identity_field(self, *, image_path: str | Path) -> bool:
        from . import ollama_vlm

        return ollama_vlm.page_has_identity_field(
            image_path=image_path, config=self.config
        )


@dataclass(frozen=True)
class DialectVisionReader:

    config: DialectConfig

    def extract_field_text(
        self, *, image_path: str | Path, field_kind: FieldKind
    ) -> str:
        from opengrader.core.llm import dialect_client

        from . import vision_prompts

        request = vision_prompts.field_text_request(field_kind)
        raw = dialect_client.generate(
            config=self.config,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            options={"temperature": 0, "think": False},
            images=[vision_prompts.image_to_base64(image_path)],
            response_format=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_field_text(raw, field_kind=field_kind)

    def extract_title_metadata(self, *, image_path: str | Path) -> TitleMetadataResult:
        from opengrader.core.llm import dialect_client

        from . import vision_prompts

        request = vision_prompts.title_metadata_request()
        raw = dialect_client.generate(
            config=self.config,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            options={"temperature": 0, "think": False},
            images=[vision_prompts.image_to_base64(image_path)],
            response_format=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_title_metadata(raw)

    def extract_question_prompt_and_answer(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> tuple[str, str]:
        from opengrader.core.llm import dialect_client

        from . import vision_prompts

        request = vision_prompts.question_prompt_and_answer_request(math_latex=math_latex)
        raw = dialect_client.generate(
            config=self.config,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            options={"temperature": 0, "think": False},
            images=[vision_prompts.image_to_base64(image_path)],
            response_format=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_question_prompt_and_answer(raw)

    def extract_handwriting_only(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> str:
        from opengrader.core.llm import dialect_client

        from . import vision_prompts

        request = vision_prompts.handwriting_only_request(math_latex=math_latex)
        raw = dialect_client.generate(
            config=self.config,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            options={"temperature": 0, "think": False},
            images=[vision_prompts.image_to_base64(image_path)],
            response_format=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_handwriting_only(raw)

    def extract_page_number(self, *, image_path: str | Path) -> int | None:
        from opengrader.core.llm import dialect_client

        from . import vision_prompts

        request = vision_prompts.page_number_request()
        raw = dialect_client.generate(
            config=self.config,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            options={"temperature": 0, "think": False},
            images=[vision_prompts.image_to_base64(image_path)],
            response_format=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_page_number(raw)

    def page_has_identity_field(self, *, image_path: str | Path) -> bool:
        from opengrader.core.llm import dialect_client

        from . import vision_prompts

        request = vision_prompts.identity_field_request()
        raw = dialect_client.generate(
            config=self.config,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            options={"temperature": 0, "think": False},
            images=[vision_prompts.image_to_base64(image_path)],
            response_format=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_identity_field(raw)


class CloudCallBlocked(RuntimeError):
    """A guarded reader refused to send an image to a cloud host. Nothing was sent."""


class IdentityFieldBlocked(CloudCallBlocked):
    """A name or ID crop was not read by any model. Nothing was sent anywhere.

    Raised for three different reasons, all with the same effect — the caller
    falls back to the local, non-AI path (Tesseract, or leaving the field
    blank for review): the `read_identity_locally` setting is off; no local
    Ollama reader is configured (only relevant on a cloud provider); or one
    is configured but could not actually do the read (no server running, no
    matching model, and so on).
    """


def _env_forbids(name: str) -> bool:
    value = os.environ.get(name)
    return isinstance(value, str) and value.strip().lower() in {"0", "false", "no", "off"}


def _clear_usage() -> None:
    """Drop whatever `core/llm/usage` slot a local read left behind.

    Called instead of `_sent` for an identity operation that ran against the
    local Ollama reader: it never touched the cloud host, so nothing is
    written to the egress log, but the usage slot still needs clearing so a
    stray token count cannot attach itself to the next real cloud call.
    """
    try:
        from opengrader.core.llm import usage

        usage.take_last()
    except Exception:  # noqa: BLE001 - accounting must never fail a read
        pass


@dataclass(frozen=True)
class GuardedVisionReader:
    """Wraps any VisionReader with the identity pin, the local opt-out, and
    the cloud egress log.

    Plain frozen fields only, so it pickles whenever `inner` and
    `local_identity_config` do. The `OPENGRADER_AI_ALLOW_CLOUD` kill switch is
    re-read from the process environment on every call, which a spawned
    worker inherits.
    """

    inner: Any
    provider: str
    host: str
    cloud: bool
    # A plain OllamaVisionConfig (never a live client, so this stays
    # picklable) used to build a throwaway local reader for identity
    # operations when `cloud` is True. None when this reader is already local
    # (nothing to redirect to) or when no local Ollama route could be built.
    # Built once, in `app_config.vision_reader()`, without ever contacting
    # Ollama — readiness is discovered by trying the real call, not by a
    # separate probe.
    local_identity_config: Any = None
    # AISettings.read_identity_locally, carried over verbatim. True (the
    # default) lets `extract_field_text` run as described above. False
    # blocks it outright, on a local or a cloud provider alike, before any
    # model is touched — see `extract_field_text` below. A plain bool, so
    # this stays picklable.
    identity_reads_allowed: bool = True

    # -- policy ---------------------------------------------------------

    def _check(self, *, what: str) -> None:
        if not self.cloud:
            return
        from opengrader.core.app_config.consent import ALLOW_CLOUD_ENV

        if _env_forbids(ALLOW_CLOUD_ENV):
            raise CloudCallBlocked(
                f"{ALLOW_CLOUD_ENV}=0 is set: {what} was not sent to {self.host}."
            )

    def _sent(self, category: str) -> None:
        try:
            from opengrader.core.llm import usage

            input_tokens, output_tokens = usage.take_last()
        except Exception:  # noqa: BLE001 - accounting must never fail a read
            input_tokens = output_tokens = None
        if not self.cloud:
            return
        from opengrader.core import egress

        egress.record(
            provider=self.provider,
            host=self.host,
            category=category,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    # -- identity-bearing operations --------------------------------------

    def _identity_call(
        self, *, what: str, category: str, run: Callable[[Any], Any]
    ) -> Any:
        """Run an operation that could show a student's name or ID.

        On a local provider this is just `run(self.inner)` — the reader is
        already on this machine. On a cloud provider the operation is never
        sent there: it is redirected to a local Ollama reader instead, still
        entirely on this machine and regardless of `OPENGRADER_AI_ALLOW_CLOUD`
        (a kill switch for the cloud host, which this route never touches).
        When no local route is configured, or the local read fails for any
        reason (no server running, no matching model pulled, a bad image),
        `IdentityFieldBlocked` is raised so the caller falls back to its own
        local path (Tesseract, or leaving the field blank for review).
        """
        if not self.cloud:
            try:
                return run(self.inner)
            finally:
                self._sent(category)
        if self.local_identity_config is None:
            raise IdentityFieldBlocked(
                f"{what} is read on this computer only; it is never sent to "
                f"the cloud service at {self.host}."
            )
        # The redirect is only safe if its target really is local. Checked
        # here, at the point of sending, with the same rule that classifies
        # providers, so nothing upstream (a changed default, a replaced
        # built-in provider) can turn it into a second route to the cloud.
        from opengrader.core.app_config import host_kind

        local_host = getattr(self.local_identity_config, "host", "")
        if host_kind(local_host) != "local":
            raise IdentityFieldBlocked(
                f"{what} is read on this computer only; the local reader is set "
                f"to {local_host or 'no host'}, which is not on this computer or "
                "network, so it was not used."
            )
        try:
            return run(OllamaVisionReader(config=self.local_identity_config))
        except Exception as exc:  # noqa: BLE001 - any local failure falls back
            raise IdentityFieldBlocked(
                f"{what} is read on this computer only; it is never sent to "
                f"the cloud service at {self.host}. The local reader could "
                f"not provide it: {exc}"
            ) from exc
        finally:
            _clear_usage()

    def extract_field_text(
        self, *, image_path: str | Path, field_kind: FieldKind
    ) -> str:
        """Read a student name or ID off a crop — the one op this pin can turn off.

        This is the only identity-bearing operation whose *output* is a
        student's identity, so it is the only one `read_identity_locally`
        governs: `extract_title_metadata`, `page_has_identity_field` and
        `extract_page_number` return a title, a yes/no or a page number, never
        a name or ID, and run exactly as before regardless of this setting.
        When the setting is off, this raises before touching `self.inner`,
        `local_identity_config`, or any model — local or cloud alike.
        """
        label = "The student ID" if field_kind == "sid" else "The student name"
        if not self.identity_reads_allowed:
            raise IdentityFieldBlocked(
                f"{label} reading with a local AI model is turned off in AI "
                "setup; it was not read by any model."
            )
        return self._identity_call(
            what=label,
            category="identity-image",
            run=lambda reader: reader.extract_field_text(
                image_path=image_path, field_kind=field_kind
            ),
        )

    def extract_title_metadata(self, *, image_path: str | Path) -> TitleMetadataResult:
        return self._identity_call(
            what="The cover-page title",
            category="title-image",
            run=lambda reader: reader.extract_title_metadata(image_path=image_path),
        )

    def page_has_identity_field(self, *, image_path: str | Path) -> bool:
        return self._identity_call(
            what="A whole scanned page",
            category="page-image",
            run=lambda reader: reader.page_has_identity_field(image_path=image_path),
        )

    def extract_page_number(self, *, image_path: str | Path) -> int | None:
        return self._identity_call(
            what="A whole scanned page",
            category="page-image",
            run=lambda reader: reader.extract_page_number(image_path=image_path),
        )

    # -- the work itself -------------------------------------------------

    def extract_question_prompt_and_answer(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> tuple[str, str]:
        self._check(what="An answer image")
        try:
            return self.inner.extract_question_prompt_and_answer(
                image_path=image_path, math_latex=math_latex
            )
        finally:
            self._sent("answer-image")

    def extract_handwriting_only(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> str:
        self._check(what="An answer image")
        try:
            return self.inner.extract_handwriting_only(
                image_path=image_path, math_latex=math_latex
            )
        finally:
            self._sent("answer-image")
