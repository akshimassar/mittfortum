"""Tests for sensor platform setup."""

from types import SimpleNamespace
from unittest.mock import Mock

from custom_components.fortum.const import (
    CONF_DEBUG_ENTITIES,
    CONF_REGION,
    DEFAULT_REGION,
    DOMAIN,
)
from custom_components.fortum.sensor import async_setup_entry
from custom_components.fortum.sensors import (
    FortumNorgesprisConsumptionLimitSensor,
    FortumPriceSensor,
    FortumStatisticsLastSyncSensor,
    FortumTomorrowMaxPriceSensor,
    FortumTomorrowMaxPriceTimeSensor,
)


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

    def _async_add_entities(new_entities, update_before_add=False):
        captured_entities.extend(new_entities)

    await async_setup_entry(mock_hass, entry, _async_add_entities)

    assert not any(
        isinstance(entity, FortumStatisticsLastSyncSensor)
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

    def _async_add_entities(new_entities, update_before_add=False):
        captured_entities.extend(new_entities)

    await async_setup_entry(mock_hass, entry, _async_add_entities)

    assert any(
        isinstance(entity, FortumStatisticsLastSyncSensor)
        for entity in captured_entities
    )


async def test_sensor_setup_creates_spot_entities_per_price_area(mock_hass) -> None:
    """Spot-price entities should be created per explicit area code."""
    entry = Mock()
    entry.entry_id = "entry-id"
    entry.data = {CONF_REGION: DEFAULT_REGION}
    entry.options = {CONF_DEBUG_ENTITIES: False}

    api_client = Mock()
    api_client.get_price_areas.return_value = ["SE3", "SE4"]

    mock_hass.data = {
        DOMAIN: {
            entry.entry_id: {
                "coordinator": Mock(),
                "price_coordinator": Mock(),
                "device": Mock(),
                "api_client": api_client,
                "metering_points": [],
            }
        }
    }

    captured_entities = []

    def _async_add_entities(new_entities, update_before_add=False):
        captured_entities.extend(new_entities)

    await async_setup_entry(mock_hass, entry, _async_add_entities)

    assert (
        len(
            [
                entity
                for entity in captured_entities
                if isinstance(entity, FortumPriceSensor)
            ]
        )
        == 2
    )
    assert (
        len(
            [
                entity
                for entity in captured_entities
                if isinstance(entity, FortumTomorrowMaxPriceSensor)
            ]
        )
        == 2
    )
    assert (
        len(
            [
                entity
                for entity in captured_entities
                if isinstance(entity, FortumTomorrowMaxPriceTimeSensor)
            ]
        )
        == 2
    )


async def test_sensor_setup_creates_norgespris_sensor_for_norway(mock_hass) -> None:
    """Norgespris sensor should be created only when configured region is Norway."""
    entry = Mock()
    entry.entry_id = "entry-id"
    entry.data = {CONF_REGION: "no"}
    entry.options = {CONF_DEBUG_ENTITIES: False}

    metering_point = Mock()
    metering_point.metering_point_no = "6094111"
    metering_point.norgespris_consumption_limit = 4000.0

    mock_hass.data = {
        DOMAIN: {
            entry.entry_id: {
                "coordinator": Mock(),
                "price_coordinator": Mock(),
                "device": Mock(),
                "session_manager": Mock(
                    get_snapshot=Mock(
                        return_value=SimpleNamespace(
                            metering_points=(metering_point,),
                        )
                    )
                ),
            }
        }
    }

    captured_entities = []

    def _async_add_entities(new_entities, update_before_add=False):
        captured_entities.extend(new_entities)

    await async_setup_entry(mock_hass, entry, _async_add_entities)

    assert any(
        isinstance(entity, FortumNorgesprisConsumptionLimitSensor)
        for entity in captured_entities
    )


async def test_sensor_setup_does_not_create_norgespris_sensor_outside_norway(
    mock_hass,
) -> None:
    """Norgespris sensor should not be created for non-Norway regions."""
    entry = Mock()
    entry.entry_id = "entry-id"
    entry.data = {CONF_REGION: "fi"}
    entry.options = {CONF_DEBUG_ENTITIES: False}

    metering_point = Mock()
    metering_point.metering_point_no = "6094111"
    metering_point.norgespris_consumption_limit = 4000.0

    mock_hass.data = {
        DOMAIN: {
            entry.entry_id: {
                "coordinator": Mock(),
                "price_coordinator": Mock(),
                "device": Mock(),
                "session_manager": Mock(
                    get_snapshot=Mock(
                        return_value=SimpleNamespace(
                            metering_points=(metering_point,),
                        )
                    )
                ),
            }
        }
    }

    captured_entities = []

    def _async_add_entities(new_entities, update_before_add=False):
        captured_entities.extend(new_entities)

    await async_setup_entry(mock_hass, entry, _async_add_entities)

    assert not any(
        isinstance(entity, FortumNorgesprisConsumptionLimitSensor)
        for entity in captured_entities
    )
