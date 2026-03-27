"""Tests for sensor platform setup."""

from unittest.mock import AsyncMock, Mock

from custom_components.fortum.const import (
    CONF_REGION,
    DEFAULT_REGION,
    DOMAIN,
)
from custom_components.fortum.sensor import async_setup_entry


async def test_sensor_setup_delegates_to_session_manager(mock_hass) -> None:
    """Sensor platform should delegate entity setup to SessionManager."""
    entry = Mock()
    entry.entry_id = "entry-id"
    entry.data = {CONF_REGION: DEFAULT_REGION}
    entry.options = {}

    session_manager = Mock(async_setup_sensor_platform=AsyncMock())
    coordinator = Mock()
    price_coordinator = Mock()
    device = Mock()

    mock_hass.data = {
        DOMAIN: {
            entry.entry_id: {
                "coordinator": coordinator,
                "price_coordinator": price_coordinator,
                "device": device,
                "session_manager": session_manager,
            }
        }
    }

    def _async_add_entities(new_entities, update_before_add=False):
        return None

    await async_setup_entry(mock_hass, entry, _async_add_entities)

    session_manager.async_setup_sensor_platform.assert_awaited_once_with(
        _async_add_entities,
        coordinator=coordinator,
        price_coordinator=price_coordinator,
        device=device,
        region=DEFAULT_REGION,
    )


async def test_sensor_setup_uses_coordinator_as_price_fallback(mock_hass) -> None:
    """Sensor platform should pass coordinator as price fallback."""
    entry = Mock()
    entry.entry_id = "entry-id"
    entry.data = {CONF_REGION: "no"}
    entry.options = {}

    session_manager = Mock(async_setup_sensor_platform=AsyncMock())
    coordinator = Mock()
    device = Mock()

    mock_hass.data = {
        DOMAIN: {
            entry.entry_id: {
                "coordinator": coordinator,
                "device": device,
                "session_manager": session_manager,
            }
        }
    }

    def _async_add_entities(new_entities, update_before_add=False):
        return None

    await async_setup_entry(mock_hass, entry, _async_add_entities)

    session_manager.async_setup_sensor_platform.assert_awaited_once_with(
        _async_add_entities,
        coordinator=coordinator,
        price_coordinator=coordinator,
        device=device,
        region="no",
    )
