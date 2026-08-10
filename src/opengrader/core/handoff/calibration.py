"""Calibration (norming) sets — every grader marks the same sample, blind.

A TA team's fairness problem has two halves and Opengrader only had the second
one. :mod:`~opengrader.core.submissions.consistency` catches unequal grading
*after* it has happened, one answer at a time, and the audit journal records who
did it. Nothing existed for the half instructors do first: sit the team down
before the pile is divided, have everyone grade the same handful of answers, and
find out where the rubric means different things to different people. That is a
*norming session*, and it is the cheapest fairness intervention there is —
disagreement found here costs a five-minute conversation, and the same
disagreement found in the consistency audit costs a regrade of every paper the
harsh grader touched.

A calibration set is deliberately built out of the machinery that already
exists rather than a parallel one:

* Each grader's sample is an ordinary **redacted packet** (:mod:`packet`), so a
  TA opens it with the same ``uv run tui`` command as any handoff and needs to
  learn nothing new.
* Every packet holds the **same students**, which is the whole point:
  agreement can only be measured on identical work
  (:data:`~opengrader.core.analytics.grader_agreement.OverlapBasis` ``same_work``).
* Comparing the returned copies is the same computation as auditing leniency in
  production grading, so it is the same engine — see
  :mod:`opengrader.core.analytics.grader_agreement`.

Three decisions carry the value:

- **Each grader gets their own aliases.** ``mint_alias`` is stable over
  *(packet id, student key)*, and each grader's packet has its own id, so the
  same student is ``og-1a2b3c4d`` in one packet and something else in the next.
  Two TAs comparing notes over a shared alias is exactly the anchoring a norming
  session exists to avoid.

- **The sample is packed ungraded, always.** A packet cut from partly-graded
  work would show the grader someone else's marks, and the first number a person
  sees is the number they agree with. Grades, rubric selections and comments are
  cleared from the copy — the *copy* only; nothing in the teacher's folder is
  touched. This also means a calibration packet is deliberately **not
  mergeable**: its baseline no longer describes its contents, and merging back a
  set whose entire purpose is disagreement would overwrite the real grading with
  whichever TA happened to be applied last.

- **The sample is random, and the seed is recorded.** Nothing is graded yet, so
  there is no difficulty signal to sample along; a uniform draw is the only
  unbiased choice. Recording the seed keeps it reproducible — a set can be
  re-cut for a grader who lost their packet and they will get the same students.
"""

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

# Where a calibration set's record lives — beside the handoff keys it indexes,
# under the same dot-dir every other piece of assignment metadata uses, so it
# travels with the folder and stays out of submission discovery.
CALIBRATION_SUBDIR = "calibration"

# How many students a set samples when the caller does not say. Small on
# purpose: a norming session is a meeting, and eight answers per grader is about
# twenty minutes of marking followed by a conversation. Larger samples measure
# more precisely and get skipped.
DEFAULT_SAMPLE_SIZE = 8


class CalibrationError(ValueError):
    """A calibration set could not be built or read."""


@dataclass(frozen=True)
class CalibrationPacket:
    """One grader's copy of the sample."""

    grader: str
    packet_id: str
    packet_path: str


@dataclass(frozen=True)
class CalibrationSet:
    """The record of one norming session, kept on the teacher's side.

    Holds only what is needed to collect the packets back in: who got one, which
    students were sampled, and the seed that chose them. The alias maps live
    where every packet's does — in the handoff keys — rather than being copied
    here, so there is one place a redaction can be undone.
    """

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
    """What :func:`build_calibration_set` wrote, for the CLI/TUI to report."""

    calibration_set: CalibrationSet
    record_path: Path
    packets: tuple[PacketResult, ...]


def resolve_calibration_dir(submissions_path: str | Path) -> Path:
    """The ``.opengrader/calibration`` dir for a submissions file or dir."""
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / ".opengrader" / CALIBRATION_SUBDIR


