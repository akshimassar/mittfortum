"""Button entities for MittFortum."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from .const import (
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
            MittFortumFullStatisticsSyncButton(
                coordinator=coordinator,
                device=device,
            )
        ]
    )


class MittFortumFullStatisticsSyncButton(MittFortumEntity, ButtonEntity):
    """Debug button to run full statistics resync."""

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
            name="Full Statistics Sync",
        )

    @property
    def available(self) -> bool:
        """Return if button is available."""
        return True

    async def async_press(self) -> None:
        """Run full backfill and overwrite existing points."""
        try:
            imported_points = await self.coordinator.async_run_statistics_sync(
                rewrite=True,
                allow_historical_backfill=True,
            )
        except APIError as exc:
            raise HomeAssistantError(f"Full statistics sync failed: {exc}") from exc

        _LOGGER.info(
            "Full statistics sync triggered manually, processed %d points",
            imported_points,
        )
