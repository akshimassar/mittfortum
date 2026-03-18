"""Test coordinator module."""

import threading
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.mittfortum.api.client import FortumAPIClient
from custom_components.mittfortum.exceptions import APIError
from custom_components.mittfortum.models import ConsumptionData
from custom_components.mittfortum.schedulers import (
    HourlyConsumptionSyncScheduler,
    SpotPriceSyncScheduler,
)


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
    client.sync_hourly_data_all_meters.return_value = 24
    return client


@pytest.fixture
def coordinator(mock_hass, mock_api_client):
    """Create a coordinator instance."""
    return HourlyConsumptionSyncScheduler(
        hass=mock_hass,
        api_client=mock_api_client,
        update_interval=timedelta(minutes=15),
    )


@pytest.fixture
def price_coordinator(mock_hass, mock_api_client):
    """Create a price coordinator instance."""
    mock_api_client.get_price_data.return_value = [
        ConsumptionData(
            value=0.0,
            unit="kWh",
            date_time=datetime.now(),
            price=0.129,
            price_unit="EUR/kWh",
        )
    ]
    return SpotPriceSyncScheduler(
        hass=mock_hass,
        api_client=mock_api_client,
        update_interval=timedelta(minutes=5),
    )


class TestHourlyConsumptionSyncScheduler:
    """Test hourly consumption coordinator."""

    async def test_init(self, coordinator, mock_hass, mock_api_client):
        """Test coordinator initialization."""
        assert coordinator.hass == mock_hass
        assert coordinator.api_client == mock_api_client
        assert coordinator.name == "MittFortum"
        assert coordinator.update_interval == timedelta(minutes=15)

    async def test_async_update_data_success(self, coordinator, mock_api_client):
        """Test successful statistics-only update cycle."""
        data = await coordinator._async_update_data()

        assert data == []
        mock_api_client.sync_hourly_data_all_meters.assert_called_once_with(
            force_resync=False,
        )
        assert coordinator.last_statistics_sync is not None

    async def test_async_update_data_unexpected_error(
        self, coordinator, mock_api_client
    ):
        """Test update failure when statistics sync raises unexpected error."""
        mock_api_client.sync_hourly_data_all_meters.side_effect = Exception("boom")

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
        sync_mock = mock_api_client.sync_hourly_data_all_meters
        sync_mock.side_effect = APIError("sync failed")

        data = await coordinator._async_update_data()

        assert data == []
        assert coordinator.last_statistics_sync is None

    async def test_async_clear_statistics_resets_sync_timestamp(
        self, coordinator, mock_api_client
    ):
        """Clearing statistics should reset last sync marker."""
        coordinator.last_statistics_sync = datetime.now().astimezone()
        mock_api_client.clear_hourly_statistics.return_value = 3

        cleared = await coordinator.async_clear_statistics()

        assert cleared == 3
        assert coordinator.last_statistics_sync is None
        mock_api_client.clear_hourly_statistics.assert_awaited_once()


class TestSpotPriceSyncScheduler:
    """Test spot price coordinator."""

    async def test_init(self, price_coordinator, mock_hass, mock_api_client):
        """Test price coordinator initialization."""
        assert price_coordinator.hass == mock_hass
        assert price_coordinator.api_client == mock_api_client
        assert price_coordinator.name == "MittFortum Price"
        assert price_coordinator.update_interval == timedelta(minutes=5)

    async def test_async_update_data_success(self, price_coordinator, mock_api_client):
        """Test successful price update."""
        data = await price_coordinator._async_update_data()

        assert len(data) == 1
        assert data[0].price == 0.129
        mock_api_client.get_price_data.assert_called_once()

    async def test_async_update_data_api_error(
        self, price_coordinator, mock_api_client
    ):
        """Test price update with API error."""
        mock_api_client.get_price_data.side_effect = APIError("API error")

        with pytest.raises(UpdateFailed) as exc_info:
            await price_coordinator._async_update_data()

        assert "API error" in str(exc_info.value)

    async def test_async_update_data_none_response(
        self, price_coordinator, mock_api_client
    ):
        """Test price update with None response."""
        mock_api_client.get_price_data.return_value = None

        data = await price_coordinator._async_update_data()
        assert data == []
