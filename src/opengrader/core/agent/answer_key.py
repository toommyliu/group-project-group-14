"""An AI assistant writing the answer key, in a folder with no student in it.

The answer key is the one stage before grading that needs no student data,
so an assistant can do it with nothing to redact — as long as it never works
in the class folder itself, where the scans and submissions are. So
`prepare_key_folder` makes a folder beside the class folder holding only
what the job needs: the class's current `qa.md` (when there is one: often a
standard key drafted from the blank assignment, with every answer still
blank), the teacher's own answer-key document (when they have one), the
format guide and instructions. The assistant writes or fills `qa.md` there
and checks it with `agent check-key` (`qa_file.check`).

`use_key` copies the result back. It replaces the class's key without asking
only when that key is still the one the folder was made from (its hash is in
the folder's marker); a key the teacher changed meanwhile, or any other file
already at the destination, needs `replace=True`.

When the class already has submissions, the copied key is checked for every
student's name and ID first: a key drafted from a scan could carry one.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.qa_file.check import KeyCheck, check_answer_key
from opengrader.core.submissions.student_file import load_student_submission_markdown

from .common import (
    AGENT_FORMAT,
    AGENT_MARKER_FILENAME,
    CLAUDE_FILENAME,
    INSTRUCTIONS_FILENAME,
    KIND_ANSWER_KEY,
    AgentError,
    guide_text,
    read_marker,
    refuse_workspace,
    self_command,
)

__all__ = [
    "KEY_FILENAME",
    "KeyWorkspace",
    "check_key_file",
    "default_key_folder",
    "make_key_folder",
    "prepare_key_folder",
    "prepare_key_workspace",
    "use_key",
]

KEY_FILENAME = "qa.md"
FORMAT_FILENAME = "qa-format.md"
_MIN_ID_DIGITS = 4


@dataclass(frozen=True)
class KeyWorkspace:
    path: Path
    source: Path | None
    has_key: bool


def _unique(parent: Path, name: str) -> Path:
    candidate, counter = parent / name, 2
    while candidate.exists():
        candidate, counter = parent / f"{name}-{counter}", counter + 1
    return candidate


def _digest(path: Path) -> str:
    return text_digest(path.read_bytes())


def text_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def default_key_folder(class_folder: str | Path) -> Path:
    """`class/` → `class-ai-answer-key/`, beside it, never inside it."""
    folder = Path(class_folder).expanduser().resolve()
    return folder.parent / f"{folder.name}-ai-answer-key"


def _identities(submissions: Path | None) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if submissions is None or not submissions.exists():
        return found
    for path in discover_submission_paths(submissions):
        try:
            document = load_student_submission_markdown(path)
        except (OSError, ValueError):
            continue
        found.append((" ".join(document.student_name.split()), document.student_id.strip()))
    return found


def _assert_no_student(text: str, identities: list[tuple[str, str]], what: str) -> None:
    folded = text.casefold()
    for name, student_id in identities:
        if name and name.casefold() in folded:
            raise AgentError(f"{what} contains a student's name, so it was not given to the assistant")
        if len(student_id) >= _MIN_ID_DIGITS and student_id in text:
            raise AgentError(f"{what} contains a student ID, so it was not given to the assistant")


def prepare_key_folder(
    *,
    qa_file: str | Path | None = None,
    source: str | Path | None = None,
    class_folder: str | Path | None = None,
    submissions: str | Path | None = None,
    output: str | Path | None = None,
    run_as: str = "",
) -> KeyWorkspace:
    """A folder for the assistant: the current key and/or the teacher's document."""
    key = Path(qa_file).expanduser().resolve() if qa_file else None
    document = Path(source).expanduser().resolve() if source else None
    if key is not None and not key.is_file():
        raise AgentError(f"no answer key at {key}")
    if document is not None and not document.is_file():
        raise AgentError(f"no answer-key document at {document}")
    for place in (class_folder, key):
        if place:
            refuse_workspace(place)
    if key is None and document is None:
        raise AgentError("give the assistant something to work from: the class's answer key, or your own answer-key document")

    base = Path(class_folder).expanduser().resolve() if class_folder else None
    if output:
        workspace = _unique(Path(output).expanduser().resolve(), "ai-answer-key")
    elif base is not None:
        target = default_key_folder(base)
        workspace = _unique(target.parent, target.name)
    else:
        anchor = (document or key).parent  # type: ignore[union-attr]
        workspace = _unique(anchor, f"{(document or key).stem}-ai-answer-key")  # type: ignore[union-attr]

    identities = _identities(Path(submissions).expanduser() if submissions else None)
    marker: dict[str, object] = {}
    if key is not None:
        marker = {"base_key": str(key), "base_hash": _digest(key)}
    return make_key_folder(
        workspace,
        key=key.read_bytes() if key is not None else None,
        document=document,
        identities=identities,
        marker=marker,
        run_as=run_as,
    )


