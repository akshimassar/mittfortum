"""Main API client for Fortum."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict, cast
from zoneinfo import ZoneInfo

from homeassistant.components.recorder.models.statistics import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.recorder import get_instance
from homeassistant.util import dt as dt_util

from ..const import (
    API_DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DOMAIN,
    HOURLY_DATA_HISTORICAL_CHUNK_DAYS,
    HOURLY_DATA_RECENT_WINDOW_DAYS,
    HOURLY_DATA_REQUEST_TIMEOUT_SECONDS,
    PRICE_RESOLUTIONS,
    get_currency_for_region,
)
from ..exceptions import (
    APIError,
    AuthenticationError,
    InvalidResponseError,
    UnexpectedStatusCodeError,
)
from ..models import CustomerDetails, MeteringPoint, SpotPricePoint, TimeSeries
from .endpoints import APIEndpoints

if TYPE_CHECKING:
    from homeassistant.components.recorder.models import (
        StatisticData,
        StatisticMetaData,
    )
    from homeassistant.core import HomeAssistant

    from .auth import OAuth2AuthClient

_LOGGER = logging.getLogger(__name__)

MAX_FULL_BACKFILL_STEPS = 104
CLEAR_STATISTICS_TIMEOUT_SECONDS = 60
REQUEST_RETRY_DELAYS = (5.0, 10.0)


class _MutableStatisticRow(TypedDict):
    """Mutable recorder statistics row before final cast."""

    start: datetime
    state: float
    mean: float
    min: float
    max: float
    sum: NotRequired[float]


def _fmt_day(value: datetime) -> str:
    """Format datetime for concise day-level logs."""
    return dt_util.as_utc(value).date().isoformat()


def _fmt_hour(value: datetime) -> str:
    """Format datetime for concise hour-level logs."""
    return dt_util.as_utc(value).replace(minute=0, second=0, microsecond=0).isoformat()


class FortumAPIClient:
    """Main API client for Fortum tRPC services."""

    def __init__(self, hass: HomeAssistant, auth_client: OAuth2AuthClient) -> None:
        """Initialize API client."""
        self._hass = hass
        self._auth_client = auth_client
        self._endpoints = APIEndpoints.for_region(getattr(auth_client, "region", "se"))
        self._earliest_available_by_metering_point: dict[str, datetime] = {}
        self._last_price_forecast_digest_by_area: dict[str, str] = {}
        self._last_hourly_stats_digest: str | None = None

    async def get_customer_id(self) -> str:
        """Extract customer ID from token or session endpoint payload."""
        id_token = self._auth_client.id_token
        if id_token and id_token != "session_based":
            try:
                import jwt

                payload = jwt.decode(id_token, options={"verify_signature": False})
                return payload["customerid"][0]["crmid"]
            except (KeyError, IndexError, ValueError) as exc:
                raise APIError(f"Failed to extract customer ID: {exc}") from exc

        session_payload = await self.get_session_payload()
        user_data = session_payload.get("user")
        if not isinstance(user_data, dict):
            raise APIError("Customer ID not found in session payload")

        customer_id = user_data.get("customerId")
        if isinstance(customer_id, str) and customer_id.strip():
            return customer_id

        raise APIError("Customer ID not found in session payload")

    async def get_customer_details(self) -> CustomerDetails:
        """Fetch customer details using session endpoint."""
        payload = await self.get_session_payload()
        try:
            return CustomerDetails.from_api_response(payload)
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidResponseError(
                f"Invalid customer details response: {exc}"
            ) from exc

    async def get_session_payload(self) -> dict[str, Any]:
        """Fetch and return raw session payload from session endpoint."""
        response = await self._get(self._endpoints.session)
        try:
            payload = response.json()
        except ValueError as exc:
            raise InvalidResponseError("Invalid JSON in session payload") from exc

        if not isinstance(payload, dict):
            raise InvalidResponseError("Session payload is not an object")
        return payload

    async def get_metering_points(self) -> list[MeteringPoint]:
        """Fetch metering points from session endpoint."""
        payload = await self.get_session_payload()

        try:
            user_data = payload.get("user")
            delivery_sites = (
                user_data.get("deliverySites") if isinstance(user_data, dict) else None
            )
            if not isinstance(delivery_sites, list):
                return []
            return [MeteringPoint.from_api_response(site) for site in delivery_sites]
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidResponseError(
                f"Invalid metering points response: {exc}"
            ) from exc

    async def get_time_series_data(
        self,
        metering_point_nos: list[str],
        from_date: datetime,
        to_date: datetime,
        resolution: str,
        series_type: str | None = None,
        request_timeout: float | None = None,
    ) -> list[TimeSeries]:
        """Fetch time series data using tRPC endpoint."""
        if from_date is None or to_date is None:
            raise APIError("from_date and to_date are required for time series fetch")

        if not resolution:
            raise APIError("resolution is required for time series fetch")

        if from_date >= to_date:
            raise APIError(
                "Invalid time series range: from_date must be earlier than to_date"
            )

        try:
            return await self._fetch_time_series_data(
                metering_point_nos,
                from_date,
                to_date,
                resolution,
                series_type=series_type,
                request_timeout=request_timeout,
            )
        except APIError as exc:
            _LOGGER.error(
                "time series fetch failed: metering_point_nos=%s from=%s to=%s "
                "resolution=%s series_type=%s error=%s",
                metering_point_nos,
                from_date.isoformat(),
                to_date.isoformat(),
                resolution,
                series_type or "default",
                exc,
            )
            raise

    async def _fetch_time_series_data(
        self,
        metering_point_nos: list[str],
        from_date: datetime,
        to_date: datetime,
        resolution: str,
        series_type: str | None = None,
        request_timeout: float | None = None,
    ) -> list[TimeSeries]:
        """Internal method to fetch time series data."""
        url = self._endpoints.get_time_series_url(
            metering_point_nos=metering_point_nos,
            from_date=from_date,
            to_date=to_date,
            resolution=resolution,
            series_type=series_type,
        )

        response = await self._get(url, request_timeout=request_timeout)

        try:
            data = await self._parse_trpc_response(response)

            if isinstance(data, list):
                parsed_series = []
                for item in data:
                    if not isinstance(item, dict):
                        raise TypeError("Time series list contains non-object entries")
                    parsed_series.append(TimeSeries.from_api_response(item))
                return parsed_series
            else:
                # Single time series
                if not isinstance(data, dict):
                    raise TypeError("Time series response item is not an object")
                return [TimeSeries.from_api_response(data)]

        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidResponseError(f"Invalid time series response: {exc}") from exc

    def _get_cookie_domain(self, cookie_name: str) -> str:
        """Determine the correct domain for a cookie based on its name.

        Args:
            cookie_name: Name of the cookie

        Returns:
            Appropriate domain for the cookie
        """
        # SSO-related cookies go to sso.fortum.com domain
        if cookie_name in ("amlbcookie", "18dddeef3f61363"):
            return ".sso.fortum.com"

        # Main site cookies (security prefixed and locale) go to main domain
        if (
            cookie_name.startswith("__Host-")
            or cookie_name.startswith("__Secure-")
            or cookie_name == "NEXT_LOCALE"
        ):
            return "www.fortum.com"

        # Default to main domain for any other cookies
        return "www.fortum.com"

    async def sync_hourly_data_for_metering_points(
        self,
        metering_points: tuple[MeteringPoint, ...],
    ) -> int:
        """Sync hourly data for provided metering points in two-week chunks."""
        if not metering_points:
            _LOGGER.debug("no metering points; skipping hourly stats sync")
            return 0

        utc_now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        two_weeks_ago = utc_now - timedelta(days=HOURLY_DATA_RECENT_WINDOW_DAYS)

        imported_points = 0
        for metering_point in metering_points:
            metering_point_no = metering_point.metering_point_no
            self._record_metering_point_earliest_available_marker(metering_point)

            sync_start, historical = await self._determine_hourly_data_sync_start(
                metering_point_no,
                two_weeks_ago,
                utc_now,
            )

            if sync_start < utc_now:
                chunk_days = (
                    HOURLY_DATA_HISTORICAL_CHUNK_DAYS
                    if historical
                    else HOURLY_DATA_RECENT_WINDOW_DAYS
                )
                imported_points += await self._sync_hourly_data(
                    metering_point_no,
                    sync_start,
                    utc_now,
                    continue_after_missing=historical,
                    chunk_days=chunk_days,
                )

        return imported_points

    async def clear_hourly_statistics_for_topology(
        self,
        metering_points: tuple[MeteringPoint, ...],
        price_areas: tuple[str, ...],
    ) -> int:
        """Clear all Fortum hourly statistics for provided topology."""
        statistic_ids: list[str] = [self._build_price_forecast_statistic_id()]
        for area_code in price_areas:
            statistic_ids.append(self._build_price_forecast_statistic_id(area_code))

        for point in metering_points:
            statistic_ids.extend(
                [
                    self._build_consumption_statistic_id(point.metering_point_no),
                    self._build_cost_statistic_id(point.metering_point_no),
                    self._build_price_statistic_id(point.metering_point_no),
                    self._build_temperature_statistic_id(point.metering_point_no),
                ]
            )

        if not statistic_ids:
            return 0

        done_event = asyncio.Event()

        def clear_done() -> None:
            self._hass.loop.call_soon_threadsafe(done_event.set)

        get_instance(self._hass).async_clear_statistics(
            statistic_ids,
            on_done=clear_done,
        )

        try:
            async with asyncio.timeout(CLEAR_STATISTICS_TIMEOUT_SECONDS):
                await done_event.wait()
        except TimeoutError as exc:
            raise APIError("Timed out while clearing statistics") from exc

        self._last_price_forecast_digest_by_area.clear()
        self._last_hourly_stats_digest = None

        return len(statistic_ids)

    async def _sync_hourly_data(
        self,
        metering_point_no: str,
        range_start: datetime,
        range_end: datetime,
        *,
        continue_after_missing: bool,
        chunk_days: int = HOURLY_DATA_RECENT_WINDOW_DAYS,
    ) -> int:
        """Sync hourly data from oldest to newest in chunk_days windows."""
        if range_start >= range_end:
            return 0

        imported_points = 0
        window_start = range_start
        steps = 0
        window = timedelta(days=chunk_days)

        while window_start < range_end:
            if steps >= MAX_FULL_BACKFILL_STEPS:
                _LOGGER.warning(
                    "stopped statistics sync after %d windows for %s (start=%s end=%s)",
                    MAX_FULL_BACKFILL_STEPS,
                    metering_point_no,
                    range_start.isoformat(),
                    range_end.isoformat(),
                )
                break

            window_end = min(window_start + window, range_end)
            (
                next_window_start,
                imported_in_window,
            ) = await self._sync_hourly_data_chunk(
                metering_point_no,
                window_start,
                window_end,
                continue_after_missing=continue_after_missing,
            )
            imported_points += imported_in_window
            window_start = next_window_start
            steps += 1

        return imported_points

    async def _sync_hourly_data_chunk(
        self,
        metering_point_no: str,
        chunk_start: datetime,
        range_end: datetime,
        *,
        continue_after_missing: bool,
    ) -> tuple[datetime, int]:
        """Sync one up-to-two-weeks hourly-data chunk and return next start."""
        imported = await self._record_hourly_data_stats(
            metering_point_no,
            chunk_start,
            range_end,
            continue_after_missing=continue_after_missing,
        )
        return range_end, imported

    async def _determine_hourly_data_sync_start(
        self,
        metering_point_no: str,
        two_weeks_ago: datetime,
        now: datetime,
    ) -> tuple[datetime, bool]:
        """Determine hourly-data sync start and whether historical mode is needed."""
        last_recorded_hour = await self._find_last_recorded_cost_stat_hour(
            metering_point_no,
            two_weeks_ago,
            now,
        )
        if last_recorded_hour is None:
            earliest = self._earliest_available_by_metering_point.get(metering_point_no)
            if earliest is None:
                _LOGGER.warning(
                    "no cost statistics in [%s, %s) for %s and earliest hour is "
                    "unknown; starting from two_weeks_ago",
                    two_weeks_ago.isoformat(),
                    now.isoformat(),
                    metering_point_no,
                )
                return two_weeks_ago, True

            _LOGGER.info(
                "no cost statistics in [%s, %s) for %s; starting historical sync "
                "from earliest_hourly_available_at_utc=%s",
                two_weeks_ago.isoformat(),
                now.isoformat(),
                metering_point_no,
                earliest.isoformat(),
            )
            return earliest, True

        next_hour = last_recorded_hour + timedelta(hours=1)
        if next_hour >= now:
            return now, False

        return next_hour, False

    async def _find_last_recorded_cost_stat_hour(
        self,
        metering_point_no: str,
        from_date: datetime,
        to_date: datetime,
    ) -> datetime | None:
        """Return last contiguous recorded cost-stat hour in [from_date, to_date)."""
        statistic_id = self._build_cost_statistic_id(metering_point_no)
        try:
            result = await get_instance(self._hass).async_add_executor_job(
                lambda: statistics_during_period(
                    self._hass,
                    start_time=from_date,
                    end_time=to_date,
                    statistic_ids={statistic_id},
                    period="hour",
                    units=None,
                    types={"sum"},
                )
            )
        except Exception as exc:
            _LOGGER.warning(
                "could not read cost statistics coverage for %s: %s",
                statistic_id,
                exc,
            )
            return None

        rows = result.get(statistic_id) if result else None
        if not rows:
            return None

        covered_hours: set[datetime] = set()
        for row in rows:
            start = self._parse_stat_start(row.get("start"))
            if start is None:
                continue
            covered_hours.add(start)

        if not covered_hours:
            return None

        first_missing_hour = self._find_first_missing_hour(
            from_date,
            to_date,
            covered_hours,
        )
        if first_missing_hour is None:
            return dt_util.as_utc(to_date).replace(
                minute=0,
                second=0,
                microsecond=0,
            ) - timedelta(hours=1)

        if first_missing_hour <= dt_util.as_utc(from_date).replace(
            minute=0,
            second=0,
            microsecond=0,
        ):
            return None

        return first_missing_hour - timedelta(hours=1)

    @staticmethod
    def _find_first_missing_hour(
        from_date: datetime,
        to_date: datetime,
        covered_hours: set[datetime],
    ) -> datetime | None:
        """Return first missing hour in [from_date, to_date)."""
        current = dt_util.as_utc(from_date).replace(minute=0, second=0, microsecond=0)
        range_end = dt_util.as_utc(to_date).replace(minute=0, second=0, microsecond=0)
        while current < range_end:
            if current not in covered_hours:
                return current
            current += timedelta(hours=1)
        return None

    @staticmethod
    def _parse_stat_start(start_raw: Any) -> datetime | None:
        """Parse recorder row start timestamp into normalized UTC hour."""
        if isinstance(start_raw, datetime):
            start = start_raw
        elif isinstance(start_raw, (int, float)):
            start = dt_util.utc_from_timestamp(start_raw)
        elif isinstance(start_raw, str):
            parsed = dt_util.parse_datetime(start_raw)
            if parsed is None:
                return None
            start = parsed
        else:
            return None

        return dt_util.as_utc(start).replace(minute=0, second=0, microsecond=0)

    async def _get_hourly_stat_sum_before_hour(
        self,
        statistic_id: str,
        hour: datetime,
    ) -> float:
        """Return cumulative sum from the hour immediately before given hour."""
        previous_hour = dt_util.as_utc(hour).replace(
            minute=0,
            second=0,
            microsecond=0,
        ) - timedelta(hours=1)

        try:
            result = await get_instance(self._hass).async_add_executor_job(
                lambda: statistics_during_period(
                    self._hass,
                    start_time=previous_hour,
                    end_time=hour,
                    statistic_ids={statistic_id},
                    period="hour",
                    units=None,
                    types={"sum"},
                )
            )
        except Exception as exc:
            _LOGGER.warning(
                "could not read previous sum for %s before %s: %s",
                statistic_id,
                hour.isoformat(),
                exc,
            )
            return 0.0

        rows = result.get(statistic_id) if result else None
        if not rows:
            return 0.0

        latest_sum = 0.0
        for row in rows:
            sum_value = row.get("sum")
            if isinstance(sum_value, (int, float)):
                latest_sum = float(sum_value)
        return latest_sum

    async def _record_hourly_data_stats(
        self,
        metering_point_no: str,
        from_date: datetime,
        to_date: datetime,
        *,
        continue_after_missing: bool,
    ) -> int:
        """Fetch hourly data and push derived statistics to HA recorder."""
        local_tz = ZoneInfo(self._endpoints.profile.timezone)
        request_from_local = (
            dt_util.as_utc(from_date)
            .astimezone(local_tz)
            .replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        )
        request_to_local = dt_util.as_utc(to_date).astimezone(local_tz).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        ) + timedelta(days=1)
        request_from_date = dt_util.as_utc(request_from_local)
        request_to_date = dt_util.as_utc(request_to_local)

        time_series_list = await self.get_time_series_data(
            metering_point_nos=[metering_point_no],
            from_date=request_from_date,
            to_date=request_to_date,
            resolution="HOUR",
            series_type="CONSUMPTION",
            request_timeout=HOURLY_DATA_REQUEST_TIMEOUT_SECONDS,
        )

        imported_points = 0
        earliest_available_hour: datetime | None = None
        latest_available_hour: datetime | None = None
        for time_series in time_series_list:
            self._record_earliest_available_marker(time_series, from_date)

            consumption_statistic_id = self._build_consumption_statistic_id(
                time_series.metering_point_no
            )
            cost_statistic_id = self._build_cost_statistic_id(
                time_series.metering_point_no
            )
            price_statistic_id = self._build_price_statistic_id(
                time_series.metering_point_no
            )
            temperature_statistic_id = self._build_temperature_statistic_id(
                time_series.metering_point_no
            )

            consumption_statistics: list[_MutableStatisticRow] = []
            cost_statistics: list[_MutableStatisticRow] = []
            price_statistics: list[_MutableStatisticRow] = []
            temperature_statistics: list[_MutableStatisticRow] = []
            first_missing_price_at: datetime | None = None
            ordered_points = sorted(time_series.series, key=lambda item: item.at_utc)
            if ordered_points:
                series_start = dt_util.as_utc(ordered_points[0].at_utc).replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                series_end = dt_util.as_utc(ordered_points[-1].at_utc).replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                if (
                    earliest_available_hour is None
                    or series_start < earliest_available_hour
                ):
                    earliest_available_hour = series_start
                if latest_available_hour is None or series_end > latest_available_hour:
                    latest_available_hour = series_end

            for point in ordered_points:
                point_time = dt_util.as_utc(point.at_utc).replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                )

                if point.price is None:
                    if first_missing_price_at is None:
                        first_missing_price_at = point_time
                    continue

                if first_missing_price_at is not None:
                    if continue_after_missing:
                        _LOGGER.warning(
                            "gap in stats for %s: %s -> %s; continuing",
                            time_series.metering_point_no,
                            first_missing_price_at.isoformat(),
                            point_time.isoformat(),
                        )
                        first_missing_price_at = None
                    else:
                        _LOGGER.warning(
                            (
                                "gap in stats for %s: %s -> %s; "
                                "skipping remaining points in window"
                            ),
                            time_series.metering_point_no,
                            first_missing_price_at.isoformat(),
                            point_time.isoformat(),
                        )
                        break

                consumption_value = float(point.total_energy)
                consumption_statistics.append(
                    {
                        "start": point_time,
                        "state": consumption_value,
                        "mean": consumption_value,
                        "min": consumption_value,
                        "max": consumption_value,
                    }
                )

                if point.cost:
                    cost_value = float(point.total_cost)
                    cost_statistics.append(
                        {
                            "start": point_time,
                            "state": cost_value,
                            "mean": cost_value,
                            "min": cost_value,
                            "max": cost_value,
                        }
                    )

                if point.price:
                    price_value = float(point.price.total)
                    price_statistics.append(
                        {
                            "start": point_time,
                            "state": price_value,
                            "mean": price_value,
                            "min": price_value,
                            "max": price_value,
                        }
                    )

                if point.temperature_reading is not None:
                    temperature_value = float(point.temperature_reading.temperature)
                    temperature_statistics.append(
                        {
                            "start": point_time,
                            "state": temperature_value,
                            "mean": temperature_value,
                            "min": temperature_value,
                            "max": temperature_value,
                        }
                    )

            if not consumption_statistics:
                continue

            consumption_statistics.sort(key=lambda row: row["start"])
            cost_statistics.sort(key=lambda row: row["start"])
            price_statistics.sort(key=lambda row: row["start"])
            temperature_statistics.sort(key=lambda row: row["start"])

            if consumption_statistics:
                consumption_sum = await self._get_hourly_stat_sum_before_hour(
                    consumption_statistic_id,
                    consumption_statistics[0]["start"],
                )
                for row in consumption_statistics:
                    state_value = row["state"]
                    consumption_sum += state_value
                    row["sum"] = consumption_sum

            if cost_statistics:
                cost_sum = await self._get_hourly_stat_sum_before_hour(
                    cost_statistic_id,
                    cost_statistics[0]["start"],
                )
                for row in cost_statistics:
                    state_value = row["state"]
                    cost_sum += state_value
                    row["sum"] = cost_sum

            consumption_metadata = cast(
                "StatisticMetaData",
                {
                    "statistic_id": consumption_statistic_id,
                    "source": DOMAIN,
                    "name": (
                        f"Fortum Hourly Consumption {time_series.metering_point_no}"
                    ),
                    "unit_of_measurement": time_series.measurement_unit,
                    "unit_class": "energy",
                    "has_mean": True,
                    "mean_type": StatisticMeanType.ARITHMETIC,
                    "has_sum": True,
                },
            )
            cost_metadata = cast(
                "StatisticMetaData",
                {
                    "statistic_id": cost_statistic_id,
                    "source": DOMAIN,
                    "name": f"Fortum Hourly Cost {time_series.metering_point_no}",
                    "unit_of_measurement": time_series.cost_unit,
                    "unit_class": None,
                    "has_mean": True,
                    "mean_type": StatisticMeanType.ARITHMETIC,
                    "has_sum": True,
                },
            )
            price_metadata = cast(
                "StatisticMetaData",
                {
                    "statistic_id": price_statistic_id,
                    "source": DOMAIN,
                    "name": f"Fortum Hourly Price {time_series.metering_point_no}",
                    "unit_of_measurement": time_series.price_unit,
                    "unit_class": None,
                    "has_mean": True,
                    "mean_type": StatisticMeanType.ARITHMETIC,
                    "has_sum": False,
                },
            )
            temperature_metadata = cast(
                "StatisticMetaData",
                {
                    "statistic_id": temperature_statistic_id,
                    "source": DOMAIN,
                    "name": (
                        f"Fortum Hourly Temperature {time_series.metering_point_no}"
                    ),
                    "unit_of_measurement": self._normalize_temperature_unit(
                        time_series.temperature_unit
                    ),
                    "unit_class": "temperature",
                    "has_mean": True,
                    "mean_type": StatisticMeanType.ARITHMETIC,
                    "has_sum": False,
                },
            )

            consumption_rows = cast("list[StatisticData]", consumption_statistics)
            cost_rows = cast("list[StatisticData]", cost_statistics)
            price_rows = cast("list[StatisticData]", price_statistics)
            temperature_rows = cast("list[StatisticData]", temperature_statistics)

            def _start_text(start: datetime | float) -> str:
                return str(start)

            consumption_unit = str(consumption_metadata["unit_of_measurement"] or "")
            cost_unit = str(cost_metadata["unit_of_measurement"] or "")
            price_unit = str(price_metadata["unit_of_measurement"] or "")
            temperature_unit = str(temperature_metadata["unit_of_measurement"] or "")

            digest_parts = [
                time_series.metering_point_no,
                consumption_metadata["statistic_id"],
                consumption_unit,
                *[
                    (
                        f"c|{_start_text(row['start'])}|"
                        f"{row['state']:.12f}|"
                        f"{row['mean']:.12f}|{row['min']:.12f}|"
                        f"{row['max']:.12f}|{row.get('sum', 0.0):.12f}"
                    )
                    for row in consumption_statistics
                ],
                cost_metadata["statistic_id"],
                cost_unit,
                *[
                    (
                        f"k|{_start_text(row['start'])}|"
                        f"{row['state']:.12f}|"
                        f"{row['mean']:.12f}|{row['min']:.12f}|"
                        f"{row['max']:.12f}|{row.get('sum', 0.0):.12f}"
                    )
                    for row in cost_statistics
                ],
                price_metadata["statistic_id"],
                price_unit,
                *[
                    (
                        f"p|{_start_text(row['start'])}|"
                        f"{row['state']:.12f}|"
                        f"{row['mean']:.12f}|{row['min']:.12f}|"
                        f"{row['max']:.12f}"
                    )
                    for row in price_statistics
                ],
                temperature_metadata["statistic_id"],
                temperature_unit,
                *[
                    (
                        f"t|{_start_text(row['start'])}|"
                        f"{row['state']:.12f}|"
                        f"{row['mean']:.12f}|{row['min']:.12f}|"
                        f"{row['max']:.12f}"
                    )
                    for row in temperature_statistics
                ],
            ]
            digest = hashlib.sha256(";".join(digest_parts).encode("utf-8")).hexdigest()
            if digest == self._last_hourly_stats_digest:
                continue

            async_add_external_statistics(
                self._hass, consumption_metadata, consumption_rows
            )
            imported_points += len(consumption_statistics)

            if cost_statistics:
                async_add_external_statistics(self._hass, cost_metadata, cost_rows)
                imported_points += len(cost_statistics)

            if price_statistics:
                async_add_external_statistics(self._hass, price_metadata, price_rows)
                imported_points += len(price_statistics)

            if temperature_statistics:
                async_add_external_statistics(
                    self._hass,
                    temperature_metadata,
                    temperature_rows,
                )
                imported_points += len(temperature_statistics)

            self._last_hourly_stats_digest = digest

        earliest_text = (
            _fmt_hour(earliest_available_hour)
            if earliest_available_hour is not None
            else "n/a"
        )
        latest_text = (
            _fmt_hour(latest_available_hour)
            if latest_available_hour is not None
            else "n/a"
        )
        _LOGGER.debug(
            "hourly stats import done: metering_point_no=%s from=%s to=%s "
            "latest_available=%s -> %s processed_records=%d",
            metering_point_no,
            _fmt_day(from_date),
            _fmt_day(to_date),
            earliest_text,
            latest_text,
            imported_points,
        )

        return imported_points

    def _record_earliest_available_marker(
        self,
        time_series: TimeSeries,
        requested_from_date: datetime,
    ) -> None:
        """Record earliest available timestamp if API payload includes it."""
        earliest_available = time_series.earliest_available_at_utc
        if earliest_available is None:
            return

        normalized = dt_util.as_utc(earliest_available).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        previous = self._earliest_available_by_metering_point.get(
            time_series.metering_point_no
        )
        if previous is not None and previous <= normalized:
            return

        self._earliest_available_by_metering_point[time_series.metering_point_no] = (
            normalized
        )
        _LOGGER.debug(
            "recorded earliest available hour for %s from API metadata: %s "
            "(requested_from=%s)",
            time_series.metering_point_no,
            _fmt_day(normalized),
            _fmt_day(requested_from_date),
        )

    def _record_metering_point_earliest_available_marker(
        self,
        metering_point: MeteringPoint,
    ) -> None:
        """Record earliest available marker from session delivery-site metadata."""
        earliest_available = metering_point.earliest_hourly_available_at_utc
        if earliest_available is None:
            return

        normalized = dt_util.as_utc(earliest_available).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        previous = self._earliest_available_by_metering_point.get(
            metering_point.metering_point_no
        )
        if previous is not None and previous <= normalized:
            return

        self._earliest_available_by_metering_point[metering_point.metering_point_no] = (
            normalized
        )
        _LOGGER.debug(
            "recorded earliest available hour for %s from user-info metadata: %s",
            metering_point.metering_point_no,
            _fmt_day(normalized),
        )

    @staticmethod
    def _build_consumption_statistic_id(metering_point_no: str) -> str:
        """Build stable statistic_id for a metering point."""
        suffix = re.sub(r"[^0-9a-z_]", "_", metering_point_no.lower()).strip("_")
        if not suffix:
            suffix = "unknown"
        return f"{DOMAIN}:hourly_consumption_{suffix}"

    @staticmethod
    def _build_cost_statistic_id(metering_point_no: str) -> str:
        """Build stable cost statistic_id for a metering point."""
        suffix = re.sub(r"[^0-9a-z_]", "_", metering_point_no.lower()).strip("_")
        if not suffix:
            suffix = "unknown"
        return f"{DOMAIN}:hourly_cost_{suffix}"

    @staticmethod
    def _build_price_statistic_id(metering_point_no: str) -> str:
        """Build stable price statistic_id for a metering point."""
        suffix = re.sub(r"[^0-9a-z_]", "_", metering_point_no.lower()).strip("_")
        if not suffix:
            suffix = "unknown"
        return f"{DOMAIN}:hourly_price_{suffix}"

    @staticmethod
    def _build_temperature_statistic_id(metering_point_no: str) -> str:
        """Build stable temperature statistic_id for a metering point."""
        suffix = re.sub(r"[^0-9a-z_]", "_", metering_point_no.lower()).strip("_")
        if not suffix:
            suffix = "unknown"
        return f"{DOMAIN}:hourly_temperature_{suffix}"

    @staticmethod
    def _build_price_forecast_statistic_id(area_code: str | None = None) -> str:
        """Build stable statistic_id for spot-price forecast data."""
        if not area_code:
            return f"{DOMAIN}:price_forecast"

        suffix = re.sub(r"[^0-9a-z_]", "_", area_code.lower()).strip("_")
        if not suffix:
            return f"{DOMAIN}:price_forecast"
        return f"{DOMAIN}:price_forecast_{suffix}"

    @staticmethod
    def _normalize_temperature_unit(unit: str) -> str:
        """Normalize API temperature unit to Home Assistant convention."""
        normalized = unit.strip().lower()
        if normalized == "celsius":
            return "°C"
        if normalized == "fahrenheit":
            return "°F"
        if normalized == "kelvin":
            return "K"
        return unit

    async def fetch_spot_prices_for_areas(
        self,
        price_areas: tuple[str, ...],
    ) -> list[SpotPricePoint]:
        """Fetch near real-time spot prices for provided price areas."""
        local_now = datetime.now(ZoneInfo(self._endpoints.profile.timezone))
        # Fetch yesterday..tomorrow(+1 day buffer) so we include tomorrow prices
        # once Fortum publishes them (typically around 15:00 local time).
        from_date = (local_now - timedelta(days=1)).date()
        to_date = (local_now + timedelta(days=2)).date()
        if not price_areas:
            _LOGGER.info("price areas missing from topology; skipping spot price fetch")
            return []

        all_price_data: list[SpotPricePoint] = []
        errors: list[str] = []
        for area_code in price_areas:
            area_price_data = await self._fetch_price_data_for_area(
                area_code,
                from_date,
                to_date,
            )
            if area_price_data is None:
                errors.append(area_code)
                continue
            if not area_price_data:
                continue

            self._record_price_forecast_statistics(area_code, area_price_data)
            all_price_data.extend(area_price_data)

        if errors and not all_price_data:
            raise APIError(
                "Failed to fetch spot price data for configured price areas: "
                + ", ".join(errors)
            )

        all_price_data.sort(key=lambda point: (point.date_time, point.area_code))
        return all_price_data

    async def _fetch_price_data_for_area(
        self,
        area_code: str,
        from_date: date_cls,
        to_date: date_cls,
    ) -> list[SpotPricePoint] | None:
        """Fetch spot prices for one explicit area code."""
        last_error: APIError | None = None
        for resolution in PRICE_RESOLUTIONS:
            try:
                url = self._endpoints.get_spot_prices_url(
                    price_area=area_code,
                    from_date=from_date,
                    to_date=to_date,
                    resolution=resolution,
                )
                response = await self._get(url)
                data = await self._parse_trpc_response(response)

                if not isinstance(data, list):
                    raise InvalidResponseError("Spot prices response is not a list")

                price_data: list[SpotPricePoint] = []
                local_tz = ZoneInfo(self._endpoints.profile.timezone)

                for area_payload in data:
                    if not isinstance(area_payload, dict):
                        continue

                    payload_area = area_payload.get("priceArea")
                    if not isinstance(payload_area, str):
                        continue

                    normalized_payload_area = payload_area.strip().upper()
                    if normalized_payload_area != area_code:
                        continue

                    price_unit = area_payload.get("priceUnit")
                    series = area_payload.get("spotPriceSeries", [])
                    if not isinstance(series, list):
                        continue

                    for point in series:
                        if not isinstance(point, dict):
                            continue
                        spot_price = point.get("spotPrice")
                        if not isinstance(spot_price, dict):
                            continue

                        total_price = spot_price.get("total")
                        at_utc_raw = point.get("atUTC")
                        if total_price is None or not isinstance(at_utc_raw, str):
                            continue

                        at_utc = datetime.fromisoformat(
                            at_utc_raw.replace("Z", "+00:00")
                        )
                        price_data.append(
                            SpotPricePoint(
                                date_time=at_utc.astimezone(local_tz),
                                price=float(total_price),
                                price_unit=str(price_unit) if price_unit else None,
                                area_code=area_code,
                            )
                        )

                if price_data:
                    price_data.sort(key=lambda point: point.date_time)
                    return price_data
            except APIError as exc:
                last_error = exc

        if last_error is not None:
            _LOGGER.warning(
                "price fetch failed area=%s error=%s", area_code, last_error
            )
            return None

        _LOGGER.debug(
            "no price data records area=%s from_date=%s to_date=%s",
            area_code,
            from_date,
            to_date,
        )
        return []

    def _record_price_forecast_statistics(
        self,
        area_code: str,
        price_data: list[SpotPricePoint],
    ) -> None:
        """Push spot-price data as hourly aggregated recorder statistics."""
        hourly_values: dict[datetime, list[float]] = {}
        for point in sorted(price_data, key=lambda item: item.date_time):
            start = dt_util.as_utc(point.date_time).replace(
                minute=0,
                second=0,
                microsecond=0,
            )
            value = float(point.price)
            hourly_values.setdefault(start, []).append(value)

        rows: list[dict[str, datetime | float]] = []
        for start in sorted(hourly_values):
            values = hourly_values[start]
            mean_value = sum(values) / len(values)
            rows.append(
                {
                    "start": start,
                    "state": mean_value,
                    "mean": mean_value,
                    "min": min(values),
                    "max": max(values),
                }
            )

        if not rows:
            _LOGGER.debug("no priced rows; skipping price forecast stats write")
            return

        first_unit = next(
            (item.price_unit for item in price_data if item.price_unit),
            None,
        )
        unit = (
            first_unit or f"{get_currency_for_region(self._endpoints.profile.code)}/kWh"
        )

        metadata = cast(
            "StatisticMetaData",
            {
                "statistic_id": self._build_price_forecast_statistic_id(area_code),
                "source": DOMAIN,
                "name": f"Fortum Price Forecast {area_code}",
                "unit_of_measurement": unit,
                "unit_class": None,
                "has_mean": True,
                "mean_type": StatisticMeanType.ARITHMETIC,
                "has_sum": False,
            },
        )

        def _start_iso(start: datetime | float) -> str:
            return str(start)

        metadata_unit = str(metadata["unit_of_measurement"] or "")
        digest_parts = [
            metadata["statistic_id"],
            metadata_unit,
            *[
                (
                    f"{_start_iso(row['start'])}|"
                    f"{row['state']:.12f}|"
                    f"{row['mean']:.12f}|{row['min']:.12f}|{row['max']:.12f}"
                )
                for row in rows
            ],
        ]
        digest = hashlib.sha256(";".join(digest_parts).encode("utf-8")).hexdigest()
        last_digest = self._last_price_forecast_digest_by_area.get(area_code)
        if digest == last_digest:
            return

        statistic_rows = cast("list[StatisticData]", rows)
        async_add_external_statistics(self._hass, metadata, statistic_rows)
        self._last_price_forecast_digest_by_area[area_code] = digest
        first_day = _fmt_day(cast("datetime", rows[0]["start"]))
        last_day = _fmt_day(cast("datetime", rows[-1]["start"]))
        _LOGGER.debug(
            "wrote price forecast stats area=%s statistic_id=%s "
            "rows=%d first=%s last=%s",
            area_code,
            metadata["statistic_id"],
            len(rows),
            first_day,
            last_day,
        )

    async def _get(
        self,
        url: str,
        request_timeout: float | None = None,
    ) -> Any:
        """Perform authenticated GET request with bounded retries."""
        async with get_async_client(self._hass) as client:
            max_attempts = len(REQUEST_RETRY_DELAYS) + 1

            # Intentionally broad retries keep request handling simple and resilient.
            # Token/session renewal runs independently and may briefly race with
            # coordinator requests; during that window, transient auth failures can
            # happen before renewal settles. We also see occasional transient backend
            # failures. A short bounded retry window smooths both cases before
            # surfacing a hard failure.
            for attempt in range(1, max_attempts + 1):
                # Add session cookies if available
                if self._auth_client.session_cookies:
                    for name, value in self._auth_client.session_cookies.items():
                        domain = self._get_cookie_domain(name)
                        if hasattr(client.cookies, "set"):
                            client.cookies.set(name, value, domain=domain)
                        else:
                            client.cookies[name] = value

                try:
                    headers = {
                        "Accept": "application/json",
                        "User-Agent": (
                            "Mozilla/5.0 (X11; Linux x86_64; rv:138.0) "
                            "Gecko/20100101 Firefox/138.0"
                        ),
                        "Content-Type": "application/json",
                        "Referer": self._endpoints.referer,
                    }

                    if (
                        "/api/trpc/" not in url
                        and "/api/auth/session" not in url
                        and self._auth_client.access_token
                        and self._auth_client.access_token != "session_based"
                    ):
                        headers["Authorization"] = (
                            f"Bearer {self._auth_client.access_token}"
                        )

                    timeout = (
                        request_timeout
                        if request_timeout is not None
                        else API_DEFAULT_REQUEST_TIMEOUT_SECONDS
                    )
                    request_kwargs: dict[str, Any] = {
                        "headers": headers,
                        "timeout": timeout,
                    }

                    response = await client.get(url, **request_kwargs)
                    return await self._handle_response(response)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    is_last_attempt = attempt == max_attempts
                    if is_last_attempt:
                        details = self._format_exception_details(exc)
                        _LOGGER.error(
                            "GET failed after %d/%d attempts for %s: %s",
                            attempt,
                            max_attempts,
                            url,
                            details,
                        )
                        if isinstance(exc, (APIError, AuthenticationError)):
                            raise
                        raise APIError("GET request failed") from exc

                    delay = REQUEST_RETRY_DELAYS[attempt - 1]
                    details = self._format_exception_details(exc)
                    _LOGGER.warning(
                        "GET failed (attempt %d/%d), retrying in %.1fs: %s",
                        attempt,
                        max_attempts,
                        delay,
                        details,
                    )
                    await asyncio.sleep(delay)

        raise APIError("GET request failed")

    @staticmethod
    def _format_exception_details(exc: Exception) -> str:
        """Return readable exception details for logs."""
        message = str(exc).strip()
        if message:
            return f"{exc.__class__.__name__}: {message}"
        return repr(exc)

    async def _parse_trpc_response(self, response: Any) -> Any:
        """Parse tRPC response format."""
        try:
            json_data = response.json()

            # tRPC response format: [{"result": {"data": {"json": actual_data}}}]
            if isinstance(json_data, list) and len(json_data) > 0:
                result = json_data[0]
                if "result" in result and "data" in result["result"]:
                    return result["result"]["data"]["json"]

            # Fallback to direct parsing if format is different
            if isinstance(json_data, dict):
                return json_data
            else:
                # If it's a list, return first item or empty dict
                return json_data[0] if json_data else {}

        except (ValueError, KeyError, IndexError) as exc:
            raise InvalidResponseError(f"Failed to parse tRPC response: {exc}") from exc

    def _handle_redirect_response(self, response) -> None:
        """Handle redirect responses (307)."""
        location = response.headers.get("Location", "")
        raise APIError(f"Unexpected redirect to: {location}")

    def _handle_server_error_response(self, response) -> None:
        """Handle 500 server error responses."""
        # Check if it's a tRPC error with specific format
        try:
            error_data = response.json()
            if isinstance(error_data, list) and len(error_data) > 0:
                error_item = error_data[0]
                if "error" in error_item:
                    error_details = error_item["error"]
                    if "json" in error_details:
                        json_error = error_details["json"]
                        error_msg = json_error.get("message", "Unknown error")
                        error_code = json_error.get("code", "Unknown")
                        raise APIError(f"Server error ({error_code}): {error_msg}")
        except (ValueError, KeyError):
            # Fall through to generic handling
            pass

        raise APIError(f"Server internal error (500): {response.text}")

    async def _handle_response(self, response) -> Any:
        """Handle API response with error checking."""
        # Handle different status codes
        if response.status_code == 307:
            self._handle_redirect_response(response)
        elif response.status_code == 401:
            raise AuthenticationError("Unauthorized (401)", status_code=401)
        elif response.status_code == 403:
            raise APIError("Access forbidden - authentication may be required")
        elif response.status_code == 500:
            self._handle_server_error_response(response)
        elif response.status_code != 200:
            raise UnexpectedStatusCodeError(
                f"Unexpected status code {response.status_code}: {response.text}"
            )

        if not response.text:
            raise InvalidResponseError("Empty response from API")

        return response
