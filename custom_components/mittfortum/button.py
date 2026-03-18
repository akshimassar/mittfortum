"""Button entities for MittFortum."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .const import (
    CLEAR_STATS_BUTTON_KEY,
    CONF_DEBUG_ENTITIES,
    DEFAULT_DEBUG_ENTITIES,
    DOMAIN,
    FULL_SYNC_BUTTON_KEY,
)
from .entity import MittFortumEntity
from .exceptions import APIError

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import MittFortumDataCoordinator
    from .device import MittFortumDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up MittFortum button entities from config entry."""
    if not entry.options.get(CONF_DEBUG_ENTITIES, DEFAULT_DEBUG_ENTITIES):
        return

    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: MittFortumDataCoordinator = data["coordinator"]
    device: MittFortumDevice = data["device"]

    async_add_entities(
        [
            MittFortumFullHistoryResyncButton(
                coordinator=coordinator,
                device=device,
            ),
            MittFortumClearStatisticsButton(
                coordinator=coordinator,
                device=device,
            ),
        ]
    )


class MittFortumFullHistoryResyncButton(MittFortumEntity, ButtonEntity):
    """Debug button to run full history re-sync."""

    def __init__(
        self,
        coordinator: MittFortumDataCoordinator,
        device: MittFortumDevice,
    ) -> None:
        """Initialize full sync button."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=FULL_SYNC_BUTTON_KEY,
            name="Full History Re-Sync",
        )

    async def async_press(self) -> None:
        """Run full history re-sync from earliest available date."""
        try:
            imported_points = await self.coordinator.async_run_statistics_sync(
                force_resync=True,
            )
        except APIError as exc:
            raise HomeAssistantError(f"Full history re-sync failed: {exc}") from exc

        _LOGGER.info(
            "Full history re-sync triggered manually, processed %d points",
            imported_points,
        )


class MittFortumClearStatisticsButton(MittFortumEntity, ButtonEntity):
    """Debug button to clear imported statistics."""

    def __init__(
        self,
        coordinator: MittFortumDataCoordinator,
        device: MittFortumDevice,
    ) -> None:
        """Initialize clear statistics button."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=CLEAR_STATS_BUTTON_KEY,
            name="Clear Statistics",
        )

    async def async_press(self) -> None:
        """Clear all imported statistics for MittFortum metering points."""
        try:
            cleared = await self.coordinator.async_clear_statistics()
        except APIError as exc:
            raise HomeAssistantError(f"Clear statistics failed: {exc}") from exc

        _LOGGER.info(
            "Statistics clear triggered manually, removed %d statistic ids",
            cleared,
        )
