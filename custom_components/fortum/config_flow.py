"""Config flow for Fortum integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from .const import (
    CONF_CREATE_DASHBOARD,
    CONF_DEBUG_ENTITIES,
    CONF_DEBUG_LOGGING,
    CONF_FORCE_SHORT_TOKEN_LIFETIME,
    CONF_REGION,
    DEFAULT_CREATE_DASHBOARD,
    DEFAULT_DEBUG_ENTITIES,
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_FORCE_SHORT_TOKEN_LIFETIME,
    DEFAULT_REGION,
    DOMAIN,
    SUPPORTED_REGIONS,
)
from .exceptions import AuthenticationError, FortumError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_REGION, default=DEFAULT_REGION): vol.In(SUPPORTED_REGIONS),
        vol.Optional(
            CONF_CREATE_DASHBOARD,
            default=DEFAULT_CREATE_DASHBOARD,
        ): bool,
        vol.Optional(CONF_DEBUG_ENTITIES, default=DEFAULT_DEBUG_ENTITIES): bool,
        vol.Optional(CONF_DEBUG_LOGGING, default=DEFAULT_DEBUG_LOGGING): bool,
        vol.Optional(
            CONF_FORCE_SHORT_TOKEN_LIFETIME,
            default=DEFAULT_FORCE_SHORT_TOKEN_LIFETIME,
        ): bool,
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    try:
        from .api import FortumAPIClient, OAuth2AuthClient

        # Test authentication
        auth_client = OAuth2AuthClient(
            hass=hass,
            username=data[CONF_USERNAME],
            password=data[CONF_PASSWORD],
            region=data.get(CONF_REGION, DEFAULT_REGION),
        )

        await auth_client.authenticate()

        # Test API connection
        api_client = FortumAPIClient(hass, auth_client)
        await api_client.get_customer_id()

        return {"title": f"Fortum ({data[CONF_USERNAME]})"}

    except AuthenticationError as exc:
        _LOGGER.exception("authentication failed")
        raise InvalidAuth from exc
    except FortumError as exc:
        _LOGGER.exception("API connection failed")
        raise CannotConnect from exc
    except Exception as exc:
        _LOGGER.exception("unexpected error during validation")
        raise CannotConnect from exc


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Fortum."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        """Create the options flow."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)

                # Check if already configured
                await self.async_set_unique_id(user_input[CONF_USERNAME])
                self._abort_if_unique_id_configured()

                entry_data = {
                    CONF_USERNAME: user_input[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_REGION: user_input.get(CONF_REGION, DEFAULT_REGION),
                }
                entry_options = {
                    CONF_DEBUG_ENTITIES: user_input.get(
                        CONF_DEBUG_ENTITIES,
                        DEFAULT_DEBUG_ENTITIES,
                    ),
                    CONF_DEBUG_LOGGING: user_input.get(
                        CONF_DEBUG_LOGGING,
                        DEFAULT_DEBUG_LOGGING,
                    ),
                    CONF_FORCE_SHORT_TOKEN_LIFETIME: user_input.get(
                        CONF_FORCE_SHORT_TOKEN_LIFETIME,
                        DEFAULT_FORCE_SHORT_TOKEN_LIFETIME,
                    ),
                    CONF_CREATE_DASHBOARD: user_input.get(
                        CONF_CREATE_DASHBOARD,
                        DEFAULT_CREATE_DASHBOARD,
                    ),
                }

                return self.async_create_entry(
                    title=info["title"],
                    data=entry_data,
                    options=entry_options,
                )

            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class InvalidAuth(Exception):
    """Error to indicate there is invalid auth."""


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Fortum."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        """Manage Fortum options."""
        if user_input is not None:
            new_data = {
                **self._config_entry.data,
                CONF_USERNAME: user_input[CONF_USERNAME],
                CONF_PASSWORD: user_input[CONF_PASSWORD],
                CONF_REGION: user_input[CONF_REGION],
            }
            new_options = {
                **self._config_entry.options,
                CONF_DEBUG_ENTITIES: user_input[CONF_DEBUG_ENTITIES],
                CONF_DEBUG_LOGGING: user_input[CONF_DEBUG_LOGGING],
                CONF_FORCE_SHORT_TOKEN_LIFETIME: user_input[
                    CONF_FORCE_SHORT_TOKEN_LIFETIME
                ],
                CONF_CREATE_DASHBOARD: user_input[CONF_CREATE_DASHBOARD],
            }

            self.hass.config_entries.async_update_entry(
                self._config_entry,
                data=new_data,
                options=new_options,
            )
            return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=self._config_entry.data.get(CONF_USERNAME, ""),
                    ): str,
                    vol.Required(
                        CONF_PASSWORD,
                        default=self._config_entry.data.get(CONF_PASSWORD, ""),
                    ): str,
                    vol.Required(
                        CONF_REGION,
                        default=self._config_entry.data.get(
                            CONF_REGION,
                            DEFAULT_REGION,
                        ),
                    ): vol.In(SUPPORTED_REGIONS),
                    vol.Required(
                        CONF_CREATE_DASHBOARD,
                        default=self._config_entry.options.get(
                            CONF_CREATE_DASHBOARD,
                            DEFAULT_CREATE_DASHBOARD,
                        ),
                    ): bool,
                    vol.Required(
                        CONF_DEBUG_ENTITIES,
                        default=self._config_entry.options.get(
                            CONF_DEBUG_ENTITIES,
                            DEFAULT_DEBUG_ENTITIES,
                        ),
                    ): bool,
                    vol.Required(
                        CONF_DEBUG_LOGGING,
                        default=self._config_entry.options.get(
                            CONF_DEBUG_LOGGING,
                            DEFAULT_DEBUG_LOGGING,
                        ),
                    ): bool,
                    vol.Required(
                        CONF_FORCE_SHORT_TOKEN_LIFETIME,
                        default=self._config_entry.options.get(
                            CONF_FORCE_SHORT_TOKEN_LIFETIME,
                            DEFAULT_FORCE_SHORT_TOKEN_LIFETIME,
                        ),
                    ): bool,
                }
            ),
        )
