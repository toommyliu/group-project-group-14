"""Reconnect, reclamation and persistence using real child processes."""

import asyncio
import json
import os
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from opengrader.server.workspace.app import create_app
from opengrader.server.terminals.manager import SessionError, TerminalManager
from opengrader.server.terminals.store import OutputHistory

QA = """---
title: Terminal test
---

- (2 points) What is two plus two?
  - **Answer**
    - 4
"""


def assignment(home: Path) -> Path:
    directory = home / "assignments" / "test"
    directory.mkdir(parents=True)
    (directory / "qa.md").write_text(QA)
    (directory / "submissions").mkdir()
    return directory


async def until(predicate, timeout=8):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.mark.skipif(os.name != "posix", reason="POSIX PTY")
def test_shell_reconnect_preserves_process_and_reclaims_only_idle(tmp_path):
    directory = assignment(tmp_path)

    async def run():
        manager = TerminalManager(tmp_path, grace_seconds=1)
        try:
            record = await manager.create("test")
            await manager.start(record.id)
            session = manager.get(record.id)
            process = session.process
            pid = process.process.pid
            await until(lambda: process.at_prompt)
            queue, _ = await manager.attach(record.id, 90, 20)
            await process.write(b"printf 'reconnect-marker\\n'\r")
            await until(
                lambda: (
                    process.at_prompt
                    and b"reconnect-marker\r\n" in session.history.value()
                )
            )
            await manager.detach(record.id, queue)
            queue, snapshot = await manager.attach(record.id, 90, 20)
            assert b"reconnect-marker\r\n" in snapshot
            assert process.process.pid == pid
            with pytest.raises(SessionError, match="another connection"):
                await manager.attach(record.id, 90, 20)
            # An old socket's cleanup cannot detach the replacement socket.
            await manager.detach(record.id, asyncio.Queue())
            await manager.expire(session.detached_at + 100)
            assert session.listener is queue and session.process is process
            await manager.detach(record.id, queue)

            # A quiet foreground job and a background job must both survive expiry.
            await process.write(b"sleep 30\r")
            await until(lambda: psutil.Process(pid).children())
            await manager.expire(session.detached_at + 100)
            assert session.process is process
            await process.write(b"\x03")
            await until(lambda: process.at_prompt)
            await process.write(b"sleep 30 &\r")
            await until(lambda: process.at_prompt and psutil.Process(pid).children())
            children = psutil.Process(pid).children()
            await manager.expire(session.detached_at + 100)
            assert session.process is process
            for child in children:
                child.terminate()
            await until(lambda: not psutil.Process(pid).children())
            await process.write(b"cd submissions\r")
            await until(
                lambda: (
                    process.at_prompt and process.cwd == str(directory / "submissions")
                )
            )
            await manager.expire(session.detached_at + 100)
            assert session.process is None
            assert not psutil.pid_exists(pid)
            assert record.status == "stopped"
        finally:
            await manager.close()
        restored = TerminalManager(tmp_path)
        try:
            session = restored.get(record.id)
            assert session.process is None
            assert b"reconnect-marker\r\n" in session.history.value()
            assert session.record.cwd == str(directory / "submissions")
            await restored.start(record.id)
            await until(lambda: session.process.at_prompt)
            assert session.process.cwd == str(directory / "submissions")
        finally:
            await restored.close()

    asyncio.run(run())


def test_catalog_caps_and_slow_consumer_do_not_grow_without_bound(tmp_path):
    assignment(tmp_path)

    async def run():
        manager = TerminalManager(tmp_path, max_saved=2, max_live=1)
        try:
            first = await manager.create("test")
            second = await manager.create("test")
            with pytest.raises(SessionError, match="Remove an old session"):
                await manager.create("test")
            await manager.start(first.id)
            with pytest.raises(SessionError, match="Stop a running session"):
                await manager.start(second.id)
            session = manager.get(first.id)
            queue, _ = await manager.attach(first.id, 80, 24)
            session.history = OutputHistory(limit=12)
            for _ in range(70):
                session.emit(b"0123456789")
            assert session.listener is None
            assert queue.qsize() == 1 and await queue.get() is None
            assert session.history.value() == b"890123456789"
            assert session.process is not None
            with pytest.raises(SessionError, match="Stop this session"):
                await manager.remove(first.id)
        finally:
            await manager.close()

    asyncio.run(run())


def test_terminal_catalog_defaults_to_ten_saved_sessions(tmp_path):
    assignment(tmp_path)

    async def run():
        manager = TerminalManager(tmp_path)
        try:
            assert manager.max_live == manager.max_saved == 10
            for _ in range(10):
                await manager.create("test")
            with pytest.raises(SessionError, match="Limit: 10"):
                await manager.create("test")
        finally:
            await manager.close()

    asyncio.run(run())


