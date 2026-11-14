"""Structured, privacy-conscious logging for Gatherly."""

from contextvars import ContextVar, Token
from datetime import UTC, datetime
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import traceback
from typing import Any


_request_id: ContextVar[str | None] = ContextVar("gatherly_request_id", default=None)
_configured = False

_SAFE_EXTRA_FIELDS = (
    "event",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "exception_type",
    "exception_frames",
    "component",
    "notifications_created",
    "emails_sent",
)


def bind_request_id(request_id: str) -> Token[str | None]:
    """Bind a server-generated request ID to the current execution context."""
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the previous request context."""
    _request_id.reset(token)


def safe_exception_frames(error: BaseException) -> list[dict[str, str | int]]:
    """Return code locations without exception messages or source-line contents."""
    return [
        {
            "file": Path(frame.filename).name,
            "line": frame.lineno,
            "function": frame.name,
        }
        for frame in traceback.extract_tb(error.__traceback__)[-8:]
    ]


class _RequestContextFilter(logging.Filter):
    """Attach the current request ID to application log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            setattr(record, "request_id", _request_id.get())
        return True


class _JsonFormatter(logging.Formatter):
    """Format logs as one JSON object per line using an explicit field allowlist."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = getattr(record, "request_id", None)
        if request_id is not None:
            payload["request_id"] = request_id
        for field in _SAFE_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_application_logging() -> None:
    """Configure bounded JSON logs once; never log request bodies or credentials."""
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    if root.level > logging.INFO:
        root.setLevel(logging.INFO)

    formatter = _JsonFormatter()
    context_filter = _RequestContextFilter()
    handlers: list[logging.Handler] = []

    try:
        directory = Path(
            os.environ.get("GATHERLY_LOG_DIRECTORY", "var/logs")
        ).expanduser()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        file_handler = RotatingFileHandler(
            directory / "gatherly.log",
            maxBytes=5_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        try:
            (directory / "gatherly.log").chmod(0o600)
        except OSError:
            pass
        handlers.append(file_handler)
    except OSError:
        # Keep the application available if persistent log storage is unavailable.
        pass

    handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.setLevel(logging.INFO)
        handler.setFormatter(formatter)
        handler.addFilter(context_filter)
        setattr(handler, "_gatherly_handler", True)
        root.addHandler(handler)

    _configured = True
    if len(handlers) == 1:
        root.warning(
            "Persistent log file unavailable; using standard error",
            extra={"event": "logging.file_unavailable", "component": "observability"},
        )
