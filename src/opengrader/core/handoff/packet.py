"""Grader packets — a redacted, self-contained copy of a cohort to hand out.

`core/claims.py` divides the pile between graders who all open **one** folder.
That assumption breaks the moment a TA takes the work home: they copy the
folder to a laptop or a USB stick, grade for an evening, and hand back a
directory that has drifted from the teacher's. Nothing in the app could
reconcile the two — the optimistic mtime guard (`core/concurrency.py`) is
designed to *refuse* exactly that situation, and refusing is the only thing it
can do.

A **packet** is the artifact that makes the round trip well-defined. It is a
copy of the cohort that is:

* **Redacted** — every student's name *and* id are replaced by an opaque
  per-packet **alias**, and the scans (which have a name written across the top
  of every sheet) are left behind entirely. This is `core/anonymize.py`'s
  data-minimization argument taken to its conclusion: Layer 2 keeps ids so the
  teacher can re-join, and here the alias plays that role instead, so there is
  nothing in the grader's copy to de-anonymize.
* **Self-contained** — the answer key, each answer's transcription, and each
  answer's image crop, so the TA runs the ordinary TUI against it and grades
  exactly as they would at home in the real folder.
* **Baselined** — the manifest records the value of every answer *at the moment
  the packet was created*. That one field is what turns the merge back
  (`merge.py`) into a real three-way merge instead of a guess about who edited
  what.

**Why not QR codes for the identity link?** A QR code exists to carry data
across a boundary whose only channel is an image — paper through a scanner,
which is what `core/qr_codes.py` is for. Both ends of a packet are files on a
disk, so encoding the alias into pixels and reading it back with OpenCV would be
slower, lossier, and impossible to diff, for no gain. What the packet *does*
borrow is the design of the personalized-QR exam: the sheet prints an anonymous
id and never the name, and the teacher keeps the id→name master key. Here the
alias is that anonymous id and the **handoff key** is that master key — written
into the *teacher's* folder, never into the packet, because a copy that contains
its own key is not redacted at all.

Two artifacts, deliberately separate:

* ``<packet>/.opengrader/packet.json`` — travels with the packet. Aliases and
  the baseline only; no real name or id appears anywhere in it.
* ``<source>/.opengrader/handoff/<packet-id>.json`` — stays with the teacher.
  The alias → real identity map, and the only thing that can undo the redaction.
  Losing it means the returned packet can no longer be merged, the same trade
  `core/anonymize.write_name_map` already makes for the name map.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
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

# Layout inside a packet. ``submissions/`` is a plain cohort folder so the TUI
# opens it with no packet-specific code: `uv run tui --qa-file … --submissions …`.
PACKET_SUBMISSIONS_DIRNAME = "submissions"
PACKET_DOTDIR = ".opengrader"
PACKET_MANIFEST_FILENAME = "packet.json"
# Teacher-side, in the *source* folder: one key file per packet handed out.
HANDOFF_SUBDIR = "handoff"

# Bumped only on an incompatible manifest grammar change, so a packet from an
# older version is rejected with a sentence rather than mis-read as empty.
PACKET_FORMAT = 1

_ALIAS_PREFIX = "og-"
_ALIAS_HEX_CHARS = 8
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class PacketError(ValueError):
    """A packet could not be built or read."""


@dataclass(frozen=True)
class PacketMember:
    """One student's place in a packet, from the teacher's side of the map."""

    alias: str
    student_key: str  # the journal key (`core/history.student_key`) this is really
    student_id: str
    student_name: str
    source_relative_path: str  # where the submission lives in the source folder


@dataclass(frozen=True)
class HandoffKey:
    """The teacher-side alias → identity map for one packet handed out.

    Kept out of the packet on purpose. Everything needed to *grade* is in the
    packet; everything needed to *know who you graded* is only here.
    """

    packet_id: str
    created_at: str
    created_by: str
    packet_path: str
    members: tuple[PacketMember, ...] = ()

    def by_alias(self) -> dict[str, PacketMember]:
        return {member.alias: member for member in self.members}


@dataclass(frozen=True)
class PacketManifest:
    """The packet's own manifest — aliases and the baseline, no identities."""

    packet_id: str
    created_at: str
    assignment_title: str
    redacted: bool
    aliases: tuple[str, ...] = ()
    # (alias, question_id) -> the value each aspect held when the packet was cut.
    baseline: Mapping[tuple[str, str], CellState] | None = None

    def baseline_cells(self) -> Mapping[tuple[str, str], CellState]:
        return self.baseline or {}


