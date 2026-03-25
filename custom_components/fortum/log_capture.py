"""In-memory log capture for Fortum diagnostics."""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

from .const import DOMAIN

LOG_LINE_RETENTION = 2000
_LOG_NAMESPACE = f"custom_components.{DOMAIN}"
_LOGGER_BUFFER_KEY = f"{DOMAIN}_diagnostics_log_handler"


class FortumDiagnosticsLogHandler(logging.Handler):
    """Logging handler storing a bounded list of recent integration log lines."""

    def __init__(self, max_lines: int = LOG_LINE_RETENTION) -> None:
        """Initialize bounded log record handler."""
        super().__init__(level=logging.NOTSET)
        self._records: deque[dict[str, Any]] = deque(maxlen=max_lines)

    def emit(self, record: logging.LogRecord) -> None:
        """Store log records as structured dictionaries."""
        exc_text: str | None = None
        if record.exc_info:
            formatter = logging.Formatter()
            exc_text = formatter.formatException(record.exc_info)

        self._records.append(
            {
                "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
                "logger": record.name,
                "level": record.levelname,
                "message": record.getMessage(),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
                "exception": exc_text,
            }
        )

    def snapshot(self) -> list[dict[str, Any]]:
        """Return a copy of captured records."""
        return list(self._records)


def ensure_diagnostics_log_capture(hass: Any) -> None:
    """Attach a bounded in-memory log capture handler for diagnostics."""
    existing = hass.data.get(_LOGGER_BUFFER_KEY)
    if isinstance(existing, FortumDiagnosticsLogHandler):
        return

    logger = logging.getLogger(_LOG_NAMESPACE)
    handler = FortumDiagnosticsLogHandler()
    logger.addHandler(handler)
    hass.data[_LOGGER_BUFFER_KEY] = handler


def remove_diagnostics_log_capture(hass: Any) -> None:
    """Remove diagnostics log capture handler if present."""
    handler = hass.data.pop(_LOGGER_BUFFER_KEY, None)
    if not isinstance(handler, FortumDiagnosticsLogHandler):
        return

    logger = logging.getLogger(_LOG_NAMESPACE)
    logger.removeHandler(handler)
    handler.close()


def get_diagnostics_log_snapshot(hass: Any) -> list[dict[str, Any]]:
    """Return captured diagnostics log lines."""
    handler = hass.data.get(_LOGGER_BUFFER_KEY)
    if not isinstance(handler, FortumDiagnosticsLogHandler):
        return []
    return handler.snapshot()
