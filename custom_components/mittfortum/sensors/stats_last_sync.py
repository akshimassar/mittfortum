"""Statistics last-sync sensor for MittFortum."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from ..const import STATS_LAST_SYNC_SENSOR_KEY
from ..entity import MittFortumEntity

if TYPE_CHECKING:
    from datetime import datetime

    from ..coordinators import HourlyConsumptionSyncCoordinator
    from ..device import MittFortumDevice


class MittFortumStatisticsLastSyncSensor(MittFortumEntity, SensorEntity):
    """Sensor exposing last successful statistics sync timestamp."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HourlyConsumptionSyncCoordinator,
        device: MittFortumDevice,
    ) -> None:
        """Initialize statistics last-sync sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=STATS_LAST_SYNC_SENSOR_KEY,
            name="Statistics Last Sync",
        )

    @property
    def native_value(self) -> datetime | None:
        """Return timestamp of latest successful statistics sync."""
        return self.coordinator.last_statistics_sync

    @property
    def device_class(self) -> SensorDeviceClass:
        """Return the device class."""
        return SensorDeviceClass.TIMESTAMP

    @property
    def available(self) -> bool:
        """Return if sensor is available."""
        return self.coordinator.last_statistics_sync is not None
