"""Price sensor for Fortum."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.sensor import SensorEntity, SensorStateClass

from ..const import PRICE_SENSOR_KEY, get_currency_for_region

if TYPE_CHECKING:
    from ..coordinators import SpotPriceSyncCoordinator
    from ..device import FortumDevice

from ..entity import FortumEntity
from ..models import SpotPricePoint


class FortumPriceSensor(FortumEntity, SensorEntity):
    """Price per kWh sensor for Fortum."""

    def __init__(
        self,
        coordinator: SpotPriceSyncCoordinator,
        device: FortumDevice,
        region: str,
        area_code: str,
    ) -> None:
        """Initialize price sensor."""
        self._area_code = area_code.upper()
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=f"{PRICE_SENSOR_KEY}_{self._area_code.lower()}",
            name=f"Price per kWh [{self._area_code}]",
        )
        self._fallback_unit = f"{get_currency_for_region(region)}/kWh"

    def _area_price_points(self) -> list[SpotPricePoint]:
        """Return price points for this entity area code."""
        data = cast(list[SpotPricePoint] | None, self.coordinator.data)
        if not data:
            return []
        return [
            point
            for point in data
            if isinstance(point.area_code, str)
            and point.area_code.strip().upper() == self._area_code
        ]

    def _current_price_point(self) -> SpotPricePoint | None:
        """Return current price point (or next future point if none started yet)."""
        price_points = self._area_price_points()
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
        price_points = self._area_price_points()
        if not price_points:
            return None

        latest_date = price_points[-1].date_time
        now = (
            datetime.now(tz=latest_date.tzinfo)
            if latest_date.tzinfo
            else datetime.now()
        )

        return {
            "price_area": self._area_code,
            "total_records_with_price": len(price_points),
            "latest_date": latest_date.isoformat(),
            "has_future_price": latest_date > now,
        }
