"""Statistics sync sensor for MittFortum."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from ..const import STATS_SYNC_SENSOR_KEY
from ..entity import MittFortumEntity

if TYPE_CHECKING:
    from datetime import datetime

    from ..device import MittFortumDevice
    from ..schedulers import HourlyConsumptionSyncScheduler


class MittFortumStatisticsSyncSensor(MittFortumEntity, SensorEntity):
    """Sensor exposing last successful statistics sync timestamp."""

    def __init__(
        self,
        coordinator: HourlyConsumptionSyncScheduler,
        device: MittFortumDevice,
    ) -> None:
        """Initialize statistics sync sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=STATS_SYNC_SENSOR_KEY,
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
