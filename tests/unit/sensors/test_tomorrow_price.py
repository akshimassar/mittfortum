"""Tests for tomorrow price sensors."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass

from custom_components.mittfortum.models import ConsumptionData
from custom_components.mittfortum.sensors.tomorrow_price import (
    MittFortumTomorrowMaxPriceSensor,
    MittFortumTomorrowMaxPriceTimeSensor,
)


def _build_coordinator(data: list[ConsumptionData]) -> Mock:
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = data
    return coordinator


def _build_device() -> Mock:
    device = Mock()
    device.device_info = {
        "identifiers": {("mittfortum", "123456")},
        "name": "Mittfortum Energy Meter",
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
            ConsumptionData(
                value=0.0,
                unit="kWh",
                date_time=now,
                cost=None,
                price=0.12,
                price_unit="EUR/kWh",
            ),
            ConsumptionData(
                value=0.0,
                unit="kWh",
                date_time=tomorrow_start + timedelta(hours=5),
                cost=None,
                price=0.30,
                price_unit="EUR/kWh",
            ),
            ConsumptionData(
                value=0.0,
                unit="kWh",
                date_time=tomorrow_start + timedelta(hours=9),
                cost=None,
                price=0.25,
                price_unit="EUR/kWh",
            ),
        ]
    )
    device = _build_device()

    price_sensor = MittFortumTomorrowMaxPriceSensor(
        coordinator=coordinator,
        device=device,
        region="fi",
    )
    time_sensor = MittFortumTomorrowMaxPriceTimeSensor(
        coordinator=coordinator,
        device=device,
    )

    assert price_sensor.name == "Tomorrow Max Price"
    assert price_sensor.native_value == 0.30
    assert price_sensor.native_unit_of_measurement == "EUR/kWh"
    assert price_sensor.state_class == SensorStateClass.MEASUREMENT
    assert price_sensor.available is True

    assert time_sensor.name == "Tomorrow Max Price Time"
    assert time_sensor.native_value == tomorrow_start + timedelta(hours=5)
    assert time_sensor.device_class == SensorDeviceClass.TIMESTAMP
    assert time_sensor.available is True


def test_tomorrow_max_price_sensors_unavailable_without_tomorrow_data() -> None:
    """Tomorrow sensors should be unavailable until tomorrow prices arrive."""
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    coordinator = _build_coordinator(
        [
            ConsumptionData(
                value=0.0,
                unit="kWh",
                date_time=now,
                cost=None,
                price=0.12,
                price_unit="EUR/kWh",
            )
        ]
    )
    device = _build_device()

    price_sensor = MittFortumTomorrowMaxPriceSensor(
        coordinator=coordinator,
        device=device,
        region="fi",
    )
    time_sensor = MittFortumTomorrowMaxPriceTimeSensor(
        coordinator=coordinator,
        device=device,
    )

    assert price_sensor.native_value is None
    assert price_sensor.available is False
    assert time_sensor.native_value is None
    assert time_sensor.available is False
