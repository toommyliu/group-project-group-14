from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import re
import secrets
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Mapping

from opengrader.core.crypto import (
    ENCRYPTED_SUFFIX,
    EncryptionError,
    decrypt_file,
    encrypt_file,
    is_encrypted,
)
from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.history.journal import current_actor
from opengrader.core.history.restore import (
    RESTORABLE_ASPECTS,
    Aspect,
    AspectValue,
    CellState,
    current_cells_from_documents,
    document_student_key,
)
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    blank_submission_file_references_markdown,
    load_student_submission_markdown,
    set_student_identity_markdown,
)

PACKET_SUBMISSIONS_DIRNAME = "submissions"
PACKET_DOTDIR = ".opengrader"
PACKET_MANIFEST_FILENAME = "packet.json"
HANDOFF_SUBDIR = "handoff"

PACKET_FORMAT = 1

_ALIAS_PREFIX = "og-"
_ALIAS_HEX_CHARS = 8
_ALIAS_SECRET_BYTES = 32
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class PacketError(ValueError):
    pass


@dataclass(frozen=True)
class PacketMember:
    alias: str
    student_key: str
    student_id: str
    student_name: str
    source_relative_path: str


@dataclass(frozen=True)
class HandoffKey:
    packet_id: str
    created_at: str
    created_by: str
    packet_path: str
    members: tuple[PacketMember, ...] = ()
    alias_secret: str = ""

    def by_alias(self) -> dict[str, PacketMember]:
        return {member.alias: member for member in self.members}


@dataclass(frozen=True)
class PacketManifest:
    packet_id: str
    created_at: str
    assignment_title: str
    redacted: bool
    aliases: tuple[str, ...] = ()
    # (alias, question_id) -> aspect values when the packet was created.
    baseline: Mapping[tuple[str, str], CellState] | None = None

    def baseline_cells(self) -> Mapping[tuple[str, str], CellState]:
        return self.baseline or {}


@dataclass(frozen=True)
class PacketResult:
    packet_path: Path
    manifest_path: Path
    key_path: Path
    manifest: PacketManifest
    key: HandoffKey
    qa_file_path: Path | None = None
    copied_crops: int = 0
    skipped_crops: tuple[str, ...] = ()

    @property
    def student_count(self) -> int:
        return len(self.key.members)


def slugify_packet_name(text: str, *, fallback: str = "packet") -> str:
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return slug or fallback


