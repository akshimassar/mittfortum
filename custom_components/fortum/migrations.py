"""Registry migrations for Fortum integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.helpers import device_registry, entity_registry

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def _migrate_target_unique_id(
    entry_id: str,
    unique_id: str,
    legacy_prefixes: set[str],
) -> str | None:
    """Return migrated unique_id target for legacy prefix IDs."""
    if unique_id.startswith(f"{entry_id}_"):
        return None

    for legacy_prefix in legacy_prefixes:
        prefix = f"{legacy_prefix}_"
        if unique_id.startswith(prefix):
            suffix = unique_id[len(prefix) :]
            if suffix:
                return f"{entry_id}_{suffix}"
            return None

    return None


async def async_migrate_unique_ids_to_entry_id(
    hass: HomeAssistant,
    entry: ConfigEntry,
    *,
    customer_id: str | None,
    username: str,
) -> None:
    """Migrate Fortum entity and device identifiers to entry_id base."""
    config = getattr(hass, "config", None)
    if config is None or not hasattr(config, "config_dir"):
        _LOGGER.debug("skipping migration because hass config storage is unavailable")
        return

    entry_id = entry.entry_id
    legacy_prefixes = {username}

    if customer_id is not None and customer_id.strip():
        legacy_prefixes.add(customer_id)

    registry = entity_registry.async_get(hass)
    entity_entries = list(
        entity_registry.async_entries_for_config_entry(registry, entry_id)
    )
    fortum_entries = [
        entry_row for entry_row in entity_entries if entry_row.platform == DOMAIN
    ]
    unique_to_entity_id = {
        entry_row.unique_id: entry_row.entity_id for entry_row in fortum_entries
    }

    for entity_entry in fortum_entries:
        target_unique_id = _migrate_target_unique_id(
            entry_id,
            entity_entry.unique_id,
            legacy_prefixes,
        )
        if target_unique_id is None or target_unique_id == entity_entry.unique_id:
            continue

        existing_entity_id = unique_to_entity_id.get(target_unique_id)
        if (
            existing_entity_id is not None
            and existing_entity_id != entity_entry.entity_id
        ):
            _LOGGER.warning(
                "skipping unique_id migration due to collision entity_id=%s "
                "current_unique_id=%s target_unique_id=%s existing_entity_id=%s",
                entity_entry.entity_id,
                entity_entry.unique_id,
                target_unique_id,
                existing_entity_id,
            )
            continue

        registry.async_update_entity(
            entity_entry.entity_id,
            new_unique_id=target_unique_id,
        )
        unique_to_entity_id.pop(entity_entry.unique_id, None)
        unique_to_entity_id[target_unique_id] = entity_entry.entity_id
        _LOGGER.info(
            "migrated entity unique_id entity_id=%s old=%s new=%s",
            entity_entry.entity_id,
            entity_entry.unique_id,
            target_unique_id,
        )

    registry = device_registry.async_get(hass)
    for device_entry in device_registry.async_entries_for_config_entry(
        registry, entry_id
    ):
        identifiers = set(device_entry.identifiers)
        matched_fortum_identifiers = {
            identifier
            for identifier in identifiers
            if identifier[0] == DOMAIN and identifier[1] in legacy_prefixes
        }

        if not matched_fortum_identifiers:
            continue

        target_identifier = (DOMAIN, entry_id)
        if matched_fortum_identifiers == {target_identifier}:
            continue

        new_identifiers = identifiers - matched_fortum_identifiers
        new_identifiers.add(target_identifier)

        registry.async_update_device(
            device_entry.id,
            new_identifiers=new_identifiers,
        )
        _LOGGER.info(
            "migrated device identifier device_id=%s old=%s new=%s",
            device_entry.id,
            sorted(matched_fortum_identifiers),
            target_identifier,
        )


async def async_remove_legacy_spot_price_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Remove legacy non-area spot-price entities from the entity registry."""
    config = getattr(hass, "config", None)
    if config is None or not hasattr(config, "config_dir"):
        _LOGGER.debug(
            "skipping legacy spot-price cleanup because hass config storage "
            "is unavailable"
        )
        return

    registry = entity_registry.async_get(hass)
    entity_entries = list(
        entity_registry.async_entries_for_config_entry(registry, entry.entry_id)
    )

    legacy_unique_ids = {
        f"{entry.entry_id}_price_per_kwh",
        f"{entry.entry_id}_tomorrow_max_price",
        f"{entry.entry_id}_tomorrow_max_price_time",
    }

    for entity_entry in entity_entries:
        if entity_entry.platform != DOMAIN:
            continue
        if entity_entry.unique_id not in legacy_unique_ids:
            continue

        registry.async_remove(entity_entry.entity_id)
        _LOGGER.info(
            "removed legacy spot-price entity entity_id=%s unique_id=%s",
            entity_entry.entity_id,
            entity_entry.unique_id,
        )
