"""Test entity module."""

from unittest.mock import Mock

import pytest

from custom_components.fortum.coordinators.hourly_consumption import (
    HourlyConsumptionSyncCoordinator,
)
from custom_components.fortum.device import FortumDevice
from custom_components.fortum.entity import FortumEntity


@pytest.fixture
def mock_coordinator():
    """Create a mock coordinator."""
    return Mock(spec=HourlyConsumptionSyncCoordinator)


@pytest.fixture
def mock_device():
    """Create a mock device."""
    device = Mock(spec=FortumDevice)
    device.unique_id = "fortum_MP123456"
    device.device_info = {
        "identifiers": {("fortum", "MP123456")},
        "name": "Main Meter",
        "manufacturer": "Fortum",
        "model": "Energy Meter",
    }
    return device


class TestFortumEntity:
    """Test Fortum entity base class."""

    def test_entity_creation(self, mock_coordinator, mock_device):
        """Test entity creation."""
        entity = FortumEntity(
            coordinator=mock_coordinator,
            device=mock_device,
            entity_key="test_key",
            name="Test Entity",
        )

        assert entity.coordinator == mock_coordinator
        assert entity._device == mock_device
        assert entity._entity_key == "test_key"
        assert entity.name == "Test Entity"

    def test_unique_id(self, mock_coordinator, mock_device):
        """Test unique ID generation."""
        entity = FortumEntity(
            coordinator=mock_coordinator,
            device=mock_device,
            entity_key="test_key",
            name="Test Entity",
        )

        assert entity.unique_id == "fortum_MP123456_test_key"

    def test_device_info_property(self, mock_coordinator, mock_device):
        """Test device info property."""
        entity = FortumEntity(
            coordinator=mock_coordinator,
            device=mock_device,
            entity_key="test_key",
            name="Test Entity",
        )

        device_info = entity.device_info
        assert device_info == mock_device.device_info

    def test_available_with_coordinator_success(self, mock_coordinator, mock_device):
        """Test availability when coordinator is successful."""
        mock_coordinator.last_update_success = True
        mock_coordinator.data = [{"value": 123}]

        entity = FortumEntity(
            coordinator=mock_coordinator,
            device=mock_device,
            entity_key="test_key",
            name="Test Entity",
        )

        assert entity.available is True

    def test_available_with_coordinator_failure(self, mock_coordinator, mock_device):
        """Test availability when coordinator fails."""
        mock_coordinator.last_update_success = False

        entity = FortumEntity(
            coordinator=mock_coordinator,
            device=mock_device,
            entity_key="test_key",
            name="Test Entity",
        )

        assert entity.available is False

    def test_available_with_no_data(self, mock_coordinator, mock_device):
        """Test availability when no data is available."""
        mock_coordinator.last_update_success = True
        mock_coordinator.data = None

        entity = FortumEntity(
            coordinator=mock_coordinator,
            device=mock_device,
            entity_key="test_key",
            name="Test Entity",
        )

        assert entity.available is False

    def test_available_with_empty_data(self, mock_coordinator, mock_device):
        """Test availability when data is empty but update succeeded."""
        mock_coordinator.last_update_success = True
        mock_coordinator.data = []

        entity = FortumEntity(
            coordinator=mock_coordinator,
            device=mock_device,
            entity_key="test_key",
            name="Test Entity",
        )

        assert entity.available is True

    def test_should_poll(self, mock_coordinator, mock_device):
        """Test that entity should not poll."""
        entity = FortumEntity(
            coordinator=mock_coordinator,
            device=mock_device,
            entity_key="test_key",
            name="Test Entity",
        )

        assert entity.should_poll is False

    def test_entity_with_default_name(self, mock_coordinator, mock_device):
        """Test entity with default name."""
        entity = FortumEntity(
            coordinator=mock_coordinator,
            device=mock_device,
            entity_key="test_key",
            name="test_key",
        )

        assert entity.name == "test_key"
