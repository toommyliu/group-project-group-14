"""What a wire dialect is, and what it is forbidden from being.

§7.2 of `About/AI_README.md` makes the central bet of this design: there are
hundreds of services an instructor might plausibly be handed and roughly three
*shapes* they speak, so ship the shapes and let the service be data. A dialect
is one of those shapes.

Two hard constraints keep a dialect small enough for that bet to pay:

* **A dialect performs no HTTP.** Retry, backoff, redaction and concurrency
  live in `core/llm/transport.py`, once, so they cannot be implemented three
  times and three ways. A dialect that imports `urllib` is a bug.
* **A dialect knows nothing about grading, OCR, rubrics or submissions.** It is
  the wire, not the domain. Nothing here may import from `core.grading`,
  `core.submissions` or `core.qa_file`.

The two operations that carry the design are `build_body` and `reply_text`:
turn a call into a request body, and pull the reply text back out. The rest of
the protocol is declarative wiring — which path, which auth header, how this
service spells a model list — and exists because those differ per service and
have to live *somewhere* that is not the transport.

A dialect is a stateless singleton, looked up by name through
`core.llm.dialects.get_dialect`. Name, never object, is what crosses a process
boundary: `core/submissions/batch.py` sends a `VisionReader` into a `spawn`ed
worker, the same constraint that already makes `math_ocr` travel as a backend
name rather than a callable.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["Dialect", "DialectError", "join_url", "media_type_for"]


class DialectError(ValueError):
    """A dialect was named that this build does not ship."""


def join_url(host: str, path: str) -> str:
    """`https://h/v1` + `/chat/completions` → `https://h/v1/chat/completions`.

    Tolerant about the trailing slash a user will inevitably paste into a
    manifest, and about a path that is already absolute — a service whose base
    URL *is* the full endpoint is a real configuration, not a mistake.
    """
    base = host.strip().rstrip("/")
    tail = path.strip()
    if not tail:
        return base
    if base.endswith(tail):
        return base
    return f"{base}/{tail.lstrip('/')}"


# The first few base64 characters of a file encode its first few bytes, so the
# magic number survives the encoding and can be read without decoding.
_MAGIC_PREFIXES: tuple[tuple[str, str], ...] = (
    ("iVBORw0KGgo", "image/png"),
    ("/9j/", "image/jpeg"),
    ("R0lGOD", "image/gif"),
    ("UklGR", "image/webp"),
)


def media_type_for(encoded_image: str) -> str:
    """What kind of image this base64 payload actually is.

    Opengrader writes PNG crops everywhere, so this looks like a formality —
    but `photo-import` takes whatever a phone produced, and Anthropic's API
    rejects a `media_type` that disagrees with the bytes rather than sniffing
    them. A mislabelled crop would fail one answer at a time, in a way that
    looks like the model refusing to read handwriting. Guess from the bytes,
    not from the pipeline we happen to have.
    """
    head = encoded_image[:16]
    for prefix, media_type in _MAGIC_PREFIXES:
        if head.startswith(prefix):
            return media_type
    return "image/png"


@runtime_checkable
class Dialect(Protocol):
    """One request/response shape. Data and two functions, no I/O."""

    name: str

    def generate_endpoint(self, host: str) -> str:
        """The URL a completion request is POSTed to."""
        ...

    def models_endpoint(self, host: str) -> str | None:
        """The URL a model list is GET from, or None if this shape has none."""
        ...

    def auth_headers(self, api_key: str | None) -> dict[str, str]:
        """Whatever this service wants a credential to look like.

        The transport attaches these verbatim and never inspects them; the
        header *name* is as service-specific as the body shape is, which is
        why it is here and not there.
        """
        ...

    def build_body(
        self,
        *,
        model: str,
        prompt: str,
        images: list[str] | None = None,
        response_schema: dict | None = None,
        num_predict: int | None = None,
        keep_alive: str | None = None,
        options: dict | None = None,
    ) -> dict:
        """The request body. `images` are base64-encoded PNG/JPEG payloads."""
        ...

    def reply_text(self, body: dict) -> str:
        """The model's answer, as text, out of a parsed response body."""
        ...

    def error_text(self, body: dict) -> str | None:
        """An error this service reported in a 200 response, if it did.

        Ollama does exactly this. A dialect whose service always uses status
        codes returns None.
        """
        ...

    def model_names(self, body: dict) -> list[str]:
        """Model ids out of a parsed model-list response."""
        ...
