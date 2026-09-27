"""The outbound audit log: what kind of student data left this machine.

`.opengrader/cloud-egress.jsonl` records **what kind of thing was sent, how
many, to which host, and when — never content**. One JSON object per line,
append-only, the same rules as the ledgers in `core/submissions/manifest.py`.

Which folder a call belongs to is process state, carried in an environment
variable so that a `spawn`ed extraction worker inherits it without anything
new crossing the pickle boundary. With no folder active, lines go to the
per-user log beside `config.json`, so a call is never unlogged.

`record` never raises: an audit log that could fail a run would be switched
off the first time it did.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from opengrader.core.submissions.manifest import resolve_manifest_path, utc_timestamp

__all__ = [
    "EGRESS_FILENAME",
    "ENV_VAR",
    "CATEGORIES",
    "activate",
    "deactivate",
    "recording",
    "active_log_path",
    "record",
    "read_entries",
    "EgressSummary",
    "summarize",
    "average_tokens",
    "estimate_cost",
]

EGRESS_FILENAME = "cloud-egress.jsonl"
ENV_VAR = "OPENGRADER_EGRESS_DIR"

# What a line may say was sent. Categories, never content. There is no
# "identity-image" category: a student name or student-ID crop is never sent
# to a cloud host (see ocr.vision_reader.GuardedVisionReader), so there is
# nothing for a line in this log to ever say about one.
CATEGORIES = frozenset(
    {
        "answer-image",  # a response crop: handwriting and marked choices
        "title-image",  # the assignment title crop on the cover page
        "page-image",  # a page sent to read its number or find a name header
        "graded-text",  # a transcribed response plus rubric, for grading
        "connection-test",  # a fixed prompt with no student data
    }
)


def activate(folder: str | Path) -> None:
    """Send this process's (and its future workers') lines to `folder`."""
    os.environ[ENV_VAR] = str(Path(folder).resolve())


def deactivate() -> None:
    os.environ.pop(ENV_VAR, None)


@contextmanager
def recording(folder: str | Path) -> Iterator[Path]:
    previous = os.environ.get(ENV_VAR)
    activate(folder)
    try:
        yield active_log_path()
    finally:
        if previous is None:
            deactivate()
        else:
            os.environ[ENV_VAR] = previous


def active_log_path() -> Path:
    folder = os.environ.get(ENV_VAR, "").strip()
    if folder:
        return resolve_manifest_path(folder, filename=EGRESS_FILENAME)
    from opengrader.core import app_config

    return app_config._config_dir() / EGRESS_FILENAME


def _loggable_host(host: str) -> str:
    """The host as it may be written to disk: no userinfo, query or fragment.

    A gateway URL pasted with a password (`https://user:pw@gw/...`) or a key
    in its query string (`...?key=...`) must not end up in a log that sits
    beside the submissions and travels with a grader packet or a backup.
    """
    from urllib.parse import urlsplit, urlunsplit

    text = (host or "").strip()
    try:
        parts = urlsplit(text)
        hostname = parts.hostname or ""
        port = parts.port
    except ValueError:
        return "[unparseable host]"
    if not parts.scheme or not hostname:
        # Not a URL at all; keep only what cannot hold a secret.
        return text.split("@")[-1].split("?")[0].split("#")[0]
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def record(
    *,
    provider: str,
    host: str,
    category: str,
    count: int = 1,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    """Append one line. Never raises."""
    try:
        entry: dict[str, object] = {
            "at": utc_timestamp(),
            "provider": provider,
            "host": _loggable_host(host),
            "category": category if category in CATEGORIES else "other",
            "count": int(count),
        }
        if input_tokens is not None:
            entry["input_tokens"] = int(input_tokens)
        if output_tokens is not None:
            entry["output_tokens"] = int(output_tokens)
        path = active_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
    except Exception:  # noqa: BLE001 - an audit log must not fail a run
        pass


def read_entries(path: Path) -> list[dict]:
    """Read and parse egress log entries.

    Tolerant of malformed lines and missing files. Returns an empty list
    if the file does not exist. Skips malformed lines without raising.
    """
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            entries = []
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if isinstance(entry, dict):
                        entries.append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue
            return entries
    except (FileNotFoundError, OSError):
        return []


@dataclass(frozen=True)
class EgressSummary:
    """Summary of egress log entries."""

    total_calls: int
    calls_by_category: dict[str, int]
    calls_by_host: dict[str, int]
    total_input_tokens: int
    total_output_tokens: int
    calls_with_tokens: int
    first_at: str | None
    last_at: str | None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON output."""
        return {
            "total_calls": self.total_calls,
            "calls_by_category": self.calls_by_category,
            "calls_by_host": self.calls_by_host,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "calls_with_tokens": self.calls_with_tokens,
            "first_at": self.first_at,
            "last_at": self.last_at,
        }


