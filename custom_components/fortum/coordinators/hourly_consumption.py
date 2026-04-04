"""Hourly consumption sync coordinator for Fortum integration."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timedelta  # noqa: TC003
from typing import TYPE_CHECKING

from homeassistant.components.recorder.statistics import (
    get_metadata,
    statistics_during_period,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.recorder import get_instance
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ..const import DEFAULT_UPDATE_INTERVAL, HOURLY_DATA_RECENT_WINDOW_DAYS
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
        self._current_month_consumption_totals: dict[str, float] = {}
        self._current_month_cost_totals: dict[str, float] = {}
        self._current_month_consumption_units: dict[str, str] = {}
        self._current_month_cost_units: dict[str, str] = {}

    def _require_snapshot(self) -> SessionSnapshot:
        """Return current session snapshot or fail coordinator update."""
        snapshot = self._session_manager.get_snapshot()
        if snapshot is None:
            raise UpdateFailed("Session snapshot unavailable")
        return snapshot

    async def async_run_statistics_sync(
        self,
    ) -> int:
        """Run statistics sync and update sync timestamp."""
        snapshot = self._require_snapshot()
        imported_points = await self.api_client.sync_hourly_data_for_metering_points(
            snapshot.metering_points,
        )
        try:
            await self._async_refresh_current_month_totals(snapshot)
        except Exception as exc:
            _LOGGER.info("failed to refresh current-month totals: %s", exc)
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
        self._current_month_consumption_totals.clear()
        self._current_month_cost_totals.clear()
        self._current_month_consumption_units.clear()
        self._current_month_cost_units.clear()
        self.last_statistics_sync = None
        self.async_update_listeners()
        return cleared

    def get_current_month_consumption_total(
        self,
        metering_point_no: str,
    ) -> float | None:
        """Return current-month consumption total for one metering point."""
        return self._current_month_consumption_totals.get(metering_point_no)

    def get_current_month_cost_total(self, metering_point_no: str) -> float | None:
        """Return current-month cost total for one metering point."""
        return self._current_month_cost_totals.get(metering_point_no)

    def get_current_month_consumption_unit(self, metering_point_no: str) -> str | None:
        """Return current-month consumption unit for one metering point."""
        return self._current_month_consumption_units.get(metering_point_no)

    def get_current_month_cost_unit(self, metering_point_no: str) -> str | None:
        """Return current-month cost unit for one metering point."""
        return self._current_month_cost_units.get(metering_point_no)

    async def _async_refresh_current_month_totals(
        self, snapshot: SessionSnapshot
    ) -> None:
        """Refresh month-to-date totals and units from hourly recorder stats."""
        month_start_utc, range_end_utc = self._current_month_window_utc()

        consumption_totals: dict[str, float] = {}
        cost_totals: dict[str, float] = {}
        consumption_units: dict[str, str] = {}
        cost_units: dict[str, str] = {}

        for metering_point in snapshot.metering_points:
            metering_point_no = metering_point.metering_point_no
            consumption_statistic_id = self.api_client._build_consumption_statistic_id(
                metering_point_no
            )
            cost_statistic_id = self.api_client._build_cost_statistic_id(
                metering_point_no
            )

            consumption_total, consumption_unit = await self._async_month_total_for_id(
                consumption_statistic_id,
                month_start_utc,
                range_end_utc,
            )
            if consumption_total is not None:
                consumption_totals[metering_point_no] = consumption_total
            if consumption_unit is not None:
                consumption_units[metering_point_no] = consumption_unit

            cost_total, cost_unit = await self._async_month_total_for_id(
                cost_statistic_id,
                month_start_utc,
                range_end_utc,
            )
            if cost_total is not None:
                cost_totals[metering_point_no] = cost_total
            if cost_unit is not None:
                cost_units[metering_point_no] = cost_unit

        self._current_month_consumption_totals = consumption_totals
        self._current_month_cost_totals = cost_totals
        self._current_month_consumption_units = consumption_units
        self._current_month_cost_units = cost_units

    async def _async_month_total_for_id(
        self,
        statistic_id: str,
        month_start_utc: datetime,
        range_end_utc: datetime,
    ) -> tuple[float | None, str | None]:
        """Return month-to-date total and unit for one statistic ID."""
        metadata, monthly_rows, baseline_rows = await get_instance(
            self.hass
        ).async_add_executor_job(
            lambda: (
                get_metadata(
                    self.hass,
                    statistic_ids={statistic_id},
                ),
                statistics_during_period(
                    self.hass,
                    start_time=month_start_utc,
                    end_time=range_end_utc,
                    statistic_ids={statistic_id},
                    period="hour",
                    units=None,
                    types={"sum"},
                ),
                statistics_during_period(
                    self.hass,
                    start_time=month_start_utc
                    - timedelta(days=HOURLY_DATA_RECENT_WINDOW_DAYS),
                    end_time=month_start_utc,
                    statistic_ids={statistic_id},
                    period="hour",
                    units=None,
                    types={"sum"},
                ),
            )
        )

        unit = self._extract_unit_from_metadata(metadata, statistic_id)
        latest_sum = self._extract_latest_sum(monthly_rows.get(statistic_id, []))
        if latest_sum is None:
            return None, unit

        baseline_sum = self._extract_latest_sum(baseline_rows.get(statistic_id, []))
        if baseline_sum is None:
            return None, unit
        return round(latest_sum - baseline_sum, 2), unit

    @staticmethod
    def _extract_latest_sum(rows: Sequence[object]) -> float | None:
        """Extract last available numeric `sum` value from recorder rows."""
        latest_sum: float | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            sum_value = row.get("sum")
            if isinstance(sum_value, (int, float)):
                latest_sum = float(sum_value)
        return latest_sum

    @staticmethod
    def _extract_unit_from_metadata(
        metadata: dict,
        statistic_id: str,
    ) -> str | None:
        """Extract `unit_of_measurement` from recorder metadata payload."""
        entry = metadata.get(statistic_id)
        if not isinstance(entry, tuple) or len(entry) < 2:
            return None

        metadata_payload = entry[1]
        if not isinstance(metadata_payload, dict):
            return None

        unit = metadata_payload.get("unit_of_measurement")
        if isinstance(unit, str) and unit:
            return unit
        return None

    @staticmethod
    def _current_month_window_utc() -> tuple[datetime, datetime]:
        """Return current month start and query end in UTC."""
        local_now = dt_util.now()
        month_start_local = local_now.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        month_start_utc = dt_util.as_utc(month_start_local)
        range_end_utc = dt_util.utcnow().replace(
            minute=0,
            second=0,
            microsecond=0,
        ) + timedelta(hours=1)
        return month_start_utc, range_end_utc

    async def _async_update_data(self) -> list[ConsumptionData]:
        """Fetch data from API."""
        try:
            data: list[ConsumptionData] = []
            await self.async_run_statistics_sync()
        except APIError as exc:
            _LOGGER.warning("hourly sync API error: %s", exc)
            raise UpdateFailed(f"API error: {exc}") from exc
        except AuthenticationError as exc:
            _LOGGER.warning("hourly sync auth error: %s", exc)
            raise ConfigEntryAuthFailed("Authentication failed") from exc
        except UpdateFailed:
            raise
        except Exception as exc:
            _LOGGER.exception("hourly sync unexpected error")
            raise UpdateFailed(f"Unexpected error: {exc}") from exc
        else:
            return data
