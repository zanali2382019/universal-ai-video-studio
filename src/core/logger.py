"""Structured and contextual logging for Universal AI Video Production Studio."""
import sys
import json
import logging
from datetime import datetime, timezone
from contextvars import ContextVar
from contextlib import contextmanager
from typing import Optional, Dict, Any, Generator
from pathlib import Path

# Context variables to track execution scope across async and synchronous flows
_current_channel_id: ContextVar[Optional[str]] = ContextVar("current_channel_id", default=None)
_current_project_id: ContextVar[Optional[str]] = ContextVar("current_project_id", default=None)
_current_task_id: ContextVar[Optional[str]] = ContextVar("current_task_id", default=None)


def set_log_context(
    channel_id: Optional[str] = None,
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> None:
    """Set execution context for structured log entries."""
    if channel_id is not None:
        _current_channel_id.set(channel_id)
    if project_id is not None:
        _current_project_id.set(project_id)
    if task_id is not None:
        _current_task_id.set(task_id)


def clear_log_context() -> None:
    """Clear execution context."""
    _current_channel_id.set(None)
    _current_project_id.set(None)
    _current_task_id.set(None)


@contextmanager
def log_context(
    channel_id: Optional[str] = None,
    project_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Generator[None, None, None]:
    """Context manager to scoped-bind execution context, restoring previous context on exit."""
    old_channel = _current_channel_id.get()
    old_project = _current_project_id.get()
    old_task = _current_task_id.get()

    if channel_id is not None:
        _current_channel_id.set(channel_id)
    if project_id is not None:
        _current_project_id.set(project_id)
    if task_id is not None:
        _current_task_id.set(task_id)

    try:
        yield
    finally:
        _current_channel_id.set(old_channel)
        _current_project_id.set(old_project)
        _current_task_id.set(old_task)


class JSONLogFormatter(logging.Formatter):
    """Formats log records as structured single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "channel_id": _current_channel_id.get(),
            "project_id": _current_project_id.get(),
            "task_id": _current_task_id.get(),
            "file": record.filename,
            "line": record.lineno,
        }

        # Include custom extra attributes if present
        if hasattr(record, "extra_data") and isinstance(record.extra_data, dict):
            log_entry["data"] = record.extra_data

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False)


class ConsoleLogFormatter(logging.Formatter):
    """Formats log records for clear human reading on terminal."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now().strftime("%H:%M:%S")
        ctx_parts = []
        ch = _current_channel_id.get()
        proj = _current_project_id.get()
        if ch:
            ctx_parts.append(f"ch={ch}")
        if proj:
            ctx_parts.append(f"proj={proj}")

        ctx_str = f" [{', '.join(ctx_parts)}]" if ctx_parts else ""
        return f"{timestamp} [{record.levelname:<5}] {record.name}{ctx_str}: {record.getMessage()}"


def get_logger(name: str, log_dir: Optional[Path] = None, log_level: str = "INFO") -> logging.Logger:
    """Configure and return a structured logger instance."""
    logger = logging.getLogger(name)

    # Avoid duplicate handlers if already configured
    if logger.handlers:
        return logger

    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.propagate = False

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ConsoleLogFormatter())
    logger.addHandler(console_handler)

    # Optional File Handler with JSON formatting
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "studio.log", encoding="utf-8")
        file_handler.setFormatter(JSONLogFormatter())
        logger.addHandler(file_handler)

    return logger

