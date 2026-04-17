"""Main API client for Fortum."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import date as date_cls
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict, cast
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
from ..models import (
    CustomerDetails,
    MeteringPoint,
    SpotPricePoint,
    TimeSeries,
    TimeSeriesDataPoint,
)
from .endpoints import APIEndpoints

if TYPE_CHECKING:
    from homeassistant.components.recorder.models import (
        StatisticData,
        StatisticMetaData,
    )
    from homeassistant.core import HomeAssistant

    from .auth import OAuth2AuthClient

_LOGGER = logging.getLogger(__name__)

CLEAR_STATISTICS_TIMEOUT_SECONDS = 60
REQUEST_RETRY_DELAYS = (5.0, 10.0)
HOURLY_VALUE_DIFF_TOLERANCE = 1e-9


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


def _fmt_utc_minute(value: datetime) -> str:
    """Format datetime in UTC minute precision for logs."""
    return dt_util.as_utc(value).strftime("%Y-%m-%d %H:%M")


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
        self._hourly_metadata_cache: dict[str, StatisticMetaData] = {}

    @staticmethod
    def _build_hourly_statistic_metadata(
        *,
        statistic_id: str,
        name: str,
        unit_of_measurement: str,
        unit_class: str | None,
        has_sum: bool,
    ) -> StatisticMetaData:
        """Build canonical hourly statistic metadata."""
        return cast(
            "StatisticMetaData",
            {
                "statistic_id": statistic_id,
                "source": DOMAIN,
                "name": name,
                "unit_of_measurement": unit_of_measurement,
                "unit_class": unit_class,
                "has_mean": True,
                "mean_type": StatisticMeanType.ARITHMETIC,
                "has_sum": has_sum,
            },
        )

    def _cache_hourly_metadata(self, *metadata_items: StatisticMetaData) -> None:
        """Store runtime metadata cache entries by statistic id."""
        for metadata in metadata_items:
            statistic_id = metadata["statistic_id"]
            self._hourly_metadata_cache[statistic_id] = cast(
                "StatisticMetaData",
                dict(metadata),
            )

    def _require_cached_hourly_metadata(self, statistic_id: str) -> StatisticMetaData:
        """Return cached statistic metadata or raise if missing."""
        metadata = self._hourly_metadata_cache.get(statistic_id)
        if metadata is None:
            raise APIError(
                "Missing runtime statistic metadata cache for "
                f"{statistic_id}; run regular stats sync first"
            )
        return metadata

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
            _LOGGER.info(
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
                if historical:
                    imported_points += await self._sync_hourly_data(
                        metering_point_no,
                        sync_start,
                        utc_now,
                        chunk_days=HOURLY_DATA_HISTORICAL_CHUNK_DAYS,
                    )
                else:
                    imported_points += await self._record_hourly_data_stats(
                        metering_point_no,
                        two_weeks_ago,
                        utc_now,
                    )

        return imported_points

    async def backfill_historical_price_gaps_for_metering_points(
        self,
        metering_points: tuple[MeteringPoint, ...],
    ) -> int:
        """Backfill recorder gaps older than the recent two-week window."""
        if not metering_points:
            _LOGGER.debug("no metering points; skipping historical gap backfill")
            return 0

        utc_now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        imported_points = 0

        for metering_point in metering_points:
            metering_point_no = metering_point.metering_point_no
            search_from: datetime | None = None

            while True:
                gap_start = await self._find_first_recorded_price_gap_hour(
                    metering_point_no,
                    now=utc_now,
                    from_date=search_from,
                )
                if gap_start is None:
                    break

                window_start = gap_start - timedelta(days=1)
                window_end = min(
                    window_start + timedelta(days=HOURLY_DATA_RECENT_WINDOW_DAYS),
                    utc_now,
                )
                imported_points_in_window = await self._record_hourly_data_stats(
                    metering_point_no,
                    window_start,
                    window_end,
                )
                imported_points += imported_points_in_window
                _LOGGER.debug(
                    "historical gap processed: metering_point_no=%s gap_start=%s "
                    "added_points=%d",
                    metering_point_no,
                    _fmt_day(gap_start),
                    imported_points_in_window,
                )
                await self._recalculate_hourly_sums_until_end(
                    metering_point_no,
                    window_start,
                    utc_now,
                )

                last_filled_day = window_end.replace(
                    hour=0,
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                search_from = last_filled_day - timedelta(days=1)

                if search_from >= utc_now:
                    break

        return imported_points

    async def clear_statistics_for_discovered_points(
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
        self._hourly_metadata_cache.clear()

        return len(statistic_ids)

    async def _sync_hourly_data(
        self,
        metering_point_no: str,
        range_start: datetime,
        range_end: datetime,
        *,
        chunk_days: int = HOURLY_DATA_RECENT_WINDOW_DAYS,
    ) -> int:
        """Sync hourly data from oldest to newest in chunk_days windows."""
        if range_start >= range_end:
            return 0

        imported_points = 0
        window_start = range_start
        window = timedelta(days=chunk_days)

        while window_start < range_end:
            window_end = min(window_start + window, range_end)
            (
                next_window_start,
                imported_in_window,
            ) = await self._sync_hourly_data_chunk(
                metering_point_no,
                window_start,
                window_end,
            )
            imported_points += imported_in_window
            window_start = next_window_start

        return imported_points

    async def _sync_hourly_data_chunk(
        self,
        metering_point_no: str,
        chunk_start: datetime,
        range_end: datetime,
    ) -> tuple[datetime, int]:
        """Sync one up-to-two-weeks hourly-data chunk and return next start."""
        imported = await self._record_hourly_data_stats(
            metering_point_no,
            chunk_start,
            range_end,
        )
        return range_end, imported

    async def _determine_hourly_data_sync_start(
        self,
        metering_point_no: str,
        two_weeks_ago: datetime,
        now: datetime,
    ) -> tuple[datetime, bool]:
        """Determine hourly-data sync start and whether historical mode is needed."""
        recent_last_recorded_hour = await self._find_last_recorded_price_stat_hour(
            metering_point_no,
            two_weeks_ago,
            now,
        )
        if recent_last_recorded_hour is not None:
            return two_weeks_ago, False

        last_recorded_hour: datetime | None = None
        for years in (5, 10, 20):
            lookback_start = now - timedelta(days=365 * years)
            last_recorded_hour = await self._find_last_recorded_price_stat_hour(
                metering_point_no,
                lookback_start,
                now,
            )
            if last_recorded_hour is not None:
                break

        if last_recorded_hour is None:
            earliest = self._earliest_available_by_metering_point.get(metering_point_no)
            if earliest is None:
                _LOGGER.warning(
                    "no price statistics in [%s, %s) for %s and earliest hour is "
                    "unknown; starting from two_weeks_ago",
                    two_weeks_ago.isoformat(),
                    now.isoformat(),
                    metering_point_no,
                )
                return two_weeks_ago, True

            _LOGGER.info(
                "no price statistics in [%s, %s) for %s; starting historical sync "
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

        return next_hour, True

    async def _get_recorded_price_stat_hours(
        self,
        metering_point_no: str,
        from_date: datetime,
        to_date: datetime,
    ) -> set[datetime]:
        """Return normalized recorded hourly starts for price stats in range."""
        statistic_id = self._build_price_statistic_id(metering_point_no)
        try:
            result = await get_instance(self._hass).async_add_executor_job(
                lambda: statistics_during_period(
                    self._hass,
                    start_time=from_date,
                    end_time=to_date,
                    statistic_ids={statistic_id},
                    period="hour",
                    units=None,
                    types={"mean"},
                )
            )
        except Exception as exc:
            _LOGGER.debug(
                "could not read price statistics coverage for %s: %s",
                statistic_id,
                exc,
            )
            return set()

        rows = result.get(statistic_id) if result else None
        if not rows:
            return set()

        recorded_hours: set[datetime] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            start = self._parse_stat_start(row.get("start"))
            if start is None:
                continue
            if from_date <= start < to_date:
                recorded_hours.add(start)
        return recorded_hours

    async def _find_last_recorded_price_stat_hour(
        self,
        metering_point_no: str,
        from_date: datetime,
        to_date: datetime,
    ) -> datetime | None:
        """Return latest recorded price-stat hour in [from_date, to_date)."""
        recorded_hours = await self._get_recorded_price_stat_hours(
            metering_point_no,
            from_date,
            to_date,
        )
        if not recorded_hours:
            return None

        return max(recorded_hours)

    async def _find_first_recorded_price_gap_hour(
        self,
        metering_point_no: str,
        *,
        now: datetime,
        from_date: datetime | None = None,
    ) -> datetime | None:
        """Return first recorder gap hour older than recent window.

        When from_date is omitted, search starts from the first recorded hour in
        recorder. Missing hours before recorder coverage are ignored.
        """
        search_end = dt_util.as_utc(now).replace(minute=0, second=0, microsecond=0)
        if from_date is None:
            search_start = dt_util.utc_from_timestamp(0).replace(
                minute=0,
                second=0,
                microsecond=0,
            )
        else:
            search_start = dt_util.as_utc(from_date).replace(
                minute=0,
                second=0,
                microsecond=0,
            )

        if search_start >= search_end:
            return None

        recorded_hours = await self._get_recorded_price_stat_hours(
            metering_point_no,
            search_start,
            search_end,
        )
        ordered_hours = sorted(recorded_hours)
        if not ordered_hours:
            return None

        recent_cutoff = search_end - timedelta(days=HOURLY_DATA_RECENT_WINDOW_DAYS)
        previous_hour = ordered_hours[0]

        for current_hour in ordered_hours[1:]:
            expected_next_hour = previous_hour + timedelta(hours=1)
            if current_hour > expected_next_hour:
                if expected_next_hour >= recent_cutoff:
                    return None
                return expected_next_hour
            previous_hour = current_hour

        return None

    async def _get_hourly_stats_values_in_window(
        self,
        statistic_ids: set[str],
        from_date: datetime,
        to_date: datetime,
        *,
        value_type: Literal["mean", "state"],
    ) -> dict[str, dict[datetime, float]]:
        """Return hourly values keyed by statistic id and hour."""
        if not statistic_ids:
            return {}

        try:
            result = await get_instance(self._hass).async_add_executor_job(
                lambda: statistics_during_period(
                    self._hass,
                    start_time=from_date,
                    end_time=to_date,
                    statistic_ids=statistic_ids,
                    period="hour",
                    units=None,
                    types={value_type},
                )
            )
        except Exception as exc:
            _LOGGER.debug(
                "could not read hourly %s statistics in window: %s",
                value_type,
                exc,
            )
            return {}

        values_by_id: dict[str, dict[datetime, float]] = {}
        for statistic_id in statistic_ids:
            rows = result.get(statistic_id) if result else None
            if not rows:
                continue

            hourly_values: dict[datetime, float] = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                start = self._parse_stat_start(row.get("start"))
                if start is None or not (from_date <= start < to_date):
                    continue

                value = row.get(value_type)
                if not isinstance(value, (int, float)):
                    continue
                hourly_values[start] = float(value)

            if hourly_values:
                values_by_id[statistic_id] = hourly_values

        return values_by_id

    @staticmethod
    def _hourly_values_differ(
        previous_value: float | None,
        current_value: float | None,
    ) -> bool:
        """Return whether two optional hourly values differ."""
        if previous_value is None and current_value is None:
            return False
        if previous_value is None or current_value is None:
            return True
        return abs(previous_value - current_value) > HOURLY_VALUE_DIFF_TOLERANCE

    @staticmethod
    def _rows_to_hourly_state_map(
        rows: list[_MutableStatisticRow],
    ) -> dict[datetime, float]:
        """Return per-hour state map for imported statistics rows."""
        return {row["start"]: float(row["state"]) for row in rows}

    async def _analyze_existing_hourly_value_differences(
        self,
        *,
        statistic_ids: tuple[str, str, str, str],
        price_statistic_id: str,
        incoming_by_statistic: dict[str, dict[datetime, float]],
        from_date: datetime,
        to_date: datetime,
    ) -> tuple[datetime | None, int]:
        """Return first differing hour and count for existing hourly values.

        Hour comparison is scoped to hours where price exists on both sides,
        because price is the canonical existence marker for the hourly core
        metrics bundle.
        """
        recorder_by_statistic = await self._get_hourly_stats_values_in_window(
            set(statistic_ids),
            from_date,
            to_date,
            value_type="state",
        )
        recorder_price_hours = set(
            recorder_by_statistic.get(price_statistic_id, {}).keys()
        )
        incoming_price_hours = set(
            incoming_by_statistic.get(price_statistic_id, {}).keys()
        )
        comparable_hours = sorted(recorder_price_hours & incoming_price_hours)

        differing_hours = 0
        first_differing_hour: datetime | None = None
        for hour in comparable_hours:
            hour_has_difference = False
            for statistic_id in statistic_ids:
                old_value = recorder_by_statistic.get(statistic_id, {}).get(hour)
                new_value = incoming_by_statistic.get(statistic_id, {}).get(hour)
                if self._hourly_values_differ(old_value, new_value):
                    hour_has_difference = True
                    break

            if hour_has_difference:
                differing_hours += 1
                if first_differing_hour is None:
                    first_differing_hour = hour

        return first_differing_hour, differing_hours

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
            _LOGGER.debug(
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

    async def _recalculate_hourly_sums_until_end(
        self,
        metering_point_no: str,
        from_hour: datetime,
        now: datetime,
    ) -> int:
        """Recalculate consumption/cost cumulative sums until end of stats."""
        range_start = dt_util.as_utc(from_hour).replace(
            minute=0, second=0, microsecond=0
        )
        range_end = dt_util.as_utc(now).replace(minute=0, second=0, microsecond=0)
        if range_start >= range_end:
            return 0

        statistic_ids = (
            self._build_consumption_statistic_id(metering_point_no),
            self._build_cost_statistic_id(metering_point_no),
        )
        metadata_by_statistic = {
            statistic_id: self._require_cached_hourly_metadata(statistic_id)
            for statistic_id in statistic_ids
        }

        state_by_statistic = await self._get_hourly_stats_values_in_window(
            set(statistic_ids),
            range_start,
            range_end,
            value_type="state",
        )

        rewritten_rows = 0
        for statistic_id in statistic_ids:
            state_by_hour = state_by_statistic.get(statistic_id)
            if not state_by_hour:
                continue

            metadata = metadata_by_statistic[statistic_id]
            if metadata.get("has_sum") is not True:
                raise APIError(
                    "Cached metadata must have has_sum=True for "
                    f"{statistic_id} during sum recalculation"
                )

            ordered_hours = sorted(state_by_hour)
            first_hour = ordered_hours[0]
            running_sum = await self._get_hourly_stat_sum_before_hour(
                statistic_id,
                first_hour,
            )

            statistic_rows: list[_MutableStatisticRow] = []
            for hour in ordered_hours:
                state_value = state_by_hour[hour]
                running_sum += state_value
                statistic_rows.append(
                    {
                        "start": hour,
                        "state": state_value,
                        "mean": state_value,
                        "min": state_value,
                        "max": state_value,
                        "sum": running_sum,
                    }
                )

            async_add_external_statistics(
                self._hass,
                metadata,
                cast("list[StatisticData]", statistic_rows),
            )
            rewritten_rows += len(statistic_rows)

        return rewritten_rows

    async def _record_hourly_data_stats(
        self,
        metering_point_no: str,
        from_date: datetime,
        to_date: datetime,
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
        sync_start = dt_util.as_utc(from_date).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        sync_end = dt_util.as_utc(to_date).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
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
            ordered_points = sorted(time_series.series, key=lambda item: item.at_utc)
            gap_summary = self._summarize_price_gaps(ordered_points)
            if gap_summary is not None:
                _LOGGER.warning(
                    "gap in stats for %s: %s",
                    time_series.metering_point_no,
                    gap_summary,
                )

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

                if point_time < sync_start or point_time >= sync_end:
                    continue

                # Fortum signals core hourly availability via price presence.
                if point.price is None:
                    continue

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

                if point.price is not None:
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
                first_consumption_hour = consumption_statistics[0]["start"]
                consumption_sum = await self._get_hourly_stat_sum_before_hour(
                    consumption_statistic_id,
                    first_consumption_hour,
                )
                for row in consumption_statistics:
                    state_value = row["state"]
                    consumption_sum += state_value
                    row["sum"] = consumption_sum

            if cost_statistics:
                first_cost_hour = cost_statistics[0]["start"]
                cost_sum = await self._get_hourly_stat_sum_before_hour(
                    cost_statistic_id,
                    first_cost_hour,
                )
                for row in cost_statistics:
                    state_value = row["state"]
                    cost_sum += state_value
                    row["sum"] = cost_sum

            consumption_metadata = self._build_hourly_statistic_metadata(
                statistic_id=consumption_statistic_id,
                name=f"Fortum Hourly Consumption {time_series.metering_point_no}",
                unit_of_measurement=time_series.measurement_unit,
                unit_class="energy",
                has_sum=True,
            )
            cost_metadata = self._build_hourly_statistic_metadata(
                statistic_id=cost_statistic_id,
                name=f"Fortum Hourly Cost {time_series.metering_point_no}",
                unit_of_measurement=time_series.cost_unit,
                unit_class=None,
                has_sum=True,
            )
            price_metadata = self._build_hourly_statistic_metadata(
                statistic_id=price_statistic_id,
                name=f"Fortum Hourly Price {time_series.metering_point_no}",
                unit_of_measurement=time_series.price_unit,
                unit_class=None,
                has_sum=False,
            )
            temperature_metadata = self._build_hourly_statistic_metadata(
                statistic_id=temperature_statistic_id,
                name=f"Fortum Hourly Temperature {time_series.metering_point_no}",
                unit_of_measurement=self._normalize_temperature_unit(
                    time_series.temperature_unit
                ),
                unit_class="temperature",
                has_sum=False,
            )
            self._cache_hourly_metadata(
                consumption_metadata,
                cost_metadata,
                price_metadata,
                temperature_metadata,
            )
            consumption_metadata = self._require_cached_hourly_metadata(
                consumption_statistic_id
            )
            cost_metadata = self._require_cached_hourly_metadata(cost_statistic_id)
            price_metadata = self._require_cached_hourly_metadata(price_statistic_id)
            temperature_metadata = self._require_cached_hourly_metadata(
                temperature_statistic_id
            )

            consumption_rows = cast("list[StatisticData]", consumption_statistics)
            cost_rows = cast("list[StatisticData]", cost_statistics)
            price_rows = cast("list[StatisticData]", price_statistics)
            temperature_rows = cast("list[StatisticData]", temperature_statistics)

            compared_statistic_ids = (
                consumption_statistic_id,
                cost_statistic_id,
                price_statistic_id,
                temperature_statistic_id,
            )
            incoming_by_statistic: dict[str, dict[datetime, float]] = {
                consumption_statistic_id: self._rows_to_hourly_state_map(
                    consumption_statistics
                ),
                cost_statistic_id: self._rows_to_hourly_state_map(cost_statistics),
                price_statistic_id: self._rows_to_hourly_state_map(price_statistics),
                temperature_statistic_id: self._rows_to_hourly_state_map(
                    temperature_statistics
                ),
            }
            (
                first_differing_hour,
                differing_hours,
            ) = await self._analyze_existing_hourly_value_differences(
                statistic_ids=compared_statistic_ids,
                price_statistic_id=price_statistic_id,
                incoming_by_statistic=incoming_by_statistic,
                from_date=sync_start,
                to_date=sync_end,
            )

            if first_differing_hour is not None:
                _LOGGER.warning(
                    "stats old values changed for %s: first_hour=%s differing_hours=%d",
                    time_series.metering_point_no,
                    _fmt_utc_minute(first_differing_hour),
                    differing_hours,
                )

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
            _fmt_day(earliest_available_hour)
            if earliest_available_hour is not None
            else "n/a"
        )
        latest_text = (
            _fmt_day(latest_available_hour)
            if latest_available_hour is not None
            else "n/a"
        )
        _LOGGER.debug(
            "hourly stats import done: metering_point_no=%s "
            "latest_available=%s -> %s processed_records=%d",
            metering_point_no,
            earliest_text,
            latest_text,
            imported_points,
        )

        return imported_points

    @staticmethod
    def _summarize_price_gaps(
        ordered_points: list[TimeSeriesDataPoint],
    ) -> str | None:
        """Return one-line summary of hourly core-metric gaps.

        Price presence is used as the existence marker for the core hourly
        bundle (energy/cost/price).
        """
        if not ordered_points:
            return None

        intervals: list[tuple[datetime, int, bool]] = []
        run_start = dt_util.as_utc(ordered_points[0].at_utc).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        run_missing = ordered_points[0].price is None
        run_hours = 1
        previous_time = run_start

        for point in ordered_points[1:]:
            point_time = dt_util.as_utc(point.at_utc).replace(
                minute=0,
                second=0,
                microsecond=0,
            )
            point_missing = point.price is None
            contiguous = point_time == previous_time + timedelta(hours=1)
            if contiguous and point_missing == run_missing:
                run_hours += 1
            else:
                intervals.append((run_start, run_hours, run_missing))
                run_start = point_time
                run_missing = point_missing
                run_hours = 1
            previous_time = point_time

        intervals.append((run_start, run_hours, run_missing))

        first_missing_index = next(
            (idx for idx, (_, _, is_missing) in enumerate(intervals) if is_missing),
            None,
        )
        if first_missing_index is None or first_missing_index == len(intervals) - 1:
            return None

        summary_parts = []
        for start, hours, is_missing in intervals[first_missing_index:]:
            state = "missing" if is_missing else "present"
            summary_parts.append(f"{_fmt_utc_minute(start)} {hours}h {state}")

        return ", ".join(summary_parts)

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
            _LOGGER.info("price fetch failed area=%s error=%s", area_code, last_error)
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
                        _LOGGER.info(
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
                    _LOGGER.debug(
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
