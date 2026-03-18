"""Data update coordinator for MittFortum integration."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta  # noqa: TC003
from typing import TYPE_CHECKING

from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_UPDATE_INTERVAL, PRICE_UPDATE_INTERVAL
from .exceptions import APIError
from .models import ConsumptionData

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .api import FortumAPIClient

_LOGGER = logging.getLogger(__name__)


class MittFortumDataCoordinator(DataUpdateCoordinator[list[ConsumptionData]]):
    """Data update coordinator for MittFortum."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: FortumAPIClient,
        update_interval: timedelta = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="MittFortum",
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
        imported_points = await self.api_client.backfill_hourly_statistics(
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
            _LOGGER.debug("Running statistics sync cycle")
            data: list[ConsumptionData] = []

            try:
                imported_points = await self.async_run_statistics_sync(
                    force_resync=False,
                )
            except APIError as exc:
                _LOGGER.warning("Hourly statistics sync failed: %s", exc)
            else:
                _LOGGER.debug(
                    "Hourly statistics sync completed, processed %d points",
                    imported_points,
                )

            _LOGGER.debug("Statistics sync cycle finished")
        except APIError as exc:
            _LOGGER.exception("API error during data update")
            raise UpdateFailed(f"API error: {exc}") from exc
        except Exception as exc:
            _LOGGER.exception("Unexpected error during data update")
            raise UpdateFailed(f"Unexpected error: {exc}") from exc
        else:
            return data


class MittFortumPriceCoordinator(DataUpdateCoordinator[list[ConsumptionData]]):
    """Price update coordinator for MittFortum."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: FortumAPIClient,
        update_interval: timedelta = PRICE_UPDATE_INTERVAL,
    ) -> None:
        """Initialize price coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="MittFortum Price",
            update_interval=update_interval,
        )
        self.api_client = api_client

    async def _async_update_data(self) -> list[ConsumptionData]:
        """Fetch price data from API."""
        try:
            _LOGGER.debug("Fetching price data from API")
            data = await self.api_client.get_price_data()
            if data is None:
                data = []
            _LOGGER.debug("Successfully fetched %d price records", len(data))
        except APIError as exc:
            _LOGGER.exception("API error during price update")
            raise UpdateFailed(f"API error: {exc}") from exc
        except Exception as exc:
            _LOGGER.exception("Unexpected error during price update")
            raise UpdateFailed(f"Unexpected error: {exc}") from exc
        else:
            return data
