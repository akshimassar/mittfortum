"""Unit tests for SessionManager."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.fortum.exceptions import APIError, InvalidResponseError
from custom_components.fortum.sensors import (
    FortumCurrentMonthConsumptionSensor,
    FortumCurrentMonthCostSensor,
    FortumMeteringPointSensor,
    FortumNorgesprisConsumptionLimitSensor,
    FortumPriceSensor,
    FortumStatisticsLastSyncSensor,
)
from custom_components.fortum.session_manager import SessionManager


def _session_payload(
    *metering_point_numbers: str,
    addresses: dict[str, str] | None = None,
    limits: dict[str, float] | None = None,
) -> dict:
    """Build a minimal Fortum session payload."""
    addresses = addresses or {}
    limits = limits or {}
    return {
        "user": {
            "customerId": "55650898",
            "deliverySites": [
                {
                    "address": addresses.get(number),
                    "consumption": {
                        "meteringPointNo": number,
                        "priceArea": "NO1" if number == "6094111" else "NO2",
                        "norgespris": {"consumptionMaxLimit": limits[number]}
                        if number in limits
                        else {},
                    },
                }
                for number in metering_point_numbers
            ],
            "postalAddress": "Test Street 123",
            "postOffice": "Test City",
            "name": "Test Customer",
        }
    }


@pytest.mark.asyncio
async def test_update_from_payload_buffers_until_platform_setup(mock_hass) -> None:
    """Session update should buffer once before sensor platform setup."""
    api_client = Mock()
    manager = SessionManager(mock_hass, "entry-id", api_client)
    manager.start()

    await manager.async_update_from_payload(_session_payload("6094111"), source="setup")

    assert manager.get_snapshot() is None
    assert manager._setup_waiting_payload is not None  # noqa: SLF001

    captured_entities = []

    def _async_add_entities(new_entities, update_before_add=False):
        captured_entities.extend(new_entities)

    device = Mock()
    device.unique_id = "customer_123"
    device.device_info = {
        "identifiers": {("fortum", "customer_123")},
        "name": "Fortum Account",
    }

    await manager.async_setup_sensor_platform(
        _async_add_entities,
        coordinator=Mock(),
        price_coordinator=Mock(),
        device=device,
        region="no",
        create_current_month_sensors=False,
    )

    snapshot = manager.get_snapshot()
    assert snapshot is not None
    assert snapshot.customer_id == "55650898"
    assert [point.metering_point_no for point in snapshot.metering_points] == [
        "6094111"
    ]
    assert list(snapshot.price_areas) == ["NO1"]
    assert manager._refresh_handle is not None  # noqa: SLF001

    await manager.stop()


@pytest.mark.asyncio
async def test_second_pre_setup_payload_raises(mock_hass) -> None:
    """Second payload before sensor setup should raise."""
    manager = SessionManager(mock_hass, "entry-id", Mock())
    manager.start()

    await manager.async_update_from_payload(_session_payload("6094111"), source="setup")

    with pytest.raises(
        InvalidResponseError,
        match="Received additional session payload before sensor platform setup",
    ):
        await manager.async_update_from_payload(
            _session_payload("6094111", "6094222"),
            source="reauth",
        )

    await manager.stop()


@pytest.mark.asyncio
async def test_sensor_platform_live_adds_new_metering_points_and_areas(
    mock_hass,
) -> None:
    """SessionManager should add new entities without reload for additive changes."""
    api_client = Mock()
    manager = SessionManager(mock_hass, "entry-id", api_client)
    manager.start()
    await manager.async_update_from_payload(_session_payload("6094111"), source="setup")

    captured_entities = []

    def _async_add_entities(new_entities, update_before_add=False):
        captured_entities.extend(new_entities)

    device = Mock()
    device.unique_id = "customer_123"
    device.device_info = {
        "identifiers": {("fortum", "customer_123")},
        "name": "Fortum Account",
    }

    await manager.async_setup_sensor_platform(
        _async_add_entities,
        coordinator=Mock(),
        price_coordinator=Mock(),
        device=device,
        region="no",
        create_current_month_sensors=False,
    )

    initial_count = len(captured_entities)
    assert any(
        isinstance(entity, FortumStatisticsLastSyncSensor)
        for entity in captured_entities
    )

    await manager.async_update_from_payload(
        _session_payload(
            "6094111",
            "6094222",
            limits={"6094111": 4000.0, "6094222": 5000.0},
        ),
        source="scheduled",
    )

    await asyncio.sleep(0)

    assert len(captured_entities) == initial_count + 5
    assert any(
        isinstance(entity, FortumMeteringPointSensor) for entity in captured_entities
    )
    assert any(isinstance(entity, FortumPriceSensor) for entity in captured_entities)

    await manager.stop()


@pytest.mark.asyncio
async def test_update_changes_existing_metering_point_values_in_place(
    mock_hass,
) -> None:
    """Session update should refresh address and Norgespris value in-place."""
    api_client = Mock()
    manager = SessionManager(mock_hass, "entry-id", api_client)
    manager.start()

    await manager.async_update_from_payload(
        _session_payload(
            "6094111",
            addresses={"6094111": "Old street 1"},
            limits={"6094111": 4000.0},
        ),
        source="setup",
    )

    captured_entities = []

    def _async_add_entities(new_entities, update_before_add=False):
        captured_entities.extend(new_entities)

    device = Mock()
    device.unique_id = "customer_123"
    device.device_info = {
        "identifiers": {("fortum", "customer_123")},
        "name": "Fortum Account",
    }

    await manager.async_setup_sensor_platform(
        _async_add_entities,
        coordinator=Mock(),
        price_coordinator=Mock(),
        device=device,
        region="no",
        create_current_month_sensors=False,
    )

    info_sensor = next(
        entity
        for entity in captured_entities
        if isinstance(entity, FortumMeteringPointSensor)
        and (entity.extra_state_attributes or {}).get("metering_point_no") == "6094111"
    )
    limit_sensor = next(
        entity
        for entity in captured_entities
        if isinstance(entity, FortumNorgesprisConsumptionLimitSensor)
    )

    assert info_sensor.native_value == "Old street 1 [NO1]"
    assert limit_sensor.native_value == 4000.0

    await manager.async_update_from_payload(
        _session_payload(
            "6094111",
            addresses={"6094111": "New street 99"},
            limits={"6094111": 6500.0},
        ),
        source="scheduled",
    )

    await asyncio.sleep(0)

    assert info_sensor.native_value == "New street 99 [NO1]"
    assert limit_sensor.native_value == 6500.0

    await manager.stop()


@pytest.mark.asyncio
async def test_setup_adds_current_month_entities_when_enabled(mock_hass) -> None:
    """SessionManager should add current-month entities when option is enabled."""
    api_client = Mock()
    manager = SessionManager(mock_hass, "entry-id", api_client)
    manager.start()

    await manager.async_update_from_payload(_session_payload("6094111"), source="setup")

    captured_entities = []

    def _async_add_entities(new_entities, update_before_add=False):
        captured_entities.extend(new_entities)

    device = Mock()
    device.unique_id = "customer_123"
    device.device_info = {
        "identifiers": {("fortum", "customer_123")},
        "name": "Fortum Account",
    }

    await manager.async_setup_sensor_platform(
        _async_add_entities,
        coordinator=Mock(),
        price_coordinator=Mock(),
        device=device,
        region="fi",
        create_current_month_sensors=True,
    )

    assert any(
        isinstance(entity, FortumCurrentMonthConsumptionSensor)
        for entity in captured_entities
    )
    assert any(
        isinstance(entity, FortumCurrentMonthCostSensor) for entity in captured_entities
    )

    await manager.stop()


@pytest.mark.asyncio
async def test_update_ignores_delivery_site_ordering(mock_hass) -> None:
    """Session update should not create duplicate entities when order changes."""
    api_client = Mock()
    manager = SessionManager(mock_hass, "entry-id", api_client)
    manager.start()

    await manager.async_update_from_payload(
        _session_payload("6094111", "6094222"),
        source="setup",
    )

    captured_entities = []

    def _async_add_entities(new_entities, update_before_add=False):
        captured_entities.extend(new_entities)

    device = Mock()
    device.unique_id = "customer_123"
    device.device_info = {
        "identifiers": {("fortum", "customer_123")},
        "name": "Fortum Account",
    }

    await manager.async_setup_sensor_platform(
        _async_add_entities,
        coordinator=Mock(),
        price_coordinator=Mock(),
        device=device,
        region="no",
        create_current_month_sensors=False,
    )

    await manager.async_update_from_payload(
        _session_payload("6094222", "6094111"),
        source="scheduled",
    )

    await asyncio.sleep(0)

    assert len(captured_entities) == 11

    await manager.stop()


@pytest.mark.asyncio
async def test_setup_sensor_platform_requires_snapshot(mock_hass) -> None:
    """Sensor platform setup should fail when snapshot is missing."""
    manager = SessionManager(mock_hass, "entry-id", Mock())

    def _async_add_entities(new_entities, update_before_add=False):
        return None

    with pytest.raises(
        InvalidResponseError,
        match="Session snapshot missing during sensor platform setup",
    ):
        await manager.async_setup_sensor_platform(
            _async_add_entities,
            coordinator=Mock(),
            price_coordinator=Mock(),
            device=Mock(),
            region="no",
            create_current_month_sensors=False,
        )


@pytest.mark.asyncio
async def test_refresh_from_api_failure_keeps_previous_snapshot(mock_hass) -> None:
    """Refresh failure should preserve snapshot and still reschedule."""
    api_client = Mock()
    api_client.get_session_payload = AsyncMock(side_effect=APIError("boom"))
    manager = SessionManager(mock_hass, "entry-id", api_client)
    manager.start()
    await manager.async_update_from_payload(_session_payload("6094111"), source="setup")

    def _async_add_entities(new_entities, update_before_add=False):
        return None

    await manager.async_setup_sensor_platform(
        _async_add_entities,
        coordinator=Mock(),
        price_coordinator=Mock(),
        device=Mock(),
        region="no",
        create_current_month_sensors=False,
    )

    previous_snapshot = manager.get_snapshot()

    await manager._async_refresh_from_api()  # noqa: SLF001

    assert manager.get_snapshot() == previous_snapshot
    assert manager._refresh_handle is not None  # noqa: SLF001

    await manager.stop()


@pytest.mark.asyncio
async def test_refresh_from_api_repeated_failures_then_recovery(mock_hass) -> None:
    """Refresh should preserve snapshot on failures and recover availability."""
    stable_payload = _session_payload("6094111")
    updated_payload = _session_payload("6094111", addresses={"6094111": "Recovered 1"})

    api_client = Mock()
    api_client.get_session_payload = AsyncMock(
        side_effect=[
            APIError("temporary failure"),
            InvalidResponseError("bad payload"),
            updated_payload,
        ]
    )

    manager = SessionManager(mock_hass, "entry-id", api_client)
    manager.start()
    await manager.async_update_from_payload(stable_payload, source="setup")

    def _async_add_entities(new_entities, update_before_add=False):
        return None

    await manager.async_setup_sensor_platform(
        _async_add_entities,
        coordinator=Mock(),
        price_coordinator=Mock(),
        device=Mock(),
        region="no",
        create_current_month_sensors=False,
    )

    previous_snapshot = manager.get_snapshot()
    assert previous_snapshot is not None

    await manager._async_refresh_from_api()  # noqa: SLF001
    assert manager.get_snapshot() == previous_snapshot
    assert manager._session_available is False  # noqa: SLF001
    assert manager._refresh_handle is not None  # noqa: SLF001

    await manager._async_refresh_from_api()  # noqa: SLF001
    assert manager.get_snapshot() == previous_snapshot
    assert manager._session_available is False  # noqa: SLF001
    assert manager._refresh_handle is not None  # noqa: SLF001

    await manager._async_refresh_from_api()  # noqa: SLF001
    recovered_snapshot = manager.get_snapshot()
    assert recovered_snapshot is not None
    assert recovered_snapshot != previous_snapshot
    assert recovered_snapshot.metering_points[0].address == "Recovered 1"
    assert manager._session_available is True  # noqa: SLF001

    await manager.stop()
