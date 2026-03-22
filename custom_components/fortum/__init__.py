"""The Fortum integration."""

from __future__ import annotations

import logging
from inspect import isawaitable
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any

from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import CONF_RESOURCE_TYPE_WS, LOVELACE_DATA
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.const import (
    CONF_PASSWORD,
    CONF_URL,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STARTED,
)
from homeassistant.core import callback
from homeassistant.setup import async_when_setup

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
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    PLATFORMS,
    PRICE_UPDATE_INTERVAL,
)
from .coordinators import (
    HourlyConsumptionSyncCoordinator,
    SpotPriceSyncCoordinator,
)
from .device import MittFortumDevice
from .exceptions import AuthenticationError, MittFortumError
from .models import MeteringPoint

_LOGGER = logging.getLogger(__name__)

_DASHBOARD_STRATEGY_FILE = "fortum-energy-strategy.js"
_DASHBOARD_STRATEGY_URL = f"/fortum-energy/{_DASHBOARD_STRATEGY_FILE}"
_DASHBOARD_STATIC_REGISTERED_KEY = f"{DOMAIN}_dashboard_static_registered"
_DASHBOARD_RESOURCE_REGISTERED_KEY = f"{DOMAIN}_dashboard_resource_registered"


def _dashboard_strategy_path() -> Path:
    """Return absolute path to dashboard strategy file."""
    return Path(__file__).parent / "frontend" / _DASHBOARD_STRATEGY_FILE


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Fortum from a config entry."""
    setup_started = monotonic()
    hass.data.setdefault(DOMAIN, {})
    _apply_debug_logging(entry)
    _LOGGER.debug("Starting Fortum setup for entry_id=%s", entry.entry_id)

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
        coordinator = HourlyConsumptionSyncCoordinator(hass, api_client)
        price_coordinator = SpotPriceSyncCoordinator(hass, api_client)

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
        await _async_register_dashboard_strategy_static_path(hass)
        _schedule_dashboard_strategy_resource_registration(hass)

        _LOGGER.debug(
            "Fortum setup finished for entry_id=%s in %.2fs",
            entry.entry_id,
            monotonic() - setup_started,
        )

    except AuthenticationError:
        _LOGGER.exception("Authentication failed for Fortum")
        _LOGGER.debug(
            "Fortum setup failed for entry_id=%s after %.2fs",
            entry.entry_id,
            monotonic() - setup_started,
        )
        return False
    except MittFortumError:
        _LOGGER.exception("Setup failed for Fortum")
        _LOGGER.debug(
            "Fortum setup failed for entry_id=%s after %.2fs",
            entry.entry_id,
            monotonic() - setup_started,
        )
        return False
    except Exception:
        _LOGGER.exception("Unexpected error setting up Fortum")
        _LOGGER.debug(
            "Fortum setup failed for entry_id=%s after %.2fs",
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


def _pause_all_sync_schedules(hass: HomeAssistant) -> None:
    """Disable and unschedule all Fortum coordinator polling."""
    domain_data = hass.data.get(DOMAIN, {})
    for key, value in domain_data.items():
        if not isinstance(value, dict):
            continue

        coordinator = value.get("coordinator")
        price_coordinator = value.get("price_coordinator")
        for target in (coordinator, price_coordinator):
            if target is None:
                continue
            target.update_interval = None
            if hasattr(target, "_unschedule_refresh"):
                target._unschedule_refresh()  # noqa: SLF001

        _LOGGER.debug("Paused sync scheduling for entry_id=%s", key)


def pause_all_sync_schedules(hass: HomeAssistant) -> None:
    """Public helper to pause all Fortum polling schedules."""
    _pause_all_sync_schedules(hass)


def _resume_all_sync_schedules(hass: HomeAssistant) -> None:
    """Re-enable and reschedule all Fortum coordinator polling."""
    domain_data = hass.data.get(DOMAIN, {})
    for key, value in domain_data.items():
        if not isinstance(value, dict):
            continue

        coordinator = value.get("coordinator")
        price_coordinator = value.get("price_coordinator")

        if coordinator is not None:
            coordinator.update_interval = DEFAULT_UPDATE_INTERVAL
            if hasattr(coordinator, "_schedule_refresh"):
                coordinator._schedule_refresh()  # noqa: SLF001

        if price_coordinator is not None:
            price_coordinator.update_interval = PRICE_UPDATE_INTERVAL
            if hasattr(price_coordinator, "_schedule_refresh"):
                price_coordinator._schedule_refresh()  # noqa: SLF001

        _LOGGER.debug("Resumed sync scheduling for entry_id=%s", key)


def resume_all_sync_schedules(hass: HomeAssistant) -> None:
    """Public helper to resume all Fortum polling schedules."""
    _resume_all_sync_schedules(hass)


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
    coordinator: HourlyConsumptionSyncCoordinator,
    price_coordinator: SpotPriceSyncCoordinator,
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
    coordinator: HourlyConsumptionSyncCoordinator,
    price_coordinator: SpotPriceSyncCoordinator,
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


async def _async_register_dashboard_strategy_static_path(hass: HomeAssistant) -> None:
    """Register static URL for the dashboard strategy JS file."""
    if hass.data.get(_DASHBOARD_STATIC_REGISTERED_KEY):
        return

    strategy_path = _dashboard_strategy_path()
    if not strategy_path.is_file():
        _LOGGER.warning("Dashboard strategy file is missing at %s", strategy_path)
        return

    if (http_component := getattr(hass, "http", None)) is None:
        _LOGGER.debug(
            "HTTP component unavailable; skipping strategy static registration"
        )
        return

    register_result = http_component.async_register_static_paths(
        [
            StaticPathConfig(
                _DASHBOARD_STRATEGY_URL,
                str(strategy_path),
                cache_headers=False,
            )
        ]
    )
    if isawaitable(register_result):
        await register_result

    hass.data[_DASHBOARD_STATIC_REGISTERED_KEY] = True
    _LOGGER.debug(
        "Registered dashboard strategy static path at %s", _DASHBOARD_STRATEGY_URL
    )


def _schedule_dashboard_strategy_resource_registration(hass: HomeAssistant) -> None:
    """Schedule Lovelace resource registration for dashboard strategy."""
    if hass.data.get(_DASHBOARD_RESOURCE_REGISTERED_KEY):
        return

    hass.data[_DASHBOARD_RESOURCE_REGISTERED_KEY] = True

    async def _async_register_resource(_hass: HomeAssistant, _component: str) -> None:
        await _async_ensure_dashboard_strategy_lovelace_resource(hass)

    async def _async_register_resource_from_event(_event: Any | None = None) -> None:
        await _async_ensure_dashboard_strategy_lovelace_resource(hass)

    config = getattr(hass, "config", None)
    if config is None or not hasattr(config, "components"):
        _LOGGER.debug(
            "Home Assistant config.components unavailable; "
            "falling back to startup event"
        )
        if hass.is_running:
            hass.async_create_task(_async_register_resource_from_event())
        else:
            hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                _async_register_resource_from_event,
            )
        return

    async_when_setup(hass, "lovelace", _async_register_resource)


async def _async_ensure_dashboard_strategy_lovelace_resource(
    hass: HomeAssistant,
) -> None:
    """Ensure strategy JS is present in Lovelace storage resources."""
    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None:
        _LOGGER.debug("Lovelace not loaded; skipping automatic resource registration")
        return

    resources = lovelace_data.resources
    if not isinstance(resources, ResourceStorageCollection):
        _LOGGER.info(
            "Lovelace resources use YAML mode; add manual resource url=%s type=module",
            _DASHBOARD_STRATEGY_URL,
        )
        return

    await resources.async_get_info()

    if any(
        item.get(CONF_URL) == _DASHBOARD_STRATEGY_URL
        for item in resources.async_items()
    ):
        return

    await resources.async_create_item(
        {
            CONF_URL: _DASHBOARD_STRATEGY_URL,
            CONF_RESOURCE_TYPE_WS: "module",
        }
    )
    _LOGGER.info(
        "Added Lovelace resource for Fortum dashboard strategy at %s",
        _DASHBOARD_STRATEGY_URL,
    )
