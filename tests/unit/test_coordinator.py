"""Test coordinator module."""

import threading
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import frame
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.mittfortum.api.client import FortumAPIClient
from custom_components.mittfortum.coordinator import (
    MittFortumDataCoordinator,
    MittFortumPriceCoordinator,
)
from custom_components.mittfortum.exceptions import APIError
from custom_components.mittfortum.models import ConsumptionData


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
    # Configure the async mock to return actual data
    test_data = [
        ConsumptionData(value=150.5, unit="kWh", date_time=datetime.now(), cost=25.50)
    ]
    # The coordinator calls get_total_consumption, not get_consumption_data
    client.get_total_consumption.return_value = test_data
    client.backfill_hourly_statistics.return_value = 24
    return client


@pytest.fixture
def coordinator(mock_hass, mock_api_client):
    """Create a coordinator instance."""
    return MittFortumDataCoordinator(
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
    return MittFortumPriceCoordinator(
        hass=mock_hass,
        api_client=mock_api_client,
        update_interval=timedelta(minutes=5),
    )


class TestMittFortumDataCoordinator:
    """Test MittFortum data coordinator."""

    async def test_init(self, coordinator, mock_hass, mock_api_client):
        """Test coordinator initialization."""
        assert coordinator.hass == mock_hass
        assert coordinator.api_client == mock_api_client
        assert coordinator.name == "MittFortum"
        assert coordinator.update_interval == timedelta(minutes=15)

    async def test_async_update_data_success(self, coordinator, mock_api_client):
        """Test successful data update."""
        data = await coordinator._async_update_data()

        assert len(data) == 1
        assert abs(data[0].value - 150.5) < 0.01
        assert data[0].unit == "kWh"
        assert abs(data[0].cost - 25.50) < 0.01
        mock_api_client.get_total_consumption.assert_called_once()
        mock_api_client.backfill_hourly_statistics.assert_called_once_with(
            rewrite=False,
            allow_historical_backfill=False,
        )
        assert coordinator.last_statistics_sync is not None

    async def test_async_update_data_authentication_error(
        self, coordinator, mock_api_client
    ):
        """Test data update with authentication error."""
        mock_api_client.get_total_consumption.side_effect = APIError("Auth failed")

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "API error" in str(exc_info.value)

    async def test_async_update_data_api_error(self, coordinator, mock_api_client):
        """Test data update with API error."""
        mock_api_client.get_total_consumption.side_effect = APIError("API error")

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "API error" in str(exc_info.value)

    async def test_async_update_data_unexpected_error(
        self, coordinator, mock_api_client
    ):
        """Test data update with unexpected error."""
        mock_api_client.get_total_consumption.side_effect = Exception(
            "Unexpected error"
        )

        with pytest.raises(UpdateFailed) as exc_info:
            await coordinator._async_update_data()

        assert "Unexpected error" in str(exc_info.value)

    async def test_async_update_data_empty_response(self, coordinator, mock_api_client):
        """Test data update with empty response."""
        mock_api_client.get_total_consumption.return_value = []

        data = await coordinator._async_update_data()
        assert data == []

    async def test_async_update_data_statistics_sync_error(
        self, coordinator, mock_api_client
    ):
        """Test data update when statistics sync fails."""
        sync_mock = mock_api_client.backfill_hourly_statistics
        sync_mock.side_effect = APIError("sync failed")

        data = await coordinator._async_update_data()

        assert len(data) == 1
        assert coordinator.last_statistics_sync is None

    async def test_schedule_initial_backfill_skips_when_price_stats_exist(
        self, coordinator, mock_api_client
    ):
        """Do not start background backfill when price stats already exist."""
        mock_api_client.has_existing_price_statistics.return_value = True

        await coordinator.async_schedule_initial_backfill()

        mock_api_client.has_existing_price_statistics.assert_awaited_once()
        assert coordinator._historical_backfill_task is None


class TestMittFortumPriceCoordinator:
    """Test MittFortum price coordinator."""

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
