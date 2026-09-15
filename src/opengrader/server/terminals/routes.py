"""HTTP session controls and the shell socket protocol."""

import asyncio
import base64
import json
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, StringConstraints, ValidationError

from .manager import SessionError, TerminalManager
from .store import TerminalRecord


class CreateTerminal(BaseModel):
    assignment_id: str
    kind: Literal["shell"] = "shell"


class TerminalSize(BaseModel):
    cols: int = Field(default=100, ge=2, le=500, strict=True)
    rows: int = Field(default=30, ge=2, le=300, strict=True)


class RenameTerminal(BaseModel):
    title: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=1,
            max_length=80,
            pattern=r"^[^\x00-\x1f\x7f]+$",
        ),
    ]


class ReorderTerminals(BaseModel):
    assignment_id: str
    ids: list[str]


class TerminalCatalog(BaseModel):
    sessions: list[TerminalRecord]
    max_sessions: int


def add_terminal_routes(
    app: FastAPI, manager: TerminalManager, assignment_for: Callable
) -> None:
    @app.exception_handler(SessionError)
    async def session_error(_request: Request, exc: SessionError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.get("/api/terminals")
    async def list_terminals(assignment_id: str) -> TerminalCatalog:
        assignment_for(assignment_id)
        return TerminalCatalog(
            sessions=manager.list_for_assignment(assignment_id),
            max_sessions=manager.max_saved,
        )

    @app.post("/api/terminals", status_code=201)
    async def create_terminal(body: CreateTerminal) -> TerminalRecord:
        assignment_for(body.assignment_id)
        return await manager.create(body.assignment_id)

    @app.put("/api/terminals/order")
    async def reorder_terminals(body: ReorderTerminals) -> list[TerminalRecord]:
        assignment_for(body.assignment_id)
        return await manager.reorder(body.assignment_id, body.ids)

    @app.patch("/api/terminals/{session_id}")
    async def rename_terminal(session_id: str, body: RenameTerminal) -> TerminalRecord:
        return await manager.rename(session_id, body.title)

    @app.post("/api/terminals/{session_id}/start")
    async def start_terminal(session_id: str, body: TerminalSize) -> TerminalRecord:
        assignment_for(manager.get(session_id).record.assignment_id)
        return await manager.start(session_id, body.cols, body.rows)

    @app.post("/api/terminals/{session_id}/stop")
    async def stop_terminal(session_id: str) -> TerminalRecord:
        return await manager.stop(session_id)

    @app.delete("/api/terminals/{session_id}", status_code=204)
    async def remove_terminal(session_id: str) -> Response:
        await manager.remove(session_id)
        return Response(status_code=204)

    @app.websocket("/api/terminals/{session_id}/ws", name="terminal_socket")
    async def terminal_socket(socket: WebSocket, session_id: str) -> None:
        # Block unrelated websites from attaching to a localhost terminal.
        origin = socket.headers.get("origin")
        expected = f"{'https' if socket.url.scheme == 'wss' else 'http'}://{socket.headers.get('host')}"
        if origin and origin != expected:
            await socket.close(code=1008, reason="Origin does not match this server.")
            return
        await socket.accept()
        queue = None
        tasks: list[asyncio.Task] = []
        try:
            session = manager.get(session_id)
            queue, snapshot = await manager.attach(session_id, 100, 30)
            process = session.process
            assert process is not None
            await socket.send_json(
                {"type": "snapshot", "data": base64.b64encode(snapshot).decode("ascii")}
            )

            async def send_output() -> None:
                while True:
                    data = await queue.get()
                    if data is None:
                        return
                    async with asyncio.timeout(10):
                        if isinstance(data, bytes):
                            await socket.send_bytes(data)
                        else:
                            await socket.send_text(data)

            async def receive_input() -> None:
                while True:
                    raw = await socket.receive_text()
                    if len(raw) > 32768:
                        raise ValueError("Input is too large.")
                    packet = json.loads(raw)
                    if not isinstance(packet, list) or len(packet) not in (1, 2):
                        raise ValueError("Expected a terminal message.")
                    kind, data = packet[0], packet[1] if len(packet) == 2 else None
                    if kind == "stdin" and isinstance(data, str):
                        async with asyncio.timeout(10):
                            await process.write(data.encode())
                    elif kind == "resize" and isinstance(data, dict):
                        size = TerminalSize(
                            cols=data.get("width"), rows=data.get("height")
                        )
                        await process.resize(size.cols, size.rows)
                    elif kind == "ping":
                        session.emit(json.dumps(["pong", data]))
                    elif kind in ("blur", "focus"):
                        pass
                    else:
                        raise ValueError("Unknown terminal message.")

            tasks = [
                asyncio.create_task(send_output()),
                asyncio.create_task(receive_input()),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except SessionError as exc:
            await socket.close(
                code=4404 if exc.status == 404 else 4409, reason=str(exc)[:120]
            )
        except (ValueError, ValidationError):
            await socket.close(code=1008, reason="Invalid terminal message.")
        except (WebSocketDisconnect, OSError, TimeoutError):
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if queue is not None:
                await manager.detach(session_id, queue)
            try:
                await socket.close()
            except (RuntimeError, OSError):
                pass
