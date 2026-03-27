"""Unit tests for Fortum button entities."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.fortum.button import (
    FortumClearStatisticsButton,
    FortumFullHistoryResyncButton,
)
from custom_components.fortum.device import FortumDevice
from custom_components.fortum.exceptions import APIError


def _mock_device() -> Mock:
    device = Mock(spec=FortumDevice)
    device.device_info = {
        "identifiers": {("fortum", "123456")},
        "name": "Fortum Energy Meter",
        "manufacturer": "Fortum",
        "model": "Energy Meter",
    }
    return device


async def test_full_history_resync_button_triggers_force_sync() -> None:
    """Button press should trigger full history re-sync."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {}
    coordinator.async_run_statistics_sync = AsyncMock(return_value=100)

    button = FortumFullHistoryResyncButton(coordinator, _mock_device(), Mock())
    with (
        patch("custom_components.fortum.button.pause_all_sync_schedules") as mock_pause,
        patch(
            "custom_components.fortum.button.resume_all_sync_schedules"
        ) as mock_resume,
    ):
        await button.async_press()

    mock_pause.assert_called_once_with(coordinator.hass)
    mock_resume.assert_called_once_with(coordinator.hass)
    coordinator.async_run_statistics_sync.assert_awaited_once_with(
        force_resync=True,
    )


async def test_full_history_resync_button_logs_elapsed_time(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Button press log should include elapsed sync time."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {}
    coordinator.async_run_statistics_sync = AsyncMock(return_value=5)

    button = FortumFullHistoryResyncButton(coordinator, _mock_device(), Mock())

    with (
        patch("custom_components.fortum.button.pause_all_sync_schedules"),
        patch("custom_components.fortum.button.resume_all_sync_schedules"),
        patch(
            "custom_components.fortum.button.time.perf_counter",
            side_effect=[10.0, 12.345],
        ),
        caplog.at_level("INFO"),
    ):
        await button.async_press()

    assert "processed 5 points in 2.35s" in caplog.text


async def test_full_history_resync_button_surfaces_api_errors() -> None:
    """Button press should raise HomeAssistantError when API fails."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {}
    coordinator.async_run_statistics_sync = AsyncMock(side_effect=APIError("boom"))

    button = FortumFullHistoryResyncButton(coordinator, _mock_device(), Mock())

    with (
        patch("custom_components.fortum.button.pause_all_sync_schedules") as mock_pause,
        patch(
            "custom_components.fortum.button.resume_all_sync_schedules"
        ) as mock_resume,
        pytest.raises(HomeAssistantError, match="Full history re-sync failed"),
    ):
        await button.async_press()

    mock_pause.assert_called_once_with(coordinator.hass)
    mock_resume.assert_called_once_with(coordinator.hass)


async def test_clear_statistics_button_triggers_clear() -> None:
    """Button press should clear imported statistics."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {}
    coordinator.async_clear_statistics = AsyncMock(return_value=3)

    button = FortumClearStatisticsButton(coordinator, _mock_device(), Mock())
    with (
        patch("custom_components.fortum.button.pause_all_sync_schedules") as mock_pause,
        patch(
            "custom_components.fortum.button.resume_all_sync_schedules"
        ) as mock_resume,
    ):
        await button.async_press()

    mock_pause.assert_called_once_with(coordinator.hass)
    mock_resume.assert_called_once_with(coordinator.hass)
    coordinator.async_clear_statistics.assert_awaited_once_with()


async def test_clear_statistics_button_surfaces_api_errors() -> None:
    """Button press should raise HomeAssistantError when clear fails."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {}
    coordinator.async_clear_statistics = AsyncMock(side_effect=APIError("boom"))

    button = FortumClearStatisticsButton(coordinator, _mock_device(), Mock())

    with (
        patch("custom_components.fortum.button.pause_all_sync_schedules") as mock_pause,
        patch(
            "custom_components.fortum.button.resume_all_sync_schedules"
        ) as mock_resume,
        pytest.raises(HomeAssistantError, match="Clear statistics failed"),
    ):
        await button.async_press()

    mock_pause.assert_called_once_with(coordinator.hass)
    mock_resume.assert_called_once_with(coordinator.hass)


def test_buttons_available_with_authenticated_session() -> None:
    """Debug buttons should be available with valid session/auth."""
    coordinator = Mock()
    coordinator.last_update_success = False
    coordinator.data = None
    coordinator.hass = Mock()
    coordinator.hass.data = {
        "fortum": {
            "entry_1": {
                "session_manager": Mock(
                    get_snapshot=Mock(
                        return_value=Mock(
                            customer_id="123",
                            metering_points=(),
                        )
                    )
                )
            }
        }
    }
    entry = Mock(entry_id="entry_1")

    full_sync = FortumFullHistoryResyncButton(coordinator, _mock_device(), entry)
    clear_stats = FortumClearStatisticsButton(coordinator, _mock_device(), entry)

    assert full_sync.available is True
    assert clear_stats.available is True


def test_buttons_unavailable_without_session_snapshot() -> None:
    """Debug buttons should be unavailable without SessionManager snapshot."""
    coordinator = Mock()
    coordinator.last_update_success = False
    coordinator.data = None
    coordinator.hass = Mock()
    coordinator.hass.data = {
        "fortum": {
            "entry_1": {
                "session_manager": Mock(get_snapshot=Mock(return_value=None)),
            }
        }
    }
    entry = Mock(entry_id="entry_1")

    full_sync = FortumFullHistoryResyncButton(coordinator, _mock_device(), entry)
    clear_stats = FortumClearStatisticsButton(coordinator, _mock_device(), entry)

    assert full_sync.available is False
    assert clear_stats.available is False
