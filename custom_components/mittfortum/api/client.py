"""Main API client for MittFortum."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.util import dt as dt_util

from ..const import (
    DOMAIN,
    PRICE_RESOLUTIONS,
    STATISTICS_BACKFILL_DAYS,
    STATISTICS_REQUEST_TIMEOUT_SECONDS,
)
from ..exceptions import APIError, InvalidResponseError, UnexpectedStatusCodeError
from ..models import ConsumptionData, CustomerDetails, MeteringPoint, TimeSeries
from .endpoints import APIEndpoints

if TYPE_CHECKING:
    from homeassistant.components.recorder.models import (
        StatisticData,
        StatisticMetaData,
    )
    from homeassistant.core import HomeAssistant

    from .auth import OAuth2AuthClient

_LOGGER = logging.getLogger(__name__)

# Constants for error messages
TOKEN_EXPIRED_RETRY_MSG = "Token expired - retry required"


class FortumAPIClient:
    """Main API client for Fortum tRPC services."""

    def __init__(self, hass: HomeAssistant, auth_client: OAuth2AuthClient) -> None:
        """Initialize API client."""
        self._hass = hass
        self._auth_client = auth_client
        self._endpoints = APIEndpoints.for_region(getattr(auth_client, "region", "se"))

    async def get_customer_id(self) -> str:
        """Extract customer ID from session data or ID token."""
        # For session-based authentication, get customer ID from session data
        session_data = self._auth_client.session_data
        if session_data and "user" in session_data:
            user_data = session_data["user"]
            customer_id = user_data.get("customerId")
            if customer_id:
                return customer_id

        # Fall back to JWT token extraction for token-based authentication
        id_token = self._auth_client.id_token
        if not id_token:
            raise APIError("No ID token or session data available")

        # Skip JWT decoding for session-based dummy tokens
        if id_token == "session_based":
            raise APIError("Customer ID not found in session data")

        try:
            import jwt

            payload = jwt.decode(id_token, options={"verify_signature": False})
            return payload["customerid"][0]["crmid"]
        except (KeyError, IndexError, ValueError) as exc:
            raise APIError(f"Failed to extract customer ID: {exc}") from exc

    async def get_customer_details(self) -> CustomerDetails:
        """Fetch customer details using session endpoint."""
        response = await self._get(self._endpoints.session)

        try:
            json_data = response.json()
            return CustomerDetails.from_api_response(json_data)
        except (ValueError, KeyError) as exc:
            raise InvalidResponseError(
                f"Invalid customer details response: {exc}"
            ) from exc

    async def get_metering_points(self) -> list[MeteringPoint]:
        """Fetch metering points from session endpoint."""
        response = await self._get(self._endpoints.session)

        try:
            json_data = response.json()

            # Extract delivery sites from session response
            if "user" in json_data and "deliverySites" in json_data["user"]:
                delivery_sites = json_data["user"]["deliverySites"]
                return [
                    MeteringPoint.from_api_response(site) for site in delivery_sites
                ]
            else:
                return []
        except (ValueError, KeyError, TypeError) as exc:
            raise InvalidResponseError(
                f"Invalid metering points response: {exc}"
            ) from exc

    async def get_time_series_data(
        self,
        metering_point_nos: list[str],
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        resolution: str = "MONTH",
        series_type: str | None = None,
        request_timeout: float | None = None,
    ) -> list[TimeSeries]:
        """Fetch time series data using tRPC endpoint with automatic retry logic."""
        # Default to last 3 months if no dates provided
        if not from_date:
            from_date = datetime.now().replace(day=1) - timedelta(days=90)
        if not to_date:
            to_date = datetime.now()

        # Try with the requested date range first
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
            if "Server error" in str(exc) or "reducing date range" in str(exc):
                _LOGGER.warning(
                    "Server error with requested date range, trying with last 30 days"
                )
                # Fallback to last 30 days
                fallback_from = datetime.now() - timedelta(days=30)
                fallback_to = datetime.now()
                try:
                    return await self._fetch_time_series_data(
                        metering_point_nos,
                        fallback_from,
                        fallback_to,
                        resolution,
                        series_type=series_type,
                        request_timeout=request_timeout,
                    )
                except APIError:
                    _LOGGER.warning(
                        "Server error with 30-day range, trying with last 7 days"
                    )
                    # Final fallback to last 7 days
                    final_from = datetime.now() - timedelta(days=7)
                    final_to = datetime.now()
                    return await self._fetch_time_series_data(
                        metering_point_nos,
                        final_from,
                        final_to,
                        resolution,
                        series_type=series_type,
                        request_timeout=request_timeout,
                    )
            else:
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

        _LOGGER.debug(
            "Fetching time series data from %s to %s with resolution %s",
            from_date.isoformat(),
            to_date.isoformat(),
            resolution,
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

    async def get_consumption_data(
        self,
        metering_point_nos: list[str] | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        resolution: str = "MONTH",
    ) -> list[ConsumptionData]:
        """Fetch consumption data and convert to legacy format."""
        if not metering_point_nos:
            # Get all metering points for the customer
            metering_points = await self.get_metering_points()
            if not metering_points:
                raise APIError("No metering points found for customer")
            metering_point_nos = [mp.metering_point_no for mp in metering_points]

        time_series_list = await self.get_time_series_data(
            metering_point_nos=metering_point_nos,
            from_date=from_date,
            to_date=to_date,
            resolution=resolution,
        )

        # Convert time series to consumption data
        consumption_data = []
        for time_series in time_series_list:
            consumption_data.extend(
                ConsumptionData.from_time_series(
                    time_series, timezone=self._endpoints.profile.timezone
                )
            )

        return consumption_data

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

    async def get_total_consumption(self) -> list[ConsumptionData]:
        """Get total consumption data for the customer."""
        return await self.get_consumption_data()

    async def backfill_hourly_consumption_statistics_last_month(self) -> int:
        """Backfill last month of hourly consumption as external statistics."""
        metering_points = await self.get_metering_points()
        metering_point_nos = [point.metering_point_no for point in metering_points]
        if not metering_point_nos:
            _LOGGER.debug("No metering points available for hourly statistics sync")
            return 0

        utc_now = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        from_date = utc_now - timedelta(days=STATISTICS_BACKFILL_DAYS)

        time_series_list = await self.get_time_series_data(
            metering_point_nos=metering_point_nos,
            from_date=from_date,
            to_date=utc_now,
            resolution="HOUR",
            series_type="CONSUMPTION",
            request_timeout=STATISTICS_REQUEST_TIMEOUT_SECONDS,
        )

        imported_points = 0
        for time_series in time_series_list:
            statistic_id = self._build_consumption_statistic_id(
                time_series.metering_point_no
            )

            statistics = []
            for point in sorted(time_series.series, key=lambda item: item.at_utc):
                point_time = dt_util.as_utc(point.at_utc).replace(
                    minute=0,
                    second=0,
                    microsecond=0,
                )
                if point_time > utc_now:
                    continue

                consumption_value = float(point.total_energy)
                statistics.append(
                    {
                        "start": point_time,
                        "state": consumption_value,
                        "mean": consumption_value,
                        "min": consumption_value,
                        "max": consumption_value,
                    }
                )

            if not statistics:
                continue

            metadata = cast(
                "StatisticMetaData",
                {
                    "statistic_id": statistic_id,
                    "source": DOMAIN,
                    "name": (
                        f"MittFortum Hourly Consumption {time_series.metering_point_no}"
                    ),
                    "unit_of_measurement": time_series.measurement_unit,
                    "has_mean": True,
                    "has_sum": False,
                },
            )

            statistic_rows = cast("list[StatisticData]", statistics)

            async_add_external_statistics(self._hass, metadata, statistic_rows)
            imported_points += len(statistics)

        return imported_points

    @staticmethod
    def _build_consumption_statistic_id(metering_point_no: str) -> str:
        """Build stable statistic_id for a metering point."""
        suffix = re.sub(r"[^0-9a-z_]", "_", metering_point_no.lower()).strip("_")
        if not suffix:
            suffix = "unknown"
        return f"{DOMAIN}:hourly_consumption_{suffix}"

    async def get_price_data(self) -> list[ConsumptionData]:
        """Get near real-time spot price data with future price points."""
        local_now = datetime.now(ZoneInfo(self._endpoints.profile.timezone))
        from_date = (local_now - timedelta(days=1)).date()
        to_date = (local_now + timedelta(days=1)).date()
        price_area = self._resolve_price_area()
        last_error: APIError | None = None

        for resolution in PRICE_RESOLUTIONS:
            try:
                url = self._endpoints.get_spot_prices_url(
                    price_area=price_area,
                    from_date=from_date,
                    to_date=to_date,
                    resolution=resolution,
                )
                response = await self._get(url)
                data = await self._parse_trpc_response(response)

                if not isinstance(data, list):
                    raise InvalidResponseError("Spot prices response is not a list")

                price_data: list[ConsumptionData] = []
                local_tz = ZoneInfo(self._endpoints.profile.timezone)

                for area_payload in data:
                    if not isinstance(area_payload, dict):
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
                            ConsumptionData(
                                date_time=at_utc.astimezone(local_tz),
                                value=0.0,
                                cost=None,
                                price=float(total_price),
                                price_unit=str(price_unit) if price_unit else None,
                                unit="kWh",
                            )
                        )

                if price_data:
                    price_data.sort(key=lambda point: point.date_time)
                    return price_data
            except APIError as exc:
                last_error = exc
                _LOGGER.debug(
                    "Price fetch failed for resolution %s: %s",
                    resolution,
                    exc,
                )

        if last_error is not None:
            raise APIError(f"Failed to fetch price data: {last_error}") from last_error

        return []

    def _resolve_price_area(self) -> str:
        """Resolve price area from session payload or region fallback."""
        session_data = self._auth_client.session_data or {}
        if isinstance(session_data, dict):
            user_data = session_data.get("user")
            if isinstance(user_data, dict):
                delivery_sites = user_data.get("deliverySites")
                if isinstance(delivery_sites, list):
                    for site in delivery_sites:
                        if not isinstance(site, dict):
                            continue
                        for key_path in (
                            ("priceArea",),
                            ("consumption", "priceArea"),
                        ):
                            value: Any = site
                            for key in key_path:
                                if not isinstance(value, dict):
                                    value = None
                                    break
                                value = value.get(key)
                            if isinstance(value, str) and value.strip():
                                return value.strip().upper()

        region_defaults = {
            "fi": "FI",
            "se": "SE3",
        }
        return region_defaults.get(self._endpoints.profile.code, "FI")

    async def _get(
        self,
        url: str,
        retry_count: int = 0,
        request_timeout: float | None = None,
    ) -> Any:
        """Perform authenticated GET request with retry logic."""
        # Allow maximum retries based on auth type:
        # - Session-based: 5 total attempts (4 retries)
        # - OAuth tokens: 2 total attempts (1 retry)
        is_session_based = self._auth_client.refresh_token == "session_based"
        max_retries = 5 if is_session_based else 2

        if retry_count >= max_retries:
            raise APIError(f"Maximum retry attempts ({max_retries}) exceeded for {url}")

        await self._ensure_valid_token()

        async with get_async_client(self._hass) as client:
            # Add session cookies if available
            if self._auth_client.session_cookies:
                for name, value in self._auth_client.session_cookies.items():
                    # Determine the correct domain for this cookie
                    domain = self._get_cookie_domain(name)

                    # Use .set() method for real httpx clients, fallback to dict access
                    # for tests
                    if hasattr(client.cookies, "set"):
                        client.cookies.set(name, value, domain=domain)
                        _LOGGER.debug(
                            "Added cookie to request: %s=%s... (domain=%s)",
                            name,
                            value[:20] if len(value) > 20 else value,
                            domain,
                        )
                    else:
                        # Fallback for test mocks that use plain dict
                        client.cookies[name] = value
                        _LOGGER.debug(
                            "Added cookie to request: %s=%s...",
                            name,
                            value[:20] if len(value) > 20 else value,
                        )
                _LOGGER.debug(
                    "Added %d session cookies to request",
                    len(self._auth_client.session_cookies),
                )

            try:
                # Build headers fresh for each attempt
                headers = {
                    "Accept": "application/json",
                    "User-Agent": (
                        "Mozilla/5.0 (X11; Linux x86_64; rv:138.0) "
                        "Gecko/20100101 Firefox/138.0"
                    ),
                    "Content-Type": "application/json",
                    "Referer": self._endpoints.referer,
                }

                # Only add Authorization header for non-session endpoints
                # if we have an access token
                if (
                    "/api/trpc/" not in url
                    and "/api/auth/session" not in url
                    and self._auth_client.access_token
                    and self._auth_client.access_token != "session_based"
                ):
                    headers["Authorization"] = (
                        f"Bearer {self._auth_client.access_token}"
                    )

                _LOGGER.debug("Making GET request to: %s (retry: %d)", url, retry_count)
                _LOGGER.debug(
                    "Request headers: %s",
                    {k: v for k, v in headers.items() if k != "Authorization"},
                )
                request_kwargs: dict[str, Any] = {"headers": headers}
                if request_timeout is not None:
                    request_kwargs["timeout"] = request_timeout

                response = await client.get(url, **request_kwargs)
                return await self._handle_response(response)
            except APIError as exc:
                return await self._handle_retry_logic(
                    exc,
                    url,
                    retry_count,
                    max_retries,
                    request_timeout,
                )
            except Exception as exc:
                _LOGGER.exception("GET request failed for %s", url)
                raise APIError("GET request failed") from exc

    async def _handle_retry_logic(
        self,
        exc: APIError,
        url: str,
        retry_count: int,
        _max_retries: int,
        request_timeout: float | None,
    ) -> Any:
        """Handle retry logic for API errors."""
        # Check if this is a token expiration that can be retried
        # Allow up to 4 retries for session-based auth, 1 retry for OAuth tokens
        # Increased from 3 to 4 retries for session-based auth due to
        # session propagation delays
        is_session_based = self._auth_client.refresh_token == "session_based"
        max_retries_for_token_error = 4 if is_session_based else 1

        if (
            str(exc) == TOKEN_EXPIRED_RETRY_MSG
            and retry_count < max_retries_for_token_error
        ):
            # Calculate progressive delay for session propagation
            # Use much longer delays for session-based auth due to
            # server-side propagation time
            if is_session_based:
                # Progressive delays: 5s, 10s, 15s, 20s (total ~50s)
                # This accounts for session propagation across different API endpoints
                delay = 5.0 + (retry_count * 5.0)
            else:
                # Standard exponential backoff for OAuth tokens
                delay = 0.1 * (2**retry_count)

            _LOGGER.info(
                "Token was refreshed, retrying request to %s "
                "(attempt %d/%d) after %ss delay",
                url,
                retry_count + 1,
                max_retries_for_token_error,
                delay,
            )

            # Add delay for session propagation with exponential backoff
            _LOGGER.debug("Adding %s second delay for session propagation", delay)
            await asyncio.sleep(delay)

            # Retry the request with the refreshed token
            return await self._get(
                url,
                retry_count + 1,
                request_timeout=request_timeout,
            )
        elif "Authentication failed" in str(exc):
            # If authentication completely failed, don't retry
            _LOGGER.error("Authentication failed, cannot retry: %s", exc)
            raise
        else:
            # Re-raise APIError without wrapping it
            _LOGGER.debug("API error (no retry): %s", exc)
            raise

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
        _LOGGER.debug("Received 307 redirect to: %s", location)

        # Check if this is a session expiration redirect
        if "sign-out" in location and "TokenExpired" in location:
            _LOGGER.warning("Session expired - TokenExpired redirect detected")
            # For session-based auth, we need to re-authenticate completely
            # Signal retry by raising specific exception
            raise APIError(TOKEN_EXPIRED_RETRY_MSG)

        # Handle other redirects
        _LOGGER.warning("Unexpected redirect to: %s", location)
        raise APIError(f"Unexpected redirect to: {location}")

    async def _handle_unauthorized_response(self) -> None:
        """Handle 401 unauthorized responses."""
        _LOGGER.info("Token expired (401), attempting refresh")
        try:
            old_token = self._auth_client.access_token
            await self._auth_client.refresh_access_token()
            new_token = self._auth_client.access_token

            _LOGGER.debug(
                "Token refresh completed. Old token: %s..., New token: %s...",
                old_token[:20] if old_token else "None",
                new_token[:20] if new_token else "None",
            )

            # Signal retry by raising specific exception
            raise APIError(TOKEN_EXPIRED_RETRY_MSG)
        except APIError as api_exc:
            # If this is our retry signal, re-raise it
            if TOKEN_EXPIRED_RETRY_MSG in str(api_exc):
                raise
            # Otherwise it's a real refresh failure
            _LOGGER.error("Token refresh failed: %s", api_exc)
            raise APIError(
                "Authentication failed - re-authentication required"
            ) from api_exc
        except Exception as refresh_exc:
            _LOGGER.error("Token refresh failed: %s", refresh_exc)
            # If refresh fails, we need to re-authenticate
            raise APIError(
                "Authentication failed - re-authentication required"
            ) from refresh_exc

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
                        _LOGGER.error(
                            "Server error (tRPC): %s (code: %s)",
                            error_msg,
                            error_code,
                        )
                        # For INTERNAL_SERVER_ERROR, suggest reducing date range
                        if error_msg == "INTERNAL_SERVER_ERROR":
                            raise APIError(
                                "Server error - try reducing date range "
                                "or changing resolution"
                            )
                        else:
                            raise APIError(f"Server error: {error_msg}")
        except (ValueError, KeyError):
            # Fall through to generic handling
            pass

        _LOGGER.error("Server error (500): %s", response.text)
        raise APIError("Server internal error - try again later")

    async def _handle_response(self, response) -> Any:
        """Handle API response with error checking."""
        _LOGGER.debug("Response status: %s", response.status_code)

        # Handle different status codes
        if response.status_code == 307:
            self._handle_redirect_response(response)
        elif response.status_code == 401:
            await self._handle_unauthorized_response()
        elif response.status_code == 403:
            _LOGGER.warning("Access forbidden, may need re-authentication")
            raise APIError("Access forbidden - authentication may be required")
        elif response.status_code == 500:
            self._handle_server_error_response(response)
        elif response.status_code != 200:
            _LOGGER.error(
                "Unexpected status code: %s, response: %s",
                response.status_code,
                response.text,
            )
            raise UnexpectedStatusCodeError(
                f"Unexpected status code {response.status_code}: {response.text}"
            )

        if not response.text:
            raise InvalidResponseError("Empty response from API")

        return response

    async def _ensure_valid_token(self, proactive: bool = True) -> None:
        """Ensure we have a valid access token.

        Args:
            proactive: If True, renew tokens before they expire (recommended).
                      If False, only renew after expiry (legacy behavior).
        """
        # Check if token needs renewal (proactive by default with 5-minute buffer)
        if proactive and self._auth_client.needs_renewal():
            _LOGGER.debug("Token needs proactive renewal (expires within 5 minutes)")
            needs_refresh = True
        elif self._auth_client.is_token_expired():
            _LOGGER.debug("Token is expired, requires immediate renewal")
            needs_refresh = True
        else:
            needs_refresh = False

        if needs_refresh:
            # Check if we have a real OAuth2 refresh token or session-based token
            if (
                self._auth_client.refresh_token
                and self._auth_client.refresh_token != "session_based"
            ):
                _LOGGER.debug("Refreshing OAuth2 access token")
                try:
                    await self._auth_client.refresh_access_token()
                    _LOGGER.info("Successfully refreshed OAuth2 access token")
                except Exception as exc:
                    _LOGGER.warning(
                        "OAuth2 token refresh failed, falling back to full "
                        "authentication: %s",
                        exc,
                    )
                    await self._auth_client.authenticate()
            else:
                # For session-based auth or no refresh token, re-authenticate
                _LOGGER.debug(
                    "Performing full re-authentication for session-based tokens"
                )
                await self._auth_client.authenticate()
                _LOGGER.info("Successfully re-authenticated session-based tokens")

    async def test_connection(self) -> dict[str, Any]:
        """Test API connection and return status information."""
        try:
            # Test session endpoint first
            session_response = await self._get(self._endpoints.session)
            session_data = session_response.json()

            # Check if we have user data
            user_data = session_data.get("user", {})
            if not user_data:
                return {
                    "success": False,
                    "error": "No user data in session - authentication may have failed",
                    "session_status": "invalid",
                }

            # Extract metering points
            metering_points = []
            if "deliverySites" in user_data:
                for site in user_data["deliverySites"]:
                    if (
                        "consumption" in site
                        and "meteringPointNo" in site["consumption"]
                    ):
                        metering_points.append(site["consumption"]["meteringPointNo"])

            if not metering_points:
                return {
                    "success": False,
                    "error": "No metering points found in session data",
                    "session_status": "valid",
                    "user_id": user_data.get("id"),
                }

            # Test a simple tRPC call with minimal data
            try:
                # Try last 24 hours with hourly resolution (minimal request)
                test_from = datetime.now() - timedelta(hours=24)
                test_to = datetime.now()

                test_series = await self._fetch_time_series_data(
                    [metering_points[0]], test_from, test_to, "HOUR"
                )

                return {
                    "success": True,
                    "session_status": "valid",
                    "user_id": user_data.get("id"),
                    "metering_points": metering_points,
                    "api_test": "passed",
                    "test_data_points": len(test_series),
                }

            except Exception as api_exc:
                return {
                    "success": False,
                    "error": f"API test failed: {api_exc}",
                    "session_status": "valid",
                    "user_id": user_data.get("id"),
                    "metering_points": metering_points,
                    "api_test": "failed",
                }

        except Exception as exc:
            return {
                "success": False,
                "error": f"Connection test failed: {exc}",
                "session_status": "unknown",
            }
