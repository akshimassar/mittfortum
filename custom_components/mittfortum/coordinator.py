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
        self._historical_backfill_task = None

    async def async_run_statistics_sync(
        self,
        *,
        rewrite: bool = False,
        allow_historical_backfill: bool = False,
    ) -> int:
        """Run statistics sync and update sync timestamp."""
        imported_points = await self.api_client.backfill_hourly_statistics(
            rewrite=rewrite,
            allow_historical_backfill=allow_historical_backfill,
        )
        self.last_statistics_sync = datetime.now().astimezone()
        self.async_update_listeners()
        return imported_points

    async def async_schedule_initial_backfill(self) -> None:
        """Schedule non-blocking long historical backfill on first startup."""
        if self._historical_backfill_task and not self._historical_backfill_task.done():
            _LOGGER.debug("Initial historical backfill already scheduled/running")
            return

        has_price_stats = await self.api_client.has_existing_price_statistics()
        if has_price_stats:
            _LOGGER.debug(
                "Skipping initial historical backfill because price statistics exist"
            )
            return

        _LOGGER.debug("Scheduling initial historical backfill task")
        self._historical_backfill_task = self.hass.async_create_task(
            self._async_run_initial_backfill_task()
        )

    async def _async_run_initial_backfill_task(self) -> None:
        """Run historical backfill in background."""
        try:
            imported = await self.async_run_statistics_sync(
                rewrite=False,
                allow_historical_backfill=True,
            )
        except APIError as exc:
            _LOGGER.warning("Initial background statistics backfill failed: %s", exc)
        else:
            _LOGGER.info(
                "Initial background statistics backfill completed, processed %d points",
                imported,
            )

    async def async_clear_statistics(self) -> int:
        """Clear all imported statistics for this integration."""
        cleared = await self.api_client.clear_hourly_statistics()
        self.last_statistics_sync = None
        self.async_update_listeners()
        return cleared

    async def _async_update_data(self) -> list[ConsumptionData]:
        """Fetch data from API."""
        try:
            _LOGGER.debug("Fetching consumption data from API")
            data = await self.api_client.get_total_consumption()
            if data is None:
                data = []

            try:
                imported_points = await self.async_run_statistics_sync(
                    rewrite=False,
                    allow_historical_backfill=False,
                )
            except APIError as exc:
                _LOGGER.warning("Hourly statistics sync failed: %s", exc)
            else:
                _LOGGER.debug(
                    "Hourly statistics sync completed, processed %d points",
                    imported_points,
                )

            _LOGGER.debug("Successfully fetched %d consumption records", len(data))
        except APIError as exc:
            # For authentication errors, provide more specific error message
            if (
                "Token expired" in str(exc)
                or "Access forbidden" in str(exc)
                or "Authentication failed" in str(exc)
            ):
                _LOGGER.warning(
                    "Authentication error during data update: %s. "
                    "This may be temporary due to session propagation.",
                    exc,
                )
                raise UpdateFailed(f"Authentication error: {exc}") from exc
            else:
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
