"""Unit tests for FortumAPIClient."""

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest

from custom_components.fortum.api.client import (
    REQUEST_RETRY_DELAYS,
    FortumAPIClient,
)
from custom_components.fortum.const import HOURLY_DATA_REQUEST_TIMEOUT_SECONDS
from custom_components.fortum.exceptions import APIError, AuthenticationError
from custom_components.fortum.models import (
    CostDataPoint,
    CustomerDetails,
    EnergyDataPoint,
    MeteringPoint,
    Price,
    SpotPricePoint,
    TemperatureReading,
    TimeSeries,
    TimeSeriesDataPoint,
)


class TestFortumAPIClient:
    """Test FortumAPIClient."""

    def test_init(self, mock_hass, mock_auth_client):
        """Test initialization."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        assert client._hass == mock_hass
        assert client._auth_client == mock_auth_client

    async def test_get_customer_id_success(self, mock_hass, mock_auth_client):
        """Test successful customer ID extraction."""
        mock_auth_client.id_token = "test_token"

        client = FortumAPIClient(mock_hass, mock_auth_client)

        with patch("jwt.decode") as mock_decode:
            mock_decode.return_value = {"customerid": [{"crmid": "customer_123"}]}

            result = await client.get_customer_id()

            assert result == "customer_123"

    async def test_get_customer_id_no_token(self, mock_hass, mock_auth_client):
        """Test customer ID extraction with no token."""
        mock_auth_client.id_token = None
        mock_auth_client.session_data = None

        client = FortumAPIClient(mock_hass, mock_auth_client)

        with pytest.raises(APIError, match="No ID token or session data available"):
            await client.get_customer_id()

    async def test_get_customer_id_from_session(self, mock_hass, mock_auth_client):
        """Test customer ID extraction from session data."""
        mock_auth_client.session_data = {"user": {"customerId": "session_customer_123"}}
        mock_auth_client.id_token = "session_based"

        client = FortumAPIClient(mock_hass, mock_auth_client)

        result = await client.get_customer_id()

        assert result == "session_customer_123"

    async def test_get_customer_id_session_based_no_data(
        self, mock_hass, mock_auth_client
    ):
        """Test customer ID extraction with session-based token but no session data."""
        mock_auth_client.session_data = None
        mock_auth_client.id_token = "session_based"

        client = FortumAPIClient(mock_hass, mock_auth_client)

        with pytest.raises(APIError, match="Customer ID not found in session data"):
            await client.get_customer_id()

    async def test_get_customer_details_success(
        self, mock_hass, mock_auth_client, sample_customer_details
    ):
        """Test successful customer details fetch."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Mock response data from session endpoint
        mock_response = Mock()
        mock_response.json.return_value = {
            "user": {
                "customerId": "customer_123",
                "postalAddress": "Test Street 123",
                "postOffice": "Test City",
                "name": "Test Customer",
            }
        }

        with patch.object(client, "_get", return_value=mock_response):
            result = await client.get_customer_details()

            assert isinstance(result, CustomerDetails)
            assert result.customer_id == "customer_123"
            assert result.postal_address == "Test Street 123"

    async def test_get_time_series_data_requires_explicit_date_range(
        self, mock_hass, mock_auth_client
    ):
        """Reject time series fetch when from/to range is missing."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        with pytest.raises(
            APIError,
            match="from_date and to_date are required for time series fetch",
        ):
            await client.get_time_series_data(
                metering_point_nos=["6094111"],
                from_date=cast("datetime", None),
                to_date=datetime.fromisoformat("2026-03-18T00:00:00+00:00"),
                resolution="HOUR",
                series_type="CONSUMPTION",
            )

    async def test_get_time_series_data_requires_explicit_resolution(
        self, mock_hass, mock_auth_client
    ):
        """Reject time series fetch when resolution is missing."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        with pytest.raises(APIError, match="resolution is required"):
            await client.get_time_series_data(
                metering_point_nos=["6094111"],
                from_date=datetime.fromisoformat("2026-03-17T00:00:00+00:00"),
                to_date=datetime.fromisoformat("2026-03-18T00:00:00+00:00"),
                resolution=cast("str", None),
                series_type="CONSUMPTION",
            )

    async def test_get_time_series_data_rejects_invalid_date_order(
        self, mock_hass, mock_auth_client
    ):
        """Reject time series fetch when from_date is not earlier than to_date."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        with pytest.raises(
            APIError,
            match="from_date must be earlier than to_date",
        ):
            await client.get_time_series_data(
                metering_point_nos=["6094111"],
                from_date=datetime.fromisoformat("2026-03-18T00:00:00+00:00"),
                to_date=datetime.fromisoformat("2026-03-18T00:00:00+00:00"),
                resolution="HOUR",
                series_type="CONSUMPTION",
            )

    async def test_get_time_series_data_retries_with_exponential_backoff(
        self, mock_hass, mock_auth_client
    ):
        """Time series fetch delegates retry behavior to _get."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        request_from = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
        request_to = datetime.fromisoformat("2026-06-30T00:00:00+00:00")

        with (
            patch.object(
                client,
                "_fetch_time_series_data",
                return_value=[],
            ) as mock_fetch,
        ):
            result = await client.get_time_series_data(
                metering_point_nos=["6094111"],
                from_date=request_from,
                to_date=request_to,
                resolution="HOUR",
                series_type="CONSUMPTION",
            )

        assert result == []
        assert mock_fetch.call_count == 1

    async def test_get_time_series_data_logs_context_after_retry_exhaustion(
        self, mock_hass, mock_auth_client
    ):
        """Log request context and raise when fetch fails."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        request_from = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
        request_to = datetime.fromisoformat("2026-06-30T00:00:00+00:00")

        with (
            patch.object(
                client,
                "_fetch_time_series_data",
                side_effect=APIError(
                    "Server error: [GraphQL] Subgraph errors redacted"
                ),
            ) as mock_fetch,
            patch("custom_components.fortum.api.client._LOGGER.error") as mock_error,
        ):
            with pytest.raises(APIError, match="Subgraph errors redacted"):
                await client.get_time_series_data(
                    metering_point_nos=["6094111"],
                    from_date=request_from,
                    to_date=request_to,
                    resolution="HOUR",
                    series_type="CONSUMPTION",
                )

        assert mock_fetch.call_count == 1
        mock_fetch.assert_called_with(
            ["6094111"],
            request_from,
            request_to,
            "HOUR",
            series_type="CONSUMPTION",
            request_timeout=None,
        )
        mock_error.assert_called_once()
        assert "time series fetch failed" in mock_error.call_args.args[0]
        assert mock_error.call_args.args[1] == ["6094111"]
        assert mock_error.call_args.args[2] == request_from.isoformat()
        assert mock_error.call_args.args[3] == request_to.isoformat()

    async def test_get_price_data_uses_spot_prices_endpoint(
        self, mock_hass, mock_auth_client
    ):
        """Test spot prices endpoint parsing for price data."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        mock_auth_client.session_data = {
            "user": {"deliverySites": [{"consumption": {"priceArea": "FI"}}]}
        }

        parsed_payload = [
            {
                "priceArea": "FI",
                "priceUnit": "c/kWh",
                "spotPriceSeries": [
                    {
                        "atUTC": "2026-03-17T22:00:00.000Z",
                        "spotPrice": {"total": 4.23},
                    },
                    {
                        "atUTC": "2026-03-17T22:15:00.000Z",
                        "spotPrice": {"total": 4.50},
                    },
                ],
            }
        ]

        with (
            patch.object(client, "_get", return_value=Mock()) as mock_get,
            patch.object(
                client,
                "_parse_trpc_response",
                return_value=parsed_payload,
            ),
            patch(
                "custom_components.fortum.api.client.async_add_external_statistics"
            ) as mock_add_stats,
        ):
            result = await client.get_price_data()

        assert len(result) == 2
        assert result[0].price == 4.23
        assert result[1].price == 4.50
        assert result[0].price_unit == "c/kWh"
        called_url = mock_get.call_args.args[0]
        assert "shared.spotPrices.listPriceAreaSpotPrices" in called_url
        assert "PER_15_MIN" in called_url
        mock_add_stats.assert_called_once()
        assert (
            mock_add_stats.call_args.args[1]["statistic_id"] == "fortum:price_forecast"
        )
        assert len(mock_add_stats.call_args.args[2]) == 1
        assert mock_add_stats.call_args.args[2][0]["start"].minute == 0
        assert mock_add_stats.call_args.args[2][0]["state"] == pytest.approx(4.365)
        assert mock_add_stats.call_args.args[2][0]["mean"] == pytest.approx(4.365)
        assert mock_add_stats.call_args.args[2][0]["min"] == 4.23
        assert mock_add_stats.call_args.args[2][0]["max"] == 4.50

    async def test_resolve_price_area_fallback(self, mock_hass, mock_auth_client):
        """Test fallback price area by region profile."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        mock_auth_client.session_data = {}

        assert client._resolve_price_area() == "SE3"

    def test_record_price_forecast_statistics_skips_unchanged_payload(
        self,
        mock_hass,
        mock_auth_client,
    ) -> None:
        """Forecast statistics write should be skipped when data digest is unchanged."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        price_data = [
            SpotPricePoint(
                date_time=datetime(2026, 3, 18, 10, 0, tzinfo=UTC),
                price=0.25,
                price_unit="EUR/kWh",
            ),
            SpotPricePoint(
                date_time=datetime(2026, 3, 18, 10, 15, tzinfo=UTC),
                price=0.35,
                price_unit="EUR/kWh",
            ),
        ]

        with patch(
            "custom_components.fortum.api.client.async_add_external_statistics"
        ) as mock_add_stats:
            client._record_price_forecast_statistics(price_data)
            client._record_price_forecast_statistics(price_data)

            assert mock_add_stats.call_count == 1

            updated_price_data = [
                SpotPricePoint(
                    date_time=datetime(2026, 3, 18, 10, 0, tzinfo=UTC),
                    price=0.26,
                    price_unit="EUR/kWh",
                ),
                SpotPricePoint(
                    date_time=datetime(2026, 3, 18, 10, 15, tzinfo=UTC),
                    price=0.35,
                    price_unit="EUR/kWh",
                ),
            ]
            client._record_price_forecast_statistics(updated_price_data)
            assert mock_add_stats.call_count == 2

    async def test_trpc_endpoints_exclude_auth_headers(
        self, mock_hass, mock_auth_client
    ):
        """Test that tRPC endpoints do NOT receive Authorization headers."""
        from unittest.mock import AsyncMock, MagicMock

        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}
        mock_auth_client.is_token_expired.return_value = False

        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Test tRPC endpoint
        trpc_url = (
            "https://www.fortum.com/se/el/api/trpc/loggedIn.timeSeries.listTimeSeries"
        )

        with patch(
            "custom_components.fortum.api.client.get_async_client"
        ) as mock_get_client:
            # Create a properly configured mock client
            mock_client = AsyncMock()

            # Mock the response with concrete values
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"test": "data"}'  # Non-empty text
            mock_response.json.return_value = [
                {"result": {"data": {"json": {"test": "data"}}}}
            ]

            mock_client.get.return_value = mock_response
            mock_client.cookies = MagicMock()

            # Configure the context manager
            mock_get_client.return_value.__aenter__.return_value = mock_client
            mock_get_client.return_value.__aexit__.return_value = None

            # Make the request
            await client._get(trpc_url)

            # Verify the call was made
            assert mock_client.get.called
            call_args = mock_client.get.call_args

            # Check that Authorization header was NOT included
            headers = call_args[1]["headers"]
            assert "Authorization" not in headers

    async def test_non_trpc_endpoints_include_auth_headers(
        self, mock_hass, mock_auth_client
    ):
        """Test that non-tRPC endpoints DO receive Authorization headers."""
        from unittest.mock import AsyncMock, MagicMock

        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}
        mock_auth_client.is_token_expired.return_value = False

        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Test non-tRPC endpoint
        api_url = "https://www.fortum.com/se/el/api/some-other-endpoint"

        with patch(
            "custom_components.fortum.api.client.get_async_client"
        ) as mock_get_client:
            # Create a properly configured mock client
            mock_client = AsyncMock()

            # Mock the response with concrete values
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"test": "data"}'  # Non-empty text
            mock_response.json.return_value = {"test": "data"}

            mock_client.get.return_value = mock_response
            mock_client.cookies = MagicMock()

            # Configure the context manager
            mock_get_client.return_value.__aenter__.return_value = mock_client
            mock_get_client.return_value.__aexit__.return_value = None

            # Make the request
            await client._get(api_url)

            # Verify the call was made
            assert mock_client.get.called
            call_args = mock_client.get.call_args

            # Check that Authorization header was included
            headers = call_args[1]["headers"]
            assert "Authorization" in headers
            assert headers["Authorization"] == "Bearer test_access_token_123"

    async def test_session_endpoints_exclude_auth_headers(
        self, mock_hass, mock_auth_client
    ):
        """Test that session endpoints do NOT receive Authorization headers."""
        from unittest.mock import AsyncMock, MagicMock

        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}
        mock_auth_client.is_token_expired.return_value = False

        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Test session endpoint
        session_url = "https://www.fortum.com/se/el/api/auth/session"

        with patch(
            "custom_components.fortum.api.client.get_async_client"
        ) as mock_get_client:
            # Create a properly configured mock client
            mock_client = AsyncMock()

            # Mock the response with concrete values
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.text = '{"user": {"test": "data"}}'  # Non-empty text
            mock_response.json.return_value = {"user": {"test": "data"}}

            mock_client.get.return_value = mock_response
            mock_client.cookies = MagicMock()

            # Configure the context manager
            mock_get_client.return_value.__aenter__.return_value = mock_client
            mock_get_client.return_value.__aexit__.return_value = None

            # Make the request
            await client._get(session_url)

            # Verify the call was made
            assert mock_client.get.called
            call_args = mock_client.get.call_args

            # Check that Authorization header was NOT included
            headers = call_args[1]["headers"]
            assert "Authorization" not in headers

    async def test_trpc_endpoint_no_auth_header(self, mock_hass, mock_auth_client):
        """Test that tRPC endpoints do NOT receive Authorization headers."""
        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}
        mock_auth_client.is_token_expired.return_value = False

        client = FortumAPIClient(mock_hass, mock_auth_client)

        # tRPC endpoint URL
        trpc_url = (
            "https://www.fortum.com/se/el/api/trpc/"
            "loggedIn.timeSeries.listTimeSeries?batch=1&input="
            "%7B%220%22%3A%7B%22json%22%3A%7B%22meteringPointNo%22%3A%5B%22123%22%5D%7D%7D%7D"
        )

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"test": "data"}'
        mock_response.json.return_value = [
            {"result": {"data": {"json": {"test": "data"}}}}
        ]

        with patch(
            "custom_components.fortum.api.client.get_async_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.cookies = {}

            mock_get_client.return_value.__aenter__.return_value = mock_client
            mock_get_client.return_value.__aexit__.return_value = None

            result = await client._get(trpc_url)

            # Verify Authorization header was NOT included
            call_args = mock_client.get.call_args
            headers = call_args[1]["headers"]
            assert "Authorization" not in headers

            # Verify we got the expected result
            assert result == mock_response

    async def test_non_trpc_endpoint_gets_auth_header(
        self, mock_hass, mock_auth_client
    ):
        """Test that non-tRPC endpoints DO receive Authorization headers."""
        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}
        mock_auth_client.is_token_expired.return_value = False

        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Non-tRPC API endpoint
        api_url = "https://www.fortum.com/se/el/api/some-other-endpoint"

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = '{"test": "data"}'
        mock_response.json.return_value = {"test": "data"}

        with patch(
            "custom_components.fortum.api.client.get_async_client"
        ) as mock_get_client:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.cookies = {}

            mock_get_client.return_value.__aenter__.return_value = mock_client
            mock_get_client.return_value.__aexit__.return_value = None

            await client._get(api_url)

            # Verify Authorization header was included
            call_args = mock_client.get.call_args
            headers = call_args[1]["headers"]
            assert "Authorization" in headers
            assert headers["Authorization"] == "Bearer test_access_token_123"

    async def test_get_propagates_api_error_without_retry(
        self, mock_hass, mock_auth_client
    ):
        """GET should retry API errors with configured delays."""
        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}
        mock_auth_client.is_token_expired.return_value = False

        client = FortumAPIClient(mock_hass, mock_auth_client)

        with (
            patch.object(
                client,
                "_handle_response",
                side_effect=[
                    APIError("some api error"),
                    APIError("some api error"),
                    Mock(status_code=200, text="{}", json=Mock(return_value={})),
                ],
            ),
            patch(
                "custom_components.fortum.api.client.asyncio.sleep",
                new=AsyncMock(),
            ) as mock_sleep,
            patch(
                "custom_components.fortum.api.client.get_async_client"
            ) as mock_get_client,
        ):
            mock_client = AsyncMock()
            mock_client.get.return_value = Mock(status_code=200, text="{}")
            mock_client.cookies = {}
            mock_get_client.return_value.__aenter__.return_value = mock_client
            mock_get_client.return_value.__aexit__.return_value = None

            result = await client._get("https://www.fortum.com/se/el/api/test")

            assert result.status_code == 200
            assert mock_client.get.call_count == 3
            assert mock_sleep.await_count == 2
            mock_sleep.assert_any_await(REQUEST_RETRY_DELAYS[0])
            mock_sleep.assert_any_await(REQUEST_RETRY_DELAYS[1])

    async def test_get_raises_authentication_error_only_after_last_retry(
        self,
        mock_hass,
        mock_auth_client,
    ):
        """AuthenticationError should be raised only after final retry attempt."""
        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}
        mock_auth_client.is_token_expired.return_value = False

        client = FortumAPIClient(mock_hass, mock_auth_client)

        with (
            patch.object(
                client,
                "_handle_response",
                side_effect=AuthenticationError("Unauthorized (401)"),
            ),
            patch(
                "custom_components.fortum.api.client.asyncio.sleep",
                new=AsyncMock(),
            ) as mock_sleep,
            patch(
                "custom_components.fortum.api.client.get_async_client"
            ) as mock_get_client,
        ):
            mock_client = AsyncMock()
            mock_client.get.return_value = Mock(status_code=401, text="unauthorized")
            mock_client.cookies = {}
            mock_get_client.return_value.__aenter__.return_value = mock_client
            mock_get_client.return_value.__aexit__.return_value = None

            with pytest.raises(AuthenticationError, match=r"Unauthorized \(401\)"):
                await client._get("https://www.fortum.com/se/el/api/test")

            assert mock_client.get.call_count == 3
            assert mock_sleep.await_count == 2
            mock_sleep.assert_any_await(REQUEST_RETRY_DELAYS[0])
            mock_sleep.assert_any_await(REQUEST_RETRY_DELAYS[1])

    async def test_get_logs_final_failure_with_url_and_details(
        self,
        mock_hass,
        mock_auth_client,
    ):
        """Final GET attempt should log URL and exception details."""
        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}
        mock_auth_client.is_token_expired.return_value = False

        client = FortumAPIClient(mock_hass, mock_auth_client)

        with (
            patch.object(
                client,
                "_handle_response",
                side_effect=APIError(""),
            ),
            patch(
                "custom_components.fortum.api.client.asyncio.sleep",
                new=AsyncMock(),
            ),
            patch(
                "custom_components.fortum.api.client.get_async_client"
            ) as mock_get_client,
            patch("custom_components.fortum.api.client._LOGGER.error") as mock_error,
        ):
            mock_client = AsyncMock()
            mock_client.get.return_value = Mock(status_code=500, text="error")
            mock_client.cookies = {}
            mock_get_client.return_value.__aenter__.return_value = mock_client
            mock_get_client.return_value.__aexit__.return_value = None

            with pytest.raises(APIError):
                await client._get("https://www.fortum.com/se/el/api/test")

        mock_error.assert_called_once()
        args = mock_error.call_args.args
        assert args[0] == "GET failed after %d/%d attempts for %s: %s"
        assert args[1] == 3
        assert args[2] == 3
        assert args[3] == "https://www.fortum.com/se/el/api/test"
        assert "APIError" in args[4]

    async def test_session_expiration_307_redirect(self, mock_hass, mock_auth_client):
        """Test 307 redirect handling for TokenExpired redirect."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Mock a response with 307 redirect to sign-out with TokenExpired
        mock_response = Mock()
        mock_response.status_code = 307
        mock_response.headers = {
            "Location": "/se/el/sign-out?loggedInMessage=TokenExpired"
        }

        with pytest.raises(
            APIError,
            match=(
                r"Unexpected redirect to: "
                r"/se/el/sign-out\?loggedInMessage=TokenExpired"
            ),
        ):
            await client._handle_response(mock_response)

    async def test_unauthorized_401_raises_authentication_error(
        self,
        mock_hass,
        mock_auth_client,
    ):
        """401 responses should raise AuthenticationError."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        mock_response = Mock()
        mock_response.status_code = 401
        mock_response.text = "unauthorized"

        with pytest.raises(AuthenticationError, match=r"Unauthorized \(401\)"):
            await client._handle_response(mock_response)

    async def test_session_expiration_307_redirect_other(
        self, mock_hass, mock_auth_client
    ):
        """Test 307 redirect to other location (not session expiration)."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Mock a response with 307 redirect to some other location
        mock_response = Mock()
        mock_response.status_code = 307
        mock_response.headers = {"Location": "/some/other/location"}

        # Test that other redirects are handled differently
        with pytest.raises(
            APIError, match="Unexpected redirect to: /some/other/location"
        ):
            await client._handle_response(mock_response)

    async def test_handle_redirect_response_method(self, mock_hass, mock_auth_client):
        """Test the _handle_redirect_response method directly."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Test session expiration redirect
        mock_response = Mock()
        mock_response.headers = {
            "Location": "/se/el/sign-out?loggedInMessage=TokenExpired"
        }

        with pytest.raises(
            APIError,
            match=(
                r"Unexpected redirect to: "
                r"/se/el/sign-out\?loggedInMessage=TokenExpired"
            ),
        ):
            client._handle_redirect_response(mock_response)

        # Test other redirect
        mock_response.headers = {"Location": "/other/path"}

        with pytest.raises(APIError, match="Unexpected redirect to: /other/path"):
            client._handle_redirect_response(mock_response)

    async def test_sync_hourly_data_all_meters_imports_energy_cost_and_price(
        self, mock_hass, mock_auth_client
    ):
        """Test backfill imports energy, cost, and price from same payload."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        time_series = TimeSeries(
            delivery_site_category="CONSUMPTION",
            measurement_unit="kWh",
            metering_point_no="6094111",
            price_unit="c/kWh",
            cost_unit="EUR",
            temperature_unit="celsius",
            series=[
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-04T00:00:00+00:00"),
                    energy=[EnergyDataPoint(value=3.83, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=0.04,
                            value=0.03,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=1.01, value=0.80, vat_amount=0.21, vat_percentage=25.5
                    ),
                    temperature_reading=TemperatureReading(temperature=2.2),
                ),
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-04T01:00:00+00:00"),
                    energy=[EnergyDataPoint(value=0.0, type="ENERGY")],
                    cost=None,
                    price=None,
                    temperature_reading=None,
                ),
            ],
        )

        with (
            patch.object(
                client,
                "get_metering_points",
                return_value=[MeteringPoint(metering_point_no="6094111")],
            ),
            patch.object(
                client,
                "get_time_series_data",
                return_value=[time_series],
            ) as mock_get_series,
            patch(
                "custom_components.fortum.api.client.async_add_external_statistics"
            ) as mock_add_stats,
        ):
            imported = await client.sync_hourly_data_all_meters()

        assert imported == 4
        assert mock_get_series.call_args.kwargs["request_timeout"] == (
            HOURLY_DATA_REQUEST_TIMEOUT_SECONDS
        )
        assert mock_add_stats.call_count == 4
        assert mock_get_series.call_count == 1

        statistic_ids = [
            call.args[1]["statistic_id"] for call in mock_add_stats.call_args_list
        ]
        assert "fortum:hourly_consumption_6094111" in statistic_ids
        assert "fortum:hourly_cost_6094111" in statistic_ids
        assert "fortum:hourly_price_6094111" in statistic_ids
        assert "fortum:hourly_temperature_6094111" in statistic_ids

        for call in mock_add_stats.call_args_list:
            assert len(call.args[2]) == 1

    async def test_sync_hourly_data_all_meters_uses_first_missing_recent_hour(
        self, mock_hass, mock_auth_client
    ):
        """Start from first missing hour when recent cost stats exist."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        fixed_now = datetime.fromisoformat("2026-03-18T00:00:00+00:00")
        two_weeks_ago = fixed_now - timedelta(days=14)

        with (
            patch.object(
                client,
                "get_metering_points",
                return_value=[MeteringPoint(metering_point_no="6094111")],
            ),
            patch.object(
                client,
                "_find_last_recorded_cost_stat_hour",
                return_value=two_weeks_ago + timedelta(hours=1),
            ),
            patch.object(
                client,
                "_sync_hourly_data",
                return_value=0,
            ) as mock_sync_forward,
            patch(
                "custom_components.fortum.api.client.dt_util.utcnow",
                return_value=fixed_now,
            ),
        ):
            imported = await client.sync_hourly_data_all_meters()

        assert imported == 0
        assert mock_sync_forward.call_count == 1
        sync_start = mock_sync_forward.call_args.args[1]
        assert sync_start == two_weeks_ago + timedelta(hours=2)

    async def test_sync_hourly_data_all_meters_force_resync_uses_earliest_start(
        self, mock_hass, mock_auth_client
    ):
        """Force re-sync should always start from earliest available marker."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        earliest_start = datetime.fromisoformat("2026-01-01T00:00:00+00:00")

        with (
            patch.object(
                client,
                "get_metering_points",
                return_value=[
                    MeteringPoint(
                        metering_point_no="6094111",
                        earliest_hourly_available_at_utc=earliest_start,
                    )
                ],
            ),
            patch.object(
                client,
                "_sync_hourly_data",
                return_value=6,
            ) as mock_sync_forward,
        ):
            imported = await client.sync_hourly_data_all_meters(force_resync=True)

        assert imported == 6
        mock_sync_forward.assert_called_once()
        assert mock_sync_forward.call_args.args[1] == earliest_start
        assert mock_sync_forward.call_args.kwargs["continue_after_missing"] is True

    async def test_find_last_recorded_cost_stat_hour_parses_string_timestamp(
        self, mock_hass, mock_auth_client
    ):
        """Parse recorder start as ISO string when datetime is not returned."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        recorder_instance = Mock()
        recorder_instance.async_add_executor_job = AsyncMock(
            return_value={
                "fortum:hourly_cost_6094111": [
                    {"start": "2026-03-17T22:00:00+00:00", "sum": 1.2}
                ]
            }
        )

        with patch(
            "custom_components.fortum.api.client.get_instance",
            return_value=recorder_instance,
        ):
            last_recorded = await client._find_last_recorded_cost_stat_hour(
                "6094111",
                datetime.fromisoformat("2026-03-17T22:00:00+00:00"),
                datetime.fromisoformat("2026-03-18T00:00:00+00:00"),
            )

        assert last_recorded == datetime.fromisoformat("2026-03-17T22:00:00+00:00")

    async def test_find_last_recorded_cost_stat_hour_parses_unix_timestamp(
        self, mock_hass, mock_auth_client
    ):
        """Parse recorder start when statistics_during_period returns unix time."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        expected_start = datetime.fromisoformat("2026-03-17T22:00:00+00:00")
        recorder_instance = Mock()
        recorder_instance.async_add_executor_job = AsyncMock(
            return_value={
                "fortum:hourly_cost_6094111": [
                    {"start": expected_start.timestamp(), "sum": 1.2}
                ]
            }
        )

        with patch(
            "custom_components.fortum.api.client.get_instance",
            return_value=recorder_instance,
        ):
            last_recorded = await client._find_last_recorded_cost_stat_hour(
                "6094111",
                datetime.fromisoformat("2026-03-17T22:00:00+00:00"),
                datetime.fromisoformat("2026-03-18T00:00:00+00:00"),
            )

        assert last_recorded == expected_start

    async def test_determine_hourly_data_sync_start_uses_userinfo_marker(
        self, mock_hass, mock_auth_client
    ):
        """Use earliest marker from user info when no recent stats exist."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        cached_earliest = datetime.fromisoformat("2025-01-06T00:00:00+00:00")
        client._earliest_available_by_metering_point["6094111"] = cached_earliest
        two_weeks_ago = datetime.fromisoformat("2026-03-04T00:00:00+00:00")
        now = datetime.fromisoformat("2026-03-18T00:00:00+00:00")

        with patch.object(
            client,
            "_find_last_recorded_cost_stat_hour",
            return_value=None,
        ):
            start, historical = await client._determine_hourly_data_sync_start(
                "6094111",
                two_weeks_ago,
                now,
                force_resync=False,
            )

        assert start == cached_earliest
        assert historical is True

    async def test_backfill_stops_after_missing_price_gap(
        self, mock_hass, mock_auth_client
    ):
        """Warn and stop import when price reappears after a missing gap."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        time_series = TimeSeries(
            delivery_site_category="CONSUMPTION",
            measurement_unit="kWh",
            metering_point_no="6094111",
            price_unit="c/kWh",
            cost_unit="EUR",
            temperature_unit="celsius",
            series=[
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-04T00:00:00+00:00"),
                    energy=[EnergyDataPoint(value=3.0, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=0.03,
                            value=0.02,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=1.2,
                        value=0.95,
                        vat_amount=0.25,
                        vat_percentage=25.5,
                    ),
                    temperature_reading=None,
                ),
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-04T01:00:00+00:00"),
                    energy=[EnergyDataPoint(value=0.0, type="ENERGY")],
                    cost=None,
                    price=None,
                    temperature_reading=None,
                ),
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-04T02:00:00+00:00"),
                    energy=[EnergyDataPoint(value=2.5, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=0.02,
                            value=0.01,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=1.0,
                        value=0.8,
                        vat_amount=0.2,
                        vat_percentage=25.5,
                    ),
                    temperature_reading=None,
                ),
            ],
        )

        with (
            patch(
                "custom_components.fortum.api.client.dt_util.utcnow",
                return_value=datetime.fromisoformat("2026-03-18T00:00:00+00:00"),
            ),
            patch.object(
                client,
                "get_metering_points",
                return_value=[MeteringPoint(metering_point_no="6094111")],
            ),
            patch.object(
                client,
                "_find_last_recorded_cost_stat_hour",
                return_value=datetime.fromisoformat("2026-03-03T23:00:00+00:00"),
            ),
            patch.object(client, "get_time_series_data", return_value=[time_series]),
            patch.object(client, "_get_hourly_stat_sum_before_hour", return_value=0.0),
            patch(
                "custom_components.fortum.api.client.async_add_external_statistics"
            ) as mock_add_stats,
            patch("custom_components.fortum.api.client._LOGGER.warning") as mock_warn,
        ):
            imported = await client.sync_hourly_data_all_meters()

        assert imported == 3
        assert mock_add_stats.call_count == 3
        for call in mock_add_stats.call_args_list:
            assert len(call.args[2]) == 1
        mock_warn.assert_called_once()

    async def test_clear_hourly_statistics_clears_all_statistic_ids(
        self, mock_hass, mock_auth_client
    ):
        """Clear helper should clear all generated hourly statistic ids."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        client._hass.loop = Mock()
        client._hass.loop.call_soon_threadsafe = lambda fn: fn()
        recorder_instance = Mock()

        def _clear(statistic_ids, *, on_done=None):
            if on_done:
                on_done()

        recorder_instance.async_clear_statistics.side_effect = _clear

        with (
            patch.object(
                client,
                "get_metering_points",
                return_value=[MeteringPoint(metering_point_no="6094111")],
            ),
            patch(
                "custom_components.fortum.api.client.get_instance",
                return_value=recorder_instance,
            ),
        ):
            cleared = await client.clear_hourly_statistics()

        assert cleared == 5
        recorder_instance.async_clear_statistics.assert_called_once()

        statistic_ids = recorder_instance.async_clear_statistics.call_args.args[0]
        assert "fortum:price_forecast" in statistic_ids

    async def test_hourly_statistics_sum_not_double_counted_across_repeated_runs(
        self, mock_hass, mock_auth_client
    ):
        """Hourly sum should remain cumulative and stable across repeated runs."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        from_date = datetime.fromisoformat("2026-03-10T00:00:00+00:00")
        to_date = datetime.fromisoformat("2026-03-10T03:00:00+00:00")

        hourly_consumption_sid = client._build_consumption_statistic_id("6094111")
        hourly_cost_sid = client._build_cost_statistic_id("6094111")

        time_series = TimeSeries(
            delivery_site_category="CONSUMPTION",
            measurement_unit="kWh",
            metering_point_no="6094111",
            price_unit="c/kWh",
            cost_unit="EUR",
            temperature_unit="celsius",
            series=[
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-10T00:00:00+00:00"),
                    energy=[EnergyDataPoint(value=1.0, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=0.5,
                            value=0.5,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=1.0,
                        value=0.8,
                        vat_amount=0.2,
                        vat_percentage=25,
                    ),
                    temperature_reading=None,
                ),
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-10T01:00:00+00:00"),
                    energy=[EnergyDataPoint(value=2.0, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=1.5,
                            value=1.5,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=1.1,
                        value=0.9,
                        vat_amount=0.2,
                        vat_percentage=25,
                    ),
                    temperature_reading=None,
                ),
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-10T02:00:00+00:00"),
                    energy=[EnergyDataPoint(value=3.0, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=2.5,
                            value=2.5,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=1.2,
                        value=1.0,
                        vat_amount=0.2,
                        vat_percentage=25,
                    ),
                    temperature_reading=None,
                ),
            ],
        )
        import_store: dict[str, list[dict[str, float | datetime]]] = {
            hourly_consumption_sid: [],
            hourly_cost_sid: [],
        }
        metadata_by_sid: dict[str, dict[str, Any]] = {}

        def _seed_sum(statistic_id: str, hour: datetime) -> float:
            previous_hour = hour - timedelta(hours=1)
            rows = import_store.get(statistic_id, [])
            for row in rows:
                if row["start"] == previous_hour:
                    return float(cast("float", row["sum"]))
            return 0.0

        def _fake_add_external_statistics(_hass, metadata, rows):
            sid = metadata["statistic_id"]
            metadata_by_sid[sid] = metadata
            if sid not in import_store:
                return
            existing_by_hour = {row["start"]: row for row in import_store[sid]}
            for row in rows:
                existing_by_hour[row["start"]] = {
                    "start": row["start"],
                    "state": float(row["state"]),
                    "sum": float(cast("float", row.get("sum", 0.0))),
                }
            import_store[sid] = sorted(
                existing_by_hour.values(), key=lambda item: item["start"]
            )

        with (
            patch(
                "custom_components.fortum.api.client.async_add_external_statistics",
                side_effect=_fake_add_external_statistics,
            ),
            patch.object(client, "get_time_series_data", return_value=[time_series]),
            patch.object(
                client,
                "_get_hourly_stat_sum_before_hour",
                side_effect=_seed_sum,
            ),
        ):
            await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
                continue_after_missing=False,
            )
            await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
                continue_after_missing=False,
            )
            await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
                continue_after_missing=False,
            )

        last_hourly_consumption = import_store[hourly_consumption_sid][-1]
        last_hourly_cost = import_store[hourly_cost_sid][-1]
        assert last_hourly_consumption["sum"] == 6.0
        assert last_hourly_cost["sum"] == 4.5
        assert metadata_by_sid[hourly_consumption_sid]["has_sum"] is True
        assert metadata_by_sid[hourly_consumption_sid]["unit_class"] == "energy"
        assert metadata_by_sid[hourly_cost_sid]["has_sum"] is True

    async def test_record_hourly_data_stats_uses_day_aligned_request_window(
        self,
        mock_hass,
        mock_auth_client,
    ) -> None:
        """Hourly stats fetch should use profile-local day-aligned request window."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        from_date = datetime.fromisoformat("2026-03-10T06:00:00+00:00")
        to_date = datetime.fromisoformat("2026-03-10T12:00:00+00:00")

        with (
            patch.object(
                client, "get_time_series_data", return_value=[]
            ) as mock_get_series,
            patch.object(client, "_get_hourly_stat_sum_before_hour", return_value=0.0),
        ):
            await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
                continue_after_missing=False,
            )

        kwargs = mock_get_series.call_args.kwargs
        profile_tz = ZoneInfo(client._endpoints.profile.timezone)
        expected_from = (
            from_date.astimezone(profile_tz)
            .replace(hour=0, minute=0, second=0, microsecond=0)
            .astimezone(ZoneInfo("UTC"))
        )
        expected_to = to_date.astimezone(profile_tz).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).astimezone(ZoneInfo("UTC")) + timedelta(days=1)

        assert kwargs["from_date"] == expected_from
        assert kwargs["to_date"] == expected_to

    async def test_record_hourly_data_stats_skips_unchanged_digest(
        self,
        mock_hass,
        mock_auth_client,
    ) -> None:
        """Repeated identical hourly payload should be skipped by digest."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        from_date = datetime.fromisoformat("2026-03-10T00:00:00+00:00")
        to_date = datetime.fromisoformat("2026-03-10T03:00:00+00:00")

        time_series = TimeSeries(
            delivery_site_category="CONSUMPTION",
            measurement_unit="kWh",
            metering_point_no="6094111",
            price_unit="c/kWh",
            cost_unit="EUR",
            temperature_unit="celsius",
            series=[
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-10T00:00:00+00:00"),
                    energy=[EnergyDataPoint(value=1.0, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=0.5,
                            value=0.5,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=1.0,
                        value=0.8,
                        vat_amount=0.2,
                        vat_percentage=25,
                    ),
                    temperature_reading=None,
                )
            ],
        )

        with (
            patch.object(client, "get_time_series_data", return_value=[time_series]),
            patch.object(client, "_get_hourly_stat_sum_before_hour", return_value=0.0),
            patch(
                "custom_components.fortum.api.client.async_add_external_statistics"
            ) as mock_add_stats,
        ):
            await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
                continue_after_missing=False,
            )
            await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
                continue_after_missing=False,
            )

        assert mock_add_stats.call_count == 3