@dataclass(frozen=True)
class PacketResult:
    """What `build_packet` wrote, for the CLI/TUI to report."""

    packet_path: Path
    manifest_path: Path
    key_path: Path
    manifest: PacketManifest
    key: HandoffKey
    qa_file_path: Path | None = None
    copied_crops: int = 0
    # Crops that could not travel (referenced from outside the submission's own
    # folder). Reported rather than dropped: that question can still be graded
    # from its transcription, but the grader should know the image is missing.
    skipped_crops: tuple[str, ...] = ()

    @property
    def student_count(self) -> int:
        return len(self.key.members)


def slugify_packet_name(text: str, *, fallback: str = "packet") -> str:
    """Filesystem-friendly packet id: lowercase, hyphen-separated."""
    slug = _SLUG_RE.sub("-", text.strip().lower()).strip("-")
    return slug or fallback


def default_packet_id(
    documents: Iterable[StudentSubmissionDocument],
    *,
    grader: str = "",
    today: datetime | None = None,
) -> str:
    """``<assignment>-for-<grader>`` (or ``-<date>``) — a name a human can file.

    The grader's name is preferred over a date because the thing a teacher needs
    to find later is "the copy Alice has", and two packets cut on the same day
    for two TAs must not collide.
    """
    title = ""
    for document in documents:
        if document.assignment_title.strip():
            title = document.assignment_title.strip()
            break
    stamp = (today or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    suffix = slugify_packet_name(grader, fallback="") if grader.strip() else ""
    base = slugify_packet_name(title, fallback="assignment")
    return f"{base}-for-{suffix}" if suffix else f"{base}-{stamp}"


def mint_alias(packet_id: str, student_key: str, *, taken: Iterable[str] = ()) -> str:
    """A stable, opaque alias for one student inside one packet.

    Derived by hash rather than by counter so re-cutting the same packet for the
    same cohort produces the *same* aliases — a teacher who loses a packet can
    reissue it and the returned copy still merges. Collisions (astronomically
    unlikely at 8 hex characters, but a wrong grade is not a rounding error) are
    resolved by a deterministic probe, the same approach
    `core/roster.assign_student_ids` uses for minted student ids.
    """
    reserved = set(taken)
    attempt = 0
    while True:
        seed = f"{packet_id}\x00{student_key}\x00{attempt}".encode("utf-8")
        candidate = _ALIAS_PREFIX + hashlib.sha256(seed).hexdigest()[:_ALIAS_HEX_CHARS]
        if candidate not in reserved:
            return candidate
        attempt += 1


# --------------------------------------------------------------------------
# Manifest / key serialization. Pure: strings in, strings out, so the whole
# format round-trips in a unit test with no folder to build.
# --------------------------------------------------------------------------


def _aspect_to_json(aspect: Aspect, value: AspectValue) -> dict[str, object]:
    if aspect == "grade":
        return {"points": value.points, "rubric": list(value.rubric)}
    return {"text": value.text}


def _aspect_from_json(aspect: Aspect, payload: Mapping[str, object]) -> AspectValue:
    """Read one baseline aspect back, coercing defensively.

    The manifest is a file on a disk that travelled; a malformed field degrades
    to the empty value, which the merge then treats as "no baseline known" for
    that aspect rather than as a confident wrong answer.
    """
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
    """Serialize a packet manifest to JSON text."""
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
    """Read a packet manifest, raising `PacketError` on anything unusable."""
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
    """Serialize the teacher-side alias → identity map to JSON text."""
    payload = {
        "key_format": PACKET_FORMAT,
        "packet_id": key.packet_id,
        "created_at": key.created_at,
        "created_by": key.created_by,
        "packet_path": key.packet_path,
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
    """Read a handoff key.

    Fails loudly on a malformed file rather than degrading to "no aliases":
    silently treating a corrupt key as absent would make the merge fall back to
    matching aliases against real ids, match nothing, and report a returned
    packet as having no changes at all.
    """
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
    )


# --------------------------------------------------------------------------
# Everything below touches the filesystem. Keep new logic above this line so it
# stays unit-testable without a cohort on disk (the split `restore.py` uses).
# --------------------------------------------------------------------------


def default_packet_output_dir(submissions_path: str | Path) -> Path:
    """Where grader copies go unless the user says otherwise.

    ``<assignment>/grader-copies/`` — a sibling of the submissions folder, not a
    child of it. Beside it is discoverable (an instructor finds it without being
    told) while staying clear of the cohort itself, so a packet is never swept
    into an integrity snapshot, a backup, or a later packet of the same folder.
    """
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base.parent / "grader-copies"


def resolve_handoff_dir(submissions_path: str | Path) -> Path:
    """The teacher-side ``.opengrader/handoff`` dir for a submissions folder."""
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / PACKET_DOTDIR / HANDOFF_SUBDIR


def resolve_manifest_path(packet_path: str | Path) -> Path:
    """The manifest inside a packet directory."""
    return Path(packet_path) / PACKET_DOTDIR / PACKET_MANIFEST_FILENAME


def read_packet_manifest(packet_path: str | Path) -> PacketManifest | None:
    """Read a packet's manifest, or ``None`` when the folder is not a packet.

    ``None`` is a real answer, not a failure: merging two hand-made copies of a
    folder is supported, and there the merge falls back to the incoming
    journal for its baseline.
    """
    path = resolve_manifest_path(packet_path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return parse_packet_manifest(text)


def read_handoff_key(path: str | Path) -> HandoffKey:
    """Read a handoff key file written by `build_packet`."""
    return parse_handoff_key(Path(path).read_text(encoding="utf-8"))


def list_handoff_keys(submissions_path: str | Path) -> list[HandoffKey]:
    """Every packet cut from this folder, newest first.

    Unreadable key files are skipped rather than raising — one corrupt record
    must not hide the other five packets a teacher is trying to account for.
    """
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
    """``parent/name``, suffixed if that folder is already spoken for."""
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
    """Copy exactly the crop images this submission references.

    Copying the whole per-student folder would be simpler and wrong: it also
    sweeps up the scanned PDF and the annotated copy, both of which have the
    student's name written on them. Walking the references instead means the
    packet contains the answer boxes and nothing else, and it works for both
    the flat and the one-level-down layouts the provider accepts.

    A crop referenced from *outside* the submission's own folder is skipped and
    named: rewriting the reference would mean re-rendering the markdown, and
    that question is still gradable from its transcription.
    """
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
    """Rewrite one submission's identity fields for the packet.

    ``set_student_identity_markdown`` is reused rather than a full re-render for
    the reason `core/anonymize.py` documents: a surgical frontmatter rewrite
    leaves every question block byte-identical, so the packet's answers are the
    teacher's answers and a later diff of the two is meaningful.

    The name is blanked and the id becomes the alias; the references to the scan
    and to the handwritten name/id crops are blanked because none of those files
    travel — and because their *paths* can carry a name on their own.
    """
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
    """Create a grader packet from a cohort, plus the teacher-side key.

    ``include_pdfs`` is refused together with ``redact`` rather than silently
    ignored: the scans are the one artifact that carries the student's
    handwritten name on every sheet, so copying them into a folder whose whole
    purpose is that names are absent would quietly undo the feature.

    ``student_keys`` restricts the packet to a subset of the cohort (by
    :func:`~opengrader.core.history.restore.student_key`); ``None`` packs
    everyone, which is what a handoff wants. The subset exists for calibration
    (:mod:`opengrader.core.handoff.calibration`), where the same small sample
    goes to every grader — and a key naming nobody is an error rather than an
    empty packet, since silently handing a TA nothing to grade is indis-
    tinguishable from the tool being broken.
    """
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
    packet_submissions = packet_root / PACKET_SUBMISSIONS_DIRNAME
    packet_submissions.mkdir(parents=True)

    members: list[PacketMember] = []
    aliases_taken: set[str] = set()
    copied_crops = 0
    skipped_crops: list[str] = []
    assignment_title = ""

    for path, document in documents:
        key = document_student_key(document)
        if not key:
            # No id and no name: there is nothing stable to alias, and merging
            # such a submission back could only guess which one it was.
            raise PacketError(
                f"{path.name} has neither a student id nor a name — resolve it "
                "in the review queue before handing out a packet, or the "
                "returned copy cannot be matched to it"
            )
        # The alias is whatever the packet's own files are keyed by, so that the
        # baseline and the returned copy speak the same language: a minted code
        # when redacting (the file's student_id becomes it), and the real
        # journal key when not (the file is untouched, so that is still its key).
        alias = mint_alias(resolved_id, key, taken=aliases_taken) if redact else key
        aliases_taken.add(alias)
        if not assignment_title and document.assignment_title.strip():
            assignment_title = document.assignment_title.strip()

        # The folder name is cosmetic and separate from the alias: an
        # un-redacted key can be ``name:tommy liu``, which is not a filename.
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

    # The baseline is built from the *documents as they are now*, keyed by
    # alias — that is what the returned copy will be compared against, so it
    # must be recorded in the packet's own vocabulary.
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
    )
    key_dir = resolve_handoff_dir(source)
    key_dir.mkdir(parents=True, exist_ok=True)
    key_path = key_dir / f"{resolved_id}.json"
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
    """The note the grader opens first — how to grade this, and what is missing.

    It states the two things a TA cannot work out from the folder: the exact
    command that opens it, and that the scans were deliberately left behind (so
    a missing whole-page view reads as a decision, not a broken install).
    """
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


# --------------------------------------------------------------------------- #
# Sealing: a packet as one encrypted file
# --------------------------------------------------------------------------- #
#
# Redaction is the packet's ordinary protection — aliases instead of names, the
# scans left behind — and it is enough for the ordinary case. It is not enough
# for the two cases where a packet carries real identity: `--no-redact` (a plain
# working copy for a co-instructor) and `--with-pdfs`. Sealing covers those, and
# covers the redacted case too against the everyday risk that has nothing to do
# with the packet's contents: a folder emailed to the wrong address, or a USB
# stick left in a taxi.
#
# Deliberately a *separate step* from `build_packet` rather than an option on
# it. `core/handoff/calibration.py` builds a packet and then edits the folder
# (blanking the grades), so a build that sometimes returned an archive instead
# of a directory would silently break it — and a calibration packet that could
# not be blanked would anchor every grader to the first one's marks.

# A sealed packet is a zip inside the standard encrypted container.
SEALED_PACKET_SUFFIX = f".zip{ENCRYPTED_SUFFIX}"


def default_sealed_packet_path(packet_path: str | Path) -> Path:
    """Where a sealed packet is written: the folder's own name plus the suffix."""
    folder = Path(packet_path)
    return folder.parent / f"{folder.name}{SEALED_PACKET_SUFFIX}"


def seal_packet(
    packet_path: str | Path,
    passphrase: str,
    *,
    destination: str | Path | None = None,
    remove_source: bool = True,
) -> Path:
    """Pack a packet folder into one encrypted file and return its path.

    ``remove_source`` defaults to True, and that default is the point: leaving
    the plaintext folder beside the sealed file would mean the thing that
    actually gets copied onto a stick is whichever one the user happens to
    grab — and half the time that is the unprotected one.
    """
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
                    # Stored relative to the packet folder, so unsealing
                    # reproduces exactly the layout the TUI expects.
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
    """Unpack a sealed packet into ``destination`` (which must not exist)."""
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
                # Resolve every member against the destination and refuse
                # anything that lands outside it. A zip can name "../../x", and
                # an archive is exactly the kind of file that arrives from
                # somebody else.
                extracted = (target / name).resolve()
                if not str(extracted).startswith(str(target.resolve())):
                    raise PacketError(
                        f"{source} contains a path that escapes the packet: {name}"
                    )
                extracted.parent.mkdir(parents=True, exist_ok=True)
                extracted.write_bytes(archive.read(name))
    return target


def is_sealed_packet(path: str | Path) -> bool:
    """True when this path is a sealed packet file rather than a folder."""
    candidate = Path(path)
    return candidate.is_file() and is_encrypted(candidate)


@contextlib.contextmanager
def opened_packet(path: str | Path, passphrase: str = "") -> Iterator[Path]:
    """Yield a packet *folder*, unsealing a sealed packet into a temp dir first.

    Every reader of a returned packet goes through here, so the merge never has
    to know whether the grader sent back a folder or a sealed file. The
    unsealed copy is removed on the way out — a merge that leaves a decrypted
    packet behind has undone the encryption it just honoured.
    """
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
