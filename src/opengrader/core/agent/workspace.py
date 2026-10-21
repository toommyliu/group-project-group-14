"""The AI assistant on a workspace assignment (`opengrader tui`, the browser app).

A workspace keeps its grades in its database, not in `.submission.md` files,
so an assistant's copy cannot be made from the class's files and merged back
into them. Only the two ends differ from the class-folder workflow; what the
assistant does inside its copy is identical:

* `prepare_workspace_packet` writes each submission as the workspace has it
  now — grades, comments, answer images, version — into a scratch folder,
  builds the ordinary hardened packet from it (`packet.build_agent_packet`:
  the same minimising, scrubbing, crop check and identity check, with the
  whole workspace fenced off), and deletes the scratch folder. What stays in
  the workspace is the handoff key, under `.opengrader/agent/<assignment>/`,
  which is how the copy's `og-…` codes find their submissions again.
* `plan_workspace_apply` is grade-merge's three-way plan with the workspace
  as "ours"; `apply_workspace_plan` sends it to the workspace service, the
  only writer of the database. There the assistant counts as a grader
  account of its own (`agent:<copy>`, `server/workspace/app.save_agent_grades`):
  it fills an answer nobody graded, and a change to a grade anyone else gave
  becomes a change request the teacher approves or dismisses (`Q`), as a
  grader's who joined the workspace does. A reading the assistant typed
  (`agent transcribe`) is not stored: a workspace keeps the scan's reading.
* a fix to the answer key — a suggestion accepted, or a key the assistant
  wrote — is stored through the service's strict key replacement, which
  refuses to drop a rubric option a saved grade uses. Whether the
  assignment's own qa.md file gets it too is the teacher's choice
  (`key_file`, required when there is such a file): write-back carries
  grades to files, never the key.

The service is reached through `request`, a `WorkspaceClient.request`, so
nothing here imports the server. Everything else reads the database
directly, as write-back does.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal, Sequence

from opengrader.core.handoff.merge import MergePlan, plan_merge, read_side, resolve_incoming_root
from opengrader.core.handoff.packet import (
    HandoffKey,
    PacketMember,
    read_handoff_key,
    read_packet_manifest,
    resolve_handoff_dir,
    slugify_packet_name,
)
from opengrader.core.history.restore import AspectValue, CellState, document_student_key
from opengrader.core.qa_file.check import KeyCheck
from opengrader.core.submissions.student_file import render_student_submission_markdown

from .answer_key import KEY_FILENAME, KeyWorkspace, _unique, check_key_file, make_key_folder, text_digest, write_marker
from .common import AGENT_MARKER_FILENAME, KIND_ANSWER_KEY, PURPOSE_GRADING, AgentError, read_marker
from .packet import AgentPacket, build_agent_packet, default_agent_packet_id, delete_packet, list_agent_packets
from .suggestions import describe_accepted, fixed_key, pending_fix, record_accepted

__all__ = [
    "KeyFile",
    "KeyFileChoiceNeeded",
    "WorkspaceAssignment",
    "WorkspaceApplyOutcome",
    "WorkspaceApplyPlan",
    "accept_workspace_suggestion",
    "agent_folder",
    "apply_workspace_plan",
    "assignment_key_file",
    "default_workspace_output_dir",
    "delete_workspace_packet",
    "list_workspace_packets",
    "plan_workspace_apply",
    "prepare_workspace_key_folder",
    "prepare_workspace_packet",
    "read_assignment",
    "require_workspace",
    "resolve_assignment",
    "use_workspace_key",
    "workspace_key_folders",
]

# `WorkspaceClient.request`: (method, path, body=None, *, timeout=…) → bytes,
# raising a RuntimeError that carries the service's reason.
Request = Callable[..., bytes]
# What to do with the assignment's qa.md file when its key changes here.
KeyFile = Literal["update", "leave"]

_AGENT_DIR = Path(".opengrader") / "agent"
_STAGING_PREFIX = ".copying-"
_ACTOR_PREFIX = "agent:"


class KeyFileChoiceNeeded(AgentError):
    """The assignment has a qa.md file, and nobody said whether it follows."""

    def __init__(self, path: Path):
        super().__init__(
            f"this assignment's answer key is also the file {path}. Say whether the "
            "change goes there too (--key-file update) or only into the workspace "
            "(--key-file leave). Writing grades back to files never carries the key."
        )
        self.path = path


# -- which workspace, which assignment --------------------------------------


def require_workspace(root: str | Path) -> Path:
    from opengrader.core.class_folder import is_workspace
    from opengrader.core.workspace.store import DATABASE

    folder = Path(root).expanduser().resolve()
    if not (folder / DATABASE).is_file():
        raise AgentError(f"{folder} is not a workspace (it has no {DATABASE})")
    if not is_workspace(folder):
        raise AgentError(
            f"{folder} is a class folder open in the browser: its grades are its "
            ".submission.md files, so give the assistant the class folder instead "
            "(the AI assistant card in classic mode, or "
            "opengrader agent prepare --submissions …)."
        )
    return folder


def _workspace(root: Path):  # noqa: ANN202 - Workspace, imported late
    from opengrader.core.workspace.store import Workspace

    return Workspace(root)


def _source(workspace, assignment_id: str) -> str:  # noqa: ANN001
    from opengrader.core.workspace.store import WorkspaceNotFound

    try:
        return workspace.assignment_source(assignment_id)
    except WorkspaceNotFound:
        raise AgentError(f"this workspace has no assignment {assignment_id!r}") from None


def resolve_assignment(root: str | Path, wanted: str = "") -> str:
    """The assignment `wanted` names (its id or its title), or the only one."""
    assignments = _workspace(require_workspace(root)).assignments()
    if not assignments:
        raise AgentError("this workspace has no assignment yet")
    if not wanted:
        if len(assignments) == 1:
            return assignments[0].id
        raise AgentError(
            "this workspace has several assignments; say which with --assignment: "
            + ", ".join(repr(item.title) for item in assignments)
        )
    for item in assignments:
        if item.id == wanted:
            return item.id
    titled = [item for item in assignments if item.title.casefold() == wanted.casefold()]
    if len(titled) == 1:
        return titled[0].id
    raise AgentError(
        f"no single assignment is called {wanted!r}; there are: "
        + ", ".join(repr(item.title) for item in assignments)
    )


def assignment_key_file(workspace, assignment_id: str) -> Path | None:  # noqa: ANN001
    """The qa.md the assignment was imported from, while it still exists."""
    from opengrader.core.workspace.context import inputs_for_assignment, resolve_path

    saved = inputs_for_assignment(workspace, assignment_id)
    if saved is None or not saved.qa:
        return None
    path = resolve_path(resolve_path(workspace.root, saved.folder), saved.qa)
    return path if path.is_file() else None


@dataclass(frozen=True)
class WorkspaceAssignment:
    """What one assignment is ready for: which assistant jobs to offer."""

    root: Path
    assignment_id: str
    title: str
    unanswered: int = 0
    rubric_gaps: int = 0
    submission_count: int = 0
    key_file: Path | None = None

    @property
    def needs_key(self) -> bool:
        return self.unanswered > 0

    @property
    def can_grade(self) -> bool:
        return self.submission_count > 0

    @property
    def can_draft_rubrics(self) -> bool:
        return self.can_grade and self.rubric_gaps > 0

    def describe(self) -> str:
        parts = [self.title]
        if self.unanswered:
            parts.append(f"{self.unanswered} question(s) without an answer")
        parts.append(
            f"{self.submission_count} submission(s)" if self.submission_count else "no submissions yet"
        )
        if self.rubric_gaps:
            parts.append(f"{self.rubric_gaps} written question(s) without partial credit")
        return " · ".join(parts)


def read_assignment(root: str | Path, assignment_id: str) -> WorkspaceAssignment:
    from opengrader.core.qa_file.answers import unanswered_count
    from opengrader.core.qa_file.markdown import parse_qa_markdown

    from .packet import rubric_gaps

    folder = require_workspace(root)
    workspace = _workspace(folder)
    source = _source(workspace, assignment_id)
    document = parse_qa_markdown(source)  # a stored key always parses
    total, _ = workspace.submission_counts(assignment_id)
    return WorkspaceAssignment(
        root=folder,
        assignment_id=assignment_id,
        title=document.title or "Untitled assignment",
        unanswered=unanswered_count(source),
        rubric_gaps=len(rubric_gaps(document)),
        submission_count=total,
        key_file=assignment_key_file(workspace, assignment_id),
    )


# -- the copy ------------------------------------------------------------------


def agent_folder(root: str | Path, assignment_id: str) -> Path:
    """Where the assignment's handoff keys live.

    Laid out like a submissions folder's `.opengrader/handoff/`, so the
    class-folder helpers (`list_agent_packets`, `delete_packet`) read it as
    they are.
    """
    return Path(root) / _AGENT_DIR / assignment_id


def default_workspace_output_dir(root: str | Path) -> Path:
    """Beside the workspace, never inside it: `course/` → `course-ai-assistant/`."""
    folder = Path(root).resolve()
    return folder.parent / f"{folder.name}-ai-assistant"


def _stage_submissions(workspace, assignment_id: str, destination: Path) -> int:  # noqa: ANN001
    """Each submission as the workspace has it now, as a `.submission.md`.

    Only what a packet may carry is written: no scan, no name or ID crop, no
    scan warnings, and no roster candidates (they name other students). A
    submission without an identity of its own — none read yet, or the same
    as another's — is given `submission-<id>` so every one gets its own code.
    """
    from opengrader.core.workspace.store import WorkspaceNotFound

    taken: set[str] = set()
    count = 0
    for submission in workspace.submissions(assignment_id):
        document = workspace.document(assignment_id, submission.id)
        folder = destination / str(submission.id)
        folder.mkdir(parents=True)
        questions = []
        for question in document.questions:
            image = None
            if question.response_image_path:
                try:
                    asset = workspace.asset(question.response_image_path)
                except WorkspaceNotFound:
                    asset = None
                if asset is not None:
                    safe = re.sub(r"[^\w.-]", "_", question.question_id)
                    target = folder / "responses" / f"{safe}{asset.suffix}"
                    target.parent.mkdir(exist_ok=True)
                    shutil.copyfile(asset, target)
                    image = str(target)
            questions.append(replace(question, response_image_path=image))
        staged = replace(
            document,
            source_pdf="",
            name_image_path=None,
            id_image_path=None,
            id_candidates=(),
            scan_warnings=(),
            assignment_version=submission.version or document.assignment_version,
            questions=questions,
        )
        key = document_student_key(staged)
        if not key or key in taken:
            staged = replace(staged, student_id=f"submission-{submission.id}")
            key = document_student_key(staged)
        taken.add(key)
        (folder / f"{submission.id}.submission.md").write_text(
            render_student_submission_markdown(staged), encoding="utf-8"
        )
        count += 1
    return count


def prepare_workspace_packet(
    root: str | Path,
    assignment_id: str,
    output_path: str | Path | None = None,
    *,
    packet_id: str = "",
    run_as: str = "",
    questions: Sequence[str] = (),
    include_graded: bool = False,
    check_crops: bool = True,
    purpose: str = PURPOSE_GRADING,
    now: datetime | None = None,
) -> AgentPacket:
    """A redacted copy of one workspace assignment for an assistant.

    The same packet `prepare_agent_packet` makes from a class folder, with
    the same options; the assistant is fenced off the whole workspace.
    """
    folder = require_workspace(root)
    workspace = _workspace(folder)
    source = _source(workspace, assignment_id)
    keys = resolve_handoff_dir(agent_folder(folder, assignment_id))
    stamp = now or datetime.now().astimezone()
    name = slugify_packet_name(packet_id or default_agent_packet_id(purpose, stamp))
    if (keys / f"{name}.json").exists():
        raise AgentError(
            f"a copy named {name} was already made from this assignment — choose another "
            "name (its key is the only way to merge that copy back)"
        )
    scratch_parent = agent_folder(folder, assignment_id)
    scratch_parent.mkdir(parents=True, exist_ok=True)
    # Inside the workspace's own `.opengrader/`, never the system temporary
    # folder: these files carry names, and leave with the `with`.
    with tempfile.TemporaryDirectory(prefix=_STAGING_PREFIX, dir=scratch_parent) as scratch:
        staging = Path(scratch)
        submissions = staging / "submissions"
        if not _stage_submissions(workspace, assignment_id, submissions):
            raise AgentError("this assignment has no submissions yet, so there is nothing to copy")
        qa_path = staging / KEY_FILENAME
        qa_path.write_text(source, encoding="utf-8")
        packet = build_agent_packet(
            submissions,
            qa_path,
            Path(output_path).expanduser() if output_path else default_workspace_output_dir(folder),
            fence=folder,
            packet_id=name,
            run_as=run_as,
            questions=questions,
            include_graded=include_graded,
            check_crops=check_crops,
            purpose=purpose,
            now=stamp,
        )
        keys.mkdir(parents=True, exist_ok=True)
        key_path = keys / packet.key_path.name
        shutil.move(str(packet.key_path), key_path)
    return replace(packet, key_path=key_path)


def list_workspace_packets(root: str | Path, assignment_id: str) -> list[Path]:
    """The copies made from this assignment that are still on disk, newest first."""
    return list_agent_packets(agent_folder(Path(root).resolve(), assignment_id))


def delete_workspace_packet(packet: str | Path, root: str | Path, assignment_id: str) -> None:
    """Remove a copy once its work is merged back; only one made from this assignment."""
    delete_packet(packet, agent_folder(Path(root).resolve(), assignment_id))


def _key_for(folder: Path, assignment_id: str, packet: Path) -> HandoffKey:
    manifest = read_packet_manifest(packet)
    if manifest is None:
        raise AgentError(f"{packet} is not an assistant's copy (it has no manifest)")
    path = resolve_handoff_dir(agent_folder(folder, assignment_id)) / f"{manifest.packet_id}.json"
    if not path.is_file():
        raise AgentError(
            f"{packet} was not made from this assignment: its key ({path.name}) is not "
            "in the workspace, so its codes cannot be matched to submissions"
        )
    return read_handoff_key(path)


def _submission_id(member: PacketMember) -> int | None:
    # Staged as `<submission id>/<submission id>.submission.md`.
    head = member.source_relative_path.split("/", 1)[0]
    return int(head) if head.isdigit() else None


# -- merging the copy back ---------------------------------------------------


@dataclass(frozen=True)
class WorkspaceApplyPlan:
    root: Path
    assignment_id: str
    packet: Path
    packet_id: str
    plan: MergePlan
    grades: tuple[object, ...]  # AgentGrade: what is sent to the service
    labels: Mapping[tuple[int, str], str]
    # Graded meanwhile by someone else: what the service will turn into a
    # change request rather than a grade.
    requests: int = 0
    # Readings the assistant typed, which a workspace does not keep.
    readings: int = 0

    @property
    def actor(self) -> str:
        return _ACTOR_PREFIX + self.packet_id

    def lines(self) -> list[str]:
        if not self.grades:
            lines = ["Nothing to merge: the copy has no grade or comment the workspace lacks."]
        else:
            filled = len(self.grades) - self.requests
            lines = [f"{filled} answer(s) graded by the assistant will be saved as AI grades."]
            if self.requests:
                lines.append(
                    f"{self.requests} answer(s) were graded here meanwhile: the assistant's grade "
                    "becomes a change request for you to approve or dismiss (Q while grading)."
                )
            for grade in self.grades:
                where = (grade.submission_id, grade.question_id)  # type: ignore[attr-defined]
                labels = ", ".join(grade.rubric_labels) or "manual score"  # type: ignore[attr-defined]
                lines.append(f"  {self.labels.get(where, where[0])} · {where[1]}: {labels}")
        if self.readings:
            lines.append(
                f"{self.readings} reading(s) the assistant typed are not kept: a workspace keeps "
                "the scan's reading. Its grades are kept."
            )
        if self.plan.unmatched_students:
            lines.append(f"{len(self.plan.unmatched_students)} code(s) in the copy match no submission.")
        if self.plan.unknown_questions:
            lines.append(f"{len(self.plan.unknown_questions)} answer(s) in the copy are no longer in the workspace.")
        return lines


def _ours(workspace, assignment_id: str, submission_ids: Sequence[int]) -> dict[tuple[str, str], CellState]:  # noqa: ANN001
    from opengrader.core.workspace.store import WorkspaceNotFound

    cells: dict[tuple[str, str], CellState] = {}
    for submission_id in submission_ids:
        try:
            answers = workspace.answers(assignment_id, submission_id)
        except WorkspaceNotFound:
            continue
        for answer in answers:
            cells[(str(submission_id), answer.question.id)] = CellState(
                values={
                    "grade": AspectValue(points=answer.points_awarded, rubric=tuple(answer.rubric_labels)),
                    "comment": AspectValue(text=answer.analysis or ""),
                }
            )
    return cells


def plan_workspace_apply(root: str | Path, assignment_id: str, packet: str | Path) -> WorkspaceApplyPlan:
    """What merging this copy back would do; nothing is written."""
    from opengrader.core.workspace.models import AgentGrade

    folder = require_workspace(root)
    workspace = _workspace(folder)
    _source(workspace, assignment_id)
    copy = Path(packet).expanduser().resolve()
    key = _key_for(folder, assignment_id, copy)
    manifest = read_packet_manifest(copy)
    by_alias = {
        member.alias.casefold(): sid
        for member in key.members
        if (sid := _submission_id(member)) is not None
    }
    translate = {alias: str(sid) for alias, sid in by_alias.items()}
    theirs = read_side(resolve_incoming_root(copy), translate=translate)
    ours = _ours(workspace, assignment_id, sorted(set(by_alias.values())))
    base: dict[tuple[str, str], CellState] = {}
    for (alias, question_id), cell in (manifest.baseline_cells() if manifest else {}).items():
        sid = by_alias.get(alias.casefold())
        if sid is not None:
            base[(str(sid), question_id)] = cell

    names = {}
    for submission in workspace.submissions(assignment_id):
        names[str(submission.id)] = submission.student_name or submission.student_id or f"submission {submission.id}"
    plan = plan_merge(
        ours,
        theirs.cells,
        base,
        labels=names,
        aspects=("grade", "comment"),
        unmatched_students=theirs.untranslated,
    )

    incoming: dict[tuple[str, str], dict[str, AspectValue]] = {}
    contested: set[tuple[str, str]] = set()
    for item in (*plan.changes, *plan.conflicts):
        incoming.setdefault((item.student_key, item.question_id), {})[item.aspect] = item.theirs
    for conflict in plan.conflicts:
        contested.add((conflict.student_key, conflict.question_id))
    grades = []
    labels: dict[tuple[int, str], str] = {}
    for (key_, question_id), values in sorted(incoming.items(), key=lambda item: (int(item[0][0]), item[0][1])):
        mine = ours[(key_, question_id)]
        grade = values.get("grade") or mine.value("grade") or AspectValue()
        comment = values.get("comment") or mine.value("comment") or AspectValue()
        manual = not grade.rubric and grade.points is not None
        sid = int(key_)
        grades.append(
            AgentGrade(
                submission_id=sid,
                question_id=question_id,
                rubric_labels=list(grade.rubric),
                score_mode="manual" if manual else "rubric",
                points_awarded=grade.points if manual else None,
                analysis=comment.text,
            )
        )
        labels[(sid, question_id)] = names.get(key_, key_)

    readings = 0
    for cell_key, cell in theirs.cells.items():
        typed = cell.value("transcription")
        start = base.get(cell_key)
        before = start.value("transcription") if start is not None else None
        if typed is not None and before is not None and not typed.matches(before, "transcription"):
            readings += 1

    return WorkspaceApplyPlan(
        root=folder,
        assignment_id=assignment_id,
        packet=copy,
        packet_id=slugify_packet_name(key.packet_id),
        plan=plan,
        grades=tuple(grades),
        labels=labels,
        requests=len(contested),
        readings=readings,
    )


@dataclass(frozen=True)
class WorkspaceApplyOutcome:
    outcomes: tuple[object, ...] = ()  # AgentGradeOutcome
    labels: Mapping[tuple[int, str], str] | None = None

    def count(self, outcome: str) -> int:
        return sum(1 for item in self.outcomes if item.outcome == outcome)  # type: ignore[attr-defined]

    def lines(self) -> list[str]:
        lines = [f"Saved {self.count('saved')} AI grade(s)."]
        if self.count("requested"):
            lines.append(
                f"{self.count('requested')} became change requests: someone had graded those "
                "answers. Approve or dismiss them with Q while grading."
            )
        if self.count("changed"):
            lines.append(f"{self.count('changed')} changed while merging: merge again to retry them.")
        labels = self.labels or {}
        for item in self.outcomes:
            if item.outcome in ("refused", "missing"):  # type: ignore[attr-defined]
                where = (item.submission_id, item.question_id)  # type: ignore[attr-defined]
                lines.append(f"  not saved, {labels.get(where, where[0])} · {where[1]}: {item.message}")  # type: ignore[attr-defined]
        return lines


def apply_workspace_plan(plan: WorkspaceApplyPlan, request: Request) -> WorkspaceApplyOutcome:
    """Send the plan to the workspace service (owner only).

    Each grade is decided there at the moment it is written, so an answer
    someone graded after the preview still becomes a request, not a grade.
    """
    from opengrader.core.workspace.models import AgentGrades, AgentGradesResult

    if not plan.grades:
        return WorkspaceApplyOutcome(labels=plan.labels)
    body = AgentGrades(actor=plan.actor, grades=list(plan.grades))  # type: ignore[arg-type]
    try:
        raw = request("POST", f"/api/assignments/{plan.assignment_id}/agent-grades", body, timeout=60)
    except RuntimeError as exc:
        raise AgentError(f"nothing was merged: {exc}") from None
    result = AgentGradesResult.model_validate_json(raw)
    return WorkspaceApplyOutcome(outcomes=tuple(result.outcomes), labels=plan.labels)


# -- the answer key ------------------------------------------------------------


def _store_key(request: Request, assignment_id: str, markdown: str, expected: str) -> None:
    from opengrader.core.workspace.models import AnswerKeyReplace

    try:
        request(
            "PUT",
            f"/api/assignments/{assignment_id}/answer-key",
            AnswerKeyReplace(qa_markdown=markdown, expected_qa_markdown=expected),
            timeout=60,
        )
    except RuntimeError as exc:
        raise AgentError(f"the workspace's answer key was not changed: {exc}") from None


def _labels_in_use(workspace, assignment_id: str, question: str) -> set[str]:  # noqa: ANN001
    import json

    used: set[str] = set()
    with workspace.connect() as db:
        for (value,) in db.execute(
            "SELECT value FROM answers WHERE assignment_id=? AND question_id=?",
            (assignment_id, question),
        ):
            answer = json.loads(value)
            if answer.get("points_awarded") is not None:
                used.update(answer.get("rubric_labels") or ())
    return used


def _follow_in_file(
    path: Path, before: str, after: str, change: Callable[[str], str] | None
) -> str:
    """Bring the qa.md file along, as the teacher chose; never by guessing.

    A file that still holds the workspace's key takes the new one whole.
    One edited since the import takes only the same one-question fix, when
    there is one; otherwise it is left alone and the reason said.
    """
    text = path.read_text(encoding="utf-8")
    if text == before:
        path.write_text(after, encoding="utf-8")
        return f"{path.name} was updated too."
    if change is None:
        return (
            f"{path.name} was left as it was: it differs from the workspace's key, so "
            "writing the new key over it would lose what is only in the file."
        )
    try:
        path.write_text(change(text), encoding="utf-8")
    except (AgentError, ValueError) as exc:
        return f"{path.name} was left as it was: {exc}"
    return f"{path.name} was updated too (only that question)."


def accept_workspace_suggestion(
    packet: str | Path,
    suggestion_id: str,
    *,
    root: str | Path,
    assignment_id: str,
    request: Request,
    key_file: KeyFile | None = None,
) -> str:
    """Store a suggested fix in the workspace's answer key (teacher only)."""
    suggestion = pending_fix(packet, suggestion_id)
    folder = require_workspace(root)
    workspace = _workspace(folder)
    current = _source(workspace, assignment_id)
    path = assignment_key_file(workspace, assignment_id)
    if path is not None and key_file is None:
        raise KeyFileChoiceNeeded(path)
    used = set() if suggestion.correct else _labels_in_use(workspace, assignment_id, suggestion.question)
    markdown = fixed_key(current, suggestion, labels_in_use=used)
    _store_key(request, assignment_id, markdown, current)
    record_accepted(packet, suggestion, markdown)
    done = describe_accepted(suggestion)
    if path is not None and key_file == "update":
        done += ". " + _follow_in_file(
            path, current, markdown, lambda text: fixed_key(text, suggestion, labels_in_use=set())
        )
    return done


