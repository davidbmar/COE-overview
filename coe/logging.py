"""Context-variable-based structlog capture for per-task log events.

Provides a capture_processor that feeds structlog events into a per-task
ContextVar buffer, and a context manager (capture_logs_ctx) that isolates
log capture to a single task without global state pollution.
"""

from __future__ import annotations

import contextvars
from collections.abc import Mapping
from typing import Any

import structlog

_capture_buffer: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "coe_log_capture", default=None
)


def capture_processor(
    _logger: Any, _method: str, event_dict: Mapping[str, Any]
) -> Mapping[str, Any]:
    """structlog processor that appends to the current task's capture buffer.

    If no buffer is active (not inside a capture_logs_ctx), the event passes through
    unchanged. This allows logging to function in any context without errors.

    Args:
        _logger: The structlog logger (unused).
        _method: The log method name (unused).
        event_dict: The event dict to process.

    Returns:
        The event_dict unchanged, allowing downstream processors to run.
    """
    buf = _capture_buffer.get()
    if buf is not None:
        buf.append(dict(event_dict))
    return event_dict


class CaptureLogsCtx:
    """Context manager: collect structlog events emitted within this task.

    Isolates log capture to a single task by using ContextVar, ensuring that
    sibling asyncio.gather tasks don't see each other's logs.

    Usage:
        with CaptureLogsCtx() as logs:
            log.info("event", key="value")
        # logs now contains [{"event": "event", "key": "value", ...}]
    """

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._token: contextvars.Token[list[dict[str, Any]] | None] | None = None

    def __enter__(self) -> list[dict[str, Any]]:
        """Set the capture buffer to this context's event list."""
        self._token = _capture_buffer.set(self.events)
        return self.events

    def __exit__(self, *exc: Any) -> None:
        """Reset the capture buffer to its prior state."""
        assert self._token is not None
        _capture_buffer.reset(self._token)


def configure_structlog() -> None:
    """Wire up structlog with the capture processor first.

    Chains processors in order:
    1. capture_processor — feeds events into the task's ContextVar buffer
    2. merge_contextvars — injects source, run_id, etc. from contextvars
    3. TimeStamper — adds iso-formatted timestamp
    4. add_log_level — ensures level is set
    5. JSONRenderer — serializes to JSON

    Uses INFO level filtering to suppress DEBUG noise in production.
    Disables logger caching to ensure capture_processor sees fresh logger instances.
    """
    structlog.configure(
        processors=[
            capture_processor,
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
        cache_logger_on_first_use=False,  # capture must see fresh logger
    )


# Backwards compatibility: old lowercase name still works
capture_logs_ctx = CaptureLogsCtx
