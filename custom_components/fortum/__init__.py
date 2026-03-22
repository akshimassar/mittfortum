"""The Fortum integration."""

from __future__ import annotations

import logging
from inspect import isawaitable, signature
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any

from homeassistant.components import frontend as ha_frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import (
    CONF_REQUIRE_ADMIN,
    CONF_RESOURCE_TYPE_WS,
    CONF_SHOW_IN_SIDEBAR,
    CONF_TITLE,
    CONF_URL_PATH,
    LOVELACE_DATA,
    MODE_STORAGE,
)
from homeassistant.components.lovelace.const import (
    DOMAIN as LOVELACE_DOMAIN,
)
from homeassistant.components.lovelace.dashboard import (
    DashboardsCollection,
    LovelaceStorage,
)
from homeassistant.components.lovelace.resources import ResourceStorageCollection
from homeassistant.const import (
    CONF_ICON,
    CONF_MODE,
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
    CONF_CREATE_DASHBOARD,
    CONF_DEBUG_LOGGING,
    CONF_FORCE_SHORT_TOKEN_LIFETIME,
    CONF_REGION,
    DEFAULT_CREATE_DASHBOARD,
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
_DASHBOARD_URL_PATH = "fortum-energy"
_DASHBOARD_TITLE = "Fortum"
_DASHBOARD_ICON = "mdi:transmission-tower"
_DASHBOARD_STRATEGY_TYPE = "custom:fortum-energy"
_DASHBOARD_STATIC_REGISTERED_KEY = f"{DOMAIN}_dashboard_static_registered"
_DASHBOARD_RESOURCE_REGISTERED_KEY = f"{DOMAIN}_dashboard_resource_registered"
_DASHBOARD_CREATE_REGISTERED_KEY = f"{DOMAIN}_dashboard_create_registered"


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
        if entry.options.get(CONF_CREATE_DASHBOARD, DEFAULT_CREATE_DASHBOARD):
            _schedule_dashboard_strategy_dashboard_creation(hass)

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


def _schedule_dashboard_strategy_dashboard_creation(hass: HomeAssistant) -> None:
    """Schedule automatic creation of Fortum strategy dashboard."""
    if hass.data.get(_DASHBOARD_CREATE_REGISTERED_KEY):
        return

    hass.data[_DASHBOARD_CREATE_REGISTERED_KEY] = True

    async def _async_create_dashboard(_hass: HomeAssistant, _component: str) -> None:
        await _async_ensure_dashboard_strategy_dashboard(hass)

    async def _async_create_dashboard_from_event(_event: Any | None = None) -> None:
        await _async_ensure_dashboard_strategy_dashboard(hass)

    config = getattr(hass, "config", None)
    if config is None or not hasattr(config, "components"):
        _LOGGER.debug(
            "Home Assistant config.components unavailable; "
            "falling back to startup event for dashboard creation"
        )
        if hass.is_running:
            hass.async_create_task(_async_create_dashboard_from_event())
        else:
            hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                _async_create_dashboard_from_event,
            )
        return

    async_when_setup(hass, "lovelace", _async_create_dashboard)


async def _async_ensure_dashboard_strategy_dashboard(hass: HomeAssistant) -> None:
    """Ensure a Fortum strategy dashboard exists in storage mode."""
    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None:
        _LOGGER.debug("Lovelace not loaded; skipping automatic dashboard creation")
        return

    if _DASHBOARD_URL_PATH in lovelace_data.dashboards:
        _LOGGER.debug(
            "Fortum dashboard already exists at /%s; skipping auto-creation",
            _DASHBOARD_URL_PATH,
        )
        return

    if _DASHBOARD_URL_PATH in lovelace_data.yaml_dashboards:
        _LOGGER.info(
            "Fortum dashboard URL /%s already configured in YAML; leaving untouched",
            _DASHBOARD_URL_PATH,
        )
        return

    dashboards_collection = DashboardsCollection(hass)
    await dashboards_collection.async_load()
    for item in dashboards_collection.async_items():
        if item.get(CONF_URL_PATH) != _DASHBOARD_URL_PATH:
            continue

        _LOGGER.debug(
            "Fortum dashboard entry for /%s already exists; skipping auto-creation",
            _DASHBOARD_URL_PATH,
        )
        return

    created_dashboard = await dashboards_collection.async_create_item(
        {
            CONF_URL_PATH: _DASHBOARD_URL_PATH,
            CONF_TITLE: _DASHBOARD_TITLE,
            CONF_ICON: _DASHBOARD_ICON,
            CONF_SHOW_IN_SIDEBAR: True,
            CONF_REQUIRE_ADMIN: False,
            CONF_MODE: MODE_STORAGE,
        }
    )

    dashboard_config = LovelaceStorage(hass, created_dashboard)
    await dashboard_config.async_save({"strategy": {"type": _DASHBOARD_STRATEGY_TYPE}})
    _register_created_dashboard_runtime(hass, lovelace_data, created_dashboard)
    _LOGGER.info(
        "Created Fortum dashboard at /%s using strategy '%s'",
        _DASHBOARD_URL_PATH,
        _DASHBOARD_STRATEGY_TYPE,
    )


def _register_created_dashboard_runtime(
    hass: HomeAssistant,
    lovelace_data: Any,
    dashboard_item: dict[str, Any],
) -> None:
    """Register a created storage dashboard in Lovelace runtime state."""
    url_path = dashboard_item[CONF_URL_PATH]
    existing = lovelace_data.dashboards.get(url_path)
    update = existing is not None

    if not update:
        lovelace_data.dashboards[url_path] = LovelaceStorage(hass, dashboard_item)
    else:
        existing.config = dashboard_item

    panel_kwargs: dict[str, Any] = {
        "frontend_url_path": url_path,
        "require_admin": dashboard_item[CONF_REQUIRE_ADMIN],
        "sidebar_title": dashboard_item[CONF_TITLE],
        "sidebar_icon": dashboard_item.get(CONF_ICON, "mdi:view-dashboard"),
        "config": {"mode": MODE_STORAGE},
        "update": update,
    }
    show_in_sidebar = dashboard_item[CONF_SHOW_IN_SIDEBAR]
    panel_signature = signature(ha_frontend.async_register_built_in_panel)
    if "show_in_sidebar" in panel_signature.parameters:
        panel_kwargs["show_in_sidebar"] = show_in_sidebar
    elif "sidebar_default_visible" in panel_signature.parameters:
        panel_kwargs["sidebar_default_visible"] = show_in_sidebar

    ha_frontend.async_register_built_in_panel(
        hass,
        LOVELACE_DOMAIN,
        **panel_kwargs,
    )


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
