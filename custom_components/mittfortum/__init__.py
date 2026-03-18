"""The MittFortum integration."""

from __future__ import annotations

import logging
from time import monotonic
from typing import TYPE_CHECKING, Any

from homeassistant.const import (
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STARTED,
)
from homeassistant.core import callback

from .api import FortumAPIClient, OAuth2AuthClient

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
from .const import (
    CONF_DEBUG_LOGGING,
    CONF_FORCE_SHORT_TOKEN_LIFETIME,
    CONF_REGION,
    DEFAULT_DEBUG_LOGGING,
    DEFAULT_FORCE_SHORT_TOKEN_LIFETIME,
    DEFAULT_REGION,
    DOMAIN,
    PLATFORMS,
)
from .device import MittFortumDevice
from .exceptions import AuthenticationError, MittFortumError
from .models import MeteringPoint
from .schedulers import (
    HourlyConsumptionSyncScheduler,
    SpotPriceSyncScheduler,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up MittFortum from a config entry."""
    setup_started = monotonic()
    hass.data.setdefault(DOMAIN, {})
    _apply_debug_logging(entry)
    _LOGGER.debug("Starting MittFortum setup for entry_id=%s", entry.entry_id)

    # Get credentials from config entry
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    region = entry.data.get(CONF_REGION, DEFAULT_REGION)

    try:
        # Initialize authentication client
        auth_client = OAuth2AuthClient(
            hass=hass,
            username=username,
            password=password,
            region=region,
            force_short_token_lifetime=entry.options.get(
                CONF_FORCE_SHORT_TOKEN_LIFETIME,
                DEFAULT_FORCE_SHORT_TOKEN_LIFETIME,
            ),
        )

        # Perform initial authentication
        await auth_client.authenticate()
        _LOGGER.debug("Authentication completed for entry_id=%s", entry.entry_id)

        # Create API client
        api_client = FortumAPIClient(hass, auth_client)

        # Extract metering points from authenticated session payload.
        metering_points = _extract_metering_points_from_session(
            auth_client.session_data
        )

        # Get customer ID for device creation from already-authenticated session.
        customer_id = _extract_customer_id_from_session(
            auth_client.session_data,
            username,
        )
        device = MittFortumDevice(customer_id)

        # Create data coordinator
        coordinator = HourlyConsumptionSyncScheduler(hass, api_client)
        price_coordinator = SpotPriceSyncScheduler(hass, api_client)

        # Store coordinator and device for platforms
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
            "price_coordinator": price_coordinator,
            "device": device,
            "api_client": api_client,
            "metering_points": metering_points,
        }

        entry.async_on_unload(entry.add_update_listener(async_reload_entry))

        # Forward setup to platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("Platform setup completed for entry_id=%s", entry.entry_id)

        # Perform all data retrieval asynchronously after HA startup completes.
        _schedule_post_setup_refreshes(hass, entry, coordinator, price_coordinator)

        _LOGGER.debug(
            "MittFortum setup finished for entry_id=%s in %.2fs",
            entry.entry_id,
            monotonic() - setup_started,
        )

    except AuthenticationError:
        _LOGGER.exception("Authentication failed for MittFortum")
        _LOGGER.debug(
            "MittFortum setup failed for entry_id=%s after %.2fs",
            entry.entry_id,
            monotonic() - setup_started,
        )
        return False
    except MittFortumError:
        _LOGGER.exception("Setup failed for MittFortum")
        _LOGGER.debug(
            "MittFortum setup failed for entry_id=%s after %.2fs",
            entry.entry_id,
            monotonic() - setup_started,
        )
        return False
    except Exception:
        _LOGGER.exception("Unexpected error setting up MittFortum")
        _LOGGER.debug(
            "MittFortum setup failed for entry_id=%s after %.2fs",
            entry.entry_id,
            monotonic() - setup_started,
        )
        return False
    else:
        return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the config entry."""
    await hass.config_entries.async_reload(entry.entry_id)


def _apply_debug_logging(entry: ConfigEntry) -> None:
    """Apply integration logger level from options."""
    debug_enabled = entry.options.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING)
    logger = logging.getLogger(f"custom_components.{DOMAIN}")
    logger.setLevel(logging.DEBUG if debug_enabled else logging.INFO)


def _extract_metering_points_from_session(
    session_data: dict | None,
) -> list[MeteringPoint]:
    """Extract metering points from already-authenticated session payload."""
    if not session_data:
        return []

    user_data = session_data.get("user")
    if not isinstance(user_data, dict):
        return []

    delivery_sites = user_data.get("deliverySites")
    if not isinstance(delivery_sites, list):
        return []

    points: list[MeteringPoint] = []
    for site in delivery_sites:
        if not isinstance(site, dict):
            continue
        try:
            points.append(MeteringPoint.from_api_response(site))
        except (TypeError, ValueError):
            continue

    return points


def _extract_customer_id_from_session(
    session_data: dict[str, Any] | None,
    fallback: str,
) -> str:
    """Extract customer ID from authenticated session data."""
    if session_data:
        user_data = session_data.get("user")
        if isinstance(user_data, dict):
            customer_id = user_data.get("customerId")
            if isinstance(customer_id, str) and customer_id.strip():
                return customer_id

    _LOGGER.warning(
        "Could not extract customerId from session data, using username fallback"
    )
    return fallback


async def _async_post_setup_refreshes(
    entry: ConfigEntry,
    coordinator: HourlyConsumptionSyncScheduler,
    price_coordinator: SpotPriceSyncScheduler,
) -> None:
    """Run data refreshes asynchronously after integration setup returns."""
    try:
        await coordinator.async_refresh()
        _LOGGER.debug(
            "_async_post_setup_refreshes: initial consumption refresh completed "
            "for config_entry_id=%s",
            entry.entry_id,
        )

        await price_coordinator.async_refresh()
        _LOGGER.debug(
            "_async_post_setup_refreshes: initial price refresh completed "
            "for config_entry_id=%s",
            entry.entry_id,
        )
    except Exception:
        _LOGGER.exception(
            "Async post-setup refresh failed for entry_id=%s",
            entry.entry_id,
        )


def _schedule_post_setup_refreshes(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HourlyConsumptionSyncScheduler,
    price_coordinator: SpotPriceSyncScheduler,
) -> None:
    """Schedule post-setup refreshes after HA startup completes."""

    if hass.is_running:
        hass.async_create_task(
            _async_post_setup_refreshes(entry, coordinator, price_coordinator)
        )
        _LOGGER.debug(
            "Scheduled async post-setup refresh immediately for entry_id=%s",
            entry.entry_id,
        )
        return

    unsub: Any | None = None

    @callback
    def _on_started(_event: Any) -> None:
        nonlocal unsub
        # Listener is one-shot; mark it consumed so unload does not try to
        # remove an already-fired listener.
        unsub = None
        hass.async_create_task(
            _async_post_setup_refreshes(entry, coordinator, price_coordinator)
        )
        _LOGGER.debug(
            "Scheduled async post-setup refresh after HA started for entry_id=%s",
            entry.entry_id,
        )

    unsub = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _on_started)

    def _unsubscribe_listener() -> None:
        nonlocal unsub
        if unsub is not None:
            unsub()
            unsub = None

    entry.async_on_unload(_unsubscribe_listener)
