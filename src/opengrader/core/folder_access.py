"""Can this account save grades in a class folder?

In folder mode every grader's app writes the submission files itself, so on
a machine several people reach over SSH, each with an account of their own,
whether a grade is saved is decided by ordinary file permissions. A folder
the account cannot write looked usable until the first save: this says so
the moment the folder is opened, naming whose files they are.

Only what grading writes is checked: the submissions folder (new files and
atomic replaces need the folder), each submission file and its folder (the
annotated PDF is written there), and the history and state folder. The
answer key is reported apart, because only rubric edits need it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_STATE_DIR = ".opengrader"


@dataclass(frozen=True)
class FolderAccess:
    account: str
    # What grading needs and this account may not write.
    unwritable: tuple[Path, ...]
    # Whose those are, by login, most first.
    owners: tuple[str, ...]
    submissions: int
    answer_key_unwritable: bool = False

    @property
    def can_grade(self) -> bool:
        return not self.unwritable

    def message(self) -> str:
        """Plain words for the grader; empty when everything is writable."""
        parts: list[str] = []
        if self.unwritable:
            whose = (
                f"they belong to {_listed(self.owners)}"
                if self.owners and self.account not in self.owners
                else "their permissions do not allow it"
            )
            parts.append(
                f"This account ({self.account}) cannot save grades in this folder: "
                f"{len(self.unwritable)} of the files and folders grading writes "
                f"are not writable, and {whose}. Grades given here will not be "
                "saved. Ask the folder's owner to give your account write access "
                "(a shared group with write permission), then open it again."
            )
        if self.answer_key_unwritable:
            parts.append(
                "The answer key is not writable by this account, so rubric edits "
                "(e) will not be saved; grading is not affected."
            )
        return " ".join(parts)


def _listed(names: tuple[str, ...]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def current_account() -> str:
    try:
        import getpass

        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name to be had
        return "this account"


def owner_of(path: Path) -> str | None:
    try:
        uid = path.stat().st_uid
    except OSError:
        return None
    try:
        import pwd
    except ImportError:  # Windows: no numeric owners to name
        return None
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def check_folder_access(submissions_dir: str | Path, answer_key: str | Path | None = None) -> FolderAccess:
    directory = Path(submissions_dir)
    files = sorted(
        path
        for path in directory.rglob("*.submission.md")
        if _STATE_DIR not in path.relative_to(directory).parts and not path.name.startswith("._")
    )
    needed: list[Path] = [directory, *files, *{path.parent for path in files}]
    state = directory / _STATE_DIR
    if state.exists():
        needed.append(state)
        needed.extend(child for child in state.iterdir() if child.is_dir())
    unwritable = tuple(path for path in needed if path.exists() and not os.access(path, os.W_OK))
    counts: dict[str, int] = {}
    for path in unwritable:
        owner = owner_of(path)
        if owner:
            counts[owner] = counts.get(owner, 0) + 1
    key = Path(answer_key) if answer_key else None
    return FolderAccess(
        account=current_account(),
        unwritable=unwritable,
        owners=tuple(sorted(counts, key=lambda name: -counts[name])),
        submissions=len(files),
        answer_key_unwritable=bool(key and key.exists() and not os.access(key, os.W_OK)),
    )
