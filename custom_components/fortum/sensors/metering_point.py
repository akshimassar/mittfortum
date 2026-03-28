"""Metering point info sensor for Fortum."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory

from ..const import (
    CURRENT_MONTH_CONSUMPTION_SENSOR_KEY,
    CURRENT_MONTH_COST_SENSOR_KEY,
    NORGESPRIS_CONSUMPTION_LIMIT_SENSOR_KEY,
    get_currency_for_region,
)

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceInfo
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from ..coordinators.hourly_consumption import HourlyConsumptionSyncCoordinator
    from ..device import FortumDevice
    from ..models import MeteringPoint

from ..entity import FortumEntity

_LOGGER = logging.getLogger(__name__)


class FortumMeteringPointSensor(SensorEntity):
    """Diagnostic sensor exposing metering point address and IDs."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:map-marker"

    def __init__(self, device: FortumDevice, metering_point: MeteringPoint) -> None:
        """Initialize metering point info sensor."""
        self._device = device
        self._metering_point = metering_point
        self._attr_name = f"Metering Point {metering_point.metering_point_no}"
        self._attr_unique_id = (
            f"{device.unique_id}_metering_point_{metering_point.metering_point_no}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self._device.device_info

    @property
    def native_value(self) -> str:
        """Return metering point address."""
        address = self._metering_point.address
        area = self._metering_point.price_area
        if address and area:
            return f"{address} [{area}]"
        if address:
            return address
        if area:
            return f"[{area}]"
        return "Unknown"

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Return metering point identifiers."""
        attributes: dict[str, str] = {
            "metering_point_no": self._metering_point.metering_point_no,
        }
        if self._metering_point.metering_point_id:
            attributes["metering_point_id"] = self._metering_point.metering_point_id
        if self._metering_point.address:
            attributes["address"] = self._metering_point.address
        if self._metering_point.price_area:
            attributes["price_area"] = self._metering_point.price_area
        return attributes

    def refresh_metering_point(self, metering_point: MeteringPoint) -> bool:
        """Update metering point payload and write state if changed."""
        if self._metering_point == metering_point:
            return False
        self._metering_point = metering_point
        if getattr(self, "hass", None) is not None:
            self.async_write_ha_state()
        return True


class FortumNorgesprisConsumptionLimitSensor(SensorEntity):
    """Sensor exposing Norgespris consumption limit for one metering point."""

    _attr_icon = "mdi:gauge"
    _attr_native_unit_of_measurement = "kWh"

    def __init__(self, device: FortumDevice, metering_point: MeteringPoint) -> None:
        """Initialize Norgespris consumption limit sensor."""
        self._device = device
        self._metering_point = metering_point
        self._attr_name = (
            f"Norgespris consumption limit {metering_point.metering_point_no}"
        )
        self._attr_unique_id = (
            f"{device.unique_id}_{NORGESPRIS_CONSUMPTION_LIMIT_SENSOR_KEY}_"
            f"{metering_point.metering_point_no}"
        )

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self._device.device_info

    @property
    def native_value(self) -> float | None:
        """Return Norgespris consumption limit in kWh."""
        return self._metering_point.norgespris_consumption_limit

    def refresh_metering_point(self, metering_point: MeteringPoint) -> bool:
        """Update metering point payload and write state if changed."""
        if self._metering_point == metering_point:
            return False
        self._metering_point = metering_point
        if getattr(self, "hass", None) is not None:
            self.async_write_ha_state()
        return True


class FortumCurrentMonthConsumptionSensor(FortumEntity, SensorEntity):
    """Month-to-date consumption sensor for one metering point."""

    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: HourlyConsumptionSyncCoordinator,
        device: FortumDevice,
        metering_point: MeteringPoint,
    ) -> None:
        """Initialize current-month consumption sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=(
                f"{CURRENT_MONTH_CONSUMPTION_SENSOR_KEY}_"
                f"{metering_point.metering_point_no}"
            ),
            name=f"Current Month Consumption {metering_point.metering_point_no}",
        )
        self._metering_point_no = metering_point.metering_point_no
        self._fallback_unit = "kWh"

    @property
    def native_value(self) -> float | None:
        """Return month-to-date consumption from hourly recorder stats."""
        coordinator = cast(Any, self.coordinator)
        return coordinator.get_current_month_consumption_total(self._metering_point_no)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return consumption unit from recorder statistic metadata."""
        coordinator = cast(Any, self.coordinator)
        return (
            coordinator.get_current_month_consumption_unit(self._metering_point_no)
            or self._fallback_unit
        )

    @property
    def available(self) -> bool:
        """Return availability based on coordinator data and metadata readiness."""
        return self.coordinator.last_update_success and self.native_value is not None


class FortumCurrentMonthCostSensor(FortumEntity, SensorEntity):
    """Month-to-date cost sensor for one metering point."""

    _attr_icon = "mdi:cash-multiple"
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 2

    def __init__(
        self,
        coordinator: HourlyConsumptionSyncCoordinator,
        device: FortumDevice,
        region: str,
        metering_point: MeteringPoint,
    ) -> None:
        """Initialize current-month cost sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=f"{CURRENT_MONTH_COST_SENSOR_KEY}_{metering_point.metering_point_no}",
            name=f"Current Month Cost {metering_point.metering_point_no}",
        )
        self._metering_point_no = metering_point.metering_point_no
        self._fallback_unit = get_currency_for_region(region)

    @property
    def native_value(self) -> float | None:
        """Return month-to-date cost from hourly recorder stats."""
        coordinator = cast(Any, self.coordinator)
        return coordinator.get_current_month_cost_total(self._metering_point_no)

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return cost unit from recorder statistic metadata."""
        coordinator = cast(Any, self.coordinator)
        return (
            coordinator.get_current_month_cost_unit(self._metering_point_no)
            or self._fallback_unit
        )

    @property
    def available(self) -> bool:
        """Return availability based on coordinator data and metadata readiness."""
        return self.coordinator.last_update_success and self.native_value is not None


class MeteringPointEntityGroup:
    """Own Fortum entities for one metering point and update logic."""

    def __init__(
        self,
        async_add_entities: AddEntitiesCallback,
        coordinator: HourlyConsumptionSyncCoordinator,
        device: FortumDevice,
        region: str,
        create_current_month_sensors: bool,
        metering_point: MeteringPoint,
    ) -> None:
        """Initialize and add entities for this metering point."""
        self._metering_point_no = metering_point.metering_point_no
        self._metering_point_sensor = FortumMeteringPointSensor(device, metering_point)

        norgespris_sensor: FortumNorgesprisConsumptionLimitSensor | None = None
        entities: list[SensorEntity] = [self._metering_point_sensor]
        if region == "no":
            norgespris_sensor = FortumNorgesprisConsumptionLimitSensor(
                device,
                metering_point,
            )
            entities.append(norgespris_sensor)

        if create_current_month_sensors:
            entities.extend(
                [
                    FortumCurrentMonthConsumptionSensor(
                        coordinator,
                        device,
                        metering_point,
                    ),
                    FortumCurrentMonthCostSensor(
                        coordinator,
                        device,
                        region,
                        metering_point,
                    ),
                ]
            )

        self._norgespris_sensor = norgespris_sensor
        async_add_entities(entities, update_before_add=False)

    @property
    def metering_point_no(self) -> str:
        """Return metering point number this group owns."""
        return self._metering_point_no

    def refresh(self, metering_point: MeteringPoint) -> None:
        """Refresh owned entities from latest metering point payload."""
        changed = self._metering_point_sensor.refresh_metering_point(metering_point)
        if self._norgespris_sensor is not None:
            changed = (
                self._norgespris_sensor.refresh_metering_point(metering_point)
                or changed
            )

        if changed:
            _LOGGER.debug(
                "updated metering point entity group metering_point_no=%s",
                self._metering_point_no,
            )


class MeteringPointEntityManager:
    """Manager that owns all metering-point entity groups."""

    def __init__(
        self,
        async_add_entities: AddEntitiesCallback,
        coordinator: HourlyConsumptionSyncCoordinator,
        device: FortumDevice,
        region: str,
        create_current_month_sensors: bool,
        metering_points: tuple[MeteringPoint, ...],
    ) -> None:
        """Initialize metering-point entity groups and create entities."""
        self._async_add_entities = async_add_entities
        self._coordinator = coordinator
        self._device = device
        self._region = region
        self._create_current_month_sensors = create_current_month_sensors
        self._groups: dict[str, MeteringPointEntityGroup] = {}
        self.refresh_all(metering_points)

    def refresh_all(self, metering_points: tuple[MeteringPoint, ...]) -> None:
        """Refresh all existing groups and add newly discovered metering points."""
        for metering_point in metering_points:
            group = self._groups.get(metering_point.metering_point_no)
            if group is None:
                self._groups[metering_point.metering_point_no] = (
                    MeteringPointEntityGroup(
                        self._async_add_entities,
                        self._coordinator,
                        self._device,
                        self._region,
                        self._create_current_month_sensors,
                        metering_point,
                    )
                )
                _LOGGER.debug(
                    "added metering point entity group metering_point_no=%s",
                    metering_point.metering_point_no,
                )
                continue

            group.refresh(metering_point)

    def for_each(self, callback: Callable[[MeteringPointEntityGroup], None]) -> None:
        """Run callback for every registered metering-point group."""
        for group in self._groups.values():
            callback(group)
