from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.history.journal import current_actor
from opengrader.core.history.restore import document_student_key
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_markdown,
)

from .merge import alias_translation
from .packet import (
    PACKET_SUBMISSIONS_DIRNAME,
    HandoffKey,
    PacketError,
    PacketResult,
    build_packet,
    read_handoff_key,
    resolve_handoff_dir,
    slugify_packet_name,
)

CALIBRATION_SUBDIR = "calibration"

DEFAULT_SAMPLE_SIZE = 8


class CalibrationError(ValueError):
    pass


@dataclass(frozen=True)
class CalibrationPacket:
    grader: str
    packet_id: str
    packet_path: str


@dataclass(frozen=True)
class CalibrationSet:
    set_id: str
    created_at: str
    created_by: str
    assignment_title: str
    sample_seed: int
    student_keys: tuple[str, ...]
    packets: tuple[CalibrationPacket, ...] = ()

    @property
    def graders(self) -> tuple[str, ...]:
        return tuple(packet.grader for packet in self.packets)

    @property
    def sample_size(self) -> int:
        return len(self.student_keys)


@dataclass(frozen=True)
class CalibrationResult:
    calibration_set: CalibrationSet
    record_path: Path
    packets: tuple[PacketResult, ...]


def resolve_calibration_dir(submissions_path: str | Path) -> Path:
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / ".opengrader" / CALIBRATION_SUBDIR


