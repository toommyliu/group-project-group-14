"""Own session processes independently of browser connections."""

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .shell import ShellProcess
from .store import OutputHistory, TerminalRecord, TerminalStore

log = logging.getLogger(__name__)
OutputQueue = asyncio.Queue[bytes | str | None]


class SessionError(ValueError):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Session:
    record: TerminalRecord
    history: OutputHistory
    process: ShellProcess | None = None
    listener: OutputQueue | None = None
    detached_at: float = field(default_factory=time.monotonic)

    def emit(self, data: bytes | str) -> None:
        if isinstance(data, bytes):
            self.history.append(data)
        if self.listener is not None:
            try:
                self.listener.put_nowait(data)
            except asyncio.QueueFull:
                # A slow client reconnects to a fresh snapshot; it cannot hold the process up.
                self.disconnect()

    def disconnect(self) -> None:
        if self.listener is not None:
            while not self.listener.empty():
                self.listener.get_nowait()
            self.listener.put_nowait(None)
            self.listener = None
        self.detached_at = time.monotonic()


class TerminalManager:
    def __init__(
        self,
        home: Path,
        *,
        grace_seconds: float = 1800,
        max_live: int = 10,
        max_saved: int = 10,
    ) -> None:
        self.home = home = home.resolve()
        self.store = TerminalStore(home / "terminals")
        self.sessions = {
            key: Session(record, self.store.history(key))
            for key, record in self.store.records.items()
        }
        self.grace_seconds = grace_seconds
        self.max_live = max_live
        self.max_saved = max_saved
        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task[None]] = set()
        self.cleaner: asyncio.Task[None] | None = None

    def start_maintenance(self) -> None:
        self.cleaner = asyncio.create_task(self._maintain())

    def get(self, session_id: str) -> Session:
        try:
            return self.sessions[session_id]
        except KeyError:
            raise SessionError("Session not found.", 404) from None

    def list_for_assignment(self, assignment_id: str) -> list[TerminalRecord]:
        return sorted(
            (
                s.record
                for s in self.sessions.values()
                if s.record.assignment_id == assignment_id
            ),
            key=lambda record: record.position,
        )

    async def rename(self, session_id: str, title: str) -> TerminalRecord:
        async with self.lock:
            record = self.get(session_id).record
            previous = record.title
            record.title = title
            try:
                self.store.save()
            except OSError:
                record.title = previous
                raise
            return record

    async def reorder(self, assignment_id: str, ids: list[str]) -> list[TerminalRecord]:
        async with self.lock:
            records = self.list_for_assignment(assignment_id)
            if len(ids) != len(records) or set(ids) != {
                record.id for record in records
            }:
                raise SessionError(
                    "The terminal list changed. Try moving the tab again."
                )
            previous = {record.id: record.position for record in records}
            for position, session_id in enumerate(ids):
                self.get(session_id).record.position = position
            try:
                self.store.save()
            except OSError:
                for record in records:
                    record.position = previous[record.id]
                raise
            return self.list_for_assignment(assignment_id)

    async def create(self, assignment_id: str) -> TerminalRecord:
        async with self.lock:
            if len(self.list_for_assignment(assignment_id)) >= self.max_saved:
                raise SessionError(
                    f"Remove an old session before creating another. Limit: {self.max_saved}."
                )
            record = TerminalRecord(
                assignment_id=assignment_id,
                title=f"Terminal {1 + sum(s.record.assignment_id == assignment_id for s in self.sessions.values())}",
                position=1
                + max(
                    (
                        s.record.position
                        for s in self.sessions.values()
                        if s.record.assignment_id == assignment_id
                    ),
                    default=-1,
                ),
                cwd=str(self.home / "assignments" / assignment_id),
            )
            self.store.records[record.id] = record
            self.sessions[record.id] = Session(record, OutputHistory())
            try:
                self.store.save()
            except OSError:
                del self.store.records[record.id]
                del self.sessions[record.id]
                raise
            return record

    async def start(
        self, session_id: str, cols: int = 100, rows: int = 30
    ) -> TerminalRecord:
        async with self.lock:
            session = self.get(session_id)
            if session.process is not None:
                return session.record
            if (
                sum(s.process is not None for s in self.sessions.values())
                >= self.max_live
            ):
                raise SessionError(
                    f"Stop a running session before starting another. Limit: {self.max_live}."
                )
            directory = self.store.directory(session_id)
            assignment = self.home / "assignments" / session.record.assignment_id
            if not (assignment / "qa.md").is_file():
                raise SessionError("The assignment is no longer available.", 404)
            process = ShellProcess(session.record.cwd, directory, session.emit)
            session.history.append(b"\r\n--- New shell started ---\r\n")
            session.process = process
            try:
                await process.start(cols, rows)
            except Exception as exc:
                await process.stop()
                session.process = None
                session.record.message = f"Could not start: {exc}"
                self.store.save()
                raise SessionError(session.record.message, 503) from exc
            session.record.status = "running"
            session.record.message = "Running"
            session.detached_at = time.monotonic()
            self.store.save()
            task = asyncio.create_task(self._watch(session, process))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return session.record

    async def _watch(self, session: Session, process: ShellProcess) -> None:
        await process.done.wait()
        async with self.lock:
            if session.process is process:
                await self._stop(session, "Session ended. Start it again to continue.")

    async def attach(
        self, session_id: str, cols: int, rows: int
    ) -> tuple[OutputQueue, bytes]:
        async with self.lock:
            session = self.get(session_id)
            if session.process is None:
                raise SessionError(
                    "Session is stopped. Start it from the session list."
                )
            if session.listener is not None:
                raise SessionError(
                    "This session is open in another connection. Disconnect there first."
                )
            queue: OutputQueue = asyncio.Queue(maxsize=64)
            # Snapshot and subscription are synchronous, so intervening output cannot be lost or duplicated.
            snapshot = session.history.value()
            session.listener = queue
            try:
                await session.process.resize(cols, rows)
            except Exception:
                session.disconnect()
                raise
            return queue, snapshot

    async def detach(self, session_id: str, listener: OutputQueue) -> None:
        async with self.lock:
            session = self.sessions.get(session_id)
            if session is not None and session.listener is listener:
                session.disconnect()

    async def stop(self, session_id: str) -> TerminalRecord:
        async with self.lock:
            session = self.get(session_id)
            await self._stop(session, "Stopped. Start this session to continue.")
            return session.record

    async def _stop(self, session: Session, message: str) -> None:
        process = session.process
        if process is not None:
            session.record.cwd = process.cwd
            await process.stop()
            session.process = None
        session.disconnect()
        session.record.status = "stopped"
        session.record.message = message
        self.store.save_history(session.record.id, session.history)
        self.store.save()

    async def remove(self, session_id: str) -> None:
        async with self.lock:
            session = self.get(session_id)
            if session.process is not None:
                raise SessionError("Stop this session before removing it.")
            del self.store.records[session_id]
            try:
                self.store.save()
            except OSError:
                self.store.records[session_id] = session.record
                raise
            del self.sessions[session_id]
            shutil.rmtree(self.store.directory(session_id), ignore_errors=True)

    async def expire(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        async with self.lock:
            for session in self.sessions.values():
                process = session.process
                if (
                    process is not None
                    and session.listener is None
                    and now - session.detached_at >= self.grace_seconds
                ):
                    if await process.park():
                        await self._stop(
                            session,
                            "Stopped after inactivity. Start this session to continue.",
                        )
                if session.history.dirty:
                    self.store.save_history(session.record.id, session.history)

    async def _maintain(self) -> None:
        while True:
            await asyncio.sleep(15)
            try:
                await self.expire()
            except Exception:
                log.exception("Could not clean up terminal sessions")

    async def close(self) -> None:
        if self.cleaner is not None:
            self.cleaner.cancel()
            await asyncio.gather(self.cleaner, return_exceptions=True)
        async with self.lock:
            for session in self.sessions.values():
                try:
                    await self._stop(
                        session, "Server stopped. Start this session to continue."
                    )
                except Exception:
                    log.exception("Could not close terminal %s", session.record.id)
        await asyncio.gather(*self.tasks, return_exceptions=True)
