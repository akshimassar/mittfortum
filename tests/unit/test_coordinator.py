"""Test coordinator module."""

import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import frame
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.fortum.api.client import FortumAPIClient
from custom_components.fortum.coordinators.hourly_consumption import (
    HourlyConsumptionSyncCoordinator,
)
from custom_components.fortum.coordinators.spot_price import SpotPriceSyncCoordinator
from custom_components.fortum.exceptions import APIError, AuthenticationError
from custom_components.fortum.models import SpotPricePoint


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    hass = Mock(spec=HomeAssistant)
    hass.loop_thread_id = threading.get_ident()
    frame.async_setup(hass)
    return hass


@pytest.fixture
def mock_api_client():
    """Create a mock API client."""
    client = AsyncMock(spec=FortumAPIClient)
    client.sync_hourly_data_for_metering_points.return_value = 24
    client._build_consumption_statistic_id.side_effect = (  # noqa: SLF001
        lambda metering_point_no: f"fortum:hourly_consumption_{metering_point_no}"
    )
    client._build_cost_statistic_id.side_effect = (  # noqa: SLF001
        lambda metering_point_no: f"fortum:hourly_cost_{metering_point_no}"
    )
    return client


@pytest.fixture
def mock_session_manager():
    """Create a mock session manager."""
    manager = Mock()
    manager.get_snapshot.return_value = SimpleNamespace(
        metering_points=(SimpleNamespace(metering_point_no="6094111"),),
        price_areas=("FI",),
    )
    return manager


@pytest.fixture
def coordinator(mock_hass, mock_api_client, mock_session_manager):
    """Create a coordinator instance."""
    return HourlyConsumptionSyncCoordinator(
        hass=mock_hass,
        api_client=mock_api_client,
        session_manager=mock_session_manager,
        update_interval=timedelta(minutes=15),
    )


@pytest.fixture
def price_coordinator(mock_hass, mock_api_client, mock_session_manager):
    """Create a price coordinator instance."""
    mock_api_client.fetch_spot_prices_for_areas.return_value = [
        SpotPricePoint(
            date_time=datetime.now(),
            price=0.129,
            price_unit="EUR/kWh",
        )
    ]
    return SpotPriceSyncCoordinator(
        hass=mock_hass,
        api_client=mock_api_client,
        session_manager=mock_session_manager,
        update_interval=timedelta(minutes=5),
    )


