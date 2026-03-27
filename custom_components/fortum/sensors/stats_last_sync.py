"""Statistics last-sync sensor for Fortum."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory

from ..const import STATS_LAST_SYNC_SENSOR_KEY
from ..entity import FortumEntity

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from ..coordinators.hourly_consumption import HourlyConsumptionSyncCoordinator
    from ..device import FortumDevice


class FortumStatisticsLastSyncSensor(FortumEntity, SensorEntity):
    """Sensor exposing last successful statistics sync timestamp."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: HourlyConsumptionSyncCoordinator,
        device: FortumDevice,
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
        coordinator = cast("HourlyConsumptionSyncCoordinator", self.coordinator)
        return coordinator.last_statistics_sync

    @property
    def device_class(self) -> SensorDeviceClass:
        """Return the device class."""
        return SensorDeviceClass.TIMESTAMP

    @property
    def available(self) -> bool:
        """Return if sensor is available."""
        coordinator = cast("HourlyConsumptionSyncCoordinator", self.coordinator)
        return (
            coordinator.last_statistics_sync is not None
            and coordinator.last_update_success
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return coordinator update health details."""
        return {"last_update_success": self.coordinator.last_update_success}


class StaticEntityManager:
    """Manager for integration-wide static entities."""

    def __init__(
        self,
        async_add_entities: AddEntitiesCallback,
        coordinator: HourlyConsumptionSyncCoordinator,
        device: FortumDevice,
    ) -> None:
        """Initialize and add static sensors once."""
        async_add_entities(
            [FortumStatisticsLastSyncSensor(coordinator, device)],
            update_before_add=False,
        )
