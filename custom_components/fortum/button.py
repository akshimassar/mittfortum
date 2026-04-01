"""Button entities for Fortum."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from . import (
    _async_ensure_dashboard_strategy_lovelace_resource,
    _async_force_recreate_dashboard_strategy_dashboard,
    pause_all_sync_schedules,
    resume_all_sync_schedules,
)
from .const import (
    CLEAR_STATS_BUTTON_KEY,
    CONF_DEBUG_ENTITIES,
    DEFAULT_DEBUG_ENTITIES,
    DOMAIN,
    RECREATE_DASHBOARD_BUTTON_KEY,
)
from .dashboard_strategy import (
    build_auto_dashboard_strategy_config,
    collect_available_metering_points,
)
from .entity import FortumEntity
from .exceptions import APIError

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinators.hourly_consumption import HourlyConsumptionSyncCoordinator
    from .device import FortumDevice

_LOGGER = logging.getLogger(__name__)


def _has_available_metering_points(hass: HomeAssistant) -> bool:
    """Return whether at least one metering point is available."""
    return bool(collect_available_metering_points(hass))


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
            FortumClearStatisticsButton(
                coordinator=coordinator,
                device=device,
                entry=entry,
            ),
            FortumForceRecreateDashboardButton(
                coordinator=coordinator,
                device=device,
                entry=entry,
            ),
        ]
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
        return _has_available_metering_points(self.coordinator.hass)

    async def async_press(self) -> None:
        """Clear all imported statistics for Fortum metering points."""
        pause_all_sync_schedules(self.coordinator.hass)
        coordinator = cast(Any, self.coordinator)
        try:
            cleared = await coordinator.async_clear_statistics()
        except APIError as exc:
            raise HomeAssistantError(f"Clear statistics failed: {exc}") from exc
        finally:
            resume_all_sync_schedules(self.coordinator.hass)

        _LOGGER.info(
            "manual statistics clear removed %d statistic ids",
            cleared,
        )


class FortumForceRecreateDashboardButton(FortumEntity, ButtonEntity):
    """Debug button to force-recreate dashboard from available points."""

    def __init__(
        self,
        coordinator: HourlyConsumptionSyncCoordinator,
        device: FortumDevice,
        entry: ConfigEntry,
    ) -> None:
        """Initialize force-recreate dashboard button."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=RECREATE_DASHBOARD_BUTTON_KEY,
            name="Re-Create Dashboard (Force)",
        )
        self._entry = entry

    @property
    def available(self) -> bool:
        """Return if button is available."""
        return _has_available_metering_points(self.coordinator.hass)

    async def async_press(self) -> None:
        """Force recreate /fortum-energy dashboard from current topology."""
        hass = self.coordinator.hass
        metering_points = collect_available_metering_points(hass)
        if not metering_points:
            raise HomeAssistantError(
                "No metering points found for dashboard generation"
            )

        strategy_config = build_auto_dashboard_strategy_config(metering_points)

        try:
            await _async_ensure_dashboard_strategy_lovelace_resource(hass)
            await _async_force_recreate_dashboard_strategy_dashboard(
                hass, strategy_config
            )
        except RuntimeError as exc:
            raise HomeAssistantError(str(exc)) from exc

        _LOGGER.debug(
            "force recreated dashboard using strategy %s with %d metering points",
            strategy_config["strategy"].get("type"),
            len(metering_points),
        )
