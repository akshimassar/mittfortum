"""Unit tests for MittFortum button entities."""

from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.mittfortum.button import (
    MittFortumClearStatisticsButton,
    MittFortumFullHistoryResyncButton,
)
from custom_components.mittfortum.device import MittFortumDevice
from custom_components.mittfortum.exceptions import APIError


def _mock_device() -> Mock:
    device = Mock(spec=MittFortumDevice)
    device.device_info = {
        "identifiers": {("mittfortum", "123456")},
        "name": "Mittfortum Energy Meter",
        "manufacturer": "Fortum",
        "model": "Energy Meter",
    }
    return device


async def test_full_history_resync_button_triggers_force_sync() -> None:
    """Button press should trigger full history re-sync."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.async_run_statistics_sync = AsyncMock(return_value=100)

    button = MittFortumFullHistoryResyncButton(coordinator, _mock_device())
    await button.async_press()

    coordinator.async_run_statistics_sync.assert_awaited_once_with(
        force_resync=True,
    )


async def test_full_history_resync_button_surfaces_api_errors() -> None:
    """Button press should raise HomeAssistantError when API fails."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.async_run_statistics_sync = AsyncMock(side_effect=APIError("boom"))

    button = MittFortumFullHistoryResyncButton(coordinator, _mock_device())

    with pytest.raises(HomeAssistantError, match="Full history re-sync failed"):
        await button.async_press()


async def test_clear_statistics_button_triggers_clear() -> None:
    """Button press should clear imported statistics."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.async_clear_statistics = AsyncMock(return_value=3)

    button = MittFortumClearStatisticsButton(coordinator, _mock_device())
    await button.async_press()

    coordinator.async_clear_statistics.assert_awaited_once_with()


async def test_clear_statistics_button_surfaces_api_errors() -> None:
    """Button press should raise HomeAssistantError when clear fails."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.async_clear_statistics = AsyncMock(side_effect=APIError("boom"))

    button = MittFortumClearStatisticsButton(coordinator, _mock_device())

    with pytest.raises(HomeAssistantError, match="Clear statistics failed"):
        await button.async_press()
