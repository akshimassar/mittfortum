"""Diagnostics support for the Fortum integration."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import CONF_REGION, DOMAIN
from .log_capture import get_diagnostics_log_snapshot

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    "access_token",
    "refresh_token",
    "id_token",
    "accessToken",
    "refreshToken",
    "idToken",
    "token",
    "authorization",
    "Authorization",
    "cookie",
    "cookies",
    "set-cookie",
    "session",
    "session_data",
    "session_cookies",
    "customerId",
    "customer_id",
}

_MESSAGE_REDACTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)\b(Bearer)\s+([^\s,;]+)"),
        r"\1 [REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b(authorization|access_token|refresh_token|id_token|token|"
            r"password|cookie|set-cookie|csrftoken)\b\s*[:=]\s*"
            r"(?:Bearer\s+)?([^\s,;]+)"
        ),
        r"\1=[REDACTED]",
    ),
    (
        re.compile(
            r'(?i)("(?:authorization|access_token|refresh_token|id_token|token|'
            r'password|cookie|set-cookie|csrftoken)"\s*:\s*")([^"]+)(")'
        ),
        r"\1[REDACTED]\3",
    ),
)


def _redact_message(message: str) -> str:
    """Redact sensitive values from plain-text log messages."""
    redacted = message
    for pattern, replacement in _MESSAGE_REDACTION_PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def _coordinator_summary(coordinator: Any) -> dict[str, Any]:
    """Return a diagnostics-safe coordinator status summary."""
    if coordinator is None:
        return {}

    interval = getattr(coordinator, "update_interval", None)
    interval_seconds = interval.total_seconds() if interval is not None else None
    return {
        "last_update_success": getattr(coordinator, "last_update_success", None),
        "last_statistics_sync": getattr(coordinator, "last_statistics_sync", None),
        "update_interval_seconds": interval_seconds,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    if not isinstance(entry_data, dict):
        entry_data = {}

    session_manager = entry_data.get("session_manager")
    snapshot = session_manager.get_snapshot() if session_manager is not None else None
    metering_points_count = len(snapshot.metering_points) if snapshot else 0

    raw_logs = get_diagnostics_log_snapshot(hass)
    safe_logs = []
    for row in raw_logs:
        safe_row = async_redact_data(dict(row), TO_REDACT)
        message = safe_row.get("message")
        if isinstance(message, str):
            safe_row["message"] = _redact_message(message)

        exception = safe_row.get("exception")
        if isinstance(exception, str):
            safe_row["exception"] = _redact_message(exception)
        safe_logs.append(safe_row)

    return {
        "entry": {
            "entry_id": entry.entry_id,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "runtime": {
            "region": entry.data.get(CONF_REGION),
            "metering_points_count": metering_points_count,
            "has_api_client": entry_data.get("api_client") is not None,
            "coordinator": _coordinator_summary(entry_data.get("coordinator")),
            "price_coordinator": _coordinator_summary(
                entry_data.get("price_coordinator")
            ),
        },
        "recent_logs": safe_logs,
    }
