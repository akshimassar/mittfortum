"""The Fortum integration."""

from __future__ import annotations

import logging
import re
from inspect import isawaitable, signature
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components import frontend as ha_frontend
from homeassistant.components.energy.data import async_get_manager
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
from .device import FortumDevice
from .exceptions import AuthenticationError, FortumError
from .log_capture import ensure_diagnostics_log_capture, remove_diagnostics_log_capture
from .logging_utils import ensure_function_name_log_prefix
from .migrations import (
    async_migrate_unique_ids_to_entry_id,
    async_remove_legacy_spot_price_entities,
)
from .session_manager import SessionManager

_LOGGER = logging.getLogger(__name__)
ensure_function_name_log_prefix()

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
    ensure_diagnostics_log_capture(hass)
    _apply_debug_logging(entry)
    _LOGGER.debug("starting integration setup for entry_id=%s", entry.entry_id)

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

        # Create API client
        api_client = FortumAPIClient(hass, auth_client)

        session_manager = SessionManager(hass, entry.entry_id, api_client)
        provider_result: Any = api_client.set_session_snapshot_provider(
            session_manager.get_snapshot
        )
        if isawaitable(provider_result):
            await provider_result

        callback_result: Any = auth_client.set_session_update_callback(
            session_manager.async_update_from_payload
        )
        if isawaitable(callback_result):
            await callback_result

        # Perform initial authentication and let callback hydrate SessionManager.
        await auth_client.authenticate()
        _LOGGER.debug("authentication completed entry_id=%s", entry.entry_id)

        session_manager.start()

        # Extract metering points from parsed session snapshot.
        snapshot = session_manager.get_snapshot()

        await async_migrate_unique_ids_to_entry_id(
            hass,
            entry,
            customer_id=snapshot.customer_id if snapshot else None,
            username=username,
        )
        await async_remove_legacy_spot_price_entities(hass, entry)
        device = FortumDevice(entry.entry_id)

        # Create data coordinator
        coordinator = HourlyConsumptionSyncCoordinator(hass, api_client)
        price_coordinator = SpotPriceSyncCoordinator(hass, api_client)

        # Store coordinator and device for platforms
        hass.data[DOMAIN][entry.entry_id] = {
            "coordinator": coordinator,
            "price_coordinator": price_coordinator,
            "device": device,
            "api_client": api_client,
            "session_manager": session_manager,
        }

        entry.async_on_unload(entry.add_update_listener(async_reload_entry))

        # Forward setup to platforms
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _LOGGER.debug("platform setup completed entry_id=%s", entry.entry_id)

        # Perform all data retrieval asynchronously after HA startup completes.
        _schedule_post_setup_refreshes(hass, entry, coordinator, price_coordinator)
        await _async_register_dashboard_strategy_static_path(hass)
        _schedule_dashboard_strategy_resource_registration(hass)
        if entry.options.get(CONF_CREATE_DASHBOARD, DEFAULT_CREATE_DASHBOARD):
            _schedule_dashboard_strategy_dashboard_creation(hass, entry.entry_id)

        _LOGGER.debug(
            "setup completed entry_id=%s in %.2fs",
            entry.entry_id,
            monotonic() - setup_started,
        )

    except AuthenticationError:
        _LOGGER.exception(
            "setup authentication failed entry_id=%s after %.2fs",
            entry.entry_id,
            monotonic() - setup_started,
        )
        return False
    except FortumError:
        _LOGGER.exception(
            "setup failed entry_id=%s after %.2fs",
            entry.entry_id,
            monotonic() - setup_started,
        )
        return False
    except Exception:
        _LOGGER.exception(
            "setup failed with unexpected error entry_id=%s after %.2fs",
            entry.entry_id,
            monotonic() - setup_started,
        )
        return False
    else:
        return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        api_client = (
            entry_data.get("api_client") if isinstance(entry_data, dict) else None
        )
        auth_client = getattr(api_client, "_auth_client", None)  # noqa: SLF001
        session_manager = (
            entry_data.get("session_manager") if isinstance(entry_data, dict) else None
        )
        stop_session_manager = getattr(session_manager, "stop", None)
        if callable(stop_session_manager):
            stop_result = stop_session_manager()
            if isawaitable(stop_result):
                await stop_result

        stop_monitor = getattr(auth_client, "stop_token_renewal_scheduler", None)
        if callable(stop_monitor):
            stop_result = stop_monitor()
            if isawaitable(stop_result):
                await stop_result

        if not hass.data[DOMAIN]:
            remove_diagnostics_log_capture(hass)

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

        _LOGGER.debug("sync scheduling paused for entry_id=%s", key)


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

        _LOGGER.debug("sync scheduling resumed for entry_id=%s", key)


