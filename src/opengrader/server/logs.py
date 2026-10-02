import logging
import logging.config
import sys
from pathlib import Path
from time import perf_counter

import structlog
from structlog.typing import EventDict

from opengrader.server.settings import LogLevel

LOG_FILE = Path(".opengrader/logs/server.jsonl")


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
