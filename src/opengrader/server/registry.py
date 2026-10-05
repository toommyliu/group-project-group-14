"""Persistent workspace locations, independent of server and client lifetimes."""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

from opengrader.core.workspace.models import CourseDetails, Term
from opengrader.core.workspace.store import DATABASE, Workspace
from opengrader.server.home import default_workspaces_dir, resolve_data_home


_MIGRATIONS: tuple[tuple[str, ...], ...] = (
    (
        "CREATE TABLE IF NOT EXISTS instance (id TEXT NOT NULL)",
        """CREATE TABLE IF NOT EXISTS workspaces (
            id TEXT PRIMARY KEY, name TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE, last_opened TEXT
        )""",
    ),
    (
        "ALTER TABLE workspaces RENAME COLUMN name TO number",
        "ALTER TABLE workspaces ADD COLUMN name TEXT",
        "ALTER TABLE workspaces ADD COLUMN term TEXT",
        "ALTER TABLE workspaces ADD COLUMN year INTEGER",
    ),
)


class RegisteredWorkspace(CourseDetails):
    id: str
    path: str
    last_opened: str | None = None

    @property
    def url_path(self) -> str:
        return f"/courses/{self.id}"

    @property
    def course(self) -> CourseDetails:
        return CourseDetails(**self.model_dump(include=set(CourseDetails.model_fields)))


def current_term(today: date) -> tuple[Term, int]:
    term: Term = (
        "spring" if today.month <= 5 else "summer" if today.month <= 7 else "fall"
    )
    return term, today.year


def folder_name(course: CourseDetails) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(course).lower()).strip("-")
    reserved = {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
    return slug if slug and slug not in reserved else "course"


class WorkspaceRegistry:
    def __init__(self, home: Path | None = None):
        self.home = (home or resolve_data_home()).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.home / "registry.sqlite3"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > len(_MIGRATIONS):
                raise ValueError(
                    f"{self.database} was written by a newer version. Update and try again."
                )
            for statements in _MIGRATIONS[version:]:
                for statement in statements:
                    db.execute(statement)
            db.execute(f"PRAGMA user_version = {len(_MIGRATIONS)}")
            if db.execute("SELECT id FROM instance").fetchone() is None:
                db.execute("INSERT INTO instance VALUES (?)", (str(uuid4()),))
        self.database.chmod(0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @property
    def id(self) -> str:
        with self.connect() as db:
            return db.execute("SELECT id FROM instance").fetchone()["id"]

    def list(self) -> list[RegisteredWorkspace]:
        with self.connect() as db:
            return [
                RegisteredWorkspace(**dict(row))
                for row in db.execute(
                    "SELECT * FROM workspaces ORDER BY last_opened DESC, number COLLATE NOCASE, id"
                )
            ]

    def get(self, workspace_id: str) -> RegisteredWorkspace:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
        if row is None:
            raise LookupError("This course isn't registered with this server.")
        return RegisteredWorkspace(**dict(row))

    def register(
        self, root: Path, course: CourseDetails | None = None, *, dry_run: bool = False
    ) -> RegisteredWorkspace:
        root = root.expanduser().resolve()
        workspace = Workspace(root)  # Never create on registration or discovery.
        workspace_id = workspace.info().id
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute(
                "SELECT * FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
            if (
                previous
                and previous["path"] != str(root)
                and (Path(previous["path"]) / DATABASE).exists()
            ):
                raise ValueError(
                    "Another copy of this course is already registered. Open the registered copy."
                )
            course = (
                course
                or (previous and RegisteredWorkspace(**dict(previous)).course)
                or CourseDetails(number=root.name[:120])
            )
            if dry_run:
                taken = db.execute(
                    "SELECT 1 FROM workspaces WHERE path=? AND id!=?",
                    (str(root), workspace_id),
                ).fetchone()
                if taken:
                    raise ValueError(
                        "This folder is already registered as another course."
                    )
                return RegisteredWorkspace(
                    **course.model_dump(),
                    id=workspace_id,
                    path=str(root),
                    last_opened=previous["last_opened"] if previous else None,
                )
            try:
                db.execute(
                    "INSERT INTO workspaces(id, path, number, name, term, year) VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET path=excluded.path, number=excluded.number, "
                    "name=excluded.name, term=excluded.term, year=excluded.year",
                    (workspace_id, str(root), course.number, course.name, course.term, course.year),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "This folder is already registered as another course."
                ) from exc
        return self.get(workspace_id)

    def update(self, workspace_id: str, course: CourseDetails) -> RegisteredWorkspace:
        with self.connect() as db:
            changed = db.execute(
                "UPDATE workspaces SET number=?, name=?, term=?, year=? WHERE id=?",
                (course.number, course.name, course.term, course.year, workspace_id),
            ).rowcount
        if not changed:
            raise LookupError("This course isn't registered with this server.")
        return self.get(workspace_id)

    def remove(self, workspace_id: str) -> None:
        with self.connect() as db:
            removed = db.execute(
                "DELETE FROM workspaces WHERE id=?", (workspace_id,)
            ).rowcount
        if not removed:
            raise LookupError("This course isn't registered with this server.")

    def destination(self, course: CourseDetails, root: Path | None = None) -> Path:
        registered = {item.path for item in self.list()}
        if root is None:
            parent = default_workspaces_dir().expanduser().resolve()
            name = folder_name(course)
            destination, suffix = parent / name, 2
            while destination.exists() or str(destination) in registered:
                destination, suffix = parent / f"{name}-{suffix}", suffix + 1
            return destination
        destination = root.expanduser().resolve()
        if str(destination) in registered:
            raise ValueError("This folder is already registered as another course.")
        if (destination / DATABASE).exists():
            raise ValueError(
                f"{destination} already has a course. Add it with `opengrader course add` instead."
            )
        return destination

    def create(
        self, course: CourseDetails, root: Path | None = None
    ) -> RegisteredWorkspace:
        destination = self.destination(course, root)
        Workspace.create(destination)
        return self.register(destination, course)

    def opened(self, workspace_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE workspaces SET last_opened=? WHERE id=?",
                (
                    datetime.now(UTC).isoformat(),
                    workspace_id,
                ),
            )
