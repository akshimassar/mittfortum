"""Metering point info sensor for Fortum."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

if TYPE_CHECKING:
    from homeassistant.helpers.device_registry import DeviceInfo

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
