"""Platform for sensor integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import CONF_REGION, DEFAULT_REGION, DOMAIN
from .sensors import (
    MittFortumCostSensor,
    MittFortumEnergySensor,
    MittFortumPriceSensor,
    MittFortumStatisticsSyncSensor,
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
    """Set up MittFortum sensors based on a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    price_coordinator = data.get("price_coordinator", coordinator)
    device = data["device"]
    region = entry.data.get(CONF_REGION, DEFAULT_REGION)

    # Create sensor entities
    entities = [
        MittFortumEnergySensor(coordinator, device),
        MittFortumCostSensor(coordinator, device, region),
        MittFortumPriceSensor(price_coordinator, device, region),
        MittFortumStatisticsSyncSensor(coordinator, device),
    ]

    # Coordinators are refreshed during integration setup, so forcing another
    # refresh before adding entities only slows down reload/startup.
    async_add_entities(entities, update_before_add=False)