class TestHourlyConsumptionSyncCoordinator:
    """Test hourly consumption coordinator."""

    async def test_init(self, coordinator, mock_hass, mock_api_client):
        """Test coordinator initialization."""
        assert coordinator.hass == mock_hass
        assert coordinator.api_client == mock_api_client
        assert coordinator.name == "Fortum"
        assert coordinator.update_interval == timedelta(minutes=15)

    async def test_async_update_data_success(self, coordinator, mock_api_client):
        """Test successful statistics-only update cycle."""
        data = await coordinator._async_update_data()

        assert data == []
        mock_api_client.sync_hourly_data_for_metering_points.assert_called_once_with(
            coordinator._session_manager.get_snapshot.return_value.metering_points,
        )
        assert coordinator.last_statistics_sync is not None

    async def test_async_update_data_unexpected_error(
        self, coordinator, mock_api_client
    ):
        """Test update failure when statistics sync raises unexpected error."""
        mock_api_client.sync_hourly_data_for_metering_points.side_effect = Exception(
            "boom"
        )

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "Unexpected error" in str(exc_info.value)

    async def test_async_update_data_empty_response(self, coordinator, mock_api_client):
        """Test statistics-only coordinator returns empty list payload."""

        data = await coordinator._async_update_data()
        assert data == []

    async def test_async_update_data_statistics_sync_error(
        self, coordinator, mock_api_client
    ):
        """Test data update when statistics sync fails."""
        sync_mock = mock_api_client.sync_hourly_data_for_metering_points
        sync_mock.side_effect = APIError("sync failed")

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "API error" in str(exc_info.value)
        assert coordinator.last_statistics_sync is None

    async def test_async_update_data_authentication_error(
        self,
        coordinator,
        mock_api_client,
    ):
        """Authentication errors should trigger config-entry reauth handling."""
        mock_api_client.sync_hourly_data_for_metering_points.side_effect = (
            AuthenticationError("Unauthorized (401)")
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_async_update_data_missing_snapshot(
        self,
        coordinator,
    ):
        """Missing session snapshot should fail the update cycle."""
        coordinator._session_manager.get_snapshot.return_value = None

        with pytest.raises(UpdateFailed, match="Session snapshot unavailable"):
            await coordinator._async_update_data()

    async def test_async_clear_statistics_resets_sync_timestamp(
        self,
        coordinator,
        mock_api_client,
    ):
        """Clearing statistics should reset last sync marker."""
        coordinator.last_statistics_sync = datetime.now().astimezone()
        mock_api_client.clear_hourly_statistics_for_topology.return_value = 3

        cleared = await coordinator.async_clear_statistics()

        assert cleared == 3
        assert coordinator.last_statistics_sync is None
        mock_api_client.clear_hourly_statistics_for_topology.assert_awaited_once_with(
            coordinator._session_manager.get_snapshot.return_value.metering_points,
            coordinator._session_manager.get_snapshot.return_value.price_areas,
        )

    async def test_statistics_sync_refreshes_month_totals_from_recorder(
        self,
        coordinator,
        mock_api_client,
    ):
        """Statistics sync should update current-month totals and units."""
        mock_api_client.sync_hourly_data_for_metering_points.return_value = 1

        recorder_instance = Mock()
        recorder_instance.async_add_executor_job = AsyncMock(
            side_effect=lambda fn: fn()
        )

        with (
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.get_instance",
                return_value=recorder_instance,
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.get_metadata",
                side_effect=[
                    {
                        "fortum:hourly_consumption_6094111": (
                            1,
                            {"unit_of_measurement": "kWh"},
                        )
                    },
                    {
                        "fortum:hourly_cost_6094111": (
                            2,
                            {"unit_of_measurement": "EUR"},
                        )
                    },
                ],
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.statistics_during_period",
                side_effect=[
                    {
                        "fortum:hourly_consumption_6094111": [
                            {"sum": 120.0},
                            {"sum": 140.5},
                        ]
                    },
                    {"fortum:hourly_consumption_6094111": [{"sum": 100.0}]},
                    {
                        "fortum:hourly_cost_6094111": [
                            {"sum": 80.0},
                            {"sum": 92.0},
                        ]
                    },
                    {"fortum:hourly_cost_6094111": [{"sum": 75.0}]},
                ],
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.dt_util.now",
                return_value=datetime.fromisoformat("2026-03-20T12:00:00+00:00"),
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.dt_util.utcnow",
                return_value=datetime.fromisoformat("2026-03-20T11:17:00+00:00"),
            ),
        ):
            imported = await coordinator.async_run_statistics_sync()

        assert imported == 1
        assert coordinator.get_current_month_consumption_total(
            "6094111"
        ) == pytest.approx(40.5)
        assert coordinator.get_current_month_consumption_unit("6094111") == "kWh"
        assert coordinator.get_current_month_cost_total("6094111") == pytest.approx(
            17.0
        )
        assert coordinator.get_current_month_cost_unit("6094111") == "EUR"

    async def test_statistics_sync_keeps_total_unavailable_without_baseline_hour(
        self,
        coordinator,
        mock_api_client,
    ):
        """Month total should be unavailable without previous-month baseline hour."""
        mock_api_client.sync_hourly_data_for_metering_points.return_value = 1

        recorder_instance = Mock()
        recorder_instance.async_add_executor_job = AsyncMock(
            side_effect=lambda fn: fn()
        )

        with (
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.get_instance",
                return_value=recorder_instance,
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.get_metadata",
                side_effect=[{}, {}],
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.statistics_during_period",
                side_effect=[
                    {"fortum:hourly_consumption_6094111": [{"sum": 10.0}]},
                    {},
                    {"fortum:hourly_cost_6094111": [{"sum": 2.5}]},
                    {},
                ],
            ) as mock_statistics_during_period,
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.dt_util.now",
                return_value=datetime.fromisoformat("2026-03-20T12:00:00+00:00"),
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.dt_util.utcnow",
                return_value=datetime.fromisoformat("2026-03-20T11:17:00+00:00"),
            ),
        ):
            await coordinator.async_run_statistics_sync()

        assert coordinator.get_current_month_consumption_total("6094111") is None
        assert coordinator.get_current_month_consumption_unit("6094111") is None
        assert coordinator.get_current_month_cost_total("6094111") is None
        assert coordinator.get_current_month_cost_unit("6094111") is None

        expected_month_start = datetime.fromisoformat("2026-03-01T00:00:00+00:00")
        baseline_query = mock_statistics_during_period.call_args_list[1].kwargs
        assert baseline_query["start_time"] == expected_month_start - timedelta(hours=1)
        assert baseline_query["end_time"] == expected_month_start

    async def test_statistics_sync_keeps_total_unavailable_without_in_month_hour(
        self,
        coordinator,
        mock_api_client,
    ):
        """Month total should be unavailable without any current-month hour."""
        mock_api_client.sync_hourly_data_for_metering_points.return_value = 1

        recorder_instance = Mock()
        recorder_instance.async_add_executor_job = AsyncMock(
            side_effect=lambda fn: fn()
        )

        with (
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.get_instance",
                return_value=recorder_instance,
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.get_metadata",
                side_effect=[
                    {
                        "fortum:hourly_consumption_6094111": (
                            1,
                            {"unit_of_measurement": "kWh"},
                        )
                    },
                    {
                        "fortum:hourly_cost_6094111": (
                            2,
                            {"unit_of_measurement": "EUR"},
                        )
                    },
                ],
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.statistics_during_period",
                side_effect=[
                    {},
                    {"fortum:hourly_consumption_6094111": [{"sum": 100.0}]},
                    {},
                    {"fortum:hourly_cost_6094111": [{"sum": 75.0}]},
                ],
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.dt_util.now",
                return_value=datetime.fromisoformat("2026-03-20T12:00:00+00:00"),
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.dt_util.utcnow",
                return_value=datetime.fromisoformat("2026-03-20T11:17:00+00:00"),
            ),
        ):
            await coordinator.async_run_statistics_sync()

        assert coordinator.get_current_month_consumption_total("6094111") is None
        assert coordinator.get_current_month_cost_total("6094111") is None

    async def test_statistics_sync_rounds_month_totals_to_two_decimals(
        self,
        coordinator,
        mock_api_client,
    ):
        """Month totals should be rounded to avoid float precision artifacts."""
        mock_api_client.sync_hourly_data_for_metering_points.return_value = 1

        recorder_instance = Mock()
        recorder_instance.async_add_executor_job = AsyncMock(
            side_effect=lambda fn: fn()
        )

        with (
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.get_instance",
                return_value=recorder_instance,
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.get_metadata",
                side_effect=[{}, {}],
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.statistics_during_period",
                side_effect=[
                    {
                        "fortum:hourly_consumption_6094111": [
                            {"sum": 2000.18000000002},
                        ]
                    },
                    {"fortum:hourly_consumption_6094111": [{"sum": 337.09}]},
                    {"fortum:hourly_cost_6094111": [{"sum": 123.456}]},
                    {"fortum:hourly_cost_6094111": [{"sum": 100.001}]},
                ],
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.dt_util.now",
                return_value=datetime.fromisoformat("2026-03-20T12:00:00+00:00"),
            ),
            patch(
                "custom_components.fortum.coordinators.hourly_consumption.dt_util.utcnow",
                return_value=datetime.fromisoformat("2026-03-20T11:17:00+00:00"),
            ),
        ):
            await coordinator.async_run_statistics_sync()

        assert coordinator.get_current_month_consumption_total("6094111") == 1663.09
        assert coordinator.get_current_month_cost_total("6094111") == 23.45