def render_calibration_set(calibration_set: CalibrationSet) -> str:
    payload = {
        "format": 1,
        "set_id": calibration_set.set_id,
        "created_at": calibration_set.created_at,
        "created_by": calibration_set.created_by,
        "assignment_title": calibration_set.assignment_title,
        "sample_seed": calibration_set.sample_seed,
        "student_keys": list(calibration_set.student_keys),
        "packets": [
            {
                "grader": packet.grader,
                "packet_id": packet.packet_id,
                "packet_path": packet.packet_path,
            }
            for packet in calibration_set.packets
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def parse_calibration_set(text: str) -> CalibrationSet:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CalibrationError(f"calibration record is not valid JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise CalibrationError("calibration record must be a JSON object")

    packets = []
    for entry in payload.get("packets") or []:
        if not isinstance(entry, Mapping):
            continue
        packets.append(
            CalibrationPacket(
                grader=str(entry.get("grader", "")),
                packet_id=str(entry.get("packet_id", "")),
                packet_path=str(entry.get("packet_path", "")),
            )
        )

    return CalibrationSet(
        set_id=str(payload.get("set_id", "")),
        created_at=str(payload.get("created_at", "")),
        created_by=str(payload.get("created_by", "")),
        assignment_title=str(payload.get("assignment_title", "")),
        sample_seed=int(payload.get("sample_seed", 0) or 0),
        student_keys=tuple(str(key) for key in payload.get("student_keys") or []),
        packets=tuple(packets),
    )


def read_calibration_set(path: str | Path) -> CalibrationSet:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CalibrationError(f"cannot read calibration record {path}: {exc}") from exc
    return parse_calibration_set(text)


def list_calibration_sets(submissions_path: str | Path) -> list[CalibrationSet]:
    directory = resolve_calibration_dir(submissions_path)
    if not directory.is_dir():
        return []
    sets: list[CalibrationSet] = []
    for path in sorted(directory.glob("*.json")):
        try:
            sets.append(read_calibration_set(path))
        except CalibrationError:
            continue
    sets.sort(key=lambda record: (record.created_at, record.set_id), reverse=True)
    return sets


def find_calibration_set(
    submissions_path: str | Path, set_id: str = ""
) -> CalibrationSet | None:
    sets = list_calibration_sets(submissions_path)
    if not set_id:
        return sets[0] if sets else None
    for record in sets:
        if record.set_id == set_id:
            return record
    return None


def sample_student_keys(
    keys: Iterable[str],
    *,
    size: int,
    seed: int,
) -> tuple[str, ...]:
    pool = sorted({key for key in keys if key})
    if not pool:
        raise CalibrationError("no identifiable students to sample from")
    if size <= 0:
        raise CalibrationError("calibration sample size must be at least 1")
    if size >= len(pool):
        return tuple(pool)
    return tuple(sorted(random.Random(seed).sample(pool, size)))


def _blank_grades(text: str, document: StudentSubmissionDocument) -> str:
    updated = text
    for question in document.questions:
        updated = update_student_submission_markdown(
            updated,
            StudentSubmissionGradeUpdate(
                question_id=question.question_id,
                points_awarded=None,
                rubric_labels=(),
                analysis="",
            ),
        )
    return updated


def _blank_packet_grades(packet_path: Path) -> int:
    root = packet_path / PACKET_SUBMISSIONS_DIRNAME
    cleared = 0
    for path in discover_submission_paths(root):
        document = load_student_submission_markdown(path)
        graded = [q for q in document.questions if q.points_awarded is not None]
        text = _blank_grades(path.read_text(encoding="utf-8"), document)
        path.write_text(text, encoding="utf-8")
        cleared += len(graded)
    return cleared


def build_calibration_set(
    submissions_path: str | Path,
    graders: Iterable[str],
    output_path: str | Path,
    *,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    qa_file_path: str | Path | None = None,
    set_id: str = "",
    seed: int | None = None,
    actor: str = "",
    now: datetime | None = None,
) -> CalibrationResult:
    names = [name.strip() for name in graders if name and name.strip()]
    if len(names) < 2:
        raise CalibrationError(
            "a calibration set needs at least two graders — agreement is a "
            "comparison, and one grader has nobody to be compared with"
        )
    if len(set(names)) != len(names):
        raise CalibrationError(f"duplicate grader name in: {', '.join(names)}")

    source = Path(submissions_path)
    submission_paths = discover_submission_paths(source)
    if not submission_paths:
        raise CalibrationError(f"no .submission.md files found under {source}")

    documents = [load_student_submission_markdown(path) for path in submission_paths]
    assignment_title = next(
        (doc.assignment_title.strip() for doc in documents if doc.assignment_title.strip()),
        "",
    )

    stamp = now or datetime.now(timezone.utc)
    resolved_seed = seed if seed is not None else int(stamp.timestamp())
    sample = sample_student_keys(
        (document_student_key(document) for document in documents),
        size=sample_size,
        seed=resolved_seed,
    )

    resolved_set_id = slugify_packet_name(
        set_id or f"calibration-{stamp.strftime('%Y%m%d')}", fallback="calibration"
    )
    record_path = resolve_calibration_dir(source) / f"{resolved_set_id}.json"
    if record_path.exists():
        raise CalibrationError(
            f"a calibration set named {resolved_set_id} already exists "
            f"({record_path}) — choose another --name, or delete it first"
        )

    packets: list[PacketResult] = []
    entries: list[CalibrationPacket] = []
    for name in names:
        packet_id = slugify_packet_name(f"{resolved_set_id}-{name}", fallback="packet")
        try:
            result = build_packet(
                source,
                output_path,
                packet_id=packet_id,
                grader=name,
                qa_file_path=qa_file_path,
                redact=True,
                actor=actor or current_actor(),
                now=stamp,
                student_keys=sample,
            )
        except PacketError as exc:
            raise CalibrationError(f"could not cut {name}'s packet: {exc}") from exc
        _blank_packet_grades(result.packet_path)
        packets.append(result)
        entries.append(
            CalibrationPacket(
                grader=name,
                packet_id=result.manifest.packet_id,
                packet_path=str(result.packet_path.resolve()),
            )
        )

    calibration_set = CalibrationSet(
        set_id=resolved_set_id,
        created_at=stamp.isoformat(),
        created_by=actor or current_actor(),
        assignment_title=assignment_title,
        sample_seed=resolved_seed,
        student_keys=sample,
        packets=tuple(entries),
    )
    record_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.write_text(render_calibration_set(calibration_set), encoding="utf-8")

    return CalibrationResult(
        calibration_set=calibration_set,
        record_path=record_path,
        packets=tuple(packets),
    )


@dataclass(frozen=True)
class CollectedGrading:
    grader: str
    packet_id: str
    documents: Mapping[str, StudentSubmissionDocument]
    graded_answers: int
    untranslated: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalibrationCollection:
    calibration_set: CalibrationSet
    gradings: tuple[CollectedGrading, ...]
    missing: tuple[tuple[str, str], ...] = ()

    @property
    def returned(self) -> int:
        return len(self.gradings)


def _read_returned_packet(
    packet_path: Path,
    handoff_key: HandoffKey,
) -> tuple[dict[str, StudentSubmissionDocument], list[str], int]:
    translate = alias_translation(handoff_key)
    root = packet_path / PACKET_SUBMISSIONS_DIRNAME
    if not root.is_dir():
        root = packet_path

    documents: dict[str, StudentSubmissionDocument] = {}
    untranslated: list[str] = []
    graded = 0
    for path in discover_submission_paths(root):
        try:
            document = load_student_submission_markdown(path)
        except (OSError, ValueError):
            untranslated.append(path.name)
            continue
        alias = document_student_key(document)
        key = translate.get(alias.casefold(), "")
        if not key:
            untranslated.append(alias or path.name)
            continue
        documents[key] = document
        graded += sum(1 for q in document.questions if q.points_awarded is not None)
    return documents, untranslated, graded


def collect_calibration_gradings(
    submissions_path: str | Path,
    calibration_set: CalibrationSet,
    *,
    packet_paths: Mapping[str, str | Path] | None = None,
) -> CalibrationCollection:
    overrides = {
        grader: Path(path) for grader, path in (packet_paths or {}).items()
    }
    handoff_dir = resolve_handoff_dir(submissions_path)

    gradings: list[CollectedGrading] = []
    missing: list[tuple[str, str]] = []

    for entry in calibration_set.packets:
        packet_path = overrides.get(entry.grader, Path(entry.packet_path))
        if not packet_path.exists():
            missing.append((entry.grader, f"packet not found at {packet_path}"))
            continue

        key_path = handoff_dir / f"{entry.packet_id}.json"
        if not key_path.exists():
            missing.append((entry.grader, f"handoff key not found at {key_path}"))
            continue
        try:
            handoff_key = read_handoff_key(key_path)
        except (OSError, ValueError) as exc:
            missing.append((entry.grader, f"handoff key unreadable: {exc}"))
            continue

        documents, untranslated, graded = _read_returned_packet(packet_path, handoff_key)
        if graded == 0:
            missing.append((entry.grader, "packet has no graded answers yet"))
            continue

        gradings.append(
            CollectedGrading(
                grader=entry.grader,
                packet_id=entry.packet_id,
                documents=documents,
                graded_answers=graded,
                untranslated=tuple(untranslated),
            )
        )

    return CalibrationCollection(
        calibration_set=calibration_set,
        gradings=tuple(gradings),
        missing=tuple(missing),
    )
