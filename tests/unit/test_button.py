"""Unit tests for MittFortum button entities."""

from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.mittfortum.button import MittFortumFullStatisticsSyncButton
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


async def test_full_statistics_sync_button_triggers_force_sync() -> None:
    """Button press should trigger full statistics sync."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.async_run_statistics_sync = AsyncMock(return_value=100)

    button = MittFortumFullStatisticsSyncButton(coordinator, _mock_device())
    await button.async_press()

    coordinator.async_run_statistics_sync.assert_awaited_once_with(
        rewrite=True,
        allow_historical_backfill=True,
    )


async def test_full_statistics_sync_button_surfaces_api_errors() -> None:
    """Button press should raise HomeAssistantError when API fails."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.async_run_statistics_sync = AsyncMock(side_effect=APIError("boom"))

    button = MittFortumFullStatisticsSyncButton(coordinator, _mock_device())

    with pytest.raises(HomeAssistantError, match="Full statistics sync failed"):
        await button.async_press()
