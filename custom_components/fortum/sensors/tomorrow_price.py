"""Tomorrow price sensors for Fortum."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, cast

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)

from ..const import (
    TOMORROW_MAX_PRICE_SENSOR_KEY,
    TOMORROW_MAX_PRICE_TIME_SENSOR_KEY,
    get_currency_for_region,
)
from ..entity import FortumEntity
from ..models import SpotPricePoint

if TYPE_CHECKING:
    from ..coordinators import SpotPriceSyncCoordinator
    from ..device import FortumDevice


class _FortumTomorrowPriceEntity(FortumEntity, SensorEntity):
    """Base entity with tomorrow price helpers."""

    def __init__(
        self,
        coordinator: SpotPriceSyncCoordinator,
        device: FortumDevice,
        entity_key: str,
        name: str,
        area_code: str,
    ) -> None:
        """Initialize tomorrow-price base entity."""
        self._area_code = area_code.upper()
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=f"{entity_key}_{self._area_code.lower()}",
            name=f"{name} {self._area_code}",
        )

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

    def _tomorrow_price_points(self) -> list[SpotPricePoint]:
        """Return price points for tomorrow in the point timezone."""
        price_points = self._area_price_points()
        if not price_points:
            return []

        latest_point = price_points[-1]
        now = (
            datetime.now(tz=latest_point.date_time.tzinfo)
            if latest_point.date_time.tzinfo
            else datetime.now()
        )
        tomorrow_date = now.date() + timedelta(days=1)

        return [
            point for point in price_points if point.date_time.date() == tomorrow_date
        ]

    def _tomorrow_max_point(self) -> SpotPricePoint | None:
        """Return first tomorrow data point at max price."""
        tomorrow_points = self._tomorrow_price_points()
        if not tomorrow_points:
            return None

        return max(
            tomorrow_points,
            key=lambda point: (
                float(point.price or 0),
                -point.date_time.timestamp(),
            ),
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self._tomorrow_max_point() is not None
        )


class FortumTomorrowMaxPriceSensor(_FortumTomorrowPriceEntity):
    """Tomorrow max spot-price sensor."""

    def __init__(
        self,
        coordinator: SpotPriceSyncCoordinator,
        device: FortumDevice,
        region: str,
        area_code: str,
    ) -> None:
        """Initialize tomorrow max price sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=TOMORROW_MAX_PRICE_SENSOR_KEY,
            name="Tomorrow Max Price",
            area_code=area_code,
        )
        self._fallback_unit = f"{get_currency_for_region(region)}/kWh"

    @property
    def native_value(self) -> float | None:
        """Return tomorrow maximum price."""
        point = self._tomorrow_max_point()
        return point.price if point else None

    @property
    def native_unit_of_measurement(self) -> str:
        """Return unit of measurement."""
        point = self._tomorrow_max_point()
        if point and point.price_unit:
            return point.price_unit
        return self._fallback_unit

    @property
    def state_class(self) -> SensorStateClass:
        """Return sensor state class."""
        return SensorStateClass.MEASUREMENT


class FortumTomorrowMaxPriceTimeSensor(_FortumTomorrowPriceEntity):
    """Timestamp sensor for tomorrow's maximum spot price."""

    def __init__(
        self,
        coordinator: SpotPriceSyncCoordinator,
        device: FortumDevice,
        area_code: str,
    ) -> None:
        """Initialize tomorrow max price time sensor."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=TOMORROW_MAX_PRICE_TIME_SENSOR_KEY,
            name="Tomorrow Max Price Time",
            area_code=area_code,
        )

    @property
    def native_value(self) -> datetime | None:
        """Return timestamp of tomorrow maximum price."""
        point = self._tomorrow_max_point()
        return point.date_time if point else None

    @property
    def device_class(self) -> SensorDeviceClass:
        """Return sensor device class."""
        return SensorDeviceClass.TIMESTAMP
