"""Test __init__.py module."""

import logging
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from custom_components.mittfortum import (
    _apply_debug_logging,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.mittfortum.const import CONF_DEBUG_LOGGING, DOMAIN


class TestInit:
    """Test integration setup and teardown."""

    async def test_async_setup_entry_success(self, mock_hass):
        """Test successful setup."""
        entry = AsyncMock(spec=ConfigEntry)
        entry.data = {
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "test_password",
        }
        entry.entry_id = "test_entry_id"
        entry.options = {}
        entry.add_update_listener = Mock(return_value=Mock())
        entry.async_on_unload = Mock()

        mock_hass.data = {DOMAIN: {}}

        with (
            patch("custom_components.mittfortum.OAuth2AuthClient") as mock_auth,
            patch("custom_components.mittfortum.FortumAPIClient") as mock_api,
            patch("custom_components.mittfortum.MittFortumDevice") as mock_device,
            patch(
                "custom_components.mittfortum.MittFortumDataCoordinator"
            ) as mock_coordinator,
            patch(
                "custom_components.mittfortum.MittFortumPriceCoordinator"
            ) as mock_price_coordinator,
        ):
            mock_auth_instance = AsyncMock()
            mock_auth.return_value = mock_auth_instance

            mock_api_instance = AsyncMock()
            mock_api_instance.get_customer_id.return_value = "customer_123"
            mock_api.return_value = mock_api_instance

            mock_device_instance = AsyncMock()
            mock_device.return_value = mock_device_instance

            mock_coordinator_instance = AsyncMock()
            mock_coordinator.return_value = mock_coordinator_instance

            mock_price_coordinator_instance = AsyncMock()
            mock_price_coordinator.return_value = mock_price_coordinator_instance

            mock_hass.config_entries.async_forward_entry_setups = AsyncMock(
                return_value=True
            )

            result = await async_setup_entry(mock_hass, entry)

            assert result is True
            assert DOMAIN in mock_hass.data
            assert entry.entry_id in mock_hass.data[DOMAIN]

    async def test_async_setup_entry_auth_failure(self, mock_hass):
        """Test setup with authentication failure."""
        entry = AsyncMock(spec=ConfigEntry)
        entry.data = {
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "wrong_password",
        }
        entry.options = {}
        entry.add_update_listener = Mock(return_value=Mock())
        entry.async_on_unload = Mock()

        mock_hass.data = {DOMAIN: {}}

        with patch("custom_components.mittfortum.OAuth2AuthClient") as mock_auth:
            mock_auth_instance = AsyncMock()
            mock_auth_instance.authenticate.side_effect = Exception("Auth failed")
            mock_auth.return_value = mock_auth_instance

            result = await async_setup_entry(mock_hass, entry)

            assert result is False

    async def test_async_unload_entry_success(self, mock_hass):
        """Test successful unload."""
        entry = AsyncMock(spec=ConfigEntry)
        entry.entry_id = "test_entry_id"

        mock_hass.data = {DOMAIN: {entry.entry_id: {"test": "data"}}}
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await async_unload_entry(mock_hass, entry)

        assert result is True
        assert entry.entry_id not in mock_hass.data[DOMAIN]

    async def test_async_unload_entry_failure(self, mock_hass):
        """Test unload failure."""
        entry = AsyncMock(spec=ConfigEntry)
        entry.entry_id = "test_entry_id"

        mock_hass.data = {DOMAIN: {entry.entry_id: {"test": "data"}}}
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        result = await async_unload_entry(mock_hass, entry)

        assert result is False
        assert entry.entry_id in mock_hass.data[DOMAIN]  # Should still be there

    def test_apply_debug_logging_uses_options_toggle(self):
        """Test debug logging level is applied from options."""
        entry = Mock(spec=ConfigEntry)
        entry.options = {CONF_DEBUG_LOGGING: True}

        with patch("custom_components.mittfortum.logging.getLogger") as mock_get_logger:
            logger = Mock()
            mock_get_logger.return_value = logger

            _apply_debug_logging(entry)

        logger.setLevel.assert_called_once_with(logging.DEBUG)
