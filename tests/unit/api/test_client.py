"""Unit tests for FortumAPIClient."""

from datetime import datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.mittfortum.api.client import FortumAPIClient
from custom_components.mittfortum.const import STATISTICS_REQUEST_TIMEOUT_SECONDS
from custom_components.mittfortum.exceptions import APIError
from custom_components.mittfortum.models import (
    CostDataPoint,
    CustomerDetails,
    EnergyDataPoint,
    MeteringPoint,
    Price,
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

    async def test_get_total_consumption_success(
        self, mock_hass, mock_auth_client, sample_consumption_data
    ):
        """Test successful total consumption fetch."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        with patch.object(
            client, "get_consumption_data", return_value=sample_consumption_data
        ):
            result = await client.get_total_consumption()

            assert result == sample_consumption_data
            assert len(result) == 2

    async def test_get_total_consumption_no_metering_points(
        self, mock_hass, mock_auth_client
    ):
        """Test total consumption fetch with no metering points."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        with patch.object(client, "get_metering_points", return_value=[]):
            with pytest.raises(APIError, match="No metering points found"):
                await client.get_consumption_data()

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
        ):
            result = await client.get_price_data()

        assert len(result) == 2
        assert result[0].price == 4.23
        assert result[1].price == 4.50
        assert result[0].price_unit == "c/kWh"
        called_url = mock_get.call_args.args[0]
        assert "shared.spotPrices.listPriceAreaSpotPrices" in called_url
        assert "PER_15_MIN" in called_url

    async def test_resolve_price_area_fallback(self, mock_hass, mock_auth_client):
        """Test fallback price area by region profile."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        mock_auth_client.session_data = {}

        assert client._resolve_price_area() == "SE3"

    async def test_get_consumption_data_passes_region_timezone(
        self, mock_hass, mock_auth_client
    ):
        """Test conversion uses configured region timezone."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        mock_time_series = Mock()

        with (
            patch.object(
                client,
                "get_time_series_data",
                return_value=[mock_time_series],
            ),
            patch(
                "custom_components.mittfortum.api.client.ConsumptionData.from_time_series",
                return_value=[],
            ) as mock_from_time_series,
        ):
            await client.get_consumption_data(metering_point_nos=["123"])

        mock_from_time_series.assert_called_once_with(
            mock_time_series, timezone="Europe/Stockholm"
        )

    async def test_ensure_valid_token_session_based(self, mock_hass, mock_auth_client):
        """Test _ensure_valid_token with session-based token."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Mock token as expired and session-based
        mock_auth_client.is_token_expired.return_value = True
        mock_auth_client.refresh_token = "session_based"

        with patch.object(mock_auth_client, "authenticate") as mock_auth:
            await client._ensure_valid_token()
            mock_auth.assert_called_once()

    async def test_ensure_valid_token_real_refresh_token(
        self, mock_hass, mock_auth_client
    ):
        """Test _ensure_valid_token with real OAuth2 refresh token."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Mock token as expired with real refresh token
        mock_auth_client.is_token_expired.return_value = True
        mock_auth_client.refresh_token = "real_refresh_token"

        with patch.object(mock_auth_client, "refresh_access_token") as mock_refresh:
            await client._ensure_valid_token()
            mock_refresh.assert_called_once()

    async def test_ensure_valid_token_not_expired(self, mock_hass, mock_auth_client):
        """Test _ensure_valid_token with valid token."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Mock token as not expired and not needing renewal
        mock_auth_client.is_token_expired.return_value = False
        mock_auth_client.needs_renewal.return_value = False

        with patch.object(mock_auth_client, "authenticate") as mock_auth:
            with patch.object(mock_auth_client, "refresh_access_token") as mock_refresh:
                await client._ensure_valid_token()
                mock_auth.assert_not_called()
                mock_refresh.assert_not_called()

    async def test_ensure_valid_token_no_refresh_token(
        self, mock_hass, mock_auth_client
    ):
        """Test _ensure_valid_token with no refresh token."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Mock token as expired with no refresh token
        mock_auth_client.is_token_expired.return_value = True
        mock_auth_client.refresh_token = None

        with patch.object(mock_auth_client, "authenticate") as mock_auth:
            await client._ensure_valid_token()
            mock_auth.assert_called_once()

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
            "custom_components.mittfortum.api.client.get_async_client"
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
            "custom_components.mittfortum.api.client.get_async_client"
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
            "custom_components.mittfortum.api.client.get_async_client"
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
            "custom_components.mittfortum.api.client.get_async_client"
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
            "custom_components.mittfortum.api.client.get_async_client"
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

    async def test_retry_logic_prevents_infinite_loop(
        self, mock_hass, mock_auth_client
    ):
        """Test that retry logic allows exactly 1 retry and prevents infinite loops."""
        mock_auth_client.access_token = "test_access_token_123"
        mock_auth_client.session_cookies = {"sessionid": "test_session"}
        mock_auth_client.is_token_expired.return_value = False
        mock_auth_client.refresh_access_token = AsyncMock()

        client = FortumAPIClient(mock_hass, mock_auth_client)

        call_count = 0

        # Mock the _handle_response method to always raise TOKEN_EXPIRED_RETRY_MSG
        async def mock_handle_response(response):
            nonlocal call_count
            call_count += 1
            # Always simulate token expiry to test retry logic
            raise APIError("Token expired - retry required")

        with patch.object(client, "_handle_response", side_effect=mock_handle_response):
            with patch(
                "homeassistant.helpers.httpx_client.get_async_client"
            ) as mock_get_client:
                mock_client = AsyncMock()
                mock_client.cookies = {}
                mock_get_client.return_value.__aenter__.return_value = mock_client
                mock_get_client.return_value.__aexit__.return_value = None

                # This should fail after exactly 2 attempts (original + 1 retry)
                with pytest.raises(APIError, match="Token expired - retry required"):
                    await client._get("https://www.fortum.com/se/el/api/test")

                # Verify exactly 2 calls were made (no infinite loop)
                assert call_count == 2

    async def test_session_expiration_307_redirect(self, mock_hass, mock_auth_client):
        """Test session expiration detection via 307 redirect to TokenExpired."""
        client = FortumAPIClient(mock_hass, mock_auth_client)

        # Mock a response with 307 redirect to sign-out with TokenExpired
        mock_response = Mock()
        mock_response.status_code = 307
        mock_response.headers = {
            "Location": "/se/el/sign-out?loggedInMessage=TokenExpired"
        }

        # Test that session expiration is properly detected
        with pytest.raises(APIError, match="Token expired - retry required"):
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

        with pytest.raises(APIError, match="Token expired - retry required"):
            client._handle_redirect_response(mock_response)

        # Test other redirect
        mock_response.headers = {"Location": "/other/path"}

        with pytest.raises(APIError, match="Unexpected redirect to: /other/path"):
            client._handle_redirect_response(mock_response)

    async def test_backfill_hourly_statistics_imports_energy_cost_and_price(
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
            patch.object(client, "_get_latest_statistics_start", return_value=None),
            patch.object(
                client,
                "get_time_series_data",
                return_value=[time_series],
            ) as mock_get_series,
            patch(
                "custom_components.mittfortum.api.client.async_add_external_statistics"
            ) as mock_add_stats,
        ):
            imported = await client.backfill_hourly_consumption_statistics_last_month()

        assert imported == 4
        assert mock_get_series.call_args.kwargs["request_timeout"] == (
            STATISTICS_REQUEST_TIMEOUT_SECONDS
        )
        assert mock_add_stats.call_count == 4
        assert mock_get_series.call_count == 1

        statistic_ids = [
            call.args[1]["statistic_id"] for call in mock_add_stats.call_args_list
        ]
        assert "mittfortum:hourly_consumption_6094111" in statistic_ids
        assert "mittfortum:hourly_cost_6094111" in statistic_ids
        assert "mittfortum:hourly_price_6094111" in statistic_ids
        assert "mittfortum:hourly_temperature_6094111" in statistic_ids

        for call in mock_add_stats.call_args_list:
            assert len(call.args[2]) == 1

    async def test_backfill_hourly_statistics_uses_first_missing_recent_hour(
        self, mock_hass, mock_auth_client
    ):
        """Start from first missing hour when recent price stats exist."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        fixed_now = datetime.fromisoformat("2026-03-18T00:00:00+00:00")
        two_weeks_ago = fixed_now - timedelta(days=14)
        covered = {
            two_weeks_ago,
            two_weeks_ago + timedelta(hours=1),
            two_weeks_ago + timedelta(hours=3),
        }

        with (
            patch.object(
                client,
                "get_metering_points",
                return_value=[MeteringPoint(metering_point_no="6094111")],
            ),
            patch.object(
                client,
                "_get_price_statistic_hours",
                return_value=covered,
            ),
            patch.object(
                client,
                "_sync_statistics_range_forward",
                return_value=0,
            ) as mock_sync_forward,
            patch(
                "custom_components.mittfortum.api.client.dt_util.utcnow",
                return_value=fixed_now,
            ),
        ):
            imported = await client.backfill_hourly_statistics()

        assert imported == 0
        assert mock_sync_forward.call_count == 1
        sync_start = mock_sync_forward.call_args.args[1]
        assert sync_start == two_weeks_ago + timedelta(hours=2)

    async def test_backfill_hourly_statistics_force_resync_uses_earliest_start(
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
                "_sync_statistics_range_forward",
                return_value=6,
            ) as mock_sync_forward,
        ):
            imported = await client.backfill_hourly_statistics(force_resync=True)

        assert imported == 6
        mock_sync_forward.assert_called_once()
        assert mock_sync_forward.call_args.args[1] == earliest_start
        assert mock_sync_forward.call_args.kwargs["continue_after_missing"] is True

    async def test_get_price_statistic_hours_parses_string_timestamp(
        self, mock_hass, mock_auth_client
    ):
        """Parse recorder start as ISO string when datetime is not returned."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        recorder_instance = Mock()
        recorder_instance.async_add_executor_job = AsyncMock(
            return_value={
                "mittfortum:hourly_price_6094111": [
                    {"start": "2026-03-17T22:00:00+00:00", "max": 1.2}
                ]
            }
        )

        with patch(
            "custom_components.mittfortum.api.client.get_instance",
            return_value=recorder_instance,
        ):
            starts = await client._get_price_statistic_hours(
                "6094111",
                datetime.fromisoformat("2026-03-04T00:00:00+00:00"),
                datetime.fromisoformat("2026-03-18T00:00:00+00:00"),
            )

        assert starts == {datetime.fromisoformat("2026-03-17T22:00:00+00:00")}

    async def test_get_price_statistic_hours_parses_unix_timestamp(
        self, mock_hass, mock_auth_client
    ):
        """Parse recorder start when statistics_during_period returns unix time."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        expected_start = datetime.fromisoformat("2026-03-17T22:00:00+00:00")
        recorder_instance = Mock()
        recorder_instance.async_add_executor_job = AsyncMock(
            return_value={
                "mittfortum:hourly_price_6094111": [
                    {"start": expected_start.timestamp(), "max": 1.2}
                ]
            }
        )

        with patch(
            "custom_components.mittfortum.api.client.get_instance",
            return_value=recorder_instance,
        ):
            starts = await client._get_price_statistic_hours(
                "6094111",
                datetime.fromisoformat("2026-03-04T00:00:00+00:00"),
                datetime.fromisoformat("2026-03-18T00:00:00+00:00"),
            )

        assert starts == {expected_start}

    async def test_determine_sync_start_prefers_userinfo_marker_when_no_recent_stats(
        self, mock_hass, mock_auth_client
    ):
        """Use earliest marker from user info when no recent stats exist."""
        client = FortumAPIClient(mock_hass, mock_auth_client)
        cached_earliest = datetime.fromisoformat("2025-01-06T00:00:00+00:00")
        client._earliest_available_by_metering_point["6094111"] = cached_earliest
        two_weeks_ago = datetime.fromisoformat("2026-03-04T00:00:00+00:00")
        now = datetime.fromisoformat("2026-03-18T00:00:00+00:00")

        with patch.object(client, "_get_price_statistic_hours", return_value=set()):
            start, historical = await client._determine_sync_start(
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
            patch.object(
                client,
                "get_metering_points",
                return_value=[MeteringPoint(metering_point_no="6094111")],
            ),
            patch.object(
                client,
                "_get_price_statistic_hours",
                return_value={datetime.fromisoformat("2026-03-04T00:00:00+00:00")},
            ),
            patch.object(client, "get_time_series_data", return_value=[time_series]),
            patch(
                "custom_components.mittfortum.api.client.async_add_external_statistics"
            ) as mock_add_stats,
            patch(
                "custom_components.mittfortum.api.client._LOGGER.warning"
            ) as mock_warn,
        ):
            imported = await client.backfill_hourly_statistics()

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
                "custom_components.mittfortum.api.client.get_instance",
                return_value=recorder_instance,
            ),
        ):
            cleared = await client.clear_hourly_statistics()

        assert cleared == 4
        recorder_instance.async_clear_statistics.assert_called_once()

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
                "custom_components.mittfortum.api.client.async_add_external_statistics",
                side_effect=_fake_add_external_statistics,
            ),
            patch.object(client, "get_time_series_data", return_value=[time_series]),
            patch.object(
                client,
                "_get_stat_sum_before_hour",
                side_effect=_seed_sum,
            ),
        ):
            await client._sync_statistics_window(
                "6094111",
                from_date,
                to_date,
                continue_after_missing=False,
            )
            await client._sync_statistics_window(
                "6094111",
                from_date,
                to_date,
                continue_after_missing=False,
            )
            await client._sync_statistics_window(
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
        assert metadata_by_sid[hourly_cost_sid]["has_sum"] is True
