"""Tests for tomorrow price sensors."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.fortum.models import SpotPricePoint
from custom_components.fortum.sensors.tomorrow_price import (
    FortumTomorrowMaxPriceSensor,
    FortumTomorrowMaxPriceTimeSensor,
)


def _build_coordinator(data: list[SpotPricePoint]) -> Mock:
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = data
    return coordinator


def _build_device() -> Mock:
    device = Mock()
    device.device_info = {
        "identifiers": {("fortum", "123456")},
        "name": "Fortum Energy Meter",
        "manufacturer": "Fortum",
        "model": "Energy Meter",
    }
    return device


def test_tomorrow_max_price_sensors_with_tomorrow_data() -> None:
    """Tomorrow sensors should expose max price and corresponding timestamp."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    tomorrow_start = (now + timedelta(days=1)).replace(hour=0)

    coordinator = _build_coordinator(
        [
            SpotPricePoint(
                date_time=now,
                price=0.12,
                price_unit="EUR/kWh",
                area_code="FI",
            ),
            SpotPricePoint(
                date_time=tomorrow_start + timedelta(hours=5),
                price=0.30,
                price_unit="EUR/kWh",
                area_code="FI",
            ),
            SpotPricePoint(
                date_time=tomorrow_start + timedelta(hours=9),
                price=0.25,
                price_unit="EUR/kWh",
                area_code="FI",
            ),
        ]
    )
    device = _build_device()

    price_sensor = FortumTomorrowMaxPriceSensor(
        coordinator=coordinator,
        device=device,
        region="fi",
        area_code="FI",
    )
    time_sensor = FortumTomorrowMaxPriceTimeSensor(
        coordinator=coordinator,
        device=device,
        area_code="FI",
    )

    assert price_sensor.name == "Tomorrow Max Price FI"
    assert price_sensor.native_value == 0.30
    assert price_sensor.native_unit_of_measurement == "EUR/kWh"
    assert price_sensor.state_class == SensorStateClass.MEASUREMENT
    assert price_sensor.available is True

    assert time_sensor.name == "Tomorrow Max Price Time FI"
    assert time_sensor.native_value == tomorrow_start + timedelta(hours=5)
    assert time_sensor.device_class == SensorDeviceClass.TIMESTAMP
    assert time_sensor.available is True


def test_tomorrow_max_price_sensors_unavailable_without_tomorrow_data() -> None:
    """Tomorrow sensors should be unavailable until tomorrow prices arrive."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    coordinator = _build_coordinator(
        [
            SpotPricePoint(
                date_time=now,
                price=0.12,
                price_unit="EUR/kWh",
                area_code="FI",
            )
        ]
    )
    device = _build_device()

    price_sensor = FortumTomorrowMaxPriceSensor(
        coordinator=coordinator,
        device=device,
        region="fi",
        area_code="FI",
    )
    time_sensor = FortumTomorrowMaxPriceTimeSensor(
        coordinator=coordinator,
        device=device,
        area_code="FI",
    )

    assert price_sensor.native_value is None
    assert price_sensor.available is False
    assert time_sensor.native_value is None
    assert time_sensor.available is False
