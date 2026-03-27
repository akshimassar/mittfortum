"""Spot-price sync coordinator for Fortum integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from time import monotonic
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from ..const import PRICE_UPDATE_INTERVAL
from ..exceptions import APIError, AuthenticationError
from ..models import SpotPricePoint

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..api import FortumAPIClient
    from ..session_manager import SessionManager, SessionSnapshot

_LOGGER = logging.getLogger(__name__)
_COORDINATOR_LOGGER = logging.getLogger(f"{__name__}.refresh")
_COORDINATOR_LOGGER.setLevel(logging.INFO)


class SpotPriceSyncCoordinator(DataUpdateCoordinator[list[SpotPricePoint]]):
    """Scheduler for near-real-time spot price refreshes."""

    def __init__(
        self,
        hass: HomeAssistant,
        api_client: FortumAPIClient,
        session_manager: SessionManager,
        update_interval: timedelta = PRICE_UPDATE_INTERVAL,
    ) -> None:
        """Initialize price scheduler."""
        super().__init__(
            hass,
            _COORDINATOR_LOGGER,
            name="Fortum Price",
            update_interval=update_interval,
        )
        self.api_client = api_client
        self._session_manager = session_manager

    def _require_snapshot(self) -> SessionSnapshot:
        """Return current session snapshot or fail coordinator update."""
        snapshot = self._session_manager.get_snapshot()
        if snapshot is None:
            raise UpdateFailed("Session snapshot unavailable")
        return snapshot

    async def _async_update_data(self) -> list[SpotPricePoint]:
        """Fetch price data from API."""
        try:
            snapshot = self._require_snapshot()
            start = monotonic()
            data = await self.api_client.fetch_spot_prices_for_areas(
                snapshot.price_areas,
            )
            if data is None:
                data = []
            _LOGGER.debug(
                "spot price refresh done records=%d elapsed=%.3fs",
                len(data),
                monotonic() - start,
            )
        except AuthenticationError as exc:
            _LOGGER.exception("spot price sync auth error")
            raise ConfigEntryAuthFailed("Authentication failed") from exc
        except APIError as exc:
            _LOGGER.warning("spot price sync API error: %s", exc)
            raise UpdateFailed(f"API error: {exc}") from exc
        except UpdateFailed:
            raise
        except Exception as exc:
            _LOGGER.exception("spot price sync unexpected error")
            raise UpdateFailed(f"Unexpected error: {exc}") from exc
        else:
            return data
