"""Price sensor for MittFortum."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.sensor import SensorEntity, SensorStateClass

from ..const import PRICE_SENSOR_KEY, get_currency_for_region

if TYPE_CHECKING:
    from ..coordinators import SpotPriceSyncCoordinator
    from ..device import MittFortumDevice

from ..entity import MittFortumEntity
from ..models import ConsumptionData


class MittFortumPriceSensor(MittFortumEntity, SensorEntity):
    """Price per kWh sensor for MittFortum."""

    def __init__(
        self,
        coordinator: SpotPriceSyncCoordinator,
        device: MittFortumDevice,
        region: str,
    ) -> None:
        """Initialize price sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=PRICE_SENSOR_KEY,
            name="Price per kWh",
        )
        self._fallback_unit = f"{get_currency_for_region(region)}/kWh"

    def _current_price_point(self) -> ConsumptionData | None:
        """Return current price point (or next future point if none started yet)."""
        data = cast(list[ConsumptionData] | None, self.coordinator.data)
        if not data:
            return None

        price_points = [item for item in data if item.price is not None]
        if not price_points:
            return None

        latest_point = price_points[-1]
        now = (
            datetime.now(tz=latest_point.date_time.tzinfo)
            if latest_point.date_time.tzinfo
            else datetime.now()
        )

        current_or_past = [item for item in price_points if item.date_time <= now]
        if current_or_past:
            return current_or_past[-1]

        return price_points[0]

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        point = self._current_price_point()
        return point.price if point else None

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit of measurement."""
        point = self._current_price_point()
        if point and point.price_unit:
            return point.price_unit
        return self._fallback_unit

    @property
    def state_class(self) -> SensorStateClass:
        """Return the state class."""
        return SensorStateClass.MEASUREMENT

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self._current_price_point() is not None
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return additional state attributes."""
        data = cast(list[ConsumptionData] | None, self.coordinator.data)
        if not data:
            return None

        price_points = [item for item in data if item.price is not None]
        if not price_points:
            return None

        latest_date = price_points[-1].date_time
        now = (
            datetime.now(tz=latest_date.tzinfo)
            if latest_date.tzinfo
            else datetime.now()
        )

        return {
            "total_records_with_price": len(price_points),
            "latest_date": latest_date.isoformat(),
            "has_future_price": latest_date > now,
        }
