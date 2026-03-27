"""Live end-to-end tests against Fortum endpoints.

These tests are opt-in and never run unless explicitly enabled with environment
variables. They are intended for manual validation while adapting API behavior
between regions.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client

from custom_components.fortum import async_setup_entry
from custom_components.fortum.api import FortumAPIClient, OAuth2AuthClient
from custom_components.fortum.const import (
    CONF_REGION,
    DOMAIN,
    HOURLY_DATA_REQUEST_TIMEOUT_SECONDS,
)
from custom_components.fortum.session_manager import SessionManager

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class E2ESettings:
    """Runtime settings for live E2E tests."""

    username: str
    password: str
    region: str


_DOTENV_LOADED = False


def _load_local_dotenv() -> None:
    """Load variables from .env in repository root if present."""
    global _DOTENV_LOADED

    if _DOTENV_LOADED:
        return

    dotenv_path = Path(__file__).resolve().parents[2] / ".env"
    if not dotenv_path.exists():
        _DOTENV_LOADED = True
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value

    _DOTENV_LOADED = True


def _is_enabled() -> bool:
    _load_local_dotenv()
    return os.getenv("FORTUM_E2E", "0") == "1"


def _is_historical_enabled() -> bool:
    _load_local_dotenv()
    return os.getenv("FORTUM_E2E_HISTORICAL", "0") == "1"


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@pytest.fixture
def e2e_settings() -> E2ESettings:
    """Load and validate E2E settings from environment variables."""
    if not _is_enabled():
        pytest.skip("Set FORTUM_E2E=1 to run live E2E tests")

    return E2ESettings(
        username=_required_env("FORTUM_USERNAME"),
        password=_required_env("FORTUM_PASSWORD"),
        region=os.getenv("FORTUM_REGION", "se").strip().lower() or "se",
    )


@pytest.fixture
def live_hass() -> HomeAssistant:
    """Create a lightweight Home Assistant mock for httpx client helpers."""
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {}
    hass.bus = MagicMock()
    hass.bus.async_listen_once = MagicMock()
    return hass


def _format_error(stage: str, exc: Exception) -> str:
    message = str(exc).replace("\n", " ").strip()
    return f"{stage} failed with {type(exc).__name__}: {message}"


def _is_sensitive_key(key: str) -> bool:
    key_lower = key.lower()
    sensitive_terms = (
        "token",
        "password",
        "cookie",
        "secret",
        "authorization",
        "idtoken",
        "accesstoken",
        "refreshtoken",
    )
    return any(term in key_lower for term in sensitive_terms)


def _safe_preview(value: object, max_len: int = 120) -> str:
    text = str(value).replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


def _collect_marker_paths(
    payload: object,
    *,
    marker_terms: tuple[str, ...],
    max_results: int = 20,
) -> list[str]:
    """Collect key paths whose names include marker terms."""
    matches: list[str] = []

    def _visit(value: object, path: str) -> None:
        if len(matches) >= max_results:
            return

        if isinstance(value, dict):
            for key, nested in value.items():
                key_lower = key.lower()
                next_path = f"{path}.{key}" if path else key
                if any(term in key_lower for term in marker_terms):
                    matches.append(next_path)
                _visit(nested, next_path)
            return

        if isinstance(value, list):
            for index, nested in enumerate(value[:5]):
                next_path = f"{path}[{index}]"
                _visit(nested, next_path)

    _visit(payload, "")
    return matches


async def _probe_auth_endpoints(hass: HomeAssistant, region: str) -> dict[str, object]:
    """Probe key auth endpoints for diagnostics (no credentials)."""
    from custom_components.fortum.api.endpoints import APIEndpoints

    endpoint_sets: dict[str, APIEndpoints] = {
        "configured": APIEndpoints.for_region(region),
        "se_fallback": APIEndpoints.for_region("se"),
    }

    probes: dict[str, object] = {}
    async with get_async_client(hass) as client:
        for label, endpoints in endpoint_sets.items():
            urls = {
                "providers": endpoints.providers,
                "csrf": endpoints.csrf,
                "signin": endpoints.signin,
                "callback": endpoints.callback_url,
                "session": endpoints.session,
            }

            results: dict[str, object] = {}
            for name, url in urls.items():
                try:
                    response = await client.get(url)
                    results[name] = {
                        "status": response.status_code,
                        "final_url": str(response.url),
                    }
                except Exception as exc:  # pragma: no cover - live diagnostics path
                    results[name] = {
                        "error": f"{type(exc).__name__}: {_safe_preview(exc, 180)}"
                    }

            probes[label] = results

    return probes


def _summarize_json(value: object, depth: int = 2) -> object:
    if depth < 0:
        return "<max-depth>"

    if isinstance(value, dict):
        output: dict[str, object] = {}
        for key, item in value.items():
            if _is_sensitive_key(key):
                output[key] = "<redacted>"
                continue
            output[key] = _summarize_json(item, depth - 1)
        return output

    if isinstance(value, list):
        if not value:
            return []
        sample = [_summarize_json(item, depth - 1) for item in value[:2]]
        if len(value) > 2:
            sample.append(f"<... {len(value) - 2} more items>")
        return sample

    if isinstance(value, str):
        return f"<str len={len(value)}>"

    return value


async def _session_debug_snapshot(api_client: FortumAPIClient) -> dict[str, object]:
    """Get sanitized diagnostic snapshot of the raw session endpoint."""
    session_url = api_client._endpoints.session
    snapshot: dict[str, object] = {"session_url": session_url}
    try:
        response = await api_client._get(session_url)
        snapshot["status_code"] = getattr(response, "status_code", "unknown")
        raw_json = response.json()
        snapshot["top_level_keys"] = (
            list(raw_json.keys()) if isinstance(raw_json, dict) else []
        )
        snapshot["sanitized_shape"] = _summarize_json(raw_json, depth=2)
    except Exception as exc:  # pragma: no cover - live diagnostics path
        snapshot["error"] = _format_error("Raw session probe", exc)
    return snapshot


async def _authenticate_with_session_manager(
    live_hass: HomeAssistant,
    e2e_settings: E2ESettings,
) -> tuple[OAuth2AuthClient, FortumAPIClient, SessionManager]:
    """Authenticate and hydrate a SessionManager snapshot for E2E flows."""
    auth_client = OAuth2AuthClient(
        hass=live_hass,
        username=e2e_settings.username,
        password=e2e_settings.password,
        region=e2e_settings.region,
    )
    api_client = FortumAPIClient(live_hass, auth_client)
    session_manager = SessionManager(live_hass, "e2e-entry", api_client)
    auth_client.set_session_update_callback(session_manager.async_update_from_payload)
    await auth_client.authenticate()

    def _async_add_entities(new_entities, update_before_add=False):
        return None

    device = MagicMock()
    device.unique_id = "e2e-device"
    device.device_info = {"identifiers": {("fortum", "e2e-device")}}

    await session_manager.async_setup_sensor_platform(
        _async_add_entities,
        coordinator=MagicMock(),
        price_coordinator=MagicMock(),
        device=device,
        region=e2e_settings.region,
    )

    return auth_client, api_client, session_manager


@pytest.mark.e2e
async def test_live_auth_and_data_flow(
    live_hass: HomeAssistant, e2e_settings: E2ESettings
):
    """Validate login, session discovery, and a real data fetch."""
    auth_client, api_client, session_manager = await _authenticate_with_session_manager(
        live_hass,
        e2e_settings,
    )

    _LOGGER.info("Starting live E2E flow for region=%s", e2e_settings.region)

    snapshot = session_manager.get_snapshot()
    assert snapshot is not None, "Session snapshot missing after login"
    assert snapshot.customer_id, "Session snapshot missing customer_id"

    session_payload = await api_client.get_session_payload()
    auth_session_keys = (
        list(session_payload.keys()) if isinstance(session_payload, dict) else []
    )
    auth_user_keys = []
    if isinstance(session_payload.get("user"), dict):
        auth_user_keys = list(session_payload["user"].keys())
    _LOGGER.info(
        "Session keys after authenticate: top=%s user=%s",
        auth_session_keys,
        auth_user_keys,
    )

    user_marker_paths = _collect_marker_paths(
        session_payload.get("user"),
        marker_terms=("earliest", "oldest", "availablefrom", "available_from"),
    )
    _LOGGER.info(
        "Session user marker keys for earliest availability: %s",
        user_marker_paths if user_marker_paths else "none",
    )

    try:
        metering_points = await api_client.get_metering_points()
    except Exception as exc:  # pragma: no cover - live diagnostics path
        session_snapshot = await _session_debug_snapshot(api_client)
        pytest.fail(
            _format_error("Metering point discovery", exc)
            + f" | session_snapshot={_safe_preview(session_snapshot, max_len=2000)}"
        )

    if not metering_points:
        session_snapshot = await _session_debug_snapshot(api_client)
        pytest.fail(
            "No metering points returned for account. "
            f"auth_session_keys={auth_session_keys} auth_user_keys={auth_user_keys} "
            f"session_snapshot={_safe_preview(session_snapshot, max_len=3000)}"
        )

    from_date = datetime.now() - timedelta(days=14)
    to_date = datetime.now()
    target_metering_points = [metering_points[0].metering_point_no]

    _LOGGER.info(
        "Metering points discovered=%d first=%s date_range=%s..%s",
        len(metering_points),
        target_metering_points[0],
        from_date.isoformat(),
        to_date.isoformat(),
    )

    try:
        time_series = await api_client.get_time_series_data(
            metering_point_nos=target_metering_points,
            from_date=from_date,
            to_date=to_date,
            resolution="DAY",
        )
    except Exception as exc:  # pragma: no cover - live diagnostics path
        session_snapshot = await _session_debug_snapshot(api_client)
        pytest.fail(
            _format_error("Time series fetch", exc)
            + f" | metering_point={target_metering_points[0]} "
            + f"session_snapshot={_safe_preview(session_snapshot, max_len=2000)}"
        )

    assert isinstance(time_series, list), (
        f"Time series response was not a list (type={type(time_series).__name__})"
    )

    _LOGGER.info("Time series records fetched=%d", len(time_series))

    if time_series:
        earliest_value = getattr(time_series[0], "earliest_available_at_utc", None)
        _LOGGER.info(
            "Parsed TimeSeries earliest_available_at_utc: %s",
            earliest_value.isoformat() if earliest_value is not None else "none",
        )

    hourly_from_date = datetime.now() - timedelta(days=14)
    hourly_to_date = datetime.now()

    _LOGGER.info(
        "Starting hourly 14-day fetch for metering_point=%s range=%s..%s",
        target_metering_points[0],
        hourly_from_date.isoformat(),
        hourly_to_date.isoformat(),
    )

    hourly_started = perf_counter()
    try:
        hourly_series = await api_client.get_time_series_data(
            metering_point_nos=target_metering_points,
            from_date=hourly_from_date,
            to_date=hourly_to_date,
            resolution="HOUR",
            series_type="CONSUMPTION",
            request_timeout=HOURLY_DATA_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # pragma: no cover - live diagnostics path
        elapsed = perf_counter() - hourly_started
        session_snapshot = await _session_debug_snapshot(api_client)
        pytest.fail(
            _format_error("Hourly 14-day time series fetch", exc)
            + f" | metering_point={target_metering_points[0]}"
            + f" elapsed_seconds={elapsed:.3f}"
            + f" session_snapshot={_safe_preview(session_snapshot, max_len=2000)}"
        )

    hourly_elapsed = perf_counter() - hourly_started
    _LOGGER.info(
        "Hourly 14-day fetch completed in %.3fs records=%d",
        hourly_elapsed,
        len(hourly_series),
    )

    assert isinstance(hourly_series, list), (
        f"Hourly time series response was not a list "
        f"(type={type(hourly_series).__name__})"
    )


@pytest.mark.e2e
async def test_live_fetch_hourly_from_session_earliest_marker(
    live_hass: HomeAssistant, e2e_settings: E2ESettings
):
    """Use session-derived earliest marker as start for an hourly fetch."""
    if not _is_historical_enabled():
        pytest.skip(
            "Set FORTUM_E2E_HISTORICAL=1 to run session-earliest hourly fetch probe"
        )

    _, api_client, _ = await _authenticate_with_session_manager(
        live_hass,
        e2e_settings,
    )

    metering_points = await api_client.get_metering_points()
    if not metering_points:
        pytest.fail("No metering points available for session-earliest fetch probe")

    selected_metering_point = next(
        (
            point
            for point in metering_points
            if point.earliest_hourly_available_at_utc is not None
        ),
        None,
    )
    if selected_metering_point is None:
        pytest.fail(
            "No metering point exposed earliest_hourly_available_at_utc in session data"
        )

    from_date = selected_metering_point.earliest_hourly_available_at_utc
    assert from_date is not None

    request_window_days = int(os.getenv("HISTORICAL_E2E_CHUNK_DAYS", "14"))
    if request_window_days < 1:
        pytest.fail(
            f"HISTORICAL_E2E_CHUNK_DAYS must be >= 1 (received {request_window_days})"
        )

    now = datetime.now(tz=from_date.tzinfo)
    to_date = min(now, from_date + timedelta(days=request_window_days))
    if to_date <= from_date:
        pytest.fail(
            "Computed invalid date range from session earliest marker: "
            f"from={from_date.isoformat()} to={to_date.isoformat()}"
        )

    _LOGGER.info(
        "Session-earliest hourly fetch metering_point=%s earliest=%s range=%s..%s",
        selected_metering_point.metering_point_no,
        from_date.isoformat(),
        from_date.isoformat(),
        to_date.isoformat(),
    )

    started = perf_counter()
    try:
        hourly_series = await api_client.get_time_series_data(
            metering_point_nos=[selected_metering_point.metering_point_no],
            from_date=from_date,
            to_date=to_date,
            resolution="HOUR",
            series_type="CONSUMPTION",
            request_timeout=HOURLY_DATA_REQUEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:  # pragma: no cover - live diagnostics path
        elapsed = perf_counter() - started
        session_snapshot = await _session_debug_snapshot(api_client)
        pytest.fail(
            _format_error("Session-earliest hourly fetch", exc)
            + f" | metering_point={selected_metering_point.metering_point_no}"
            + f" from={from_date.isoformat()} to={to_date.isoformat()}"
            + f" elapsed_seconds={elapsed:.3f}"
            + f" session_snapshot={_safe_preview(session_snapshot, max_len=2000)}"
        )

    elapsed = perf_counter() - started
    _LOGGER.info(
        "Session-earliest hourly fetch completed in %.3fs series_count=%d",
        elapsed,
        len(hourly_series),
    )

    assert isinstance(hourly_series, list), (
        "Session-earliest hourly fetch did not return list "
        f"(type={type(hourly_series).__name__})"
    )


@pytest.mark.e2e
async def test_live_integration_setup_under_five_seconds(
    live_hass: HomeAssistant, e2e_settings: E2ESettings
):
    """Measure setup path duration (auth-only + async post-setup scheduling)."""
    live_hass.data.setdefault(DOMAIN, {})
    live_hass.config_entries = MagicMock()
    live_hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)

    def _schedule_task(target, name=None, eager_start=True):
        del name, eager_start
        return asyncio.create_task(target)

    live_hass.async_create_task = _schedule_task

    entry = MagicMock()
    entry.entry_id = "e2e_startup"
    entry.data = {
        CONF_USERNAME: e2e_settings.username,
        CONF_PASSWORD: e2e_settings.password,
        CONF_REGION: e2e_settings.region,
    }
    entry.options = {}
    entry.add_update_listener = MagicMock(return_value=MagicMock())
    entry.async_on_unload = MagicMock()

    started = perf_counter()
    with (
        patch(
            "custom_components.fortum.HourlyConsumptionSyncCoordinator"
        ) as mock_coord,
        patch("custom_components.fortum.SpotPriceSyncCoordinator") as mock_price_coord,
    ):
        mock_coord.return_value = AsyncMock()
        mock_price_coord.return_value = AsyncMock()
        result = await async_setup_entry(live_hass, entry)
    elapsed = perf_counter() - started

    assert result is True, "Integration setup did not complete successfully"
    assert elapsed < 5.0, (
        "Integration setup exceeded startup target: "
        f"elapsed_seconds={elapsed:.3f} (target<5.000)"
    )
