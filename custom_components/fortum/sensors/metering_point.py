"""Metering point info sensor for Fortum."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory

from ..const import NORGESPRIS_CONSUMPTION_LIMIT_SENSOR_KEY

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceInfo
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from ..device import FortumDevice
    from ..models import MeteringPoint


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

    def refresh_metering_point(self, metering_point: MeteringPoint) -> None:
        """Update metering point payload and write state if changed."""
        if self._metering_point == metering_point:
            return
        self._metering_point = metering_point
        if getattr(self, "hass", None) is not None:
            self.async_write_ha_state()


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

    def refresh_metering_point(self, metering_point: MeteringPoint) -> None:
        """Update metering point payload and write state if changed."""
        if self._metering_point == metering_point:
            return
        self._metering_point = metering_point
        if getattr(self, "hass", None) is not None:
            self.async_write_ha_state()


class MeteringPointSensors:
    """Own Fortum entities for one metering point and update logic."""

    def __init__(
        self,
        async_add_entities: AddEntitiesCallback,
        device: FortumDevice,
        region: str,
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

        self._norgespris_sensor = norgespris_sensor
        async_add_entities(entities, update_before_add=False)

    @property
    def metering_point_no(self) -> str:
        """Return metering point number this group owns."""
        return self._metering_point_no

    def refresh(self, metering_point: MeteringPoint) -> None:
        """Refresh owned entities from latest metering point payload."""
        self._metering_point_sensor.refresh_metering_point(metering_point)
        if self._norgespris_sensor is not None:
            self._norgespris_sensor.refresh_metering_point(metering_point)


class MeteringPointSensorRegistry:
    """Registry that owns all metering-point sensor groups."""

    def __init__(
        self,
        async_add_entities: AddEntitiesCallback,
        device: FortumDevice,
        region: str,
        metering_points: tuple[MeteringPoint, ...],
    ) -> None:
        """Initialize metering-point sensor groups and create entities."""
        self._async_add_entities = async_add_entities
        self._device = device
        self._region = region
        self._groups: dict[str, MeteringPointSensors] = {}
        self.refresh_all(metering_points)

    def refresh_all(self, metering_points: tuple[MeteringPoint, ...]) -> None:
        """Refresh all existing groups and add newly discovered metering points."""
        for metering_point in metering_points:
            group = self._groups.get(metering_point.metering_point_no)
            if group is None:
                self._groups[metering_point.metering_point_no] = MeteringPointSensors(
                    self._async_add_entities,
                    self._device,
                    self._region,
                    metering_point,
                )
                continue

            group.refresh(metering_point)

    def for_each(self, callback: Callable[[MeteringPointSensors], None]) -> None:
        """Run callback for every registered metering-point group."""
        for group in self._groups.values():
            callback(group)
