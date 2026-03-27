"""Tests for Fortum diagnostics support."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from custom_components.fortum.const import DOMAIN
from custom_components.fortum.diagnostics import async_get_config_entry_diagnostics
from custom_components.fortum.log_capture import (
    LOG_LINE_RETENTION,
    ensure_diagnostics_log_capture,
    get_diagnostics_log_snapshot,
    remove_diagnostics_log_capture,
)


@pytest.fixture
def clean_log_capture(mock_hass):
    """Ensure diagnostics log handler is detached after each test."""
    yield
    remove_diagnostics_log_capture(mock_hass)


class TestDiagnostics:
    """Test diagnostics payload and sensitive-data handling."""

    async def test_config_entry_diagnostics_redacts_sensitive_data(
        self,
        mock_hass,
        clean_log_capture,
    ):
        """Diagnostics should redact credentials and token-like log values."""
        ensure_diagnostics_log_capture(mock_hass)
        logger = logging.getLogger("custom_components.fortum")
        logger.warning(
            "request failed authorization=Bearer super-secret access_token=abc123"
        )

        entry = Mock()
        entry.entry_id = "entry-1"
        entry.data = {
            "username": "me@example.com",
            "password": "very-secret",
            "region": "fi",
        }
        entry.options = {"debug_logging": True}

        coordinator = SimpleNamespace(
            last_update_success=True,
            last_statistics_sync=datetime(2026, 3, 25),
            update_interval=timedelta(minutes=30),
        )
        price_coordinator = SimpleNamespace(
            last_update_success=False,
            last_statistics_sync=None,
            update_interval=timedelta(minutes=5),
        )
        mock_hass.data[DOMAIN] = {
            entry.entry_id: {
                "coordinator": coordinator,
                "price_coordinator": price_coordinator,
                "session_manager": SimpleNamespace(
                    get_snapshot=lambda: SimpleNamespace(
                        metering_points=(Mock(), Mock())
                    )
                ),
                "api_client": object(),
            }
        }

        diagnostics = await async_get_config_entry_diagnostics(mock_hass, entry)

        diagnostics_text = str(diagnostics)
        assert "very-secret" not in diagnostics_text
        assert "super-secret" not in diagnostics_text
        assert "abc123" not in diagnostics_text
        assert diagnostics["entry"]["data"]["password"] != "very-secret"
        assert diagnostics["entry"]["data"]["username"] != "me@example.com"
        assert diagnostics["runtime"]["metering_points_count"] == 2
        assert diagnostics["runtime"]["coordinator"]["last_update_success"] is True
        assert (
            diagnostics["runtime"]["price_coordinator"]["last_update_success"] is False
        )
        assert diagnostics["recent_logs"]
        assert "[REDACTED]" in diagnostics["recent_logs"][-1]["message"]

    async def test_diagnostics_logs_are_capped(self, mock_hass, clean_log_capture):
        """Log capture should keep only the configured retention window."""
        ensure_diagnostics_log_capture(mock_hass)
        logger = logging.getLogger("custom_components.fortum.api.client")

        for idx in range(LOG_LINE_RETENTION + 10):
            logger.warning("line %d", idx)

        logs = get_diagnostics_log_snapshot(mock_hass)
        assert len(logs) == LOG_LINE_RETENTION
        assert logs[0]["message"].startswith("test_diagnostics_logs_are_capped:")
        assert logs[0]["message"].endswith("line 10")
        assert logs[-1]["message"].endswith(f"line {LOG_LINE_RETENTION + 9}")

    async def test_diagnostics_handles_missing_runtime(
        self, mock_hass, clean_log_capture
    ):
        """Diagnostics should work even when runtime entry data is unavailable."""
        ensure_diagnostics_log_capture(mock_hass)
        entry = Mock()
        entry.entry_id = "missing"
        entry.data = {"username": "test@example.com", "password": "pw", "region": "se"}
        entry.options = {}

        diagnostics = await async_get_config_entry_diagnostics(mock_hass, entry)

        assert diagnostics["runtime"]["metering_points_count"] == 0
        assert diagnostics["runtime"]["coordinator"] == {}
        assert diagnostics["runtime"]["price_coordinator"] == {}