def resume_all_sync_schedules(hass: HomeAssistant) -> None:
    """Public helper to resume all Fortum polling schedules."""
    _resume_all_sync_schedules(hass)


def _apply_debug_logging(entry: ConfigEntry) -> None:
    """Apply integration logger level from options."""
    debug_enabled = entry.options.get(CONF_DEBUG_LOGGING, DEFAULT_DEBUG_LOGGING)
    logger = logging.getLogger(f"custom_components.{DOMAIN}")
    logger.setLevel(logging.DEBUG if debug_enabled else logging.INFO)


async def _async_post_setup_refreshes(
    entry: ConfigEntry,
    coordinator: HourlyConsumptionSyncCoordinator,
    price_coordinator: SpotPriceSyncCoordinator,
) -> None:
    """Run data refreshes asynchronously after integration setup returns."""
    try:
        await coordinator.async_refresh()
        _LOGGER.debug(
            "initial consumption refresh done entry_id=%s",
            entry.entry_id,
        )

        await price_coordinator.async_refresh()
        _LOGGER.debug(
            "initial price refresh done entry_id=%s",
            entry.entry_id,
        )
    except Exception:
        _LOGGER.exception(
            "post-setup refresh failed entry_id=%s",
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
            "scheduled post-setup refresh now for entry_id=%s",
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
            "scheduled post-setup refresh after HA start for entry_id=%s",
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
        _LOGGER.warning("dashboard strategy file missing at %s", strategy_path)
        return

    if (http_component := getattr(hass, "http", None)) is None:
        _LOGGER.debug(
            "http component unavailable; skipping strategy static path registration"
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
        "registered dashboard strategy static path %s", _DASHBOARD_STRATEGY_URL
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
            "home assistant config.components unavailable; "
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


def _schedule_dashboard_strategy_dashboard_creation(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    """Schedule automatic creation of Fortum strategy dashboard."""
    if hass.data.get(_DASHBOARD_CREATE_REGISTERED_KEY):
        return

    hass.data[_DASHBOARD_CREATE_REGISTERED_KEY] = True

    async def _async_create_dashboard(_hass: HomeAssistant, _component: str) -> None:
        created = await _async_ensure_dashboard_strategy_dashboard(hass)
        if created:
            await _async_bootstrap_energy_preferences(hass, entry_id)

    config = getattr(hass, "config", None)
    if config is None or not hasattr(config, "components"):
        _LOGGER.warning(
            "home assistant config.components unavailable; "
            "skipping dashboard creation on this start"
        )
        return

    async_when_setup(hass, "lovelace", _async_create_dashboard)


async def _async_ensure_dashboard_strategy_dashboard(hass: HomeAssistant) -> bool:
    """Ensure a Fortum strategy dashboard exists in storage mode."""
    lovelace_data = hass.data.get(LOVELACE_DATA)
    if lovelace_data is None:
        _LOGGER.debug("lovelace not loaded; skipping automatic dashboard creation")
        return False

    if _DASHBOARD_URL_PATH in lovelace_data.dashboards:
        _LOGGER.debug(
            "dashboard already exists at /%s; skipping auto-creation",
            _DASHBOARD_URL_PATH,
        )
        return False

    if _DASHBOARD_URL_PATH in lovelace_data.yaml_dashboards:
        _LOGGER.info(
            "dashboard /%s already configured in YAML; leaving untouched",
            _DASHBOARD_URL_PATH,
        )
        return False

    dashboards_collection = DashboardsCollection(hass)
    await dashboards_collection.async_load()
    for item in dashboards_collection.async_items():
        if item.get(CONF_URL_PATH) != _DASHBOARD_URL_PATH:
            continue

        _LOGGER.debug(
            "dashboard entry for /%s already exists; skipping auto-creation",
            _DASHBOARD_URL_PATH,
        )
        return False

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
        "created dashboard at /%s using strategy '%s'",
        _DASHBOARD_URL_PATH,
        _DASHBOARD_STRATEGY_TYPE,
    )
    return True


def _build_hourly_statistic_id(stat_type: str, metering_point_no: str) -> str:
    """Build stable statistic id for a metering point and stat type."""
    suffix = re.sub(r"[^0-9a-z_]", "_", metering_point_no.lower()).strip("_")
    if not suffix:
        suffix = "unknown"
    return f"{DOMAIN}:hourly_{stat_type}_{suffix}"


async def _async_bootstrap_energy_preferences(
    hass: HomeAssistant,
    entry_id: str,
) -> None:
    """Add Fortum energy sources if user has no energy sources configured."""
    manager = await async_get_manager(hass)
    energy_sources = list((manager.data or {}).get("energy_sources", []))
    if energy_sources:
        _LOGGER.debug("energy sources already configured; skipping bootstrap")
        return

    entry_data = hass.data.get(DOMAIN, {}).get(entry_id)
    if not isinstance(entry_data, dict):
        return

    session_manager = entry_data.get("session_manager")
    snapshot = session_manager.get_snapshot() if session_manager is not None else None
    metering_points = list(snapshot.metering_points) if snapshot else []
    if not metering_points:
        _LOGGER.debug("no metering points available; skipping energy bootstrap")
        return

    fortum_stat_pairs: list[tuple[str, str]] = []
    for point in metering_points:
        metering_point_no = point.metering_point_no
        if not metering_point_no:
            continue

        fortum_stat_pairs.append(
            (
                _build_hourly_statistic_id("consumption", metering_point_no),
                _build_hourly_statistic_id("cost", metering_point_no),
            )
        )

    if not fortum_stat_pairs:
        return

    schema_mode = _energy_bootstrap_schema_mode()
    if schema_mode is None:
        _LOGGER.info("skipping energy bootstrap on unsupported HA version")
        return

    if schema_mode == "legacy":
        flow_from = [
            {
                "stat_energy_from": stat_energy_from,
                "stat_cost": stat_cost,
                "entity_energy_price": None,
                "number_energy_price": None,
            }
            for stat_energy_from, stat_cost in fortum_stat_pairs
        ]
        fortum_sources: list[dict[str, Any]] = [
            {
                "type": "grid",
                "flow_from": flow_from,
                "flow_to": [],
                "cost_adjustment_day": 0.0,
            }
        ]
    else:
        fortum_sources = [
            {
                "type": "grid",
                "stat_energy_from": stat_energy_from,
                "stat_energy_to": None,
                "stat_cost": stat_cost,
                "entity_energy_price": None,
                "number_energy_price": None,
                "stat_compensation": None,
                "entity_energy_price_export": None,
                "number_energy_price_export": None,
                "cost_adjustment_day": 0.0,
            }
            for stat_energy_from, stat_cost in fortum_stat_pairs
        ]

    await manager.async_update(cast(Any, {"energy_sources": fortum_sources}))
    _LOGGER.info(
        "added %d source(s) to energy preferences",
        len(fortum_stat_pairs),
    )


def _energy_bootstrap_schema_mode() -> str | None:
    """Return Energy source schema mode for the running Home Assistant version."""
    try:
        from homeassistant.const import __version__ as ha_version  # noqa: PLC0415
    except Exception:
        return None

    match = re.match(r"^(\d+)\.(\d+)", ha_version)
    if not match:
        return None

    major = int(match.group(1))
    minor = int(match.group(2))

    if major != 2026:
        return None

    if minor in {1, 2}:
        return "legacy"

    if minor == 3:
        return "unified"

    return None


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
        _LOGGER.debug("lovelace not loaded; skipping automatic resource registration")
        return

    resources = lovelace_data.resources
    if not isinstance(resources, ResourceStorageCollection):
        _LOGGER.info(
            "lovelace resources use YAML mode; add manual resource URL=%s type=module",
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
        "added lovelace resource for dashboard strategy at %s",
        _DASHBOARD_STRATEGY_URL,
    )
