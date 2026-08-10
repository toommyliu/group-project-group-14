"""Resume ledger for the extraction pipeline — and for the run orchestrator.

Extracting a stack of scans is the longest-running thing Opengrader does: every
PDF is rendered, OCR'd, and (optionally) sent to a vision model. Before this
a module that is run and then dies/fails at submission 180 of 200 had to start over from
submission 1.

The ledger fixes that. It is an **append-only JSONL file** at
``<output-dir>/.opengrader/extraction-manifest.jsonl`` — the same append-only
shape the regrade store uses (``core/submissions/regrade.py``), for the same
reason: a process killed mid-write can only ever corrupt the final line, and
:func:`read_manifest` skips unparseable lines, so one truncated write never
makes the whole ledger unreadable.

One line is appended per **successfully** extracted PDF. Failures are never
recorded — the absence of a record simply means "not done", which is exactly
what makes a retry pick the work back up.

**This module now backs two ledgers, not one.** ``core/pipeline.py`` — the
``opengrader-run`` orchestrator — needs exactly the same question answered one
level up ("did this *stage* already do its work, on this input, under these
settings?"), so it writes its own log at
``<output-dir>/.opengrader/run-ledger.jsonl`` (:data:`RUN_LEDGER_FILENAME`)
using these same records, with the stage name as the ``key``. The only
generalization that took was a keyword-only ``filename`` on the three functions
that touch the path; every rule below — same input, same output-shaping
settings, outputs still present, contents never inspected — applies unchanged.
Writing a second, near-identical ledger module would have meant two places to
get the "never invalidate on changed output" rule wrong.

A record is reusable (i.e. that PDF can be skipped on a re-run) only when all
three of these still hold:

1. the **input** is byte-for-byte the same (``source_sha256``),
2. the **settings** that shape the output are the same (``settings_id`` —
   see :func:`settings_fingerprint`), and
3. every **output** the run produced is still on disk.

Deliberately *not* part of the check: the current contents of those outputs. A
``.submission.md`` is edited by the grading TUI as soon as anyone grades it, so
invalidating on a changed output would make a later ``--resume`` re-extract
graded work and destroy the grades. Existence only — never content.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

_MANIFEST_DIRNAME = ".opengrader"
_MANIFEST_FILENAME = "extraction-manifest.jsonl"

# The orchestrator's stage ledger, alongside the extraction one in the same
# dot-dir. A separate file rather than a shared one with a namespaced key: the
# two are folded by different readers on different keys (a PDF stem vs a stage
# name), and mixing them would make ``read_manifest`` hand each caller the
# other's records to filter out — a filter that is easy to forget and silent
# when it is.
RUN_LEDGER_FILENAME = "run-ledger.jsonl"

# Hash inputs in 1 MiB chunks so a 400-page scan never has to fit in memory.
_HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ExtractionRecord:
    """One successfully extracted submission PDF.

    ``key`` is the source PDF's *stem* (filename without ``.pdf``) rather than
    its full path, because the stem is what decides the output folder
    (``<output-dir>/<stem>/``). Keying on the stem means moving or renaming the
    input directory doesn't invalidate the ledger, while two different PDFs that
    share a stem — which would collide in the output anyway — correctly count as
    the same slot.
    """

    key: str
    source: str  # the input path as the run saw it; for humans reading the log
    source_sha256: str
    settings_id: str
    outputs: tuple[str, ...] = ()  # relative to the output dir where possible
    student_id: str = ""
    student_name: str = ""
    completed_at: str = ""
    duration_seconds: float = 0.0

    def to_json(self) -> str:
        return json.dumps(
            {
                "key": self.key,
                "source": self.source,
                "source_sha256": self.source_sha256,
                "settings_id": self.settings_id,
                "outputs": list(self.outputs),
                "student_id": self.student_id,
                "student_name": self.student_name,
                "completed_at": self.completed_at,
                "duration_seconds": round(self.duration_seconds, 3),
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, line: str) -> "ExtractionRecord":
        data = json.loads(line)
        if not isinstance(data, dict):
            raise ValueError("manifest line is not an object")
        key = str(data.get("key", ""))
        if not key:
            raise ValueError("manifest line has no key")
        raw_outputs = data.get("outputs", [])
        outputs = tuple(str(item) for item in raw_outputs) if isinstance(raw_outputs, list) else ()
        try:
            duration = float(data.get("duration_seconds", 0.0))
        except (TypeError, ValueError):
            duration = 0.0
        return cls(
            key=key,
            source=str(data.get("source", "")),
            source_sha256=str(data.get("source_sha256", "")),
            settings_id=str(data.get("settings_id", "")),
            outputs=outputs,
            student_id=str(data.get("student_id", "")),
            student_name=str(data.get("student_name", "")),
            completed_at=str(data.get("completed_at", "")),
            duration_seconds=duration,
        )


def resolve_manifest_path(
    output_dir: str | Path, *, filename: str = _MANIFEST_FILENAME
) -> Path:
    """Return the ``.opengrader/<filename>`` ledger path for an output dir.

    ``filename`` defaults to the extraction manifest, so every existing caller
    is unaffected; the orchestrator passes :data:`RUN_LEDGER_FILENAME`.
    """
    base = Path(output_dir)
    if base.is_file():
        base = base.parent
    return base / _MANIFEST_DIRNAME / filename


def file_fingerprint(path: str | Path) -> str:
    """SHA-256 of a file's bytes, or ``""`` if it can't be read.

    An empty fingerprint is never reusable (see :func:`record_is_reusable`), so
    an unreadable input safely falls through to a real extraction attempt, which
    will report the actual problem, rather than blowing up here.
    """
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def input_fingerprint(path: str | Path) -> str:
    """Fingerprint of a stage's input, which may be one file **or a directory**.

    It lives next to :func:`file_fingerprint` because it answers that function's
    question for the one caller whose input is not always a single file: the
    ``opengrader-run`` orchestrator, whose ``--scan`` is either a bulk scan PDF
    or a folder of already-split per-student PDFs. Keeping it here means the
    value that goes into ``source_sha256`` is produced in the same module that
    decides what ``source_sha256`` means.

    A file is hashed byte-for-byte, exactly as before. A **directory** is
    fingerprinted from the sorted ``(name, size, mtime_ns)`` of its ``*.pdf``
    children instead — the same identity tuple ``core/render_cache.py``
    memoizes on. That is deliberate and it is a cost decision: hashing every
    byte of a 200-PDF folder on each run would take longer than the split stage
    the fingerprint exists to skip, which would make resume a net loss. The
    tuple misses only an edit that preserves a file's size *and* its mtime to
    the nanosecond, which no scanner, splitter or copy produces.

    Non-recursive, matching :func:`~opengrader.core.submissions.scan_check.discover_scan_pdfs`
    — a split directory is flat, and the fingerprint must cover exactly the
    files the stage will read. Returns ``""`` for a missing path or an empty
    folder, and an empty fingerprint is never reusable (see
    :func:`record_is_reusable`), so an input that vanished falls through to a
    real attempt that reports the actual problem.
    """
    target = Path(path)
    if target.is_file():
        return file_fingerprint(target)
    if not target.is_dir():
        return ""

    entries: list[tuple[str, int, int]] = []
    for candidate in sorted(target.glob("*.pdf")):
        try:
            stat = candidate.stat()
        except OSError:
            continue
        entries.append((candidate.name, stat.st_size, stat.st_mtime_ns))
    if not entries:
        return ""

    digest = hashlib.sha256()
    for name, size, mtime_ns in entries:
        digest.update(f"{name}\0{size}\0{mtime_ns}\0".encode("utf-8"))
    return digest.hexdigest()


def settings_fingerprint(settings: Mapping[str, object]) -> str:
    """SHA-256 over the run options that change what extraction produces.

    Callers pass only the options that actually affect the output (DPI, the
    blank template, the roster, which model transcribes answers, …) and leave
    out the ones that don't (worker count, progress display, the Ollama host).
    Change any of the former and every record's ``settings_id`` stops matching,
    so a resumed run correctly re-extracts instead of handing back stale files.

    ``default=str`` keeps the hash total: a ``Path`` or any other unexpected
    value still contributes rather than raising mid-run.
    """
    canonical = json.dumps(settings, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_record(
    output_dir: str | Path,
    record: ExtractionRecord,
    *,
    filename: str = _MANIFEST_FILENAME,
) -> Path:
    """Append one record and flush it to the OS before returning.

    Flushing per record is what makes the ledger survive a `kill -9` partway
    through a batch — the point of the whole feature. The cost is one small
    write per submission, which is nothing next to the OCR that produced it.
    """
    path = resolve_manifest_path(output_dir, filename=filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.to_json() + "\n")
        handle.flush()
    return path


def read_manifest(
    output_dir: str | Path, *, filename: str = _MANIFEST_FILENAME
) -> dict[str, ExtractionRecord]:
    """Fold the append-only log into the current record per source key.

    Later lines win, so re-extracting a PDF supersedes its earlier record
    without anyone having to rewrite the file. A missing file is an empty
    ledger (the normal first-run case), and malformed lines are skipped.
    """
    path = resolve_manifest_path(output_dir, filename=filename)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    latest: dict[str, ExtractionRecord] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = ExtractionRecord.from_json(line)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
        latest[record.key] = record
    return latest


def record_is_reusable(
    record: ExtractionRecord | None,
    *,
    output_dir: str | Path,
    source_sha256: str,
    settings_id: str,
) -> bool:
    """True when a resumed run may skip this PDF entirely.

    Requires a same-bytes input, same output-shaping settings, and every
    recorded output still present on disk. Content of the outputs is
    deliberately not checked — see this module's docstring.
    """
    if record is None:
        return False
    if not source_sha256 or record.source_sha256 != source_sha256:
        return False
    if not settings_id or record.settings_id != settings_id:
        return False
    base = Path(output_dir)
    for relative in record.outputs:
        candidate = Path(relative)
        resolved = candidate if candidate.is_absolute() else base / candidate
        if not resolved.exists():
            return False
    return True


def relative_outputs(output_dir: str | Path, paths: Iterable[str | Path]) -> tuple[str, ...]:
    """Normalize produced paths to output-dir-relative strings, de-duplicated.

    Relative storage keeps the ledger valid when the whole submissions folder is
    moved or copied to another machine. A path that genuinely lives outside the
    output dir is kept absolute rather than dropped, so the existence check
    still covers it.
    """
    base = Path(output_dir).resolve()
    seen: dict[str, None] = {}
    for item in paths:
        if not item:
            continue
        path = Path(item)
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        try:
            text = str(resolved.relative_to(base))
        except ValueError:
            text = str(resolved)
        seen.setdefault(text, None)
    return tuple(seen)


def utc_timestamp() -> str:
    """Current UTC time as a sortable ISO-8601 string (matches the journal)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
