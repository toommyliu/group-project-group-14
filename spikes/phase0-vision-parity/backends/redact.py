"""One redaction point, mirroring AI_README.md §7.7 L8 / §7.3's
`core/llm/transport.py`: every error message that might reach a terminal or
a log passes through here first, so a leaked API key never does.
"""

from __future__ import annotations

import re

PLACEHOLDER = "***REDACTED***"

# Shapes that are key-like regardless of which secret we were told about.
# Deliberately conservative: each needs a distinctive prefix or an explicit
# header name, so ordinary prose and base64 image data are not mangled.
_KEY_SHAPED = (
    # Vendor-prefixed keys: sk-…, sk-ant-…, xai-…, gsk_…
    re.compile(r"\b(?:sk|xai|gsk|pk)[-_][A-Za-z0-9\-_]{16,}"),
    # "Authorization: Bearer <token>" / "Bearer <token>"
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]{16,}=*"),
    # "x-api-key: <token>" in a dumped header or error string
    re.compile(r"(?i)\b(x-api-key|api[-_]?key)\s*[:=]\s*[A-Za-z0-9\-._~+/]{8,}=*"),
)


def scrub(text: str, *secrets: str | None) -> str:
    """Strip secrets out of a message before it can reach a terminal or a log.

    Two passes, because either alone is insufficient:

    1. Exact replacement of the secret values we were handed. This is the
       reliable one — it catches a key however it was embedded.
    2. A pattern pass for key-*shaped* substrings. This is defence in depth
       for the case the first pass cannot cover: a key that reached the
       message by a path this call site never saw (a server echoing a header
       back in an error body, say), and which we therefore cannot name.

    Mirrors AI_README.md §7.7 L8. Keep the two passes together — the pattern
    pass is not a substitute for knowing the secret, and the exact pass is not
    a substitute for not knowing it.
    """
    result = text
    for secret in secrets:
        if secret:
            result = result.replace(secret, PLACEHOLDER)
    for pattern in _KEY_SHAPED:
        result = pattern.sub(PLACEHOLDER, result)
    return result
