from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .manifest import ExtractionRecord, read_manifest, resolve_manifest_path

_ACTIVE_RECENCY_SECONDS = 180.0


@dataclass(frozen=True)
class ExtractionPollResult:
    new_keys: tuple[str, ...]
    appears_active: bool
    idle_seconds: float | None = None


class ExtractionWatcher:
    def __init__(self, output_dir: str | Path) -> None:
        self._output_dir = Path(output_dir)
        self._manifest_path = resolve_manifest_path(self._output_dir)
        self._seen_keys: set[str] = set()
        self._stat_signature: tuple[int, int] | None = None
        self._records: dict[str, ExtractionRecord] = {}

    def poll(self, *, known_active: bool | None = None) -> ExtractionPollResult:
        signature = self._stat_manifest()
        if signature is None:
            return ExtractionPollResult(
                new_keys=(),
                appears_active=bool(known_active),
                idle_seconds=None,
            )

        if signature != self._stat_signature:
            self._records = read_manifest(self._output_dir)
            self._stat_signature = signature

        new_keys = tuple(
            key
            for key, record in self._records.items()
            if key not in self._seen_keys
            and _outputs_present(self._output_dir, record)
        )
        self._seen_keys.update(new_keys)

        idle_seconds = self._idle_seconds()
        appears_active = (
            known_active
            if known_active is not None
            else (idle_seconds is not None and idle_seconds < _ACTIVE_RECENCY_SECONDS)
        )
        return ExtractionPollResult(
            new_keys=new_keys,
            appears_active=appears_active,
            idle_seconds=idle_seconds,
        )

    def _stat_manifest(self) -> tuple[int, int] | None:
        try:
            stat = self._manifest_path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _idle_seconds(self) -> float | None:
        try:
            mtime = self._manifest_path.stat().st_mtime
        except OSError:
            return None
        return max(0.0, time.time() - mtime)


def _outputs_present(output_dir: Path, record: ExtractionRecord) -> bool:
    for relative in record.outputs:
        candidate = Path(relative)
        resolved = candidate if candidate.is_absolute() else output_dir / candidate
        if not resolved.exists():
            return False
    return True
