"""What this user opened recently, newest first — one small per-user file.

Kept beside the AI settings (`recent.json`), never inside an assignment
folder, so it can hold paths from many courses and is never shared by a
backup or a grader packet. Every read and write is best effort: a missing,
malformed or unwritable file costs the list, never the app.

`opengrader tui` records each workspace it opens, with the assignment last
opened in it; outside any workspace it offers them back
(`tui/recent_workspaces.py`), and a workspace's assignment list starts on
the assignment you were last grading. `opengrader classic` records each class
folder it grades and lists the last few on its Start grading card. Both lists
are the same widget (`tui/ui/widgets/recent_list.py`).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

__all__ = [
    "RecentKind",
    "RecentItem",
    "MAX_RECENTS",
    "recents_path",
    "load_recents",
    "record_recent",
    "forget_recent",
    "recent_workspace",
    "short_path",
]

RecentKind = Literal["folder", "workspace"]
RECENTS_FILENAME = "recent.json"
MAX_RECENTS = 12


@dataclass(frozen=True)
class RecentItem:
    kind: RecentKind
    path: str
    title: str = ""
    qa_file: str = ""
    submissions: str = ""
    opened_at: str = ""
    # Workspaces only: the assignment last opened there (id + title), so the
    # assignment list can start on it and the recent list can name it.
    assignment: str = ""
    assignment_title: str = ""

    @property
    def key(self) -> tuple[str, str, str]:
        # A folder opened with two different answer keys is two entries.
        return (self.kind, self.path, self.qa_file)

    def exists(self) -> bool:
        if self.kind == "workspace":
            return (Path(self.path) / ".opengrader" / "workspace.sqlite3").is_file()
        return Path(self.submissions or self.path).exists() and (
            not self.qa_file or Path(self.qa_file).is_file()
        )


def recents_path() -> Path:
    from opengrader.core.app_config import settings_path

    return settings_path().parent / RECENTS_FILENAME


def load_recents(path: Path | None = None) -> list[RecentItem]:
    target = path or recents_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("recent"), list):
        return []
    items: list[RecentItem] = []
    for entry in data["recent"]:
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind")
        where = entry.get("path")
        if kind not in ("folder", "workspace") or not isinstance(where, str) or not where:
            continue
        items.append(
            RecentItem(
                kind=kind,
                path=where,
                title=_text(entry.get("title")),
                qa_file=_text(entry.get("qa_file")),
                submissions=_text(entry.get("submissions")),
                opened_at=_text(entry.get("opened_at")),
                assignment=_text(entry.get("assignment")),
                assignment_title=_text(entry.get("assignment_title")),
            )
        )
    return items[:MAX_RECENTS]


def record_recent(
    kind: RecentKind,
    path: str | Path,
    *,
    title: str = "",
    qa_file: str | Path | None = None,
    submissions: str | Path | None = None,
    assignment: str | None = None,
    assignment_title: str | None = None,
    store: Path | None = None,
) -> None:
    """Put this at the top of the list. Best effort: never raises `OSError`.

    `assignment`/`assignment_title` left as `None` keep what this entry
    already had, so re-recording a workspace when it is merely reopened does
    not forget which assignment was being graded there; pass `""` to clear.
    """
    target = store or recents_path()
    existing = load_recents(target)
    item = RecentItem(
        kind=kind,
        path=_absolute(path),
        title=title.strip() or Path(str(path)).expanduser().name,
        qa_file=_absolute(qa_file) if qa_file else "",
        submissions=_absolute(submissions) if submissions else "",
        opened_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    previous = next((old for old in existing if old.key == item.key), None)
    item = replace(
        item,
        assignment=_kept(assignment, previous.assignment if previous else ""),
        assignment_title=_kept(
            assignment_title, previous.assignment_title if previous else ""
        ),
    )
    items = [item, *(old for old in existing if old.key != item.key)]
    _write(target, items[:MAX_RECENTS])


def recent_workspace(path: str | Path, *, store: Path | None = None) -> RecentItem | None:
    """This workspace's entry, if it has been opened before."""
    where = _absolute(path)
    return next(
        (
            item
            for item in load_recents(store)
            if item.kind == "workspace" and item.path == where
        ),
        None,
    )


def forget_recent(item: RecentItem, *, store: Path | None = None) -> None:
    target = store or recents_path()
    _write(target, [old for old in load_recents(target) if old.key != item.key])


def _write(target: Path, items: list[RecentItem]) -> None:
    body = json.dumps({"version": 1, "recent": [asdict(i) for i in items]}, indent=2)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_text(body + "\n", encoding="utf-8")
        temporary.replace(target)
    except OSError:
        return


def _absolute(value: str | Path) -> str:
    return str(Path(str(value)).expanduser().resolve())


def short_path(path: str | Path) -> str:
    """`~/…` for a path under the home folder, as a shell would print it."""
    text = str(path)
    home = str(Path.home())
    if text == home or text.startswith(home + os.sep):
        return "~" + text[len(home):]
    return text


def _kept(new: str | None, old: str) -> str:
    return old if new is None else new.strip()


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
