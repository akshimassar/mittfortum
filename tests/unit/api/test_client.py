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

        client = FortumAPIClient(mock_hass, mock_auth_client)

        with (
            patch.object(
                client,
                "get_session_payload",
                return_value={"user": {"customerId": "session_customer_123"}},
            ),
        ):
            result = await client.get_customer_id()

        assert result == "session_customer_123"

    async def test_get_customer_id_from_session(self, mock_hass, mock_auth_client):
        """Test customer ID extraction from session payload."""
        mock_auth_client.id_token = "session_based"

        client = FortumAPIClient(mock_hass, mock_auth_client)
        with patch.object(
            client,
            "get_session_payload",
            return_value={"user": {"customerId": "session_customer_123"}},
        ):
            result = await client.get_customer_id()

        assert result == "session_customer_123"

    async def test_get_customer_id_session_based_no_data(
        self, mock_hass, mock_auth_client
    ):
        """Test customer ID extraction with session-based token but no session data."""
        mock_auth_client.id_token = "session_based"

        client = FortumAPIClient(mock_hass, mock_auth_client)

        with (
            patch.object(client, "get_session_payload", return_value={}),
            pytest.raises(
                APIError,
                match="Customer ID not found in session payload",
            ),
        ):
            await client.get_customer_id()

    async def test_get_customer_details_success(
        self, mock_hass, mock_auth_client, sample_customer_details
    ):
        """Test successful customer details fetch."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        with patch.object(
            client,
            "get_session_payload",
            return_value={
                "user": {
                    "customerId": sample_customer_details.customer_id,
                    "postalAddress": sample_customer_details.postal_address,
                    "postOffice": sample_customer_details.post_office,
                    "name": sample_customer_details.name,
                }
            },
        ):
            result = await client.get_customer_details()

        assert isinstance(result, CustomerDetails)
        assert result.customer_id == sample_customer_details.customer_id
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

    async def test_get_time_series_data_raises_after_retry_exhaustion(
        self, mock_hass, mock_auth_client
    ):
        """Raise when fetch fails after retry exhaustion."""
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

    async def test_fetch_spot_prices_for_areas_uses_spot_prices_endpoint(
        self, mock_hass, mock_auth_client
    ):
        """Test spot prices endpoint parsing for price data."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

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
            result = await client.fetch_spot_prices_for_areas(("FI",))

        assert len(result) == 2
        assert result[0].price == 4.23
        assert result[1].price == 4.50
        assert result[0].price_unit == "c/kWh"
        assert result[0].area_code == "FI"
        called_url = mock_get.call_args.args[0]
        assert "shared.spotPrices.listPriceAreaSpotPrices" in called_url
        assert "PER_15_MIN" in called_url
        mock_add_stats.assert_called_once()
        assert (
            mock_add_stats.call_args.args[1]["statistic_id"]
            == "fortum:price_forecast_fi"
        )
        assert len(mock_add_stats.call_args.args[2]) == 1
        assert mock_add_stats.call_args.args[2][0]["start"].minute == 0
        assert mock_add_stats.call_args.args[2][0]["state"] == pytest.approx(4.365)
        assert mock_add_stats.call_args.args[2][0]["mean"] == pytest.approx(4.365)
        assert mock_add_stats.call_args.args[2][0]["min"] == 4.23
        assert mock_add_stats.call_args.args[2][0]["max"] == 4.50

    async def test_fetch_spot_prices_for_areas_without_price_area_returns_empty(
        self, mock_hass, mock_auth_client
    ):
        """Spot price data should be skipped when no topology areas exist."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        with (
            patch.object(client, "_get") as mock_get,
            patch(
                "custom_components.fortum.api.client.async_add_external_statistics"
            ) as mock_add_stats,
        ):
            result = await client.fetch_spot_prices_for_areas(())

        assert result == []
        mock_get.assert_not_called()
        mock_add_stats.assert_not_called()

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
            client._record_price_forecast_statistics("FI", price_data)
            client._record_price_forecast_statistics("FI", price_data)

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
            client._record_price_forecast_statistics("FI", updated_price_data)
            assert mock_add_stats.call_count == 2

    async def test_trpc_endpoints_exclude_auth_headers(
        self, mock_hass, mock_auth_client
    ):
        """Test that tRPC endpoints do NOT receive Authorization headers."""
        from unittest.mock import AsyncMock, MagicMock

        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}

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

    async def test_get_retries_mixed_errors_then_succeeds(
        self, mock_hass, mock_auth_client
    ):
        """GET should recover after transient auth and API errors."""
        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}

        client = FortumAPIClient(mock_hass, mock_auth_client)

        with (
            patch.object(
                client,
                "_handle_response",
                side_effect=[
                    AuthenticationError("Unauthorized (401)"),
                    APIError("temporary backend issue"),
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

    async def test_get_raises_after_final_failure(
        self,
        mock_hass,
        mock_auth_client,
    ):
        """Final GET attempt should raise APIError."""
        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}

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
        ):
            mock_client = AsyncMock()
            mock_client.get.return_value = Mock(status_code=500, text="error")
            mock_client.cookies = {}
            mock_get_client.return_value.__aenter__.return_value = mock_client
            mock_get_client.return_value.__aexit__.return_value = None

            with pytest.raises(APIError):
                await client._get("https://www.fortum.com/se/el/api/test")

    async def test_get_wraps_unexpected_exception_after_retry_exhaustion(
        self,
        mock_hass,
        mock_auth_client,
    ):
        """GET should raise APIError for non-domain failures after retries."""
        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}

        client = FortumAPIClient(mock_hass, mock_auth_client)

        with (
            patch.object(
                client,
                "_handle_response",
                side_effect=RuntimeError("network stack exploded"),
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
            mock_client.get.return_value = Mock(status_code=500, text="error")
            mock_client.cookies = {}
            mock_get_client.return_value.__aenter__.return_value = mock_client
            mock_get_client.return_value.__aexit__.return_value = None

            with pytest.raises(APIError, match="GET request failed"):
                await client._get("https://www.fortum.com/se/el/api/test")

            assert mock_client.get.call_count == 3
            assert mock_sleep.await_count == 2

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

    async def test_sync_hourly_data_for_metering_points_imports_energy_cost_and_price(
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
                    energy=[],
                    cost=None,
                    price=None,
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
                "get_time_series_data",
                return_value=[time_series],
            ) as mock_get_series,
            patch(
                "custom_components.fortum.api.client.async_add_external_statistics"
            ) as mock_add_stats,
        ):
            imported = await client.sync_hourly_data_for_metering_points(
                (MeteringPoint(metering_point_no="6094111"),)
            )

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

    async def test_sync_hourly_data_for_metering_points_syncs_full_recent_window(
        self, mock_hass, mock_auth_client
    ):
        """Recent sync should always request full two-week window."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        fixed_now = datetime.fromisoformat("2026-03-18T00:00:00+00:00")
        two_weeks_ago = fixed_now - timedelta(days=14)

        with (
            patch.object(
                client,
                "_find_last_recorded_price_stat_hour",
                return_value=fixed_now - timedelta(hours=1),
            ),
            patch.object(
                client,
                "_record_hourly_data_stats",
                return_value=0,
            ) as mock_record,
            patch(
                "custom_components.fortum.api.client.dt_util.utcnow",
                return_value=fixed_now,
            ),
        ):
            imported = await client.sync_hourly_data_for_metering_points(
                (MeteringPoint(metering_point_no="6094111"),)
            )

        assert imported == 0
        assert mock_record.call_count == 1
        assert mock_record.call_args.args[1] == two_weeks_ago
        assert mock_record.call_args.args[2] == fixed_now

    async def test_find_last_recorded_price_stat_hour_parses_string_timestamp(
        self, mock_hass, mock_auth_client
    ):
        """Parse recorder start as ISO string when datetime is not returned."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        recorder_instance = Mock()
        recorder_instance.async_add_executor_job = AsyncMock(
            return_value={
                "fortum:hourly_price_6094111": [
                    {"start": "2026-03-17T22:00:00+00:00", "mean": 1.2}
                ]
            }
        )

        with patch(
            "custom_components.fortum.api.client.get_instance",
            return_value=recorder_instance,
        ):
            last_recorded = await client._find_last_recorded_price_stat_hour(
                "6094111",
                datetime.fromisoformat("2026-03-17T22:00:00+00:00"),
                datetime.fromisoformat("2026-03-18T00:00:00+00:00"),
            )

        assert last_recorded == datetime.fromisoformat("2026-03-17T22:00:00+00:00")

    async def test_find_last_recorded_price_stat_hour_parses_unix_timestamp(
        self, mock_hass, mock_auth_client
    ):
        """Parse recorder start when statistics_during_period returns unix time."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        expected_start = datetime.fromisoformat("2026-03-17T22:00:00+00:00")
        recorder_instance = Mock()
        recorder_instance.async_add_executor_job = AsyncMock(
            return_value={
                "fortum:hourly_price_6094111": [
                    {"start": expected_start.timestamp(), "mean": 1.2}
                ]
            }
        )

        with patch(
            "custom_components.fortum.api.client.get_instance",
            return_value=recorder_instance,
        ):
            last_recorded = await client._find_last_recorded_price_stat_hour(
                "6094111",
                datetime.fromisoformat("2026-03-17T22:00:00+00:00"),
                datetime.fromisoformat("2026-03-18T00:00:00+00:00"),
            )

        assert last_recorded == expected_start

    async def test_find_last_recorded_price_stat_hour_returns_latest_with_gaps(
        self, mock_hass, mock_auth_client
    ):
        """Latest recorded hour should be returned even when gaps exist."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        recorder_instance = Mock()
        recorder_instance.async_add_executor_job = AsyncMock(
            return_value={
                "fortum:hourly_price_6094111": [
                    {"start": "2026-03-01T00:00:00+00:00", "mean": 1.2},
                    {"start": "2026-03-03T12:00:00+00:00", "mean": 2.4},
                    {"start": "2026-03-10T23:00:00+00:00", "mean": 3.1},
                ]
            }
        )

        with patch(
            "custom_components.fortum.api.client.get_instance",
            return_value=recorder_instance,
        ):
            last_recorded = await client._find_last_recorded_price_stat_hour(
                "6094111",
                datetime.fromisoformat("2026-03-01T00:00:00+00:00"),
                datetime.fromisoformat("2026-03-18T00:00:00+00:00"),
            )

        assert last_recorded == datetime.fromisoformat("2026-03-10T23:00:00+00:00")

    async def test_find_first_recorded_price_gap_hour_starts_from_first_recorded_hour(
        self, mock_hass, mock_auth_client
    ):
        """Gap search should ignore missing hours before recorder coverage."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        now = datetime.fromisoformat("2026-03-18T00:00:00+00:00")
        with patch.object(
            client,
            "_get_recorded_price_stat_hours",
            return_value={
                datetime.fromisoformat("2026-01-10T00:00:00+00:00"),
                datetime.fromisoformat("2026-01-12T00:00:00+00:00"),
            },
        ):
            gap_hour = await client._find_first_recorded_price_gap_hour(
                "6094111",
                now=now,
            )

        assert gap_hour == datetime.fromisoformat("2026-01-10T01:00:00+00:00")

    async def test_find_first_recorded_price_gap_hour_returns_none_for_recent_gap(
        self, mock_hass, mock_auth_client
    ):
        """Gap search should skip gaps in the recent two-week window."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        now = datetime.fromisoformat("2026-03-18T00:00:00+00:00")
        with patch.object(
            client,
            "_get_recorded_price_stat_hours",
            return_value={
                datetime.fromisoformat("2026-03-15T00:00:00+00:00"),
                datetime.fromisoformat("2026-03-17T00:00:00+00:00"),
            },
        ):
            gap_hour = await client._find_first_recorded_price_gap_hour(
                "6094111",
                now=now,
            )

        assert gap_hour is None

    async def test_backfill_historical_price_gaps_updates_search_from(
        self, mock_hass, mock_auth_client
    ):
        """Historical gap backfill should advance finder from last filled day."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        now = datetime.fromisoformat("2026-03-18T00:00:00+00:00")
        gap_start_1 = datetime.fromisoformat("2026-01-10T10:00:00+00:00")
        gap_start_2 = datetime.fromisoformat("2026-01-25T14:00:00+00:00")
        gap_start_3 = datetime.fromisoformat("2026-02-10T08:00:00+00:00")

        with (
            patch(
                "custom_components.fortum.api.client.dt_util.utcnow",
                return_value=now,
            ),
            patch.object(
                client,
                "_find_first_recorded_price_gap_hour",
                side_effect=[gap_start_1, gap_start_2, gap_start_3, None],
            ) as mock_find_gap,
            patch.object(
                client,
                "_record_hourly_data_stats",
                side_effect=[5, 3, 4],
            ) as mock_record,
            patch.object(
                client,
                "_recalculate_hourly_sums_until_end",
                return_value=20,
            ) as mock_recalculate,
        ):
            imported = await client.backfill_historical_price_gaps_for_metering_points(
                (MeteringPoint(metering_point_no="6094111"),)
            )

        assert imported == 12
        assert mock_record.call_count == 3
        assert mock_record.call_args_list[0].args[1] == datetime.fromisoformat(
            "2026-01-09T10:00:00+00:00"
        )
        assert mock_record.call_args_list[0].args[2] == datetime.fromisoformat(
            "2026-01-23T10:00:00+00:00"
        )
        assert mock_record.call_args_list[1].args[1] == datetime.fromisoformat(
            "2026-01-24T14:00:00+00:00"
        )
        assert mock_record.call_args_list[1].args[2] == datetime.fromisoformat(
            "2026-02-07T14:00:00+00:00"
        )
        assert mock_record.call_args_list[2].args[1] == datetime.fromisoformat(
            "2026-02-09T08:00:00+00:00"
        )
        assert mock_record.call_args_list[2].args[2] == datetime.fromisoformat(
            "2026-02-23T08:00:00+00:00"
        )
        assert mock_recalculate.call_count == 3
        assert mock_find_gap.call_count == 4
        assert mock_find_gap.call_args_list[1].kwargs["from_date"] == (
            datetime.fromisoformat("2026-01-22T00:00:00+00:00")
        )
        assert mock_find_gap.call_args_list[2].kwargs["from_date"] == (
            datetime.fromisoformat("2026-02-06T00:00:00+00:00")
        )
        assert mock_find_gap.call_args_list[3].kwargs["from_date"] == (
            datetime.fromisoformat("2026-02-22T00:00:00+00:00")
        )

    async def test_record_hourly_data_stats_populates_runtime_metadata_cache(
        self,
        mock_hass,
        mock_auth_client,
    ) -> None:
        """Regular hourly sync should refresh runtime metadata cache."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        from_date = datetime.fromisoformat("2026-03-10T00:00:00+00:00")
        to_date = datetime.fromisoformat("2026-03-10T01:00:00+00:00")

        time_series = TimeSeries(
            delivery_site_category="CONSUMPTION",
            measurement_unit="MWh",
            metering_point_no="6094111",
            price_unit="SEK/kWh",
            cost_unit="SEK",
            temperature_unit="celsius",
            series=[
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-10T00:00:00+00:00"),
                    energy=[EnergyDataPoint(value=1.0, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=1.0,
                            value=1.0,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=2.0,
                        value=1.6,
                        vat_amount=0.4,
                        vat_percentage=25,
                    ),
                    temperature_reading=TemperatureReading(temperature=5.0),
                )
            ],
        )

        with (
            patch.object(client, "get_time_series_data", return_value=[time_series]),
            patch.object(client, "_get_hourly_stat_sum_before_hour", return_value=0.0),
            patch.object(
                client,
                "_get_hourly_stats_values_in_window",
                return_value={},
            ),
            patch("custom_components.fortum.api.client.async_add_external_statistics"),
        ):
            await client._record_hourly_data_stats("6094111", from_date, to_date)

        consumption_sid = client._build_consumption_statistic_id("6094111")
        cost_sid = client._build_cost_statistic_id("6094111")
        assert (
            client._hourly_metadata_cache[consumption_sid]["unit_of_measurement"]
            == "MWh"
        )
        assert client._hourly_metadata_cache[cost_sid]["unit_of_measurement"] == "SEK"

    async def test_recalculate_hourly_sums_until_end_raises_without_metadata_cache(
        self,
        mock_hass,
        mock_auth_client,
    ) -> None:
        """Sum recalculation should fail without runtime metadata cache."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        with pytest.raises(APIError, match="Missing runtime statistic metadata cache"):
            await client._recalculate_hourly_sums_until_end(
                "6094111",
                datetime.fromisoformat("2026-03-10T00:00:00+00:00"),
                datetime.fromisoformat("2026-03-11T00:00:00+00:00"),
            )

    async def test_recalculate_hourly_sums_until_end_uses_cached_metadata(
        self,
        mock_hass,
        mock_auth_client,
    ) -> None:
        """Sum recalculation should reuse cached metadata when writing rows."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        from_hour = datetime.fromisoformat("2026-03-10T00:00:00+00:00")
        to_hour = datetime.fromisoformat("2026-03-11T00:00:00+00:00")

        consumption_sid = client._build_consumption_statistic_id("6094111")
        cost_sid = client._build_cost_statistic_id("6094111")
        client._cache_hourly_metadata(
            client._build_hourly_statistic_metadata(
                statistic_id=consumption_sid,
                name="Consumption from cache",
                unit_of_measurement="cached-consumption",
                unit_class="energy",
                has_sum=True,
            ),
            client._build_hourly_statistic_metadata(
                statistic_id=cost_sid,
                name="Cost from cache",
                unit_of_measurement="cached-cost",
                unit_class=None,
                has_sum=True,
            ),
        )

        recorder_values = {
            consumption_sid: {
                datetime.fromisoformat("2026-03-10T00:00:00+00:00"): 1.0,
            },
            cost_sid: {
                datetime.fromisoformat("2026-03-10T00:00:00+00:00"): 0.5,
            },
        }
        metadata_used: list[dict[str, Any]] = []

        def _capture_metadata(_hass, metadata, _rows):
            metadata_used.append(dict(metadata))

        with (
            patch.object(
                client,
                "_get_hourly_stats_values_in_window",
                return_value=recorder_values,
            ),
            patch.object(client, "_get_hourly_stat_sum_before_hour", return_value=0.0),
            patch(
                "custom_components.fortum.api.client.async_add_external_statistics",
                side_effect=_capture_metadata,
            ),
        ):
            rewritten = await client._recalculate_hourly_sums_until_end(
                "6094111",
                from_hour,
                to_hour,
            )

        assert rewritten == 2
        used_units = {entry["unit_of_measurement"] for entry in metadata_used}
        assert used_units == {"cached-consumption", "cached-cost"}

    async def test_determine_hourly_data_sync_start_uses_userinfo_marker(
        self, mock_hass, mock_auth_client
    ):
        """Use earliest marker from user info when no recent stats exist."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        cached_earliest = datetime.fromisoformat("2025-01-06T00:00:00+00:00")
        client._earliest_available_by_metering_point["6094111"] = cached_earliest
        two_weeks_ago = datetime.fromisoformat("2026-03-04T00:00:00+00:00")
        now = datetime.fromisoformat("2026-03-18T00:00:00+00:00")

        with (
            patch.object(
                client,
                "_find_last_recorded_price_stat_hour",
                return_value=None,
            ),
        ):
            start, historical = await client._determine_hourly_data_sync_start(
                "6094111",
                two_weeks_ago,
                now,
            )

        assert start == cached_earliest
        assert historical is True

    async def test_determine_hourly_data_sync_start_falls_back_to_long_history(
        self, mock_hass, mock_auth_client
    ):
        """Use latest recorder hour from long history when recent window is empty."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        two_weeks_ago = datetime.fromisoformat("2026-03-04T00:00:00+00:00")
        now = datetime.fromisoformat("2026-03-18T00:00:00+00:00")
        fallback_last_hour = datetime.fromisoformat("2025-12-31T23:00:00+00:00")

        with (
            patch.object(
                client,
                "_find_last_recorded_price_stat_hour",
                side_effect=[None, fallback_last_hour],
            ) as mock_find_last,
        ):
            start, historical = await client._determine_hourly_data_sync_start(
                "6094111",
                two_weeks_ago,
                now,
            )

        assert start == datetime.fromisoformat("2026-01-01T00:00:00+00:00")
        assert historical is True
        assert mock_find_last.call_count == 2
        assert mock_find_last.call_args_list[1].args[1] == datetime.fromisoformat(
            "2021-03-19T00:00:00+00:00"
        )

    async def test_recent_sync_continues_after_missing_price_gap(
        self, mock_hass, mock_auth_client
    ):
        """Recent-window sync continues when price reappears after a gap."""
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
                    energy=[],
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
                "_find_last_recorded_price_stat_hour",
                return_value=datetime.fromisoformat("2026-03-03T23:00:00+00:00"),
            ),
            patch.object(client, "get_time_series_data", return_value=[time_series]),
            patch.object(client, "_get_hourly_stat_sum_before_hour", return_value=0.0),
            patch(
                "custom_components.fortum.api.client.async_add_external_statistics"
            ) as mock_add_stats,
        ):
            imported = await client.sync_hourly_data_for_metering_points(
                (MeteringPoint(metering_point_no="6094111"),)
            )

        assert imported == 6
        assert mock_add_stats.call_count == 3
        for call in mock_add_stats.call_args_list:
            assert len(call.args[2]) == 2

    def test_summarize_price_gaps_formats_one_line_runs(
        self, mock_hass, mock_auth_client
    ):
        """Gap summary should use UTC start hour and run-length format."""
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
                    at_utc=datetime.fromisoformat("2026-03-30T20:00:00+00:00"),
                    energy=[EnergyDataPoint(value=1.0, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=0.50,
                            value=0.50,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=1.2,
                        value=1.0,
                        vat_amount=0.2,
                        vat_percentage=25.0,
                    ),
                    temperature_reading=None,
                ),
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-30T21:00:00+00:00"),
                    energy=[],
                    cost=None,
                    price=None,
                    temperature_reading=TemperatureReading(temperature=1.0),
                ),
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-30T22:00:00+00:00"),
                    energy=[],
                    cost=None,
                    price=None,
                    temperature_reading=None,
                ),
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-30T23:00:00+00:00"),
                    energy=[EnergyDataPoint(value=1.0, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=0.60,
                            value=0.60,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=1.4,
                        value=1.1,
                        vat_amount=0.3,
                        vat_percentage=25.0,
                    ),
                    temperature_reading=None,
                ),
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-31T00:00:00+00:00"),
                    energy=[EnergyDataPoint(value=1.0, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=0.70,
                            value=0.70,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=1.5,
                        value=1.2,
                        vat_amount=0.3,
                        vat_percentage=25.0,
                    ),
                    temperature_reading=None,
                ),
            ],
        )

        summary = client._summarize_price_gaps(
            sorted(time_series.series, key=lambda point: point.at_utc)
        )

        assert summary == "2026-03-30 21:00 2h missing, 2026-03-30 23:00 2h present"

    def test_summarize_price_gaps_includes_trailing_missing_run(
        self, mock_hass, mock_auth_client
    ):
        """Gap summary should include trailing missing interval at end."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        points = [
            TimeSeriesDataPoint(
                at_utc=datetime.fromisoformat("2026-03-30T20:00:00+00:00"),
                energy=[EnergyDataPoint(value=1.0, type="ENERGY")],
                cost=[
                    CostDataPoint(
                        total=0.50,
                        value=0.50,
                        type="COST_SALES_ELECTRICITY",
                    )
                ],
                price=Price(total=1.2, value=1.0, vat_amount=0.2, vat_percentage=25.0),
                temperature_reading=None,
            ),
            TimeSeriesDataPoint(
                at_utc=datetime.fromisoformat("2026-03-30T21:00:00+00:00"),
                energy=[],
                cost=None,
                price=None,
                temperature_reading=None,
            ),
            TimeSeriesDataPoint(
                at_utc=datetime.fromisoformat("2026-03-30T22:00:00+00:00"),
                energy=[],
                cost=None,
                price=None,
                temperature_reading=TemperatureReading(temperature=2.2),
            ),
            TimeSeriesDataPoint(
                at_utc=datetime.fromisoformat("2026-03-30T23:00:00+00:00"),
                energy=[EnergyDataPoint(value=1.0, type="ENERGY")],
                cost=[
                    CostDataPoint(
                        total=0.60,
                        value=0.60,
                        type="COST_SALES_ELECTRICITY",
                    )
                ],
                price=Price(total=1.4, value=1.1, vat_amount=0.3, vat_percentage=25.0),
                temperature_reading=None,
            ),
            TimeSeriesDataPoint(
                at_utc=datetime.fromisoformat("2026-03-31T00:00:00+00:00"),
                energy=[],
                cost=None,
                price=None,
                temperature_reading=None,
            ),
            TimeSeriesDataPoint(
                at_utc=datetime.fromisoformat("2026-03-31T01:00:00+00:00"),
                energy=[],
                cost=None,
                price=None,
                temperature_reading=None,
            ),
        ]

        summary = client._summarize_price_gaps(points)

        assert summary == (
            "2026-03-30 21:00 2h missing, 2026-03-30 23:00 1h present, "
            "2026-03-31 00:00 2h missing"
        )

    def test_summarize_price_gaps_ignores_trailing_missing_only(
        self, mock_hass, mock_auth_client
    ):
        """Trailing missing-only run should not be considered actionable gap."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        points = [
            TimeSeriesDataPoint(
                at_utc=datetime.fromisoformat("2026-03-30T20:00:00+00:00"),
                energy=[EnergyDataPoint(value=1.0, type="ENERGY")],
                cost=[
                    CostDataPoint(
                        total=0.50,
                        value=0.50,
                        type="COST_SALES_ELECTRICITY",
                    )
                ],
                price=Price(total=1.2, value=1.0, vat_amount=0.2, vat_percentage=25.0),
                temperature_reading=None,
            ),
            TimeSeriesDataPoint(
                at_utc=datetime.fromisoformat("2026-03-30T21:00:00+00:00"),
                energy=[EnergyDataPoint(value=1.0, type="ENERGY")],
                cost=[
                    CostDataPoint(
                        total=0.55,
                        value=0.55,
                        type="COST_SALES_ELECTRICITY",
                    )
                ],
                price=Price(total=1.3, value=1.1, vat_amount=0.2, vat_percentage=25.0),
                temperature_reading=None,
            ),
            TimeSeriesDataPoint(
                at_utc=datetime.fromisoformat("2026-03-30T22:00:00+00:00"),
                energy=[],
                cost=None,
                price=None,
                temperature_reading=None,
            ),
            TimeSeriesDataPoint(
                at_utc=datetime.fromisoformat("2026-03-30T23:00:00+00:00"),
                energy=[],
                cost=None,
                price=None,
                temperature_reading=None,
            ),
        ]

        summary = client._summarize_price_gaps(points)

        assert summary is None

    async def test_clear_statistics_for_discovered_points_clears_all_statistic_ids(
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
            patch(
                "custom_components.fortum.api.client.get_instance",
                return_value=recorder_instance,
            ),
        ):
            cleared = await client.clear_statistics_for_discovered_points(
                (MeteringPoint(metering_point_no="6094111"),),
                (),
            )

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
            )
            await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
            )
            await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
            )

        last_hourly_consumption = import_store[hourly_consumption_sid][-1]
        last_hourly_cost = import_store[hourly_cost_sid][-1]
        assert last_hourly_consumption["sum"] == 6.0
        assert last_hourly_cost["sum"] == 4.5
        assert metadata_by_sid[hourly_consumption_sid]["has_sum"] is True
        assert metadata_by_sid[hourly_consumption_sid]["unit_class"] == "energy"
        assert metadata_by_sid[hourly_cost_sid]["has_sum"] is True

    async def test_gap_backfill_rewrites_tail_from_first_missing_hour(
        self, mock_hass, mock_auth_client
    ):
        """Gap backfill should keep polling missing hour and rewrite tail sums."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        now = datetime.fromisoformat("2026-03-18T00:00:00+00:00")
        start_hour = datetime.fromisoformat("2026-03-04T00:00:00+00:00")
        gap_start = datetime.fromisoformat("2026-03-04T10:00:00+00:00")
        final_hour = datetime.fromisoformat("2026-03-05T15:00:00+00:00")

        hourly_consumption_sid = client._build_consumption_statistic_id("6094111")
        hourly_cost_sid = client._build_cost_statistic_id("6094111")
        import_store: dict[str, list[dict[str, float | datetime]]] = {
            hourly_consumption_sid: [],
            hourly_cost_sid: [],
        }

        # Seed existing recorder rows: present block, then multi-day gap,
        # then sparse tail.
        for offset in range(10):
            hour = start_hour + timedelta(hours=offset)
            import_store[hourly_consumption_sid].append(
                {"start": hour, "state": 1.0, "sum": float(offset + 1)}
            )
            import_store[hourly_cost_sid].append(
                {"start": hour, "state": 0.5, "sum": float(offset + 1) * 0.5}
            )

        for offset in range(34, 40):
            hour = start_hour + timedelta(hours=offset)
            import_store[hourly_consumption_sid].append(
                {"start": hour, "state": 1.0, "sum": float(offset - 23)}
            )
            import_store[hourly_cost_sid].append(
                {"start": hour, "state": 0.5, "sum": float(offset - 23) * 0.5}
            )

        for sid in (hourly_consumption_sid, hourly_cost_sid):
            import_store[sid] = sorted(import_store[sid], key=lambda row: row["start"])

        def _build_series(*, fill_all_after: datetime) -> TimeSeries:
            points: list[TimeSeriesDataPoint] = []
            hour = gap_start
            while hour <= final_hour:
                should_fill = hour >= fill_all_after or hour == gap_start
                points.append(
                    TimeSeriesDataPoint(
                        at_utc=hour,
                        energy=[EnergyDataPoint(value=2.0, type="ENERGY")]
                        if should_fill
                        else [],
                        cost=[
                            CostDataPoint(
                                total=1.0,
                                value=1.0,
                                type="COST_SALES_ELECTRICITY",
                            )
                        ]
                        if should_fill
                        else None,
                        price=Price(
                            total=1.0,
                            value=0.8,
                            vat_amount=0.2,
                            vat_percentage=25,
                        )
                        if should_fill
                        else None,
                        temperature_reading=None,
                    )
                )
                hour += timedelta(hours=1)

            return TimeSeries(
                delivery_site_category="CONSUMPTION",
                measurement_unit="kWh",
                metering_point_no="6094111",
                price_unit="c/kWh",
                cost_unit="EUR",
                temperature_unit="celsius",
                series=points,
            )

        series_run_one = _build_series(
            fill_all_after=datetime.fromisoformat("2026-03-05T09:00:00+00:00")
        )
        series_run_two = _build_series(fill_all_after=gap_start + timedelta(hours=1))

        seed_calls: list[tuple[str, datetime]] = []

        def _seed_sum(statistic_id: str, hour: datetime) -> float:
            seed_calls.append((statistic_id, hour))
            previous_hour = hour - timedelta(hours=1)
            rows = import_store.get(statistic_id, [])
            for row in rows:
                if row["start"] == previous_hour:
                    return float(cast("float", row["sum"]))
            return 0.0

        def _fake_add_external_statistics(_hass, metadata, rows):
            sid = metadata["statistic_id"]
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
                "custom_components.fortum.api.client.dt_util.utcnow",
                return_value=now,
            ),
            patch.object(
                client,
                "get_time_series_data",
                side_effect=[[series_run_one], [series_run_two]],
            ),
            patch.object(
                client,
                "_get_hourly_stat_sum_before_hour",
                side_effect=_seed_sum,
            ),
            patch.object(
                client,
                "_find_last_recorded_price_stat_hour",
                return_value=now - timedelta(hours=1),
            ),
            patch(
                "custom_components.fortum.api.client.async_add_external_statistics",
                side_effect=_fake_add_external_statistics,
            ),
        ):
            await client.sync_hourly_data_for_metering_points(
                (MeteringPoint(metering_point_no="6094111"),)
            )
            await client.sync_hourly_data_for_metering_points(
                (MeteringPoint(metering_point_no="6094111"),)
            )

        assert seed_calls

        consumption_by_hour = {
            cast("datetime", row["start"]): row
            for row in import_store[hourly_consumption_sid]
        }
        cost_by_hour = {
            cast("datetime", row["start"]): row for row in import_store[hourly_cost_sid]
        }

        assert (
            consumption_by_hour[datetime.fromisoformat("2026-03-04T10:00:00+00:00")][
                "sum"
            ]
            == 12.0
        )
        assert (
            consumption_by_hour[datetime.fromisoformat("2026-03-04T11:00:00+00:00")][
                "sum"
            ]
            == 14.0
        )
        assert (
            consumption_by_hour[datetime.fromisoformat("2026-03-05T10:00:00+00:00")][
                "sum"
            ]
            == 60.0
        )
        assert (
            consumption_by_hour[datetime.fromisoformat("2026-03-05T15:00:00+00:00")][
                "sum"
            ]
            == 70.0
        )

        assert (
            cost_by_hour[datetime.fromisoformat("2026-03-04T10:00:00+00:00")]["sum"]
            == 6.0
        )
        assert (
            cost_by_hour[datetime.fromisoformat("2026-03-05T15:00:00+00:00")]["sum"]
            == 35.0
        )

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

    async def test_record_hourly_data_stats_filters_rows_to_sync_window(
        self,
        mock_hass,
        mock_auth_client,
    ) -> None:
        """Hourly stats import should process only rows in [from_date, to_date)."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        from_date = datetime.fromisoformat("2026-03-10T06:00:00+00:00")
        to_date = datetime.fromisoformat("2026-03-10T08:00:00+00:00")

        time_series = TimeSeries(
            delivery_site_category="CONSUMPTION",
            measurement_unit="kWh",
            metering_point_no="6094111",
            price_unit="c/kWh",
            cost_unit="EUR",
            temperature_unit="celsius",
            series=[
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-10T05:00:00+00:00"),
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
                    at_utc=datetime.fromisoformat("2026-03-10T06:00:00+00:00"),
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
                    at_utc=datetime.fromisoformat("2026-03-10T07:00:00+00:00"),
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
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-10T08:00:00+00:00"),
                    energy=[EnergyDataPoint(value=4.0, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=3.5,
                            value=3.5,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=1.3,
                        value=1.1,
                        vat_amount=0.2,
                        vat_percentage=25,
                    ),
                    temperature_reading=None,
                ),
            ],
        )

        added_rows: dict[str, list[dict[str, Any]]] = {}
        base_sum_calls: list[tuple[str, datetime]] = []

        def _fake_add_external_statistics(_hass, metadata, rows):
            sid = metadata["statistic_id"]
            added_rows[sid] = [dict(row) for row in rows]

        async def _base_sum(statistic_id: str, hour: datetime) -> float:
            base_sum_calls.append((statistic_id, hour))
            return 10.0

        with (
            patch.object(client, "get_time_series_data", return_value=[time_series]),
            patch.object(
                client,
                "_get_hourly_stat_sum_before_hour",
                side_effect=_base_sum,
            ),
            patch(
                "custom_components.fortum.api.client.async_add_external_statistics",
                side_effect=_fake_add_external_statistics,
            ),
        ):
            imported = await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
            )

        consumption_sid = client._build_consumption_statistic_id("6094111")
        cost_sid = client._build_cost_statistic_id("6094111")

        assert imported == 6
        assert [row["start"] for row in added_rows[consumption_sid]] == [
            datetime.fromisoformat("2026-03-10T06:00:00+00:00"),
            datetime.fromisoformat("2026-03-10T07:00:00+00:00"),
        ]
        assert [row["start"] for row in added_rows[cost_sid]] == [
            datetime.fromisoformat("2026-03-10T06:00:00+00:00"),
            datetime.fromisoformat("2026-03-10T07:00:00+00:00"),
        ]
        assert all(call[1] == from_date for call in base_sum_calls)

    async def test_record_hourly_data_stats_with_sparse_data(
        self,
        mock_hass,
        mock_auth_client,
    ) -> None:
        """Hourly stats import handles sparse windows without new records."""
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
                    energy=[],
                    cost=None,
                    price=None,
                    temperature_reading=TemperatureReading(temperature=2.5),
                ),
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-10T02:00:00+00:00"),
                    energy=[],
                    cost=None,
                    price=None,
                    temperature_reading=None,
                ),
            ],
        )

        with patch.object(client, "get_time_series_data", return_value=[time_series]):
            imported = await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
            )

        assert imported == 0

    async def test_record_hourly_data_stats_warns_when_old_values_change(
        self,
        mock_hass,
        mock_auth_client,
    ) -> None:
        """Warn once with first hour and count when existing values change."""
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
                ),
                TimeSeriesDataPoint(
                    at_utc=datetime.fromisoformat("2026-03-10T01:00:00+00:00"),
                    energy=[EnergyDataPoint(value=2.0, type="ENERGY")],
                    cost=[
                        CostDataPoint(
                            total=1.0,
                            value=1.0,
                            type="COST_SALES_ELECTRICITY",
                        )
                    ],
                    price=Price(
                        total=1.2,
                        value=1.0,
                        vat_amount=0.2,
                        vat_percentage=25,
                    ),
                    temperature_reading=TemperatureReading(temperature=5.0),
                ),
            ],
        )

        consumption_sid = client._build_consumption_statistic_id("6094111")
        cost_sid = client._build_cost_statistic_id("6094111")
        price_sid = client._build_price_statistic_id("6094111")
        temperature_sid = client._build_temperature_statistic_id("6094111")
        recorder_values = {
            consumption_sid: {
                datetime.fromisoformat("2026-03-10T00:00:00+00:00"): 1.0,
                datetime.fromisoformat("2026-03-10T01:00:00+00:00"): 3.0,
            },
            cost_sid: {
                datetime.fromisoformat("2026-03-10T00:00:00+00:00"): 0.5,
                datetime.fromisoformat("2026-03-10T01:00:00+00:00"): 1.0,
            },
            price_sid: {
                datetime.fromisoformat("2026-03-10T00:00:00+00:00"): 1.0,
                datetime.fromisoformat("2026-03-10T01:00:00+00:00"): 1.2,
            },
            temperature_sid: {
                datetime.fromisoformat("2026-03-10T00:00:00+00:00"): 7.0,
                datetime.fromisoformat("2026-03-10T01:00:00+00:00"): 5.0,
            },
        }

        with (
            patch.object(client, "get_time_series_data", return_value=[time_series]),
            patch.object(client, "_get_hourly_stat_sum_before_hour", return_value=0.0),
            patch.object(
                client,
                "_get_hourly_stats_values_in_window",
                return_value=recorder_values,
            ),
            patch("custom_components.fortum.api.client.async_add_external_statistics"),
            patch(
                "custom_components.fortum.api.client._LOGGER.warning"
            ) as mock_warning,
        ):
            await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
            )

        assert mock_warning.call_count == 1
        assert mock_warning.call_args.args[0] == (
            "stats old values changed for %s: first_hour=%s differing_hours=%d"
        )
        assert mock_warning.call_args.args[2] == "2026-03-10 00:00"
        assert mock_warning.call_args.args[3] == 2

    async def test_record_hourly_data_stats_does_not_warn_for_tiny_float_drift(
        self,
        mock_hass,
        mock_auth_client,
    ) -> None:
        """Ignore old-value differences within tolerance."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        from_date = datetime.fromisoformat("2026-03-10T00:00:00+00:00")
        to_date = datetime.fromisoformat("2026-03-10T01:00:00+00:00")

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
                    temperature_reading=TemperatureReading(temperature=3.0),
                )
            ],
        )

        consumption_sid = client._build_consumption_statistic_id("6094111")
        cost_sid = client._build_cost_statistic_id("6094111")
        price_sid = client._build_price_statistic_id("6094111")
        temperature_sid = client._build_temperature_statistic_id("6094111")
        tiny = 1e-10
        recorder_values = {
            consumption_sid: {
                datetime.fromisoformat("2026-03-10T00:00:00+00:00"): 1.0 + tiny
            },
            cost_sid: {datetime.fromisoformat("2026-03-10T00:00:00+00:00"): 0.5 + tiny},
            price_sid: {
                datetime.fromisoformat("2026-03-10T00:00:00+00:00"): 1.0 + tiny
            },
            temperature_sid: {
                datetime.fromisoformat("2026-03-10T00:00:00+00:00"): 3.0 + tiny
            },
        }

        with (
            patch.object(client, "get_time_series_data", return_value=[time_series]),
            patch.object(client, "_get_hourly_stat_sum_before_hour", return_value=0.0),
            patch.object(
                client,
                "_get_hourly_stats_values_in_window",
                return_value=recorder_values,
            ),
            patch("custom_components.fortum.api.client.async_add_external_statistics"),
            patch(
                "custom_components.fortum.api.client._LOGGER.warning"
            ) as mock_warning,
        ):
            await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
            )

        assert not mock_warning.called

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
            )
            await client._record_hourly_data_stats(
                "6094111",
                from_date,
                to_date,
            )

        assert mock_add_stats.call_count == 3