def make_key_folder(
    folder: Path,
    *,
    key: bytes | None,
    document: Path | None,
    identities: list[tuple[str, str]],
    marker: dict[str, object],
    run_as: str = "",
) -> KeyWorkspace:
    """Write the assistant's folder: the key (as it is now), the teacher's
    document, the format guide and the instructions.

    `marker` says where the key came from, so the finished one goes back
    only over the key it started from.
    """
    if key is not None:
        _assert_no_student(key.decode("utf-8"), identities, "the answer key")

    (folder / ".opengrader").mkdir(parents=True)
    marker = {"agent_format": AGENT_FORMAT, "kind": KIND_ANSWER_KEY, **marker}
    if key is not None:
        (folder / KEY_FILENAME).write_bytes(key)
    copied: Path | None = None
    if document is not None:
        copied = folder / document.name
        shutil.copy2(document, copied)
        marker["source"] = document.name
    (folder / FORMAT_FILENAME).write_text(guide_text("qa-format.md"), encoding="utf-8")

    if key is not None and document is not None:
        task = (
            f"`qa.md` here is the class's answer key, and `{copied.name}` is the teacher's own "
            "answer key. Fill in every answer `qa.md` is missing from it, and correct any that disagree with it."
        )
    elif key is not None:
        task = (
            "`qa.md` here is the class's answer key; some of its answers are blank. Fill each blank "
            "answer from the question itself, and leave a question blank when its text alone does not "
            "settle the answer."
        )
    else:
        task = f"Write `qa.md` from the teacher's answer key, `{copied.name}`."  # type: ignore[union-attr]
    (folder / INSTRUCTIONS_FILENAME).write_text(
        guide_text("agent-answer-key.md")
        .replace("{og}", run_as or self_command())
        .replace("{task}", task)
        .replace("{source}", copied.name if copied is not None else "qa.md"),
        encoding="utf-8",
    )
    (folder / CLAUDE_FILENAME).write_text(f"@{INSTRUCTIONS_FILENAME}\n", encoding="utf-8")
    (folder / ".opengrader" / AGENT_MARKER_FILENAME).write_text(
        json.dumps(marker, indent=2) + "\n", encoding="utf-8"
    )
    return KeyWorkspace(path=folder, source=copied, has_key=key is not None)


def write_marker(folder: Path, marker: dict[str, object]) -> None:
    (folder / ".opengrader" / AGENT_MARKER_FILENAME).write_text(
        json.dumps(marker, indent=2) + "\n", encoding="utf-8"
    )


def prepare_key_workspace(
    source: str | Path, output: str | Path | None = None, *, run_as: str = ""
) -> KeyWorkspace:
    """A key folder from the teacher's document alone (`agent answer-key --source`)."""
    return prepare_key_folder(source=source, output=output, run_as=run_as)


def check_key_file(path: str | Path) -> KeyCheck:
    key = Path(path)
    try:
        text = key.read_text(encoding="utf-8")
    except OSError as exc:
        return KeyCheck(errors=[f"cannot read {key}: {exc}"])
    return check_answer_key(text)


def use_key(workspace: str | Path, destination: str | Path, *, replace: bool = False) -> KeyCheck:
    """Copy the folder's finished key to `destination` (teacher only)."""
    folder = Path(workspace)
    key = folder / KEY_FILENAME
    if not key.is_file():
        raise AgentError(f"{key} does not exist yet: the assistant has not written the key")
    result = check_key_file(key)
    if not result.ok:
        raise AgentError("the key does not load yet:\n" + "\n".join(result.errors))
    target = Path(destination).expanduser()
    if target.exists() and not replace:
        marker = read_marker(folder)
        unchanged = (
            str(marker.get("base_key", "")) == str(target.resolve())
            and str(marker.get("base_hash", "")) == _digest(target)
        )
        if not unchanged:
            raise AgentError(
                f"{target} already exists and is not the key this folder was made from "
                "(or it changed since); replace it on purpose"
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(key, target)
    # What the class has now is this folder's new starting point: another
    # round with the assistant replaces it without asking, unless it changes.
    marker = dict(read_marker(folder))
    marker["base_key"], marker["base_hash"] = str(target.resolve()), _digest(target)
    write_marker(folder, marker)
    return result
