from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

_MANIFEST_DIRNAME = ".opengrader"
_MANIFEST_FILENAME = "extraction-manifest.jsonl"

RUN_LEDGER_FILENAME = "run-ledger.jsonl"

_HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ExtractionRecord:
    key: str
    source: str
    source_sha256: str
    settings_id: str
    outputs: tuple[str, ...] = ()
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
    base = Path(output_dir)
    if base.is_file():
        base = base.parent
    return base / _MANIFEST_DIRNAME / filename


def file_fingerprint(path: str | Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def input_fingerprint(path: str | Path) -> str:
    """Directory fingerprints use direct PDF filenames, sizes, and mtimes; edits preserving
    all three are invisible. Missing or empty inputs cannot be reused.
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
    canonical = json.dumps(settings, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def append_record(
    output_dir: str | Path,
    record: ExtractionRecord,
    *,
    filename: str = _MANIFEST_FILENAME,
) -> Path:
    path = resolve_manifest_path(output_dir, filename=filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(record.to_json() + "\n")
        handle.flush()
    return path


def read_manifest(
    output_dir: str | Path, *, filename: str = _MANIFEST_FILENAME
) -> dict[str, ExtractionRecord]:
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
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