def render_calibration_set(calibration_set: CalibrationSet) -> str:
    """Serialize a set record. Indented and key-sorted so a diff is readable."""
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
    """Read a set record back, raising with context on anything malformed."""
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
    """Every calibration set recorded for a cohort, newest first.

    An unreadable record is skipped rather than raised: one damaged file must
    not hide the other sessions, exactly as the journal treats a bad line.
    """
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
    """Look up one set by id, or the most recent when no id is given."""
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
    """Draw ``size`` student keys uniformly, reproducibly from ``seed``.

    Sorted before sampling so the draw depends on the seed alone and not on the
    order the filesystem happened to list the submissions in — that is what
    makes "re-cut this set" give the same students back. A ``size`` at or above
    the cohort takes everyone rather than raising: a class of five is a small
    class, not a mistake.
    """
    pool = sorted({key for key in keys if key})
    if not pool:
        raise CalibrationError("no identifiable students to sample from")
    if size <= 0:
        raise CalibrationError("calibration sample size must be at least 1")
    if size >= len(pool):
        return tuple(pool)
    return tuple(sorted(random.Random(seed).sample(pool, size)))


def _blank_grades(text: str, document: StudentSubmissionDocument) -> str:
    """Clear every grade, rubric selection and comment from a copy's markdown.

    Passing ``rubric_labels=()`` alongside ``points_awarded=None`` is what makes
    the writer emit a *blank* Points line: ``None`` on its own reads as "leave
    unchanged" for every field, so the rubric is the field that carries the
    intent — the same rule ``history/restore`` relies on to restore an answer to
    ungraded.
    """
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
    """Blank the grades in a freshly-built packet. Returns answers cleared."""
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
    """Cut one blind, identical packet per grader and record the session.

    Every packet holds the same sampled students, ungraded, under per-packet
    aliases. The teacher-side handoff keys are written by
    :func:`~opengrader.core.handoff.packet.build_packet` as usual — they are what
    :func:`collect_calibration_gradings` reads the returned copies back with.

    Raises :class:`CalibrationError` rather than producing a half-built session
    (no graders, an unusable sample, a name already taken); a partially-cut set
    would be worse than none, because the missing grader's absence is invisible
    until the report is short one column.
    """
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
    """One grader's returned copy, re-keyed into the teacher's vocabulary."""

    grader: str
    packet_id: str
    documents: Mapping[str, StudentSubmissionDocument]
    graded_answers: int
    # Submissions in the copy whose alias is not in the handoff key. Reported
    # rather than dropped silently: it means part of that grader's work cannot
    # be attributed, which changes how the report should be read.
    untranslated: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalibrationCollection:
    """Every returned packet that could be read, and every one that could not."""

    calibration_set: CalibrationSet
    gradings: tuple[CollectedGrading, ...]
    # (grader, why) for packets that are missing, unreadable, or unstarted.
    missing: tuple[tuple[str, str], ...] = ()

    @property
    def returned(self) -> int:
        return len(self.gradings)


def _read_returned_packet(
    packet_path: Path,
    handoff_key: HandoffKey,
) -> tuple[dict[str, StudentSubmissionDocument], list[str], int]:
    """Load one returned packet, mapping each alias back to our student key."""
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
    """Read every grader's returned packet back into the teacher's vocabulary.

    Packets are found where the set recorded them, unless ``packet_paths``
    overrides a grader's location (a TA who handed the folder back somewhere
    else). Each copy is de-aliased with its own handoff key, so what comes out
    is keyed by real student — which is what makes two graders' marks on the
    same answer comparable.

    A packet that is missing, unreadable, or simply not graded yet becomes a
    ``missing`` entry with the reason. Nothing raises: a norming session where
    one TA has not finished should still report on the ones who have.
    """
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
            # Without the key every alias fails to match, and the merge module's
            # rule applies here too: report it loudly rather than returning an
            # empty grading that reads as "this TA did nothing".
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
