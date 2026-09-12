from __future__ import annotations

import contextlib
import hashlib
import json
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Literal, Mapping, Sequence

from opengrader.core.crypto import (
    ENCRYPTED_SUFFIX,
    EncryptionError,
    decrypt_file,
    encrypt_file,
    is_encrypted,
)

APPEND_ONLY_SUFFIXES = (".jsonl",)

EXCLUDED_DIR_NAMES = (".tui_cache", "__pycache__")

SNAPSHOT_DOTDIR = ".opengrader"
SNAPSHOT_FILENAME = "integrity.json"
BACKUP_MANIFEST_NAME = "opengrader-backup.json"

SNAPSHOT_FORMAT = 1

VerifyStatus = Literal[
    "unchanged",
    "appended",
    "rewritten",
    "modified",
    "missing",
    "added",
    "unreadable",
]

CONCERNING_STATUSES: tuple[VerifyStatus, ...] = (
    "rewritten",
    "modified",
    "missing",
    "unreadable",
)


class IntegrityError(ValueError):
    pass


@dataclass(frozen=True)
class FileRecord:
    path: str
    size: int
    sha256: str
    append_only: bool = False


@dataclass(frozen=True)
class IntegritySnapshot:
    created_at: str
    root: str
    files: tuple[FileRecord, ...] = ()

    def by_path(self) -> dict[str, FileRecord]:
        return {record.path: record for record in self.files}

    @property
    def total_bytes(self) -> int:
        return sum(record.size for record in self.files)


@dataclass(frozen=True)
class FileFinding:
    path: str
    status: VerifyStatus
    detail: str = ""
    # None means unchecked; zero means checked with no activity.
    recorded_activity: int | None = None

    @property
    def is_concerning(self) -> bool:
        return self.status in CONCERNING_STATUSES

    def describe(self) -> str:
        parts = [f"{self.status}: {self.path}"]
        if self.detail:
            parts.append(f"({self.detail})")
        if self.status == "modified" and self.recorded_activity is not None:
            parts.append(
                f"— {self.recorded_activity} grading action(s) recorded since"
                if self.recorded_activity
                else "— no grading activity recorded since"
            )
        return " ".join(parts)


@dataclass(frozen=True)
class IntegrityReport:
    snapshot_at: str
    root: str
    findings: tuple[FileFinding, ...] = ()

    @property
    def concerning(self) -> tuple[FileFinding, ...]:
        return tuple(finding for finding in self.findings if finding.is_concerning)

    @property
    def unexplained(self) -> tuple[FileFinding, ...]:
        return tuple(
            finding
            for finding in self.findings
            if finding.status == "modified" and finding.recorded_activity == 0
        )

    def count(self, status: VerifyStatus) -> int:
        return sum(1 for finding in self.findings if finding.status == status)

    @property
    def ok(self) -> bool:
        return not self.concerning


def is_append_only(relative_path: str) -> bool:
    return Path(relative_path).suffix.lower() in APPEND_ONLY_SUFFIXES


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compare_measurements(
    snapshot: IntegritySnapshot,
    measured: Mapping[str, tuple[int, str, str]],
    *,
    activity: Mapping[str, int] | None = None,
) -> IntegrityReport:
    recorded_activity = activity or {}
    findings: list[FileFinding] = []
    known = snapshot.by_path()

    for path, record in sorted(known.items()):
        measurement = measured.get(path)
        if measurement is None:
            findings.append(FileFinding(path=path, status="missing"))
            continue
        size, whole_hash, prefix_hash = measurement
        if not whole_hash and not prefix_hash:
            findings.append(
                FileFinding(path=path, status="unreadable", detail="could not be read")
            )
            continue

        if whole_hash == record.sha256 and size == record.size:
            findings.append(FileFinding(path=path, status="unchanged"))
            continue

        if record.append_only:
            if size < record.size:
                findings.append(
                    FileFinding(
                        path=path,
                        status="rewritten",
                        detail=f"shrank from {record.size} to {size} bytes",
                    )
                )
            elif prefix_hash == record.sha256:
                findings.append(
                    FileFinding(
                        path=path,
                        status="appended",
                        detail=f"+{size - record.size} bytes",
                    )
                )
            else:
                findings.append(
                    FileFinding(
                        path=path,
                        status="rewritten",
                        detail="the recorded lines no longer match",
                    )
                )
            continue

        findings.append(
            FileFinding(
                path=path,
                status="modified",
                detail=f"{record.size} → {size} bytes",
                recorded_activity=recorded_activity.get(path),
            )
        )

    for path in sorted(measured):
        if path not in known:
            findings.append(FileFinding(path=path, status="added"))

    return IntegrityReport(
        snapshot_at=snapshot.created_at, root=snapshot.root, findings=tuple(findings)
    )