class TestSpotPriceSyncCoordinator:
    """Test spot price coordinator."""

    async def test_init(self, price_coordinator, mock_hass, mock_api_client):
        """Test price coordinator initialization."""
        assert price_coordinator.hass == mock_hass
        assert price_coordinator.api_client == mock_api_client
        assert price_coordinator.name == "Fortum Price"
        assert price_coordinator.update_interval == timedelta(minutes=5)

    async def test_async_update_data_success(self, price_coordinator, mock_api_client):
        """Test successful price update."""
        data = await price_coordinator._async_update_data()

        assert len(data) == 1
        assert data[0].price == 0.129
        mock_api_client.fetch_spot_prices_for_areas.assert_called_once_with(
            price_coordinator._session_manager.get_snapshot.return_value.price_areas,
        )

    async def test_async_update_data_api_error(
        self, price_coordinator, mock_api_client
    ):
        """Test price update with API error."""
        mock_api_client.fetch_spot_prices_for_areas.side_effect = APIError("API error")

        with pytest.raises(UpdateFailed) as exc_info:
            await price_coordinator._async_update_data()

        assert "API error" in str(exc_info.value)

    async def test_async_update_data_none_response(
        self, price_coordinator, mock_api_client
    ):
        """Test price update with None response."""
        mock_api_client.fetch_spot_prices_for_areas.return_value = None

        data = await price_coordinator._async_update_data()
        assert data == []

    async def test_async_update_data_authentication_error(
        self,
        price_coordinator,
        mock_api_client,
    ):
        """Price coordinator should surface auth failures as reauth-required."""
        mock_api_client.fetch_spot_prices_for_areas.side_effect = AuthenticationError(
            "Unauthorized (401)"
        )

        with pytest.raises(ConfigEntryAuthFailed):
            await price_coordinator._async_update_data()

    async def test_async_update_data_missing_snapshot(self, price_coordinator):
        """Missing session snapshot should fail price update cycle."""
        price_coordinator._session_manager.get_snapshot.return_value = None

        with pytest.raises(UpdateFailed, match="Session snapshot unavailable"):
            await price_coordinator._async_update_data()
