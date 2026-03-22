"""Test statistics last-sync sensor."""

from datetime import datetime
from unittest.mock import Mock

from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.mittfortum.device import MittFortumDevice
from custom_components.mittfortum.sensors.stats_last_sync import (
    MittFortumStatisticsLastSyncSensor,
)


def test_stats_last_sync_sensor_properties() -> None:
    """Test sensor metadata and value."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.last_statistics_sync = datetime.now().astimezone()

    device = Mock(spec=MittFortumDevice)
    device.device_info = {
        "identifiers": {("mittfortum", "123456")},
        "name": "Mittfortum Energy Meter",
        "manufacturer": "Fortum",
        "model": "Energy Meter",
    }

    sensor = MittFortumStatisticsLastSyncSensor(coordinator=coordinator, device=device)

    assert sensor.name == "Statistics Last Sync"
    assert sensor.device_class == SensorDeviceClass.TIMESTAMP
    assert sensor.entity_category == EntityCategory.DIAGNOSTIC
    assert sensor.native_value == coordinator.last_statistics_sync
    assert sensor.available is True


def test_stats_last_sync_sensor_unavailable_without_sync() -> None:
    """Test sensor availability before first sync."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.last_statistics_sync = None

    device = Mock(spec=MittFortumDevice)
    device.device_info = {
        "identifiers": {("mittfortum", "123456")},
        "name": "Mittfortum Energy Meter",
        "manufacturer": "Fortum",
        "model": "Energy Meter",
    }

    sensor = MittFortumStatisticsLastSyncSensor(coordinator=coordinator, device=device)

    assert sensor.native_value is None
    assert sensor.available is False