def workspace_key_folder_name(root: Path, title: str) -> str:
    return f"{root.name}-{slugify_packet_name(title, fallback='assignment')}-ai-answer-key"


def prepare_workspace_key_folder(
    root: str | Path,
    assignment_id: str,
    *,
    source: str | Path | None = None,
    output: str | Path | None = None,
    run_as: str = "",
) -> KeyWorkspace:
    """A folder where the assistant fills or corrects this assignment's key.

    Beside the workspace, with its key as it is now and the teacher's own
    document if given — no student work. The key is refused if it names a
    student of the assignment.
    """
    folder = require_workspace(root)
    workspace = _workspace(folder)
    current = _source(workspace, assignment_id)
    document = Path(source).expanduser().resolve() if source else None
    if document is not None and not document.is_file():
        raise AgentError(f"no answer-key document at {document}")
    title = read_assignment(folder, assignment_id).title
    parent = Path(output).expanduser().resolve() if output else folder.parent
    target = _unique(parent, workspace_key_folder_name(folder, title))
    identities = [
        (" ".join(item.student_name.split()), item.student_id.strip())
        for item in workspace.submissions(assignment_id)
    ]
    return make_key_folder(
        target,
        key=current.encode("utf-8"),
        document=document,
        identities=identities,
        marker={
            "workspace": str(folder),
            "assignment_id": assignment_id,
            "base_hash": text_digest(current.encode("utf-8")),
        },
        run_as=run_as,
    )


