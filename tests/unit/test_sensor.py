"""Tests for sensor platform setup."""

from unittest.mock import Mock

from custom_components.mittfortum.const import (
    CONF_DEBUG_ENTITIES,
    CONF_REGION,
    DEFAULT_REGION,
    DOMAIN,
)
from custom_components.mittfortum.sensor import async_setup_entry
from custom_components.mittfortum.sensors import MittFortumStatisticsLastSyncSensor


async def test_sensor_setup_excludes_statistics_last_sync_when_debug_disabled(
    mock_hass,
) -> None:
    """Statistics-last-sync sensor should not be created when debug entities are off."""
    entry = Mock()
    entry.entry_id = "entry-id"
    entry.data = {CONF_REGION: DEFAULT_REGION}
    entry.options = {CONF_DEBUG_ENTITIES: False}

    mock_hass.data = {
        DOMAIN: {
            entry.entry_id: {
                "coordinator": Mock(),
                "price_coordinator": Mock(),
                "device": Mock(),
                "metering_points": [],
            }
        }
    }

    captured_entities = []

    def _async_add_entities(entities, update_before_add=False):
        captured_entities.extend(entities)

    await async_setup_entry(mock_hass, entry, _async_add_entities)

    assert not any(
        isinstance(entity, MittFortumStatisticsLastSyncSensor)
        for entity in captured_entities
    )


async def test_sensor_setup_includes_statistics_last_sync_when_debug_enabled(
    mock_hass,
) -> None:
    """Statistics-last-sync sensor should be created when debug entities are on."""
    entry = Mock()
    entry.entry_id = "entry-id"
    entry.data = {CONF_REGION: DEFAULT_REGION}
    entry.options = {CONF_DEBUG_ENTITIES: True}

    mock_hass.data = {
        DOMAIN: {
            entry.entry_id: {
                "coordinator": Mock(),
                "price_coordinator": Mock(),
                "device": Mock(),
                "metering_points": [],
            }
        }
    }

    captured_entities = []

    def _async_add_entities(entities, update_before_add=False):
        captured_entities.extend(entities)

    await async_setup_entry(mock_hass, entry, _async_add_entities)

    assert any(
        isinstance(entity, MittFortumStatisticsLastSyncSensor)
        for entity in captured_entities
    )
