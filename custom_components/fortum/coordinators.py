"""Data update coordinators for Fortum integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta  # noqa: TC003
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_UPDATE_INTERVAL, PRICE_UPDATE_INTERVAL
from .exceptions import APIError, AuthenticationError
from .models import ConsumptionData, SpotPricePoint

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .api import FortumAPIClient

_LOGGER = logging.getLogger(__name__)


class HourlyConsumptionSyncCoordinator(DataUpdateCoordinator[list[ConsumptionData]]):
    """Scheduler for hourly statistics sync."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: FortumAPIClient,
        update_interval: timedelta = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        """Initialize scheduler."""
        super().__init__(
            hass,
            _LOGGER,
            name="Fortum",
            update_interval=update_interval,
        )
        self.api_client = api_client
        self.last_statistics_sync: datetime | None = None

    async def async_run_statistics_sync(
        self,
        *,
        force_resync: bool = False,
    ) -> int:
        """Run statistics sync and update sync timestamp."""
        imported_points = await self.api_client.sync_hourly_data_all_meters(
            force_resync=force_resync,
        )
        self.last_statistics_sync = datetime.now().astimezone()
        self.async_update_listeners()
        return imported_points

    async def async_clear_statistics(self) -> int:
        """Clear all imported statistics for this integration."""
        cleared = await self.api_client.clear_hourly_statistics()
        self.last_statistics_sync = None
        self.async_update_listeners()
        return cleared

    async def _async_update_data(self) -> list[ConsumptionData]:
        """Fetch data from API."""
        try:
            _LOGGER.debug("HourlyConsumptionSyncCoordinator._async_update_data: start")
            data: list[ConsumptionData] = []

            try:
                imported_points = await self.async_run_statistics_sync(
                    force_resync=False,
                )
            except APIError as exc:
                _LOGGER.warning(
                    "HourlyConsumptionSyncCoordinator._async_update_data: failed: %s",
                    exc,
                )
            else:
                _LOGGER.debug(
                    "HourlyConsumptionSyncCoordinator._async_update_data: "
                    "processed_points=%d",
                    imported_points,
                )
        except APIError as exc:
            _LOGGER.exception(
                "HourlyConsumptionSyncCoordinator._async_update_data: API error"
            )
            raise UpdateFailed(f"API error: {exc}") from exc
        except AuthenticationError as exc:
            _LOGGER.exception(
                "HourlyConsumptionSyncCoordinator._async_update_data: auth error"
            )
            raise ConfigEntryAuthFailed("Authentication failed") from exc
        except Exception as exc:
            _LOGGER.exception(
                "HourlyConsumptionSyncCoordinator._async_update_data: unexpected error"
            )
            raise UpdateFailed(f"Unexpected error: {exc}") from exc
        else:
            return data


class SpotPriceSyncCoordinator(DataUpdateCoordinator[list[SpotPricePoint]]):
    """Scheduler for near-real-time spot price refreshes."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: FortumAPIClient,
        update_interval: timedelta = PRICE_UPDATE_INTERVAL,
    ) -> None:
        """Initialize price scheduler."""
        super().__init__(
            hass,
            _LOGGER,
            name="Fortum Price",
            update_interval=update_interval,
        )
        self.api_client = api_client

    async def _async_update_data(self) -> list[SpotPricePoint]:
        """Fetch price data from API."""
        try:
            _LOGGER.debug("SpotPriceSyncCoordinator._async_update_data: start")
            data = await self.api_client.get_price_data()
            if data is None:
                data = []
            _LOGGER.debug(
                "SpotPriceSyncCoordinator._async_update_data: fetched_records=%d",
                len(data),
            )
        except AuthenticationError as exc:
            _LOGGER.exception("SpotPriceSyncCoordinator._async_update_data: auth error")
            raise ConfigEntryAuthFailed("Authentication failed") from exc
        except APIError as exc:
            _LOGGER.exception("SpotPriceSyncCoordinator._async_update_data: API error")
            raise UpdateFailed(f"API error: {exc}") from exc
        except Exception as exc:
            _LOGGER.exception(
                "SpotPriceSyncCoordinator._async_update_data: unexpected error"
            )
            raise UpdateFailed(f"Unexpected error: {exc}") from exc
        else:
            return data