def test_http_catalog_does_not_spawn_and_socket_validates_ownership(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assignment_id = client.post("/api/assignments", json={"qa_markdown": QA}).json()[
            "id"
        ]
        created = client.post(
            f"/api/assignments/{assignment_id}/terminals", json={"kind": "shell"}
        )
        assert created.status_code == 201
        record = created.json()
        manager = app.state.terminals
        assert manager.get(record["id"]).process is None
        assert client.get(
            f"/api/assignments/{assignment_id}/terminals"
        ).json() == {"sessions": [record], "max_sessions": 10}
        assert (
            client.post(
                "/api/assignments/missing/terminals", json={"kind": "shell"}
            ).status_code
            == 404
        )
        assert (
            client.post(
                f"/api/terminals/{record['id']}/start", json={"cols": 0}
            ).status_code
            == 422
        )
        with client.websocket_connect(f"/api/terminals/{record['id']}/ws") as socket:
            with pytest.raises(WebSocketDisconnect) as stopped:
                socket.receive_json()
            assert stopped.value.code == 4409
        with pytest.raises(WebSocketDisconnect) as foreign:
            with client.websocket_connect(
                f"/api/terminals/{record['id']}/ws",
                headers={"origin": "https://unrelated.example"},
            ):
                pass
        assert foreign.value.code == 1008
        assert (
            client.post(f"/api/terminals/{record['id']}/start", json={}).status_code
            == 200
        )
        assert client.delete(f"/api/assignments/{assignment_id}").status_code == 409
        with client.websocket_connect(f"/api/terminals/{record['id']}/ws") as socket:
            assert socket.receive_json()["type"] == "snapshot"
            socket.send_json(["resize", {"width": -1, "height": 20}])
            while True:
                event = socket.receive()
                if event["type"] == "websocket.close":
                    assert event["code"] == 1008
                    break
        assert client.post(f"/api/terminals/{record['id']}/stop").status_code == 200
        assert client.delete(f"/api/terminals/{record['id']}").status_code == 204


@pytest.mark.skipif(os.name != "posix", reason="POSIX PTY")
def test_assignment_deletion_requires_confirmation_to_stop_terminals(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assignment_id = client.post(
            "/api/assignments", json={"qa_markdown": QA}
        ).json()["id"]
        record = client.post(
            f"/api/assignments/{assignment_id}/terminals"
        ).json()
        started = client.post(f"/api/terminals/{record['id']}/start", json={})
        assert started.status_code == 200
        process = app.state.terminals.get(record["id"]).process
        assert process is not None
        pid = process.process.pid

        assert client.delete(f"/api/assignments/{assignment_id}").status_code == 409
        assert psutil.pid_exists(pid)

        response = client.delete(
            f"/api/assignments/{assignment_id}",
            params={"stop_terminal_sessions": True},
        )
        assert response.status_code == 204
        assert not psutil.pid_exists(pid)
        assert app.state.terminals.get(record["id"]).process is None
        assert client.get(f"/api/assignments/{assignment_id}/qa").status_code == 404


def test_terminal_names_and_order_survive_restart_and_reject_stale_lists(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assignment_id = client.post("/api/assignments", json={"qa_markdown": QA}).json()["id"]
        records = [
            client.post(f"/api/assignments/{assignment_id}/terminals").json()
            for _ in range(3)
        ]
        ids = [record["id"] for record in records]
        assert (
            client.post(
                f"/api/assignments/{assignment_id}/terminals", json={"kind": "tui"}
            ).status_code
            == 422
        )
        url = f"/api/terminals/{ids[0]}"
        assert client.patch(url, json={"title": "   "}).status_code == 422
        assert client.patch(url, json={"title": "bad\nname"}).status_code == 422
        renamed = client.patch(url, json={"title": "  Extraction  "})
        assert renamed.status_code == 200
        assert renamed.json()["title"] == "Extraction"
        order = [ids[2], ids[0], ids[1]]
        response = client.put(
            f"/api/assignments/{assignment_id}/terminals/order", json={"ids": order}
        )
        assert response.status_code == 200
        assert [record["id"] for record in response.json()] == order
        for invalid in [ids[:2], [ids[0]] * 3, [ids[0], ids[1], "f" * 32]]:
            assert (
                client.put(
                    f"/api/assignments/{assignment_id}/terminals/order",
                    json={"ids": invalid},
                ).status_code
                == 409
            )
        assert [
            record["id"]
            for record in client.get(
                f"/api/assignments/{assignment_id}/terminals"
            ).json()["sessions"]
        ] == order

    # Old browser-TUI entries must not prevent saved shells from loading.
    catalog = tmp_path / ".opengrader" / "terminals" / "sessions.json"
    stored = json.loads(catalog.read_text())
    stored.append({**records[0], "id": "e" * 32, "kind": "tui"})
    catalog.write_text(json.dumps(stored))
    with TestClient(create_app(tmp_path)) as client:
        restored = client.get(f"/api/assignments/{assignment_id}/terminals").json()[
            "sessions"
        ]
        assert [record["id"] for record in restored] == order
        assert restored[1]["title"] == "Extraction"
        assert all(record["status"] == "stopped" for record in restored)


def test_metadata_save_failure_restores_in_memory_state(tmp_path, monkeypatch):
    assignment(tmp_path)

    async def run():
        manager = TerminalManager(tmp_path)
        first = await manager.create("test")
        second = await manager.create("test")
        original_title = first.title

        def fail():
            raise OSError("Disk full")

        with monkeypatch.context() as patch:
            patch.setattr(manager.store, "save", fail)
            with pytest.raises(OSError):
                await manager.rename(first.id, "Unsaved name")
            with pytest.raises(OSError):
                await manager.reorder("test", [second.id, first.id])
        assert first.title == original_title
        assert [record.id for record in manager.list_for_assignment("test")] == [
            first.id,
            second.id,
        ]
        await manager.close()

    asyncio.run(run())
