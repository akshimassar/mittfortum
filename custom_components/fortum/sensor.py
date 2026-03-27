"""Platform for sensor integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import (
    CONF_REGION,
    DEFAULT_REGION,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fortum sensors based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    price_coordinator = data.get("price_coordinator", coordinator)
    session_manager = data["session_manager"]
    device = data["device"]
    region = entry.data.get(CONF_REGION, DEFAULT_REGION)

    await session_manager.async_setup_sensor_platform(
        async_add_entities,
        coordinator=coordinator,
        price_coordinator=price_coordinator,
        device=device,
        region=region,
    )
