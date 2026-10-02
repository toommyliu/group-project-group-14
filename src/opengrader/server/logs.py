import logging
import logging.config
import secrets
import sys
from pathlib import Path
from time import perf_counter

import structlog
from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, Response
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from structlog.typing import EventDict

from opengrader.server.settings import LogLevel

LOG_FILE = Path(".opengrader/logs/server.jsonl")
_SLOW_MS = 1000.0

logger = structlog.stdlib.get_logger(__name__)


def _lead(_logger: object, _method: str, event_dict: EventDict) -> EventDict:
    first = {k: event_dict.pop(k) for k in ("timestamp", "level", "event", "logger") if k in event_dict}
    return first | event_dict


_SHARED = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
]


def configure(level: LogLevel, *, workspace: Path | None = None, detached: bool = False) -> None:
    handlers: dict[str, dict] = {}
    if not detached:
        handlers["console"] = {
            "class": "logging.StreamHandler",
            "stream": sys.stderr,
            "formatter": "console",
        }
    if workspace is not None:
        path = workspace / LOG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers["file"] = {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": str(path),
            "maxBytes": 5 * 1024 * 1024,
            "backupCount": 4,
            "encoding": "utf-8",
            "formatter": "json",
        }
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "console": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "foreign_pre_chain": _SHARED,
                    "processors": [
                        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
                        structlog.dev.ConsoleRenderer(
                            colors=sys.stderr.isatty(),
                            exception_formatter=structlog.dev.plain_traceback,
                        ),
                    ],
                },
                "json": {
                    "()": structlog.stdlib.ProcessorFormatter,
                    "foreign_pre_chain": _SHARED,
                    "processors": [
                        structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                        structlog.processors.TimeStamper(fmt="iso", utc=True),
                        structlog.processors.ExceptionRenderer(
                            structlog.tracebacks.ExceptionDictTransformer(show_locals=False)
                        ),
                        _lead,
                        structlog.processors.JSONRenderer(),
                    ],
                },
            },
            "handlers": handlers,
            "root": {"level": "WARNING", "handlers": list(handlers)},
            "loggers": {"opengrader": {"level": level.upper()}},
        }
    )
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *_SHARED,
            structlog.processors.StackInfoRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def elapsed_ms(started: float) -> float:
    return round((perf_counter() - started) * 1000, 1)


def observe(app: FastAPI) -> ASGIApp:
    app.add_exception_handler(Exception, _server_error)
    app.add_exception_handler(HTTPException, _http_error)
    return _RequestLog(app)


class _RequestLog:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=secrets.token_hex(4), method=scope["method"], path=scope["path"]
        )
        started = perf_counter()
        status = 500
        elapsed: float | None = None

        async def send_logged(message: Message) -> None:
            nonlocal status, elapsed
            if message["type"] == "http.response.start":
                status = message["status"]
                elapsed = elapsed_ms(started)
            await send(message)

        def fields() -> dict:
            client = scope.get("client")
            return {
                "route": getattr(scope.get("route"), "path", None),
                "client": client[0] if client else None,
                "duration_ms": elapsed if elapsed is not None else elapsed_ms(started),
            }

        try:
            await self.app(scope, receive, send_logged)
        except Exception:
            logger.exception("http.request", status=status, **fields())
        else:
            details = fields()
            logger.log(_level(status, details["duration_ms"]), "http.request", status=status, **details)


def _level(status: int, duration_ms: float) -> int:
    if status >= 500:
        return logging.WARNING
    if duration_ms >= _SLOW_MS:
        return logging.INFO
    return logging.DEBUG


def _with_reference(detail: object) -> str:
    reference = structlog.contextvars.get_contextvars()["request_id"]
    return (
        f"{detail} If it keeps happening, "
        f"give this reference to whoever set up Opengrader: {reference}."
    )


async def _http_error(request: Request, exc: HTTPException) -> Response:
    if exc.status_code < 500:
        return await http_exception_handler(request, exc)
    logger.error("request.failed", status=exc.status_code, exc_info=exc.__cause__ or exc)
    return JSONResponse(
        {"detail": _with_reference(exc.detail)}, status_code=exc.status_code, headers=exc.headers
    )


async def _server_error(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(
        {"detail": _with_reference("The server couldn't finish this request.")},
        status_code=500,
    )
