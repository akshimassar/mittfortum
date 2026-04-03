"""Tests for Fortum registry migrations."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from custom_components.fortum.migrations import (
    async_migrate_unique_ids_to_entry_id,
    async_remove_legacy_spot_price_entities,
)


class _FakeEntityRegistry:
    def __init__(self) -> None:
        self.updated: list[tuple[str, str]] = []
        self.removed: list[str] = []

    def async_update_entity(self, entity_id: str, *, new_unique_id: str) -> None:
        self.updated.append((entity_id, new_unique_id))

    def async_remove(self, entity_id: str) -> None:
        self.removed.append(entity_id)


class _FakeDeviceRegistry:
    def __init__(self) -> None:
        self.updated: list[tuple[str, set[tuple[str, str]]]] = []

    def async_update_device(
        self,
        device_id: str,
        *,
        new_identifiers: set[tuple[str, str]],
    ) -> None:
        self.updated.append((device_id, new_identifiers))


async def test_migrate_unique_ids_to_entry_id_updates_entities_and_device(
    mock_hass,
) -> None:
    """Migrate legacy customer-based identifiers to entry-based identifiers."""
    entry = SimpleNamespace(entry_id="01ABC")
    entity_entries = [
        SimpleNamespace(
            entity_id="sensor.price_per_kwh",
            unique_id="55650898_price_per_kwh",
            platform="fortum",
        ),
        SimpleNamespace(
            entity_id="sensor.metering_point_6094111",
            unique_id="55650898_metering_point_6094111",
            platform="fortum",
        ),
    ]
    device_entries = [
        SimpleNamespace(
            id="device-1",
            identifiers={("fortum", "55650898"), ("other", "value")},
        )
    ]

    entity_registry = _FakeEntityRegistry()
    device_registry = _FakeDeviceRegistry()

    with (
        patch(
            "custom_components.fortum.migrations.entity_registry.async_get",
            return_value=entity_registry,
        ),
        patch(
            "custom_components.fortum.migrations.entity_registry.async_entries_for_config_entry",
            return_value=entity_entries,
        ),
        patch(
            "custom_components.fortum.migrations.device_registry.async_get",
            return_value=device_registry,
        ),
        patch(
            "custom_components.fortum.migrations.device_registry.async_entries_for_config_entry",
            return_value=device_entries,
        ),
    ):
        await async_migrate_unique_ids_to_entry_id(
            mock_hass,
            cast(Any, entry),
            customer_id="55650898",
            username="test@example.com",
        )

    assert entity_registry.updated == [
        ("sensor.price_per_kwh", "01ABC_price_per_kwh"),
        ("sensor.metering_point_6094111", "01ABC_metering_point_6094111"),
    ]
    assert device_registry.updated == [
        ("device-1", {("fortum", "01ABC"), ("other", "value")})
    ]


async def test_migrate_unique_ids_to_entry_id_skips_when_already_migrated(
    mock_hass,
) -> None:
    """Do nothing when all identifiers already use entry_id."""
    entry = SimpleNamespace(entry_id="01ABC")
    entity_entries = [
        SimpleNamespace(
            entity_id="sensor.price_per_kwh",
            unique_id="01ABC_price_per_kwh",
            platform="fortum",
        )
    ]
    device_entries = [SimpleNamespace(id="device-1", identifiers={("fortum", "01ABC")})]

    entity_registry = _FakeEntityRegistry()
    device_registry = _FakeDeviceRegistry()

    with (
        patch(
            "custom_components.fortum.migrations.entity_registry.async_get",
            return_value=entity_registry,
        ),
        patch(
            "custom_components.fortum.migrations.entity_registry.async_entries_for_config_entry",
            return_value=entity_entries,
        ),
        patch(
            "custom_components.fortum.migrations.device_registry.async_get",
            return_value=device_registry,
        ),
        patch(
            "custom_components.fortum.migrations.device_registry.async_entries_for_config_entry",
            return_value=device_entries,
        ),
    ):
        await async_migrate_unique_ids_to_entry_id(
            mock_hass,
            cast(Any, entry),
            customer_id="55650898",
            username="test@example.com",
        )

    assert entity_registry.updated == []
    assert device_registry.updated == []


async def test_migrate_unique_ids_to_entry_id_skips_collision(mock_hass) -> None:
    """Skip migration when target unique_id already exists."""
    entry = SimpleNamespace(entry_id="01ABC")
    entity_entries = [
        SimpleNamespace(
            entity_id="sensor.price_per_kwh",
            unique_id="01ABC_price_per_kwh",
            platform="fortum",
        ),
        SimpleNamespace(
            entity_id="sensor.price_per_kwh_legacy",
            unique_id="55650898_price_per_kwh",
            platform="fortum",
        ),
    ]

    entity_registry = _FakeEntityRegistry()
    device_registry = _FakeDeviceRegistry()

    with (
        patch(
            "custom_components.fortum.migrations.entity_registry.async_get",
            return_value=entity_registry,
        ),
        patch(
            "custom_components.fortum.migrations.entity_registry.async_entries_for_config_entry",
            return_value=entity_entries,
        ),
        patch(
            "custom_components.fortum.migrations.device_registry.async_get",
            return_value=device_registry,
        ),
        patch(
            "custom_components.fortum.migrations.device_registry.async_entries_for_config_entry",
            return_value=[],
        ),
    ):
        await async_migrate_unique_ids_to_entry_id(
            mock_hass,
            cast(Any, entry),
            customer_id="55650898",
            username="test@example.com",
        )

    assert entity_registry.updated == []


async def test_migrate_unique_ids_to_entry_id_uses_username_prefix_when_needed(
    mock_hass,
) -> None:
    """Migrate entities created with username-based unique_id prefix."""
    entry = SimpleNamespace(entry_id="01ABC")
    entity_entries = [
        SimpleNamespace(
            entity_id="sensor.price_per_kwh",
            unique_id="user@example.com_price_per_kwh",
            platform="fortum",
        )
    ]

    entity_registry = _FakeEntityRegistry()
    device_registry = _FakeDeviceRegistry()

    with (
        patch(
            "custom_components.fortum.migrations.entity_registry.async_get",
            return_value=entity_registry,
        ),
        patch(
            "custom_components.fortum.migrations.entity_registry.async_entries_for_config_entry",
            return_value=entity_entries,
        ),
        patch(
            "custom_components.fortum.migrations.device_registry.async_get",
            return_value=device_registry,
        ),
        patch(
            "custom_components.fortum.migrations.device_registry.async_entries_for_config_entry",
            return_value=[],
        ),
    ):
        await async_migrate_unique_ids_to_entry_id(
            mock_hass,
            cast(Any, entry),
            customer_id=None,
            username="user@example.com",
        )

    assert entity_registry.updated == [("sensor.price_per_kwh", "01ABC_price_per_kwh")]


async def test_remove_legacy_spot_price_entities(mock_hass) -> None:
    """Remove legacy non-area spot-price entities after migration."""
    entry = SimpleNamespace(entry_id="01ABC")
    entity_entries = [
        SimpleNamespace(
            entity_id="sensor.price_per_kwh",
            unique_id="01ABC_price_per_kwh",
            platform="fortum",
        ),
        SimpleNamespace(
            entity_id="sensor.tomorrow_max_price",
            unique_id="01ABC_tomorrow_max_price",
            platform="fortum",
        ),
        SimpleNamespace(
            entity_id="sensor.tomorrow_max_price_time",
            unique_id="01ABC_tomorrow_max_price_time",
            platform="fortum",
        ),
        SimpleNamespace(
            entity_id="sensor.price_per_kwh_se3",
            unique_id="01ABC_price_per_kwh_se3",
            platform="fortum",
        ),
    ]

    entity_registry = _FakeEntityRegistry()

    with (
        patch(
            "custom_components.fortum.migrations.entity_registry.async_get",
            return_value=entity_registry,
        ),
        patch(
            "custom_components.fortum.migrations.entity_registry.async_entries_for_config_entry",
            return_value=entity_entries,
        ),
    ):
        await async_remove_legacy_spot_price_entities(mock_hass, cast(Any, entry))

    assert entity_registry.removed == [
        "sensor.price_per_kwh",
        "sensor.tomorrow_max_price",
        "sensor.tomorrow_max_price_time",
    ]