def render_snapshot(snapshot: IntegritySnapshot) -> str:
    payload = {
        "snapshot_format": SNAPSHOT_FORMAT,
        "created_at": snapshot.created_at,
        "root": snapshot.root,
        "files": [
            {
                "path": record.path,
                "size": record.size,
                "sha256": record.sha256,
                "append_only": record.append_only,
            }
            for record in snapshot.files
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def parse_snapshot(text: str) -> IntegritySnapshot:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise IntegrityError(f"snapshot is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise IntegrityError("snapshot must be a JSON object")
    version = data.get("snapshot_format")
    if isinstance(version, int) and version > SNAPSHOT_FORMAT:
        raise IntegrityError(
            f"snapshot was written by a newer Opengrader (format {version}, "
            f"this version reads {SNAPSHOT_FORMAT})"
        )
    raw_files = data.get("files")
    if not isinstance(raw_files, list):
        raise IntegrityError("snapshot has no file list")

    files: list[FileRecord] = []
    for entry in raw_files:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("path", "")).strip()
        if not path:
            continue
        try:
            size = int(entry.get("size", 0))
        except (TypeError, ValueError):
            size = 0
        files.append(
            FileRecord(
                path=path,
                size=size,
                sha256=str(entry.get("sha256", "")),
                append_only=bool(entry.get("append_only", False)),
            )
        )
    return IntegritySnapshot(
        created_at=str(data.get("created_at", "")),
        root=str(data.get("root", "")),
        files=tuple(files),
    )


def resolve_snapshot_path(root: str | Path) -> Path:
    base = Path(root)
    if base.is_file():
        base = base.parent
    return base / SNAPSHOT_DOTDIR / SNAPSHOT_FILENAME


def default_backup_path(
    root: str | Path, *, today: datetime | None = None, encrypted: bool = False
) -> Path:
    base = Path(root)
    if base.is_file():
        base = base.parent
    stamp = (today or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    tail = f".zip{ENCRYPTED_SUFFIX}" if encrypted else ".zip"
    candidate = base.parent / f"{base.name}-backup-{stamp}{tail}"
    counter = 2
    while candidate.exists():
        candidate = base.parent / f"{base.name}-backup-{stamp}-{counter}{tail}"
        counter += 1
    return candidate


def iter_tracked_files(root: str | Path) -> list[Path]:
    base = Path(root)
    snapshot_path = resolve_snapshot_path(base).resolve()
    tracked: list[Path] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        if path.resolve() == snapshot_path:
            continue
        tracked.append(path)
    return tracked


def _is_inside(candidate: Path, folder: Path) -> bool:
    try:
        resolved_folder = folder.resolve()
        resolved_candidate = (
            candidate.resolve()
            if candidate.exists()
            else candidate.parent.resolve() / candidate.name
        )
    except OSError:
        return False
    return resolved_folder == resolved_candidate or resolved_folder in resolved_candidate.parents


def _hash_file(path: Path, *, limit: int | None = None) -> tuple[int, str]:
    digest = hashlib.sha256()
    read = 0
    with path.open("rb") as handle:
        while True:
            budget = 1 << 20 if limit is None else min(1 << 20, limit - read)
            if budget <= 0:
                break
            chunk = handle.read(budget)
            if not chunk:
                break
            digest.update(chunk)
            read += len(chunk)
    return read, digest.hexdigest()


def build_snapshot(
    root: str | Path, *, now: datetime | None = None
) -> IntegritySnapshot:
    base = Path(root)
    if not base.is_dir():
        raise IntegrityError(f"not a directory: {base}")
    records: list[FileRecord] = []
    for path in iter_tracked_files(base):
        relative = path.relative_to(base).as_posix()
        try:
            size, digest = _hash_file(path)
        except OSError as exc:
            raise IntegrityError(f"could not read {relative}: {exc}") from exc
        records.append(
            FileRecord(
                path=relative,
                size=size,
                sha256=digest,
                append_only=is_append_only(relative),
            )
        )
    stamp = now or datetime.now(timezone.utc)
    return IntegritySnapshot(
        created_at=stamp.isoformat(),
        root=str(base.resolve()),
        files=tuple(records),
    )


def write_snapshot(root: str | Path, snapshot: IntegritySnapshot) -> Path:
    target = resolve_snapshot_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_snapshot(snapshot), encoding="utf-8")
    return target


def read_snapshot(root: str | Path) -> IntegritySnapshot | None:
    path = resolve_snapshot_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return parse_snapshot(text)


def measure_folder(
    root: str | Path, snapshot: IntegritySnapshot
) -> dict[str, tuple[int, str, str]]:
    base = Path(root)
    known = snapshot.by_path()
    measured: dict[str, tuple[int, str, str]] = {}
    for path in iter_tracked_files(base):
        relative = path.relative_to(base).as_posix()
        try:
            size, digest = _hash_file(path)
        except OSError:
            measured[relative] = (0, "", "")
            continue
        prefix = ""
        record = known.get(relative)
        if record is not None and record.append_only and size > record.size:
            try:
                _, prefix = _hash_file(path, limit=record.size)
            except OSError:
                prefix = ""
        measured[relative] = (size, digest, prefix)
    return measured


def journal_activity_by_file(
    root: str | Path,
    snapshot: IntegritySnapshot,
) -> dict[str, int]:
    from opengrader.core.history.journal import read_journal, resolve_history_dir
    from opengrader.core.history.restore import document_student_key
    from opengrader.core.submissions.student_file import (
        load_student_submission_markdown,
    )

    base = Path(root)
    entries = read_journal(resolve_history_dir(base))
    if not entries:
        return {}

    since = snapshot.created_at
    counts_by_key: dict[str, int] = {}
    for entry in entries:
        if entry.timestamp <= since:
            continue
        key = (entry.student_id or "").strip().casefold()
        if not key:
            continue
        counts_by_key[key] = counts_by_key.get(key, 0) + 1

    activity: dict[str, int] = {}
    for record in snapshot.files:
        if not record.path.endswith(".submission.md"):
            continue
        path = base / record.path
        try:
            document = load_student_submission_markdown(path)
        except (OSError, ValueError):
            continue
        key = document_student_key(document)
        if key:
            activity[record.path] = counts_by_key.get(key, 0)
    return activity


def verify_folder(
    root: str | Path,
    snapshot: IntegritySnapshot | None = None,
    *,
    explain: bool = True,
) -> IntegrityReport:
    base = Path(root)
    resolved = snapshot or read_snapshot(base)
    if resolved is None:
        raise IntegrityError(
            f"no integrity snapshot for {base} — run 'snapshot' first to record "
            "one, then 'verify' can say what moved since"
        )
    measured = measure_folder(base, resolved)
    activity = journal_activity_by_file(base, resolved) if explain else {}
    return compare_measurements(resolved, measured, activity=activity)


@dataclass(frozen=True)
class BackupResult:
    archive_path: Path
    file_count: int
    total_bytes: int
    verified: bool
    mismatches: tuple[str, ...] = ()
    encrypted: bool = False


@contextlib.contextmanager
def _readable_archive(
    archive_path: str | Path, passphrase: str = ""
) -> Iterator[Path]:
    source = Path(archive_path)
    if not is_encrypted(source):
        if passphrase:
            raise IntegrityError(
                f"{source} is not encrypted — no passphrase is needed to read it"
            )
        yield source
        return

    if not passphrase:
        raise IntegrityError(
            f"{source} is encrypted — supply the passphrase it was written with"
        )
    with tempfile.TemporaryDirectory(prefix="opengrader-backup-") as workspace:
        plain = Path(workspace) / "archive.zip"
        try:
            decrypt_file(source, plain, passphrase)
        except EncryptionError as exc:
            raise IntegrityError(f"{source}: {exc}") from exc
        yield plain


def create_backup(
    root: str | Path,
    archive_path: str | Path,
    *,
    now: datetime | None = None,
    passphrase: str = "",
) -> BackupResult:
    base = Path(root)
    target = Path(archive_path)
    if target.exists():
        raise IntegrityError(
            f"{target} already exists — backups are never overwritten, since the "
            "existing one may be the only copy"
        )
    if _is_inside(target, base):
        raise IntegrityError(
            f"{target} is inside the folder being backed up — a backup kept "
            "there is lost with the thing it protects. Write it somewhere else "
            f"(the default is {default_backup_path(base, encrypted=bool(passphrase))})."
        )
    snapshot = build_snapshot(base, now=now)
    target.parent.mkdir(parents=True, exist_ok=True)

    if passphrase:
        with tempfile.TemporaryDirectory(prefix="opengrader-backup-") as workspace:
            staged = Path(workspace) / "archive.zip"
            _write_archive(base, staged, snapshot)
            try:
                encrypt_file(staged, target, passphrase)
            except EncryptionError as exc:
                raise IntegrityError(f"could not encrypt the backup: {exc}") from exc
    else:
        _write_archive(base, target, snapshot)

    mismatches = verify_backup(target, passphrase=passphrase)
    return BackupResult(
        archive_path=target,
        file_count=len(snapshot.files),
        total_bytes=snapshot.total_bytes,
        verified=not mismatches,
        mismatches=mismatches,
        encrypted=bool(passphrase),
    )


def _write_archive(
    base: Path, target: Path, snapshot: IntegritySnapshot
) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for record in snapshot.files:
            archive.write(base / record.path, arcname=record.path)
        archive.writestr(BACKUP_MANIFEST_NAME, render_snapshot(snapshot))


def read_backup_manifest(
    archive_path: str | Path, *, passphrase: str = ""
) -> IntegritySnapshot:
    with _readable_archive(archive_path, passphrase) as readable:
        try:
            with zipfile.ZipFile(readable) as archive:
                return parse_snapshot(
                    archive.read(BACKUP_MANIFEST_NAME).decode("utf-8")
                )
        except KeyError as exc:
            raise IntegrityError(
                f"{archive_path} has no {BACKUP_MANIFEST_NAME} — it was not written "
                "by Opengrader, so there is nothing to verify it against"
            ) from exc
        except (OSError, zipfile.BadZipFile) as exc:
            raise IntegrityError(f"could not read {archive_path}: {exc}") from exc


def verify_backup(
    archive_path: str | Path, *, passphrase: str = ""
) -> tuple[str, ...]:
    with _readable_archive(archive_path, passphrase) as readable:
        snapshot = _manifest_from_open_archive(readable, archive_path)
        mismatches: list[str] = []
        with zipfile.ZipFile(readable) as archive:
            names = set(archive.namelist())
            for record in snapshot.files:
                if record.path not in names:
                    mismatches.append(record.path)
                    continue
                data = archive.read(record.path)
                if len(data) != record.size or hash_bytes(data) != record.sha256:
                    mismatches.append(record.path)
        return tuple(mismatches)


def _manifest_from_open_archive(
    readable: Path, original: str | Path
) -> IntegritySnapshot:
    try:
        with zipfile.ZipFile(readable) as archive:
            return parse_snapshot(archive.read(BACKUP_MANIFEST_NAME).decode("utf-8"))
    except KeyError as exc:
        raise IntegrityError(
            f"{original} has no {BACKUP_MANIFEST_NAME} — it was not written "
            "by Opengrader, so there is nothing to verify it against"
        ) from exc
    except (OSError, zipfile.BadZipFile) as exc:
        raise IntegrityError(f"could not read {original}: {exc}") from exc


def restore_backup(
    archive_path: str | Path, destination: str | Path, *, passphrase: str = ""
) -> BackupResult:
    target = Path(destination)
    if target.exists():
        raise IntegrityError(
            f"{target} already exists — restore into a new directory, then "
            "compare the two yourself"
        )

    with _readable_archive(archive_path, passphrase) as readable:
        snapshot = _manifest_from_open_archive(readable, archive_path)
        mismatches = verify_backup(readable)
        if mismatches:
            raise IntegrityError(
                f"{archive_path} does not match its own manifest "
                f"({len(mismatches)} file(s)) — refusing to restore a damaged backup"
            )

        target.mkdir(parents=True)
        with zipfile.ZipFile(readable) as archive:
            for record in snapshot.files:
                destination_path = target / record.path
                destination_path.parent.mkdir(parents=True, exist_ok=True)
                destination_path.write_bytes(archive.read(record.path))

    extracted = measure_folder(target, snapshot)
    report = compare_measurements(snapshot, extracted)
    return BackupResult(
        archive_path=Path(archive_path),
        file_count=len(snapshot.files),
        total_bytes=snapshot.total_bytes,
        verified=report.ok,
        mismatches=tuple(finding.path for finding in report.concerning),
        encrypted=is_encrypted(archive_path),
    )


def summarize_report(report: IntegrityReport) -> str:
    order: Sequence[VerifyStatus] = (
        "unchanged",
        "appended",
        "added",
        "modified",
        "rewritten",
        "missing",
        "unreadable",
    )
    parts = [
        f"{report.count(status)} {status}" for status in order if report.count(status)
    ]
    return ", ".join(parts) or "nothing recorded"


def iter_concerning(report: IntegrityReport) -> Iterable[FileFinding]:
    severity = {"rewritten": 0, "missing": 1, "unreadable": 2, "modified": 3}
    return sorted(
        report.concerning, key=lambda f: (severity.get(f.status, 9), f.path)
    )