def summarize(
    folder_or_path: str | Path, *, since: str | None = None
) -> EgressSummary:
    """Summarize egress log entries.

    Accepts a submissions folder or a direct file path. If a folder, resolves
    to `.opengrader/cloud-egress.jsonl`. Filters entries by `since` ISO timestamp
    if provided (entries with at >= since).
    """
    path = Path(folder_or_path)
    if path.is_dir():
        path = resolve_manifest_path(path, filename=EGRESS_FILENAME)

    entries = read_entries(path)

    if since is not None:
        entries = [e for e in entries if isinstance(e.get("at"), str) and e["at"] >= since]

    calls_by_category: dict[str, int] = {}
    calls_by_host: dict[str, int] = {}
    total_input_tokens = 0
    total_output_tokens = 0
    calls_with_tokens = 0
    first_at = None
    last_at = None

    for entry in entries:
        # Update first_at and last_at
        at_str = entry.get("at")
        if isinstance(at_str, str):
            if first_at is None:
                first_at = at_str
            last_at = at_str

        # Count by category
        category = entry.get("category", "other")
        if isinstance(category, str):
            calls_by_category[category] = calls_by_category.get(category, 0) + 1

        # Count by host
        host = entry.get("host", "")
        if isinstance(host, str):
            calls_by_host[host] = calls_by_host.get(host, 0) + 1

        # Sum tokens
        input_tokens = entry.get("input_tokens")
        output_tokens = entry.get("output_tokens")
        if isinstance(input_tokens, int):
            total_input_tokens += input_tokens
        if isinstance(output_tokens, int):
            total_output_tokens += output_tokens
        if input_tokens is not None or output_tokens is not None:
            calls_with_tokens += 1

    return EgressSummary(
        total_calls=len(entries),
        calls_by_category=calls_by_category,
        calls_by_host=calls_by_host,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        calls_with_tokens=calls_with_tokens,
        first_at=first_at,
        last_at=last_at,
    )


def average_tokens(
    summary_or_entries: EgressSummary | list[dict], category: str | None = None
) -> tuple[float, float] | None:
    """Calculate average tokens per call.

    Accepts either an EgressSummary or a list of entries. If category is
    provided, filters to that category. Returns (avg_input, avg_output) or
    None if no calls reported tokens.
    """
    if isinstance(summary_or_entries, EgressSummary):
        if summary_or_entries.calls_with_tokens == 0:
            return None
        avg_input = summary_or_entries.total_input_tokens / summary_or_entries.calls_with_tokens
        avg_output = summary_or_entries.total_output_tokens / summary_or_entries.calls_with_tokens
        return (avg_input, avg_output)

    # Handle list of entries
    entries = summary_or_entries
    if category is not None:
        entries = [e for e in entries if e.get("category") == category]

    calls_with_tokens = 0
    total_input_tokens = 0
    total_output_tokens = 0

    for entry in entries:
        input_tokens = entry.get("input_tokens")
        output_tokens = entry.get("output_tokens")
        if isinstance(input_tokens, int):
            total_input_tokens += input_tokens
        if isinstance(output_tokens, int):
            total_output_tokens += output_tokens
        if input_tokens is not None or output_tokens is not None:
            calls_with_tokens += 1

    if calls_with_tokens == 0:
        return None

    avg_input = total_input_tokens / calls_with_tokens
    avg_output = total_output_tokens / calls_with_tokens
    return (avg_input, avg_output)


def estimate_cost(
    input_tokens: float, output_tokens: float, pricing: object
) -> float | None:
    """Estimate cost for given token counts.

    Returns None if pricing is None, not stated, or lacks required attributes.
    Accepts any object with input_per_mtok and output_per_mtok attributes.
    """
    if pricing is None:
        return None

    try:
        input_per_mtok = getattr(pricing, "input_per_mtok", None)
        output_per_mtok = getattr(pricing, "output_per_mtok", None)

        if input_per_mtok is None or output_per_mtok is None:
            return None

        # Convert to float to handle any numeric type
        input_per_mtok = float(input_per_mtok)
        output_per_mtok = float(output_per_mtok)

        # If both are 0, pricing is not stated
        if input_per_mtok == 0 and output_per_mtok == 0:
            return None

        # Cost is (tokens / 1_000_000) * (price per million tokens)
        input_cost = (input_tokens / 1_000_000) * input_per_mtok
        output_cost = (output_tokens / 1_000_000) * output_per_mtok
        return input_cost + output_cost
    except (AttributeError, TypeError, ValueError):
        return None
