"""Test config flow."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.mittfortum.config_flow import (
    CannotConnect,
    ConfigFlow,
    InvalidAuth,
    OptionsFlowHandler,
    validate_input,
)
from custom_components.mittfortum.const import (
    CONF_DEBUG_ENTITIES,
    CONF_DEBUG_LOGGING,
    CONF_REGION,
)
from custom_components.mittfortum.exceptions import AuthenticationError, MittFortumError


@pytest.fixture
def mock_hass():
    """Create a mock Home Assistant instance."""
    return Mock(spec=HomeAssistant)


@pytest.fixture
def config_flow(mock_hass):
    """Create a config flow instance."""
    flow = ConfigFlow()
    flow.hass = mock_hass
    return flow


class TestMittFortumConfigFlow:
    """Test MittFortum config flow."""

    async def test_form_step_user(self, config_flow):
        """Test user step shows form."""
        result = await config_flow.async_step_user()

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {}

    @patch("custom_components.mittfortum.config_flow.validate_input")
    async def test_form_step_user_valid_credentials(self, mock_validate, config_flow):
        """Test user step with valid credentials."""
        mock_validate.return_value = {"title": "MittFortum (test_user)"}

        user_input = {
            CONF_USERNAME: "test_user",
            CONF_PASSWORD: "test_password",
        }

        with (
            patch.object(config_flow, "async_set_unique_id") as mock_set_id,
            patch.object(config_flow, "_abort_if_unique_id_configured"),
        ):
            result = await config_flow.async_step_user(user_input)

            assert result["type"] == FlowResultType.CREATE_ENTRY
            assert result["title"] == "MittFortum (test_user)"
            assert result["data"] == user_input
            mock_set_id.assert_called_once_with("test_user")

    @patch("custom_components.mittfortum.config_flow.validate_input")
    async def test_form_step_user_invalid_credentials(self, mock_validate, config_flow):
        """Test user step with invalid credentials."""
        mock_validate.side_effect = InvalidAuth()

        user_input = {
            CONF_USERNAME: "invalid_user",
            CONF_PASSWORD: "invalid_password",
        }

        result = await config_flow.async_step_user(user_input)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "invalid_auth"}

    @patch("custom_components.mittfortum.config_flow.validate_input")
    async def test_form_step_user_connection_error(self, mock_validate, config_flow):
        """Test user step with connection error."""
        mock_validate.side_effect = CannotConnect()

        user_input = {
            CONF_USERNAME: "test_user",
            CONF_PASSWORD: "test_password",
        }

        result = await config_flow.async_step_user(user_input)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "cannot_connect"}

    @patch("custom_components.mittfortum.config_flow.validate_input")
    async def test_form_step_user_unexpected_error(self, mock_validate, config_flow):
        """Test user step with unexpected error."""
        mock_validate.side_effect = Exception("Unexpected error")

        user_input = {
            CONF_USERNAME: "test_user",
            CONF_PASSWORD: "test_password",
        }

        result = await config_flow.async_step_user(user_input)

        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"
        assert result["errors"] == {"base": "unknown"}


class TestValidateInput:
    """Test validate_input function."""

    @patch("custom_components.mittfortum.api.OAuth2AuthClient")
    @patch("custom_components.mittfortum.api.FortumAPIClient")
    async def test_validate_input_success(
        self, mock_api_client_class, mock_auth_client_class, mock_hass
    ):
        """Test successful validation."""
        mock_auth_client = AsyncMock()
        mock_auth_client_class.return_value = mock_auth_client

        mock_api_client = AsyncMock()
        mock_api_client_class.return_value = mock_api_client
        mock_api_client.get_customer_id.return_value = "12345"

        data = {
            CONF_USERNAME: "test_user",
            CONF_PASSWORD: "test_password",
        }

        result = await validate_input(mock_hass, data)
        assert result["title"] == "MittFortum (test_user)"

    @patch("custom_components.mittfortum.api.OAuth2AuthClient")
    @patch("custom_components.mittfortum.api.FortumAPIClient")
    async def test_validate_input_auth_error(
        self, mock_api_client_class, mock_auth_client_class, mock_hass
    ):
        """Test validation with authentication error."""
        mock_auth_client = AsyncMock()
        mock_auth_client_class.return_value = mock_auth_client

        mock_api_client = AsyncMock()
        mock_api_client_class.return_value = mock_api_client
        mock_api_client.get_customer_id.side_effect = AuthenticationError(
            "Invalid credentials"
        )

        data = {
            CONF_USERNAME: "invalid_user",
            CONF_PASSWORD: "invalid_password",
        }

        with pytest.raises(InvalidAuth):
            await validate_input(mock_hass, data)

    @patch("custom_components.mittfortum.api.OAuth2AuthClient")
    @patch("custom_components.mittfortum.api.FortumAPIClient")
    async def test_validate_input_api_error(
        self, mock_api_client_class, mock_auth_client_class, mock_hass
    ):
        """Test validation with API error."""
        mock_auth_client = AsyncMock()
        mock_auth_client_class.return_value = mock_auth_client

        mock_api_client = AsyncMock()
        mock_api_client_class.return_value = mock_api_client
        mock_api_client.get_customer_id.side_effect = MittFortumError("API error")

        data = {
            CONF_USERNAME: "test_user",
            CONF_PASSWORD: "test_password",
        }

        with pytest.raises(CannotConnect):
            await validate_input(mock_hass, data)


class TestMittFortumOptionsFlow:
    """Test MittFortum options flow."""

    async def test_options_form_shows_debug_toggle(self):
        """Test options form renders debug entities option."""
        mock_entry = Mock()
        mock_entry.data = {
            CONF_USERNAME: "old_user",
            CONF_PASSWORD: "old_pass",
            CONF_REGION: "se",
        }
        mock_entry.options = {}

        flow = OptionsFlowHandler(mock_entry)
        flow.hass = Mock()
        flow.hass.config_entries = Mock()
        result = await flow.async_step_init()

        assert result.get("type") == FlowResultType.FORM
        assert result.get("step_id") == "init"

    async def test_options_form_saves_debug_toggle(self):
        """Test options flow updates credentials/region and debug flag."""
        mock_entry = Mock()
        mock_entry.data = {
            CONF_USERNAME: "old_user",
            CONF_PASSWORD: "old_pass",
            CONF_REGION: "se",
        }
        mock_entry.options = {CONF_DEBUG_ENTITIES: False}

        flow = OptionsFlowHandler(mock_entry)
        flow.hass = Mock()
        flow.hass.config_entries = Mock()
        flow.hass.config_entries.async_update_entry = Mock()

        result = await flow.async_step_init(
            {
                CONF_USERNAME: "new_user",
                CONF_PASSWORD: "new_pass",
                CONF_REGION: "fi",
                CONF_DEBUG_ENTITIES: True,
                CONF_DEBUG_LOGGING: True,
            }
        )

        assert result.get("type") == FlowResultType.CREATE_ENTRY
        assert result.get("data") == {
            CONF_DEBUG_ENTITIES: True,
            CONF_DEBUG_LOGGING: True,
        }
        flow.hass.config_entries.async_update_entry.assert_called_once()
