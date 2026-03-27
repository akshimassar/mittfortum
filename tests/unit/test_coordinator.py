"""Test coordinator module."""

import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
            force_resync=False,
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
