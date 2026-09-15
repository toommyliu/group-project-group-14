from contextvars import ContextVar
import logging
from time import perf_counter

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from uvicorn.logging import AccessFormatter


_response_duration: ContextVar[float | None] = ContextVar("response_duration", default=None)


class TimedAccessFormatter(AccessFormatter):
    def formatMessage(self, record: logging.LogRecord) -> str:
        message = super().formatMessage(record)
        duration = _response_duration.get()
        return message if duration is None else f"{message} {duration:.1f} ms"


class RequestTiming:
    """Measure through response headers, excluding body streaming and background work."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = perf_counter()

        async def send_timed(message: Message) -> None:
            if message["type"] != "http.response.start":
                await send(message)
                return
            # Uvicorn writes its access log inside send; keep concurrent requests isolated.
            token = _response_duration.set((perf_counter() - started) * 1000)
            try:
                await send(message)
            finally:
                _response_duration.reset(token)

        await self.app(scope, receive, send_timed)
