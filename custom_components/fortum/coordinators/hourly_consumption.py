"""Hourly consumption sync coordinator for Fortum integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta  # noqa: TC003
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ..const import DEFAULT_UPDATE_INTERVAL
from ..exceptions import APIError, AuthenticationError
from ..models import ConsumptionData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..api import FortumAPIClient
    from ..session_manager import SessionManager, SessionSnapshot

_LOGGER = logging.getLogger(__name__)
_COORDINATOR_LOGGER = logging.getLogger(f"{__name__}.refresh")
_COORDINATOR_LOGGER.setLevel(logging.INFO)


class HourlyConsumptionSyncCoordinator(DataUpdateCoordinator[list[ConsumptionData]]):
    """Scheduler for hourly statistics sync."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: FortumAPIClient,
        session_manager: SessionManager,
        update_interval: timedelta = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        """Initialize scheduler."""
        super().__init__(
            hass,
            _COORDINATOR_LOGGER,
            name="Fortum",
            update_interval=update_interval,
        )
        self.api_client = api_client
        self._session_manager = session_manager
        self.last_statistics_sync: datetime | None = None

    def _require_snapshot(self) -> SessionSnapshot:
        """Return current session snapshot or fail coordinator update."""
        snapshot = self._session_manager.get_snapshot()
        if snapshot is None:
            raise UpdateFailed("Session snapshot unavailable")
        return snapshot

    async def async_run_statistics_sync(
        self,
        *,
        force_resync: bool = False,
    ) -> int:
        """Run statistics sync and update sync timestamp."""
        snapshot = self._require_snapshot()
        imported_points = await self.api_client.sync_hourly_data_for_metering_points(
            snapshot.metering_points,
            force_resync=force_resync,
        )
        self.last_statistics_sync = datetime.now().astimezone()
        self.async_update_listeners()
        return imported_points

    async def async_clear_statistics(self) -> int:
        """Clear all imported statistics for this integration."""
        snapshot = self._require_snapshot()
        cleared = await self.api_client.clear_hourly_statistics_for_topology(
            snapshot.metering_points,
            snapshot.price_areas,
        )
        self.last_statistics_sync = None
        self.async_update_listeners()
        return cleared

    async def _async_update_data(self) -> list[ConsumptionData]:
        """Fetch data from API."""
        try:
            data: list[ConsumptionData] = []
            await self.async_run_statistics_sync(
                force_resync=False,
            )
        except APIError as exc:
            _LOGGER.exception("hourly sync API error")
            raise UpdateFailed(f"API error: {exc}") from exc
        except AuthenticationError as exc:
            _LOGGER.exception("hourly sync auth error")
            raise ConfigEntryAuthFailed("Authentication failed") from exc
        except UpdateFailed:
            raise
        except Exception as exc:
            _LOGGER.exception("hourly sync unexpected error")
            raise UpdateFailed(f"Unexpected error: {exc}") from exc
        else:
            return data
