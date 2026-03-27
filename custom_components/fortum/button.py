"""Button entities for Fortum."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from . import pause_all_sync_schedules, resume_all_sync_schedules
from .const import (
    CLEAR_STATS_BUTTON_KEY,
    CONF_DEBUG_ENTITIES,
    DEFAULT_DEBUG_ENTITIES,
    DOMAIN,
    FULL_SYNC_BUTTON_KEY,
)
from .entity import FortumEntity
from .exceptions import APIError

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinators import HourlyConsumptionSyncCoordinator
    from .device import FortumDevice

_LOGGER = logging.getLogger(__name__)


def _has_authenticated_session(hass: HomeAssistant, entry_id: str) -> bool:
    """Return whether the integration has a usable auth/session context."""
    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not isinstance(entry_data, dict):
        return False

    session_manager = entry_data.get("session_manager")
    if session_manager is None:
        return False

    snapshot = session_manager.get_snapshot()
    if snapshot is None:
        return False

    return bool(snapshot.customer_id or snapshot.metering_points)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fortum button entities from config entry."""
    if not entry.options.get(CONF_DEBUG_ENTITIES, DEFAULT_DEBUG_ENTITIES):
        return

    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: HourlyConsumptionSyncCoordinator = data["coordinator"]
    device: FortumDevice = data["device"]

    async_add_entities(
        [
            FortumFullHistoryResyncButton(
                coordinator=coordinator,
                device=device,
                entry=entry,
            ),
            FortumClearStatisticsButton(
                coordinator=coordinator,
                device=device,
                entry=entry,
            ),
        ]
    )


class FortumFullHistoryResyncButton(FortumEntity, ButtonEntity):
    """Debug button to run full history re-sync."""

    def __init__(
        self,
        coordinator: HourlyConsumptionSyncCoordinator,
        device: FortumDevice,
        entry: ConfigEntry,
    ) -> None:
        """Initialize full sync button."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=FULL_SYNC_BUTTON_KEY,
            name="Full History Re-Sync",
        )
        self._entry = entry

    @property
    def available(self) -> bool:
        """Return if button is available."""
        return _has_authenticated_session(self.coordinator.hass, self._entry.entry_id)

    async def async_press(self) -> None:
        """Run full history re-sync from earliest available date."""
        started = time.perf_counter()
        pause_all_sync_schedules(self.coordinator.hass)
        try:
            imported_points = await self.coordinator.async_run_statistics_sync(
                force_resync=True,
            )
        except APIError as exc:
            elapsed = time.perf_counter() - started
            raise HomeAssistantError(
                f"Full history re-sync failed after {elapsed:.2f}s: {exc}"
            ) from exc
        finally:
            resume_all_sync_schedules(self.coordinator.hass)

        elapsed = time.perf_counter() - started
        _LOGGER.info(
            "manual full-history re-sync processed %d points in %.2fs",
            imported_points,
            elapsed,
        )


class FortumClearStatisticsButton(FortumEntity, ButtonEntity):
    """Debug button to clear imported statistics."""

    def __init__(
        self,
        coordinator: HourlyConsumptionSyncCoordinator,
        device: FortumDevice,
        entry: ConfigEntry,
    ) -> None:
        """Initialize clear statistics button."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=CLEAR_STATS_BUTTON_KEY,
            name="Clear Statistics",
        )
        self._entry = entry

    @property
    def available(self) -> bool:
        """Return if button is available."""
        return _has_authenticated_session(self.coordinator.hass, self._entry.entry_id)

    async def async_press(self) -> None:
        """Clear all imported statistics for Fortum metering points."""
        pause_all_sync_schedules(self.coordinator.hass)
        try:
            cleared = await self.coordinator.async_clear_statistics()
        except APIError as exc:
            raise HomeAssistantError(f"Clear statistics failed: {exc}") from exc
        finally:
            resume_all_sync_schedules(self.coordinator.hass)

        _LOGGER.info(
            "manual statistics clear removed %d statistic ids",
            cleared,
        )
