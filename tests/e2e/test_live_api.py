"""Live end-to-end tests against Fortum endpoints.

These tests are opt-in and never run unless explicitly enabled with environment
variables. They are intended for manual validation while adapting API behavior
between regions.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client

from custom_components.mittfortum.api import FortumAPIClient, OAuth2AuthClient
from custom_components.mittfortum.const import STATISTICS_REQUEST_TIMEOUT_SECONDS

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


async def _probe_auth_endpoints(hass: HomeAssistant, region: str) -> dict[str, object]:
    """Probe key auth endpoints for diagnostics (no credentials)."""
    from custom_components.mittfortum.api.endpoints import APIEndpoints

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


@pytest.mark.e2e
async def test_live_auth_and_data_flow(
    live_hass: HomeAssistant, e2e_settings: E2ESettings
):
    """Validate login, session discovery, and a real data fetch."""
    auth_client = OAuth2AuthClient(
        hass=live_hass,
        username=e2e_settings.username,
        password=e2e_settings.password,
        region=e2e_settings.region,
    )

    api_client = FortumAPIClient(live_hass, auth_client)

    _LOGGER.info("Starting live E2E flow for region=%s", e2e_settings.region)

    try:
        await auth_client.authenticate()
    except Exception as exc:  # pragma: no cover - live diagnostics path
        endpoint_probe = await _probe_auth_endpoints(live_hass, e2e_settings.region)
        pytest.fail(
            _format_error("Authentication", exc)
            + f" | endpoint_probe={_safe_preview(endpoint_probe, max_len=3000)}"
        )

    assert auth_client.session_data is not None, "Session data missing after login"
    assert auth_client.session_data.get("user"), (
        "No user object found in session. "
        f"Session keys: {list(auth_client.session_data.keys())}"
    )

    auth_session_keys = list(auth_client.session_data.keys())
    auth_user_keys = []
    if isinstance(auth_client.session_data.get("user"), dict):
        auth_user_keys = list(auth_client.session_data["user"].keys())
    _LOGGER.info(
        "Session keys after authenticate: top=%s user=%s",
        auth_session_keys,
        auth_user_keys,
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
            request_timeout=STATISTICS_REQUEST_TIMEOUT_SECONDS,
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
