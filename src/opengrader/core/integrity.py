"""Integrity snapshots and verified backups for a grading folder.

Every durable thing Opengrader produces is a file in one directory: the
``.submission.md`` grades, the crops they are graded from, the audit journal,
the curve, the claims. There is no database to repair it and no server holding a
second copy. A folder on a USB stick that gets yanked mid-write, a sync client
that resolves a conflict by picking a side, a shell glob that hits one directory
too many — all of these fail *silently*, and the first symptom is a student
asking why their grade changed.

Two capabilities, deliberately in that order:

* **Verify** — record a checksum of every file once (`build_snapshot`), then
  ask later what moved (`verify_snapshot`). Cheap enough to run before and
  after anything risky.
* **Back up** — write a single archive with its own manifest inside, and
  *verify it after writing* (`create_backup`). An unverified backup is a belief,
  not a copy; the whole point is that this one has been read back.

Two design decisions carry most of the value:

* **Append-only logs are verified by their prefix.** The journal, the claims
  and the regrade logs are *supposed* to grow, so a whole-file hash would
  report every one of them as "modified" on every run and the report would
  become noise nobody reads. Instead the recorded length and the hash of
  exactly those first bytes are checked: growth is normal, a changed prefix is
  rewriting history and is called out as such.
* **A modified grade file is not automatically a problem, and the tool says
  which.** Cross-referencing the audit journal separates "changed, and there
  are 12 grading actions recorded since the snapshot" from "changed, with no
  recorded grading activity at all" — the second is the one worth
  investigating, and burying it among ordinary grading is how a real
  corruption goes unnoticed.

The classification layer is pure — it is handed measurements — so the whole
report unit-tests with no folder. Only the functions below the I/O divider
touch a disk.
"""

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

# Files whose whole purpose is to grow. Verified by prefix (see the module
# docstring) rather than reported as modified on every append.
APPEND_ONLY_SUFFIXES = (".jsonl",)

# Never recorded: the snapshot cannot contain its own checksum, and the image
# caches are derived data that is rebuilt on demand and would only add churn.
EXCLUDED_DIR_NAMES = (".tui_cache", "__pycache__")

SNAPSHOT_DOTDIR = ".opengrader"
SNAPSHOT_FILENAME = "integrity.json"
# Written inside every backup archive so an archive can be checked on its own,
# on a machine that never had the folder.
BACKUP_MANIFEST_NAME = "opengrader-backup.json"

SNAPSHOT_FORMAT = 1

VerifyStatus = Literal[
    "unchanged",  # byte-identical to the snapshot
    "appended",   # append-only file grew, recorded prefix intact — expected
    "rewritten",  # append-only file's recorded prefix changed — history edited
    "modified",   # ordinary file's contents differ
    "missing",    # recorded, but no longer on disk
    "added",      # on disk, but not in the snapshot
    "unreadable", # present but could not be read
]

# The statuses that mean "look at this". `added` is intentionally not one of
# them: grading a folder legitimately creates files (annotated PDFs, journals,
# new crops), and flagging those would train the reader to ignore the report.
CONCERNING_STATUSES: tuple[VerifyStatus, ...] = (
    "rewritten",
    "modified",
    "missing",
    "unreadable",
)


class IntegrityError(ValueError):
    """A snapshot or backup could not be read."""


@dataclass(frozen=True)
class FileRecord:
    """The recorded fingerprint of one file."""

    path: str  # relative to the snapshot root, posix-separated
    size: int
    sha256: str
    append_only: bool = False


@dataclass(frozen=True)
class IntegritySnapshot:
    """Every file in a grading folder as it stood at one moment."""

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
    """One file's verdict, with enough detail to act on it."""

    path: str
    status: VerifyStatus
    detail: str = ""
    # Grading actions recorded in the journal since the snapshot for the student
    # this file belongs to. ``None`` means the question was never asked (not a
    # submission file, or no journal); ``0`` means asked and answered "none",
    # which is the interesting case.
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
    """The full comparison of a folder against its snapshot."""

    snapshot_at: str
    root: str
    findings: tuple[FileFinding, ...] = ()

    @property
    def concerning(self) -> tuple[FileFinding, ...]:
        return tuple(finding for finding in self.findings if finding.is_concerning)

    @property
    def unexplained(self) -> tuple[FileFinding, ...]:
        """Modified grade files with no grading recorded to explain them.

        The one list worth reading first: everything here changed without the
        app being the thing that changed it.
        """
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
    """Should this file be verified by prefix rather than in full?"""
    return Path(relative_path).suffix.lower() in APPEND_ONLY_SUFFIXES


def hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def compare_measurements(
    snapshot: IntegritySnapshot,
    measured: Mapping[str, tuple[int, str, str]],
    *,
    activity: Mapping[str, int] | None = None,
) -> IntegrityReport:
    """Classify a folder against its snapshot from measurements alone.

    ``measured`` maps a relative path to ``(size, whole_file_sha256,
    recorded_prefix_sha256)``. The third element is the hash of just the first
    *recorded* bytes, which is what makes an append-only log verifiable while it
    is still being appended to; for a file that shrank, or one the caller could
    not read, pass an empty string and it is reported rather than guessed at.

    Pure on purpose: the decision table is the part worth testing, and it needs
    no files to exercise.
    """
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
    """Serialize a snapshot to JSON text."""
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
    """Read a snapshot, raising `IntegrityError` on anything unusable.

    Loud rather than lenient: a half-read snapshot would verify a folder against
    a subset of itself and report a clean bill of health for files it never
    looked at, which is worse than having no snapshot at all.
    """
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


# --------------------------------------------------------------------------
# Everything below touches the filesystem. Keep new classification logic above
# this line so it stays testable without a folder to build.
# --------------------------------------------------------------------------


def resolve_snapshot_path(root: str | Path) -> Path:
    """The ``.opengrader/integrity.json`` path for a grading folder.

    Deliberately **not** configurable. A snapshot is a fingerprint *of* this
    folder and is only meaningful beside it: put it anywhere else and `verify`
    cannot find it without being told where, and moving or copying the folder
    silently orphans it. It lives in the same dot-dir as `policy.json`,
    `curve.json` and `claims.jsonl` for exactly the reason those do — so it
    travels with the assignment and stays out of submission discovery.
    """
    base = Path(root)
    if base.is_file():
        base = base.parent
    return base / SNAPSHOT_DOTDIR / SNAPSHOT_FILENAME


def default_backup_path(
    root: str | Path, *, today: datetime | None = None, encrypted: bool = False
) -> Path:
    """Where an archive of ``root`` goes unless the user says otherwise.

    ``<parent>/<folder>-backup-<YYYY-MM-DD>.zip`` — **beside** the folder, never
    inside it, which is the whole point: a backup stored in the thing it backs
    up is lost with it, and would be swept into every later backup besides. A
    same-day repeat is suffixed rather than refused, since `create_backup` will
    not overwrite and being told "that name is taken" helps nobody.

    ``encrypted=True`` appends ``.enc``, so an archive that needs a passphrase
    says so in its own name — the person who finds it in two years is usually
    not the person who wrote it.
    """
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
    """Every file worth fingerprinting under ``root``, in a stable order.

    The snapshot file itself is excluded because it is written *after* the
    measurements and so could never match them, and the disposable caches are
    excluded because their churn would drown the report.
    """
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
    """Would ``candidate`` land under ``folder``?

    Resolved on both sides so a relative path, a ``..`` or a symlinked folder
    cannot sneak past — the check exists to prevent a real mistake, not to
    pass a string comparison.
    """
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
    """Stream a file's size and SHA-256, optionally only its first ``limit`` bytes."""
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
    """Fingerprint every tracked file under ``root``."""
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
    """Persist a snapshot into the folder it describes."""
    target = resolve_snapshot_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_snapshot(snapshot), encoding="utf-8")
    return target


def read_snapshot(root: str | Path) -> IntegritySnapshot | None:
    """Load a folder's snapshot, or ``None`` when it has never been taken."""
    path = resolve_snapshot_path(root)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return parse_snapshot(text)


def measure_folder(
    root: str | Path, snapshot: IntegritySnapshot
) -> dict[str, tuple[int, str, str]]:
    """Measure a folder for `compare_measurements`.

    Each file yields its size, its whole-file hash, and — only where the
    snapshot recorded an append-only file that has grown — the hash of exactly
    the recorded number of leading bytes. The prefix hash is computed on demand
    rather than always, so an ordinary run does not read the journal twice.
    """
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
    """Count grading actions recorded since the snapshot, per submission file.

    This is what turns "modified" into an answer. It maps each submission file
    to its student key, then counts journal entries for that student that are
    newer than the snapshot. A submission that changed with a matching burst of
    recorded grading is ordinary work; one that changed with none is the thing
    the report exists to surface.

    Best-effort throughout: a folder with no journal, or a file that will not
    parse, simply yields no count, and the finding falls back to saying only
    that the file changed.
    """
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
        # ISO-8601 UTC strings compare correctly as text, which keeps this a
        # string comparison rather than a parse of every entry in a long journal.
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
    """Compare a folder against its snapshot (or a supplied one)."""
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
    """What `create_backup` wrote, and whether reading it back agreed."""

    archive_path: Path
    file_count: int
    total_bytes: int
    verified: bool
    mismatches: tuple[str, ...] = ()
    # Whether the archive needs a passphrase to read. Reported so the CLI and
    # the TUI can say it out loud: an archive nobody remembers the passphrase
    # for is indistinguishable from a lost one.
    encrypted: bool = False