def workspace_key_folders(root: str | Path, assignment_id: str) -> list[Path]:
    """Answer-key folders made for this assignment earlier, newest first."""
    folder = Path(root).expanduser().resolve()
    found = []
    for candidate in folder.parent.glob(f"{folder.name}-*-ai-answer-key*"):
        try:
            if not (candidate / ".opengrader" / AGENT_MARKER_FILENAME).is_file():
                continue
            marker = read_marker(candidate)
        except (OSError, ValueError):
            continue
        if (
            marker.get("kind") == KIND_ANSWER_KEY
            and marker.get("workspace") == str(folder)
            and marker.get("assignment_id") == assignment_id
        ):
            found.append(candidate)
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


def use_workspace_key(
    key_folder: str | Path,
    *,
    root: str | Path,
    assignment_id: str,
    request: Request,
    replace_changed: bool = False,
    key_file: KeyFile | None = None,
) -> tuple[KeyCheck, str]:
    """Store the assistant's finished key as the assignment's (teacher only).

    Without `replace_changed` only over the key the folder was made from.
    Once there are submissions the questions must stay the same, and a
    rubric option a saved grade uses must stay (the service refuses
    otherwise).
    """
    folder = Path(key_folder).expanduser().resolve()
    key = folder / KEY_FILENAME
    if not key.is_file():
        raise AgentError(f"{key} does not exist yet: the assistant has not written the key")
    result = check_key_file(key)
    if not result.ok:
        raise AgentError("the key does not load yet:\n" + "\n".join(result.errors))
    marker = dict(read_marker(folder))
    workspace_folder = require_workspace(root)
    if marker.get("workspace") != str(workspace_folder) or marker.get("assignment_id") != assignment_id:
        raise AgentError(f"{folder} was not made for this assignment")
    workspace = _workspace(workspace_folder)
    current = _source(workspace, assignment_id)
    if marker.get("base_hash") != text_digest(current.encode("utf-8")) and not replace_changed:
        raise AgentError(
            "the assignment's answer key changed since this folder was made; replace it on purpose"
        )
    path = assignment_key_file(workspace, assignment_id)
    if path is not None and key_file is None:
        raise KeyFileChoiceNeeded(path)
    markdown = key.read_text(encoding="utf-8")
    note = ""
    if markdown != current:
        _store_key(request, assignment_id, markdown, current)
        note = "Stored as the assignment's answer key."
        if path is not None and key_file == "update":
            note += " " + _follow_in_file(path, current, markdown, None)
    else:
        note = "The assignment already has this key."
    marker["base_hash"] = text_digest(markdown.encode("utf-8"))
    write_marker(folder, marker)
    return result, note