def default_packet_id(
    documents: Iterable[StudentSubmissionDocument],
    *,
    grader: str = "",
    today: datetime | None = None,
) -> str:
    title = ""
    for document in documents:
        if document.assignment_title.strip():
            title = document.assignment_title.strip()
            break
    stamp = (today or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    suffix = slugify_packet_name(grader, fallback="") if grader.strip() else ""
    base = slugify_packet_name(title, fallback="assignment")
    return f"{base}-for-{suffix}" if suffix else f"{base}-{stamp}"


def new_alias_secret() -> str:
    return secrets.token_hex(_ALIAS_SECRET_BYTES)


def mint_alias(
    packet_id: str,
    student_key: str,
    *,
    secret: str,
    taken: Iterable[str] = (),
) -> str:
    # Keyed: the packet id travels inside the packet and student ids are few
    # enough to try every one, so an unkeyed hash reverses against a roster.
    if not secret:
        raise PacketError("an alias needs the packet's secret")
    key = secret.encode("utf-8")
    reserved = set(taken)
    attempt = 0
    while True:
        message = f"{packet_id}\x00{student_key}\x00{attempt}".encode("utf-8")
        digest = hmac.new(key, message, hashlib.sha256).hexdigest()
        candidate = _ALIAS_PREFIX + digest[:_ALIAS_HEX_CHARS]
        if candidate not in reserved:
            return candidate
        attempt += 1


def _aspect_to_json(aspect: Aspect, value: AspectValue) -> dict[str, object]:
    if aspect == "grade":
        return {"points": value.points, "rubric": list(value.rubric)}
    return {"text": value.text}


def _aspect_from_json(aspect: Aspect, payload: Mapping[str, object]) -> AspectValue:
    if aspect == "grade":
        raw_points = payload.get("points")
        try:
            points = None if raw_points is None else float(raw_points)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            points = None
        raw_rubric = payload.get("rubric")
        rubric = (
            tuple(str(label) for label in raw_rubric)
            if isinstance(raw_rubric, (list, tuple))
            else ()
        )
        return AspectValue(points=points, rubric=rubric)
    raw_text = payload.get("text")
    return AspectValue(text="" if raw_text is None else str(raw_text))


def render_packet_manifest(manifest: PacketManifest) -> str:
    baseline: dict[str, dict[str, dict[str, object]]] = {}
    for (alias, question_id), cell in sorted(manifest.baseline_cells().items()):
        by_aspect = {
            aspect: _aspect_to_json(aspect, value)
            for aspect, value in cell.values.items()
        }
        baseline.setdefault(alias, {})[question_id] = by_aspect

    payload = {
        "packet_format": PACKET_FORMAT,
        "packet_id": manifest.packet_id,
        "created_at": manifest.created_at,
        "assignment_title": manifest.assignment_title,
        "redacted": manifest.redacted,
        "aliases": list(manifest.aliases),
        "baseline": baseline,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def parse_packet_manifest(text: str) -> PacketManifest:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PacketError(f"packet manifest is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PacketError("packet manifest must be a JSON object")

    version = data.get("packet_format")
    if isinstance(version, int) and version > PACKET_FORMAT:
        raise PacketError(
            f"packet was written by a newer Opengrader (format {version}, "
            f"this version reads {PACKET_FORMAT}) — upgrade before merging"
        )

    baseline: dict[tuple[str, str], CellState] = {}
    raw_baseline = data.get("baseline")
    if isinstance(raw_baseline, dict):
        for alias, questions in raw_baseline.items():
            if not isinstance(questions, dict):
                continue
            for question_id, aspects in questions.items():
                if not isinstance(aspects, dict):
                    continue
                values: dict[Aspect, AspectValue] = {}
                for aspect in RESTORABLE_ASPECTS:
                    payload = aspects.get(aspect)
                    if isinstance(payload, dict):
                        values[aspect] = _aspect_from_json(aspect, payload)
                if values:
                    baseline[(str(alias), str(question_id))] = CellState(values=values)

    raw_aliases = data.get("aliases")
    aliases = (
        tuple(str(alias) for alias in raw_aliases)
        if isinstance(raw_aliases, (list, tuple))
        else ()
    )
    return PacketManifest(
        packet_id=str(data.get("packet_id", "")),
        created_at=str(data.get("created_at", "")),
        assignment_title=str(data.get("assignment_title", "")),
        redacted=bool(data.get("redacted", True)),
        aliases=aliases,
        baseline=baseline,
    )


def render_handoff_key(key: HandoffKey) -> str:
    payload = {
        "key_format": PACKET_FORMAT,
        "packet_id": key.packet_id,
        "created_at": key.created_at,
        "created_by": key.created_by,
        "packet_path": key.packet_path,
        "alias_secret": key.alias_secret,
        "aliases": {
            member.alias: {
                "student_key": member.student_key,
                "student_id": member.student_id,
                "student_name": member.student_name,
                "source": member.source_relative_path,
            }
            for member in key.members
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def parse_handoff_key(text: str) -> HandoffKey:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PacketError(f"handoff key is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise PacketError("handoff key must be a JSON object")

    members: list[PacketMember] = []
    raw_aliases = data.get("aliases")
    if not isinstance(raw_aliases, dict):
        raise PacketError("handoff key has no alias map")
    for alias, entry in raw_aliases.items():
        if not isinstance(entry, dict):
            continue
        members.append(
            PacketMember(
                alias=str(alias),
                student_key=str(entry.get("student_key", "")),
                student_id=str(entry.get("student_id", "")),
                student_name=str(entry.get("student_name", "")),
                source_relative_path=str(entry.get("source", "")),
            )
        )
    return HandoffKey(
        packet_id=str(data.get("packet_id", "")),
        created_at=str(data.get("created_at", "")),
        created_by=str(data.get("created_by", "")),
        packet_path=str(data.get("packet_path", "")),
        members=tuple(sorted(members, key=lambda member: member.alias)),
        alias_secret=str(data.get("alias_secret", "")),
    )


def default_packet_output_dir(submissions_path: str | Path) -> Path:
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base.parent / "grader-copies"


def resolve_handoff_dir(submissions_path: str | Path) -> Path:
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / PACKET_DOTDIR / HANDOFF_SUBDIR


def resolve_manifest_path(packet_path: str | Path) -> Path:
    return Path(packet_path) / PACKET_DOTDIR / PACKET_MANIFEST_FILENAME


def read_packet_manifest(packet_path: str | Path) -> PacketManifest | None:
    path = resolve_manifest_path(packet_path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return parse_packet_manifest(text)


def read_handoff_key(path: str | Path) -> HandoffKey:
    return parse_handoff_key(Path(path).read_text(encoding="utf-8"))


def list_handoff_keys(submissions_path: str | Path) -> list[HandoffKey]:
    directory = resolve_handoff_dir(submissions_path)
    if not directory.is_dir():
        return []
    keys: list[HandoffKey] = []
    for key_file in sorted(directory.glob("*.json")):
        try:
            keys.append(read_handoff_key(key_file))
        except (OSError, PacketError):
            continue
    keys.sort(key=lambda key: key.created_at, reverse=True)
    return keys


def _unique_dir(parent: Path, name: str) -> Path:
    candidate = parent / name
    counter = 2
    while candidate.exists():
        candidate = parent / f"{name}-{counter}"
        counter += 1
    return candidate


def _copy_referenced_crops(
    document: StudentSubmissionDocument,
    source_dir: Path,
    dest_dir: Path,
) -> tuple[int, list[str]]:
    copied = 0
    skipped: list[str] = []
    for question in document.questions:
        raw_path = question.response_image_path
        if not raw_path:
            continue
        image_path = Path(raw_path)
        if not image_path.exists():
            continue
        try:
            relative = image_path.resolve().relative_to(source_dir.resolve())
        except ValueError:
            skipped.append(question.question_id)
            continue
        target = dest_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image_path, target)
        copied += 1
    return copied, skipped


def _redact_submission_text(text: str, *, alias: str, redacted: bool) -> str:
    if not redacted:
        return text
    updated = set_student_identity_markdown(text, student_id=alias, student_name="")
    return blank_submission_file_references_markdown(updated)


def build_packet(
    submissions_path: str | Path,
    output_path: str | Path,
    *,
    packet_id: str = "",
    grader: str = "",
    qa_file_path: str | Path | None = None,
    redact: bool = True,
    include_pdfs: bool = False,
    actor: str = "",
    now: datetime | None = None,
    student_keys: Iterable[str] | None = None,
) -> PacketResult:
    if include_pdfs and redact:
        raise PacketError(
            "a redacted packet cannot include the scanned PDFs — the student "
            "writes their name across the top of every sheet. Use --no-redact "
            "for a plain working copy, or leave the scans behind."
        )

    source = Path(submissions_path)
    submission_paths = discover_submission_paths(source)
    if not submission_paths:
        raise PacketError(f"no .submission.md files found under {source}")

    source_root = source if source.is_dir() else source.parent
    documents = [
        (path, load_student_submission_markdown(path)) for path in submission_paths
    ]
    if student_keys is not None:
        wanted = {key for key in student_keys if key}
        documents = [
            (path, document)
            for path, document in documents
            if document_student_key(document) in wanted
        ]
        if not documents:
            raise PacketError(
                f"none of the {len(wanted)} requested student(s) were found under "
                f"{source} — the packet would contain nothing to grade"
            )
    stamp = now or datetime.now(timezone.utc)
    resolved_id = slugify_packet_name(
        packet_id or default_packet_id((doc for _, doc in documents), grader=grader, today=stamp)
    )

    packet_root = Path(output_path) / resolved_id
    if packet_root.exists():
        raise PacketError(
            f"{packet_root} already exists — choose another name, or delete it "
            "first (overwriting a packet would discard grading it may hold)"
        )
    key_path = resolve_handoff_dir(source) / f"{resolved_id}.json"
    if key_path.exists():
        raise PacketError(
            f"a packet named {resolved_id} was already handed out from this "
            f"folder — choose another name (its key, {key_path}, is the only "
            "way to merge that copy back)"
        )
    packet_submissions = packet_root / PACKET_SUBMISSIONS_DIRNAME
    packet_submissions.mkdir(parents=True)

    alias_secret = new_alias_secret() if redact else ""
    members: list[PacketMember] = []
    aliases_taken: set[str] = set()
    copied_crops = 0
    skipped_crops: list[str] = []
    assignment_title = ""

    for path, document in documents:
        key = document_student_key(document)
        if not key:
            raise PacketError(
                f"{path.name} has neither a student id nor a name — resolve it "
                "in the review queue before handing out a packet, or the "
                "returned copy cannot be matched to it"
            )
        alias = (
            mint_alias(resolved_id, key, secret=alias_secret, taken=aliases_taken)
            if redact
            else key
        )
        aliases_taken.add(alias)
        if not assignment_title and document.assignment_title.strip():
            assignment_title = document.assignment_title.strip()

        folder_name = alias if redact else slugify_packet_name(
            path.parent.name if path.parent != source_root else path.name,
            fallback="submission",
        )
        member_dir = _unique_dir(packet_submissions, folder_name)
        member_dir.mkdir(parents=True, exist_ok=True)
        target = member_dir / f"{member_dir.name}.submission.md"
        target.write_text(
            _redact_submission_text(
                path.read_text(encoding="utf-8"), alias=alias, redacted=redact
            ),
            encoding="utf-8",
        )

        crops, skipped = _copy_referenced_crops(document, path.parent, member_dir)
        copied_crops += crops
        skipped_crops.extend(f"{alias}/{qid}" for qid in skipped)

        if include_pdfs:
            for pdf in sorted(path.parent.glob("*.pdf")):
                shutil.copy2(pdf, member_dir / pdf.name)

        try:
            source_relative = path.relative_to(source_root).as_posix()
        except ValueError:
            source_relative = path.name
        members.append(
            PacketMember(
                alias=alias,
                student_key=key,
                student_id=document.student_id,
                student_name=document.student_name,
                source_relative_path=source_relative,
            )
        )

    alias_by_key = {member.student_key: member.alias for member in members}
    baseline: dict[tuple[str, str], CellState] = {}
    for (key, question_id), cell in current_cells_from_documents(
        doc for _, doc in documents
    ).items():
        alias = alias_by_key.get(key)
        if alias is not None:
            baseline[(alias, question_id)] = cell

    manifest = PacketManifest(
        packet_id=resolved_id,
        created_at=stamp.isoformat(),
        assignment_title=assignment_title,
        redacted=redact,
        aliases=tuple(member.alias for member in members),
        baseline=baseline,
    )
    manifest_path = resolve_manifest_path(packet_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(render_packet_manifest(manifest), encoding="utf-8")

    copied_qa: Path | None = None
    if qa_file_path is not None:
        qa_source = Path(qa_file_path)
        copied_qa = packet_root / qa_source.name
        shutil.copy2(qa_source, copied_qa)

    key_record = HandoffKey(
        packet_id=resolved_id,
        created_at=stamp.isoformat(),
        created_by=actor or current_actor(),
        packet_path=str(packet_root.resolve()),
        members=tuple(members),
        alias_secret=alias_secret,
    )
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(render_handoff_key(key_record), encoding="utf-8")

    (packet_root / "README.txt").write_text(
        _packet_readme(manifest, copied_qa, len(members)), encoding="utf-8"
    )

    return PacketResult(
        packet_path=packet_root,
        manifest_path=manifest_path,
        key_path=key_path,
        manifest=manifest,
        key=key_record,
        qa_file_path=copied_qa,
        copied_crops=copied_crops,
        skipped_crops=tuple(skipped_crops),
    )


def _packet_readme(
    manifest: PacketManifest, qa_file: Path | None, student_count: int
) -> str:
    qa_name = qa_file.name if qa_file is not None else "<your answer key>.md"
    title = manifest.assignment_title or "this assignment"
    lines = [
        f"Opengrader grader packet: {manifest.packet_id}",
        f"Assignment: {title}",
        f"Created:    {manifest.created_at}",
        f"Students:   {student_count}",
        "",
        "To grade this packet:",
        "",
        f"    uv run tui --qa-file {qa_name} --submissions {PACKET_SUBMISSIONS_DIRNAME}/",
        "",
        "Grade normally, then send the whole folder back to whoever gave it to",
        "you. They will merge your grades into the master copy.",
        "",
    ]
    if manifest.redacted:
        lines += [
            "This copy is anonymous by design. Each student appears under an",
            "opaque code (og-xxxxxxxx) instead of a name or a student id, and the",
            "scanned exam PDFs were left out because the student's name is",
            "written across the top of every sheet. You will see each answer's",
            "image crop and its transcription, which is what grading needs.",
            "",
            "Only the person who created this packet can turn the codes back",
            "into names.",
            "",
        ]
    lines += [
        "Do not rename the folders inside submissions/ — the codes are how your",
        "grades find their way back to the right student.",
    ]
    return "\n".join(lines) + "\n"


SEALED_PACKET_SUFFIX = f".zip{ENCRYPTED_SUFFIX}"


def default_sealed_packet_path(packet_path: str | Path) -> Path:
    folder = Path(packet_path)
    return folder.parent / f"{folder.name}{SEALED_PACKET_SUFFIX}"


def seal_packet(
    packet_path: str | Path,
    passphrase: str,
    *,
    destination: str | Path | None = None,
    remove_source: bool = True,
) -> Path:
    folder = Path(packet_path)
    if not folder.is_dir():
        raise PacketError(f"{folder} is not a packet folder")
    target = Path(destination) if destination else default_sealed_packet_path(folder)
    if target.exists():
        raise PacketError(f"{target} already exists — refusing to overwrite it")

    with tempfile.TemporaryDirectory(prefix="opengrader-packet-") as workspace:
        staged = Path(workspace) / "packet.zip"
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    archive.write(path, arcname=str(path.relative_to(folder)))
        try:
            encrypt_file(staged, target, passphrase)
        except EncryptionError as exc:
            raise PacketError(f"could not seal the packet: {exc}") from exc

    if remove_source:
        shutil.rmtree(folder)
    return target


def unseal_packet(
    archive_path: str | Path, destination: str | Path, passphrase: str
) -> Path:
    source = Path(archive_path)
    target = Path(destination)
    if target.exists():
        raise PacketError(
            f"{target} already exists — unseal into a new directory so nothing "
            "already there can be overwritten"
        )
    if not is_encrypted(source):
        raise PacketError(f"{source} is not a sealed Opengrader packet")

    with tempfile.TemporaryDirectory(prefix="opengrader-packet-") as workspace:
        plain = Path(workspace) / "packet.zip"
        try:
            decrypt_file(source, plain, passphrase)
        except EncryptionError as exc:
            raise PacketError(f"{source}: {exc}") from exc
        target.mkdir(parents=True)
        with zipfile.ZipFile(plain) as archive:
            for name in archive.namelist():
                extracted = (target / name).resolve()
                if not str(extracted).startswith(str(target.resolve())):
                    raise PacketError(
                        f"{source} contains a path that escapes the packet: {name}"
                    )
                extracted.parent.mkdir(parents=True, exist_ok=True)
                extracted.write_bytes(archive.read(name))
    return target


def is_sealed_packet(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.is_file() and is_encrypted(candidate)


@contextlib.contextmanager
def opened_packet(path: str | Path, passphrase: str = "") -> Iterator[Path]:
    source = Path(path)
    if not is_sealed_packet(source):
        if passphrase:
            raise PacketError(
                f"{source} is not sealed — no passphrase is needed to read it"
            )
        yield source
        return

    if not passphrase:
        raise PacketError(
            f"{source} is a sealed packet — supply the passphrase it was sealed with"
        )
    with tempfile.TemporaryDirectory(prefix="opengrader-packet-") as workspace:
        yield unseal_packet(source, Path(workspace) / "packet", passphrase)