@contextlib.contextmanager
def _readable_archive(
    archive_path: str | Path, passphrase: str = ""
) -> Iterator[Path]:
    """Yield a path to a plain zip, decrypting an encrypted archive if needed.

    Every reader (manifest, verify, restore) goes through here, so encryption is
    invisible to them and there is exactly one place that knows how to open a
    backup. The decrypted copy lives in a temporary directory and is removed on
    the way out — an encrypted archive that leaves a plaintext twin lying beside
    it has protected nothing.
    """
    source = Path(archive_path)
    if not is_encrypted(source):
        if passphrase:
            # Said rather than ignored: someone who typed a passphrase for an
            # unencrypted archive has misunderstood which file they are holding.
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
    """Write a verified zip archive of a grading folder.

    The snapshot is embedded in the archive, so the copy can be checked years
    later on a machine that never had the original. Then the archive is
    **read back and compared against that manifest** before this returns: a
    backup nobody has read is a belief about a file, and the entire reason to
    make one is the day the original is gone.

    With a ``passphrase`` the archive is encrypted (``core/crypto.py``) — the
    answer to a backup on a stick or a laptop that gets stolen, since a backup
    holds every name, id and scan there is. The read-back verification then
    happens **through the decryption**, so what is checked is the file that was
    actually written: an encrypted backup that cannot be decrypted is not a
    backup, and finding that out years later is finding out too late.

    Refuses to overwrite an existing archive — a backup file is exactly the kind
    of thing that must not be silently replaced by a shorter one.
    """
    base = Path(root)
    target = Path(archive_path)
    if target.exists():
        raise IntegrityError(
            f"{target} already exists — backups are never overwritten, since the "
            "existing one may be the only copy"
        )
    if _is_inside(target, base):
        # Not a matter of taste: the drive that loses the folder loses the
        # backup with it, and every later backup would pack the previous one
        # inside itself.
        raise IntegrityError(
            f"{target} is inside the folder being backed up — a backup kept "
            "there is lost with the thing it protects. Write it somewhere else "
            f"(the default is {default_backup_path(base, encrypted=bool(passphrase))})."
        )
    snapshot = build_snapshot(base, now=now)
    target.parent.mkdir(parents=True, exist_ok=True)

    if passphrase:
        # Build the zip in a temporary directory and encrypt it into place, so
        # the plaintext archive never exists at the destination — not even for
        # the moment between writing and encrypting.
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
    """Write the zip itself: every tracked file, plus the manifest inside it."""
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for record in snapshot.files:
            archive.write(base / record.path, arcname=record.path)
        archive.writestr(BACKUP_MANIFEST_NAME, render_snapshot(snapshot))


def read_backup_manifest(
    archive_path: str | Path, *, passphrase: str = ""
) -> IntegritySnapshot:
    """Read the snapshot embedded in a backup archive."""
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
    """Check an archive's contents against its own manifest.

    Returns the paths that disagree — empty means the archive holds exactly what
    it claims to. Reads each member's bytes rather than trusting the zip
    directory's CRC, since the question being asked is whether the *grading
    data* survived, not whether the container is well-formed.
    """
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
    """Read the manifest out of an already-decrypted archive.

    Split out so verify/restore decrypt **once** rather than once per read; on a
    multi-gigabyte archive the difference is minutes.
    """
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
    """Extract a backup into a **new** directory, then verify what landed.

    ``destination`` must not already exist. Restoring on top of a folder is how
    a good backup destroys a better original — the caller can always merge
    afterwards, but they cannot un-overwrite.
    """
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
    """One line naming every status that occurred, for a CLI or a status bar."""
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
    """The findings a reader must see, worst first."""
    severity = {"rewritten": 0, "missing": 1, "unreadable": 2, "modified": 3}
    return sorted(
        report.concerning, key=lambda f: (severity.get(f.status, 9), f.path)
    )
