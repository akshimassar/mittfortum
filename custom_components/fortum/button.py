"""Button entities for Fortum."""

from __future__ import annotations

import logging
import time
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
    FULL_SYNC_BUTTON_KEY,
    RECREATE_MULTIPOINT_DASHBOARD_BUTTON_KEY,
    RECREATE_SINGLE_DASHBOARD_BUTTON_KEY,
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
_METERING_POINT_NUMBER_KEY = "number"
_METERING_POINT_NAME_KEY = "name"


def _build_multipoint_dashboard_strategy_config(
    metering_points: list[Any],
) -> dict[str, Any]:
    """Build one-time multipoint strategy config payload."""
    strategy_points: list[dict[str, Any]] = []
    for point in metering_points:
        metering_point_no = getattr(point, "metering_point_no", None)
        if not isinstance(metering_point_no, str) or not metering_point_no.strip():
            continue
        metering_point_no = metering_point_no.strip()
        address = getattr(point, "address", None)
        name = (
            address.strip()
            if isinstance(address, str) and address.strip()
            else metering_point_no
        )
        strategy_points.append(
            {
                _METERING_POINT_NUMBER_KEY: metering_point_no,
                _METERING_POINT_NAME_KEY: name,
                "itemization": [],
            }
        )

    strategy_points.sort(key=lambda item: item[_METERING_POINT_NUMBER_KEY])
    return {
        "strategy": {
            "type": "custom:fortum-energy-multipoint",
            "metering_points": strategy_points,
        }
    }


def _build_single_dashboard_strategy_config(
    metering_points: list[Any],
) -> dict[str, Any]:
    """Build one-time single strategy config payload."""
    point_numbers = sorted(
        {
            metering_point_no.strip()
            for point in metering_points
            for metering_point_no in [getattr(point, "metering_point_no", None)]
            if isinstance(metering_point_no, str) and metering_point_no.strip()
        }
    )

    if not point_numbers:
        raise HomeAssistantError("No metering points found for dashboard generation")
    if len(point_numbers) > 1:
        raise HomeAssistantError(
            "Multiple metering points found; use multipoint dashboard button"
        )

    return {
        "strategy": {
            "type": "custom:fortum-energy-single",
            "fortum": {
                "metering_point_number": point_numbers[0],
            },
        }
    }


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
            FortumForceRecreateSingleDashboardButton(
                coordinator=coordinator,
                device=device,
                entry=entry,
            ),
            FortumForceRecreateMultipointDashboardButton(
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
        coordinator = cast(Any, self.coordinator)
        try:
            imported_points = await coordinator.async_run_statistics_sync(
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


class FortumForceRecreateMultipointDashboardButton(FortumEntity, ButtonEntity):
    """Debug button to force-recreate multipoint dashboard."""

    def __init__(
        self,
        coordinator: HourlyConsumptionSyncCoordinator,
        device: FortumDevice,
        entry: ConfigEntry,
    ) -> None:
        """Initialize force-recreate multipoint dashboard button."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=RECREATE_MULTIPOINT_DASHBOARD_BUTTON_KEY,
            name="Force Recreate Multipoint Dashboard",
        )
        self._entry = entry

    @property
    def available(self) -> bool:
        """Return if button is available."""
        return _has_authenticated_session(self.coordinator.hass, self._entry.entry_id)

    async def async_press(self) -> None:
        """Force recreate /fortum-energy dashboard with multipoint strategy config."""
        hass = self.coordinator.hass
        entry_data = hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        if not isinstance(entry_data, dict):
            raise HomeAssistantError("Entry data is unavailable")

        session_manager = entry_data.get("session_manager")
        snapshot = (
            session_manager.get_snapshot() if session_manager is not None else None
        )
        metering_points = list(snapshot.metering_points) if snapshot else []
        if not metering_points:
            raise HomeAssistantError(
                "No metering points found for dashboard generation"
            )

        strategy_config = _build_multipoint_dashboard_strategy_config(metering_points)
        if not strategy_config["strategy"]["metering_points"]:
            raise HomeAssistantError("No valid metering point numbers found")

        try:
            await _async_ensure_dashboard_strategy_lovelace_resource(hass)
            await _async_force_recreate_dashboard_strategy_dashboard(
                hass, strategy_config
            )
        except RuntimeError as exc:
            raise HomeAssistantError(str(exc)) from exc

        _LOGGER.debug(
            "force recreated multipoint dashboard with %d metering points",
            len(strategy_config["strategy"]["metering_points"]),
        )


class FortumForceRecreateSingleDashboardButton(FortumEntity, ButtonEntity):
    """Debug button to force-recreate single dashboard."""

    def __init__(
        self,
        coordinator: HourlyConsumptionSyncCoordinator,
        device: FortumDevice,
        entry: ConfigEntry,
    ) -> None:
        """Initialize force-recreate single dashboard button."""
        super().__init__(
            coordinator=coordinator,
            device=device,
            entity_key=RECREATE_SINGLE_DASHBOARD_BUTTON_KEY,
            name="Force Recreate Single Dashboard",
        )
        self._entry = entry

    @property
    def available(self) -> bool:
        """Return if button is available."""
        return _has_authenticated_session(self.coordinator.hass, self._entry.entry_id)

    async def async_press(self) -> None:
        """Force recreate /fortum-energy dashboard with single strategy config."""
        hass = self.coordinator.hass
        entry_data = hass.data.get(DOMAIN, {}).get(self._entry.entry_id)
        if not isinstance(entry_data, dict):
            raise HomeAssistantError("Entry data is unavailable")

        session_manager = entry_data.get("session_manager")
        snapshot = (
            session_manager.get_snapshot() if session_manager is not None else None
        )
        metering_points = list(snapshot.metering_points) if snapshot else []

        strategy_config = _build_single_dashboard_strategy_config(metering_points)

        try:
            await _async_ensure_dashboard_strategy_lovelace_resource(hass)
            await _async_force_recreate_dashboard_strategy_dashboard(
                hass, strategy_config
            )
        except RuntimeError as exc:
            raise HomeAssistantError(str(exc)) from exc

        _LOGGER.debug(
            "force recreated single dashboard for metering point %s",
            strategy_config["strategy"]["fortum"]["metering_point_number"],
        )
