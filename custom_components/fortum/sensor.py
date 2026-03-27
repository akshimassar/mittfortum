"""Platform for sensor integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import (
    CONF_DEBUG_ENTITIES,
    CONF_REGION,
    DEFAULT_DEBUG_ENTITIES,
    DEFAULT_REGION,
    DOMAIN,
)
from .sensors import (
    FortumMeteringPointSensor,
    FortumNorgesprisConsumptionLimitSensor,
    FortumPriceSensor,
    FortumStatisticsLastSyncSensor,
    FortumTomorrowMaxPriceSensor,
    FortumTomorrowMaxPriceTimeSensor,
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
    api_client = data.get("api_client")
    device = data["device"]
    metering_points = data.get("metering_points", [])
    region = entry.data.get(CONF_REGION, DEFAULT_REGION)
    price_areas = []
    if api_client is not None and hasattr(api_client, "get_price_areas"):
        price_areas = api_client.get_price_areas()

    # Create sensor entities
    entities = [
        *[
            FortumPriceSensor(price_coordinator, device, region, area_code)
            for area_code in price_areas
        ],
        *[
            FortumTomorrowMaxPriceSensor(price_coordinator, device, region, area_code)
            for area_code in price_areas
        ],
        *[
            FortumTomorrowMaxPriceTimeSensor(price_coordinator, device, area_code)
            for area_code in price_areas
        ],
        *[
            FortumMeteringPointSensor(device, metering_point)
            for metering_point in metering_points
        ],
        *[
            FortumNorgesprisConsumptionLimitSensor(device, metering_point)
            for metering_point in metering_points
            if region == "no"
        ],
    ]

    if entry.options.get(CONF_DEBUG_ENTITIES, DEFAULT_DEBUG_ENTITIES):
        entities.append(FortumStatisticsLastSyncSensor(coordinator, device))

    # Coordinators are refreshed during integration setup, so forcing another
    # refresh before adding entities only slows down reload/startup.
    async_add_entities(entities, update_before_add=False)
