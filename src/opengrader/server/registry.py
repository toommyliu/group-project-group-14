"""Persistent workspace locations, independent of server and client lifetimes."""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel

from opengrader.core.workspace.store import DATABASE, Workspace
from opengrader.server.home import default_workspaces_dir, resolve_data_home


class RegisteredWorkspace(BaseModel):
    id: str
    name: str
    path: str
    last_opened: str | None = None

    @property
    def url_path(self) -> str:
        return f"/courses/{self.id}"


def workspace_name(name: str) -> str:
    name = name.strip()
    if (
        not name
        or len(name) > 120
        or name in {".", ".."}
        or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
        or name.endswith(".")
        or name.split(".")[0].upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
    ):
        raise ValueError(
            "Choose a workspace name of 1–120 characters without filename punctuation."
        )
    return name


class WorkspaceRegistry:
    def __init__(self, home: Path | None = None):
        self.home = (home or resolve_data_home()).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.home / "registry.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS instance (id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE, last_opened TEXT
                );
            """)
            db.execute("BEGIN IMMEDIATE")
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
                    "SELECT * FROM workspaces ORDER BY last_opened DESC, name COLLATE NOCASE, id"
                )
            ]

    def get(self, workspace_id: str) -> RegisteredWorkspace:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM workspaces WHERE id=?", (workspace_id,)
            ).fetchone()
        if row is None:
            raise LookupError("Workspace is not registered with this server.")
        return RegisteredWorkspace(**dict(row))

    def register(self, root: Path, name: str | None = None) -> RegisteredWorkspace:
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
                    "Another copy of this workspace is already registered. Open the registered copy."
                )
            display_name = (
                workspace_name(name)
                if name
                else previous["name"]
                if previous
                else root.name
            )
            try:
                db.execute(
                    "INSERT INTO workspaces(id, name, path) VALUES (?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET name=excluded.name, path=excluded.path",
                    (workspace_id, display_name, str(root)),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(
                    "This location is already registered as another workspace."
                ) from exc
        return self.get(workspace_id)

    def create(self, name: str, root: Path | None = None) -> RegisteredWorkspace:
        name = workspace_name(name)
        destination = (root or default_workspaces_dir() / name).expanduser().resolve()
        # Default creation must not silently adopt an existing course directory.
        if root is None and destination.exists():
            raise ValueError(
                "A folder with that name already exists. Choose another name or register the existing workspace."
            )
        Workspace.create(destination)
        return self.register(destination, name)

    def opened(self, workspace_id: str) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE workspaces SET last_opened=? WHERE id=?",
                (
                    datetime.now(UTC).isoformat(),
                    workspace_id,
                ),
            )
