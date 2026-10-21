"""Making an assistant packet: a grader packet hardened so no name or ID is in it."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Sequence

from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.handoff.packet import (
    PACKET_SUBMISSIONS_DIRNAME,
    HandoffKey,
    PacketManifest,
    build_packet,
    list_handoff_keys,
    render_packet_manifest,
    resolve_manifest_path,
)
from opengrader.core.history.restore import CellState
from opengrader.core.qa_file import load_qa_markdown
from opengrader.core.submissions.student_file import (
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_markdown,
)

from .common import (
    AGENT_FORMAT,
    AGENT_MARKER_FILENAME,
    CLAUDE_FILENAME,
    INSTRUCTIONS_FILENAME,
    KIND_GRADING,
    PURPOSE_GRADING,
    PURPOSE_RUBRICS,
    AgentError,
    _ID_REMOVED,
    _MIN_ID_DIGITS,
    _MIN_NAME_PART,
    _NAME_REMOVED,
    guide_text,
    refuse_workspace,
    self_command,
)
from .assistant_settings import write_assistant_settings
from .crop_check import require_tesseract, scan_crops
from .tasks import packet_status, write_withheld

@dataclass(frozen=True)
class AgentPacket:
    path: Path
    key_path: Path
    students: int
    tasks: int
    scrubbed: int
    unknown_version: int
    left_out: int = 0
    images_withheld: int = 0
    images_checked: int = 0


def default_agent_output_dir(submissions_path: str | Path) -> Path:
    """Beside the class folder, never inside it.

    Inside, an assistant started in the packet is one `..` away from the
    real files, and the class folder's own tools would find the packet's
    submissions as more students. `class/submissions` → `class-ai-assistant/`.
    """
    base = Path(submissions_path).resolve()
    if base.is_file():
        base = base.parent
    class_folder = base.parent
    return class_folder.parent / f"{class_folder.name}-ai-assistant"


def default_agent_packet_id(purpose: str, stamp: datetime) -> str:
    return f"ai-{'rubrics' if purpose == PURPOSE_RUBRICS else 'assistant'}-{stamp:%Y-%m-%d-%H%M}"


def prepare_agent_packet(
    submissions_path: str | Path,
    qa_file_path: str | Path,
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
    """A packet for an assistant, holding as little as the work needs.

    Only answers still to grade go in (`include_graded` adds the teacher's
    graded ones as examples; `questions` narrows it further), each answer
    image is checked for the student's name or ID with Tesseract
    (`crop_check`), and the folder carries a Claude Code settings file that
    fences the assistant in (`assistant_settings`).

    `purpose="rubrics"` makes a copy for drafting partial credit instead:
    only the questions whose rubric describes no partial-credit mistakes yet
    (`rubric_gaps`), with every answer to them, graded or not — what students
    actually wrote is what a rubric is drafted from — and no grading.
    """
    submissions = Path(submissions_path)
    refuse_workspace(submissions)
    return build_agent_packet(
        submissions,
        qa_file_path,
        Path(output_path) if output_path else default_agent_output_dir(submissions),
        fence=submissions.resolve().parent,
        packet_id=packet_id,
        run_as=run_as,
        questions=questions,
        include_graded=include_graded,
        check_crops=check_crops,
        purpose=purpose,
        now=now,
    )


def build_agent_packet(
    submissions: Path,
    qa_file_path: str | Path,
    output_path: Path,
    *,
    fence: Path,
    packet_id: str = "",
    run_as: str = "",
    questions: Sequence[str] = (),
    include_graded: bool = False,
    check_crops: bool = True,
    purpose: str = PURPOSE_GRADING,
    now: datetime | None = None,
) -> AgentPacket:
    """`prepare_agent_packet` without the class-folder checks.

    `submissions` is whatever folder of `.submission.md` files the packet is
    made from: a class's own, or a workspace's grades written out for the
    occasion (`core/agent/workspace.py`). `fence` is the folder the
    assistant is kept out of — the class folder, or the whole workspace.
    """
    if purpose not in (PURPOSE_GRADING, PURPOSE_RUBRICS):
        raise AgentError(f"purpose is {PURPOSE_GRADING!r} or {PURPOSE_RUBRICS!r}")
    qa_path = Path(qa_file_path)
    qa_document = load_qa_markdown(qa_path)  # an answer key that does not parse is refused here
    if purpose == PURPOSE_RUBRICS:
        gaps = rubric_gaps(qa_document)
        questions = [q for q in questions if q in gaps] if questions else sorted(gaps)
        if not questions:
            raise AgentError("every written question already has partial credit, so there is no rubric to draft")
        include_graded = True
    if check_crops:
        require_tesseract()  # before anything is written
    stamp = now or datetime.now().astimezone()
    result = build_packet(
        submissions,
        output_path,
        packet_id=packet_id or default_agent_packet_id(purpose, stamp),
        qa_file_path=qa_path,
        redact=True,
        include_pdfs=False,
        now=stamp,
    )
    try:
        manifest, left_out = _minimise(
            result.packet_path, result.manifest, set(questions), include_graded
        )
        scrubbed = _scrub_packet(result.packet_path, result.key, manifest)
        checked, withheld = (
            _withhold_identifying_crops(result.packet_path, result.key) if check_crops else (0, 0)
        )
        _write_instructions(
            result.packet_path, qa_path.name, manifest, run_as or self_command(), purpose
        )
        write_assistant_settings(result.packet_path, class_folder=fence, run_as=run_as or self_command())
        _assert_no_identity(result.packet_path, result.key)
        status = packet_status(result.packet_path)
        if purpose == PURPOSE_GRADING and not status.ungraded and not include_graded:
            raise AgentError("nothing is left to grade in this class, so no copy was made")
    except Exception:
        # Both were created a moment ago by this call; a packet that failed
        # the check must not be left for anyone to hand out.
        shutil.rmtree(result.packet_path, ignore_errors=True)
        result.key_path.unlink(missing_ok=True)
        raise

    return AgentPacket(
        path=result.packet_path,
        key_path=result.key_path,
        students=result.student_count,
        tasks=status.ungraded,
        scrubbed=scrubbed,
        unknown_version=len(status.unknown_version),
        left_out=left_out,
        images_withheld=withheld,
        images_checked=checked,
    )


_QUESTION_START = re.compile(r"^- (\S+):\s*$")


def _drop_questions(content: str, drop: set[str]) -> str:
    """The submission text without the question blocks in `drop`, all else as it was."""
    kept: list[str] = []
    skipping = False
    for line in content.split("\n"):
        start = _QUESTION_START.match(line)
        if start:
            skipping = start.group(1) in drop
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def _minimise(
    packet: Path, manifest: PacketManifest, questions: set[str], include_graded: bool
) -> tuple[PacketManifest, int]:
    """Leave out every answer the assistant does not need: graded ones (unless
    asked for as examples) and other questions (when some were named)."""
    left_out = 0
    kept_cells: set[tuple[str, str]] = set()
    for path in discover_submission_paths(packet / PACKET_SUBMISSIONS_DIRNAME):
        document = load_student_submission_markdown(path)
        drop: set[str] = set()
        for question in document.questions:
            wanted = (not questions or question.question_id in questions) and (
                include_graded or question.points_awarded is None
            )
            if wanted:
                kept_cells.add((document.student_id, question.question_id))
                continue
            drop.add(question.question_id)
            if question.response_image_path:
                Path(question.response_image_path).unlink(missing_ok=True)
        if drop:
            left_out += len(drop)
            path.write_text(_drop_questions(path.read_text(encoding="utf-8"), drop), encoding="utf-8")
    baseline = {cell: state for cell, state in manifest.baseline_cells().items() if cell in kept_cells}
    trimmed = replace(manifest, baseline=baseline)
    resolve_manifest_path(packet).write_text(render_packet_manifest(trimmed), encoding="utf-8")
    return trimmed, left_out


def _withhold_identifying_crops(packet: Path, key: HandoffKey) -> tuple[int, int]:
    members = key.by_alias()
    crops: list[tuple[Path, str, str, str, str]] = []
    for path in discover_submission_paths(packet / PACKET_SUBMISSIONS_DIRNAME):
        document = load_student_submission_markdown(path)
        member = members.get(document.student_id)
        if member is None:
            continue
        for question in document.questions:
            image = Path(question.response_image_path or "")
            if question.response_image_path and image.is_file():
                crops.append((image, member.alias, question.question_id, member.student_name, member.student_id))
    findings = scan_crops(crops)
    withheld: dict[str, str] = {}
    for finding in findings:
        finding.image.unlink(missing_ok=True)
        withheld[f"{finding.alias}/{finding.question_id}"] = finding.what
    write_withheld(packet, withheld)
    return len(crops), len(findings)


def _identity_patterns(name: str, student_id: str) -> list[tuple[re.Pattern[str], str]]:
    patterns: list[tuple[re.Pattern[str], str]] = []
    name = " ".join(name.split())
    if name:
        parts = name.split(" ")
        whole = [name]
        if len(parts) > 1:
            whole.append(f"{parts[-1]}, {' '.join(parts[:-1])}")
        for text in whole:
            patterns.append(
                (re.compile(rf"(?<!\w){re.escape(text)}(?!\w)", re.IGNORECASE), _NAME_REMOVED)
            )
        for part in parts:
            if len(part) >= _MIN_NAME_PART:
                # Case-sensitive: "Will" the name, not "will" the word.
                patterns.append(
                    (re.compile(rf"(?<!\w){re.escape(part)}(?!\w)"), _NAME_REMOVED)
                )
    student_id = student_id.strip()
    if len(student_id) >= _MIN_ID_DIGITS:
        patterns.append(
            (re.compile(rf"(?<![0-9A-Za-z]){re.escape(student_id)}(?![0-9A-Za-z])", re.IGNORECASE), _ID_REMOVED)
        )
    return patterns


def _scrub(text: str, patterns: Sequence[tuple[re.Pattern[str], str]]) -> str:
    for pattern, replacement in patterns:
        text = pattern.sub(replacement, text)
    return text


def _scrub_packet(packet: Path, key: HandoffKey, manifest: PacketManifest) -> int:
    patterns_by_alias = {
        member.alias: _identity_patterns(member.student_name, member.student_id)
        for member in key.members
    }
    scrubbed = 0
    for path in discover_submission_paths(packet / PACKET_SUBMISSIONS_DIRNAME):
        document = load_student_submission_markdown(path)
        patterns = patterns_by_alias.get(document.student_id, [])
        content = path.read_text(encoding="utf-8")
        updated = _drop_scan_warnings(content)
        for question in document.questions:
            text = question.response_text or ""
            comment = question.analysis or ""
            clean_text, clean_comment = _scrub(text, patterns), _scrub(comment, patterns)
            if clean_text == text and clean_comment == comment:
                continue
            scrubbed += 1
            updated = update_student_submission_markdown(
                updated,
                StudentSubmissionGradeUpdate(
                    question_id=question.question_id,
                    response_text=clean_text if clean_text != text else None,
                    analysis=clean_comment if clean_comment != comment else None,
                ),
            )
        if updated != content:
            path.write_text(updated, encoding="utf-8")

    # The merge compares the returned packet with this baseline; scrubbed
    # the same way, a scrub reads as "unchanged" instead of as an edit.
    baseline: dict[tuple[str, str], CellState] = {}
    for (alias, question_id), cell in manifest.baseline_cells().items():
        patterns = patterns_by_alias.get(alias, [])
        values = dict(cell.values)
        for aspect in ("comment", "transcription"):
            value = values.get(aspect)
            if value is not None and value.text:
                values[aspect] = replace(value, text=_scrub(value.text, patterns))
        baseline[(alias, question_id)] = CellState(values=values)
    resolve_manifest_path(packet).write_text(
        render_packet_manifest(replace(manifest, baseline=baseline)), encoding="utf-8"
    )
    return scrubbed


def _drop_scan_warnings(content: str) -> str:
    # Warnings name source files, which a class may have named after students.
    lines = content.split("\n")
    if not lines or lines[0].strip() != "---":
        return content
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            break
        if lines[index].startswith("scan_warnings:"):
            del lines[index]
            break
    return "\n".join(lines)


def _assert_no_identity(packet: Path, key: HandoffKey) -> None:
    checks: list[tuple[re.Pattern[str], str]] = []
    for member in key.members:
        name = " ".join(member.student_name.split())
        if name:
            checks.append((re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE), "a student's name"))
        student_id = member.student_id.strip()
        if len(student_id) >= _MIN_ID_DIGITS:
            checks.append((re.compile(rf"(?<![0-9A-Za-z]){re.escape(student_id)}(?![0-9A-Za-z])"), "a student id"))
    for path in sorted(packet.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".md", ".json", ".txt"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern, what in checks:
            if pattern.search(text):
                raise AgentError(
                    f"{path.relative_to(packet)} still contains {what} after "
                    "redaction, so no packet was made. Remove it from the "
                    "answer key or the comments and try again."
                )
    for path in packet.rglob("*"):
        if path.is_file() and path.suffix.lower() == ".pdf":
            raise AgentError(f"{path.relative_to(packet)}: a scan is never put in an assistant packet")


def rubric_gaps(document) -> set[str]:  # noqa: ANN001 - QADocument
    """Written questions (not multiple choice) whose rubric names no partial-credit mistake."""
    from opengrader.core.grading.rubric_draft import has_partial_credit_items
    from opengrader.core.qa_file.markdown import flatten_rubric_options

    versions = list(document.version_questions.values()) or [document.questions]
    gaps: set[str] = set()
    for questions in versions:
        for question in questions:
            for item in question.subquestions or [question]:
                if item.choices:
                    continue
                if not has_partial_credit_items(flatten_rubric_options(item.rubric)):
                    gaps.add(item.id)
    return gaps


def _write_instructions(
    packet: Path, qa_name: str, manifest: PacketManifest, run_as: str, purpose: str
) -> None:
    instructions = (
        guide_text("agent-rubrics.md" if purpose == PURPOSE_RUBRICS else "agent-packet.md")
        .replace("{assignment}", manifest.assignment_title or "this assignment")
        .replace("{qa_file}", qa_name)
        .replace("{students}", str(len(manifest.aliases)))
        .replace("{og}", run_as)
    )
    (packet / INSTRUCTIONS_FILENAME).write_text(instructions, encoding="utf-8")
    (packet / CLAUDE_FILENAME).write_text(f"@{INSTRUCTIONS_FILENAME}\n", encoding="utf-8")
    # build_packet's README is for a human grader with the grading app.
    (packet / "README.txt").write_text(
        f"A copy of {manifest.assignment_title or 'an assignment'} for an AI "
        f"assistant, with no student names or IDs. Instructions: {INSTRUCTIONS_FILENAME}.\n",
        encoding="utf-8",
    )
    marker = {
        "agent_format": AGENT_FORMAT,
        "kind": KIND_GRADING,
        "purpose": purpose,
        "qa_file": qa_name,
        "packet_id": manifest.packet_id,
    }
    (packet / ".opengrader" / AGENT_MARKER_FILENAME).write_text(
        json.dumps(marker, indent=2) + "\n", encoding="utf-8"
    )


def list_agent_packets(submissions_path: str | Path) -> list[Path]:
    """The assistant packets made from this class that are still on disk, newest first.

    Found through the handoff keys the class folder keeps, so a teacher who
    comes back another day finds the copy without remembering where it went.
    """
    found: list[Path] = []
    for key in list_handoff_keys(submissions_path):
        packet = Path(key.packet_path)
        if (packet / ".opengrader" / AGENT_MARKER_FILENAME).is_file():
            found.append(packet)
    return found


def delete_packet(packet: str | Path, submissions_path: str | Path) -> None:
    """Remove a copy once its work is merged back (teacher only).

    Only a folder this class recorded handing to an assistant is deleted —
    never a path that merely looks like one. The handoff key stays in the
    class folder: it holds nothing the class folder does not already, and
    the audit journal still names the merge.
    """
    target = Path(packet).resolve()
    known = {path.resolve() for path in list_agent_packets(submissions_path)}
    if target not in known:
        raise AgentError(f"{target} is not an assistant copy made from this class")
    shutil.rmtree(target)
