"""Manual live auth-init matrix probes across regions.

This test is intentionally opt-in and only runs when both:
- FORTUM_E2E=1
- FORTUM_E2E_AUTH_MATRIX=1
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import urlencode

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client

from custom_components.fortum.api.auth import OAuth2AuthClient
from custom_components.fortum.api.endpoints import APIEndpoints
from custom_components.fortum.const import SUPPORTED_REGIONS

_LOGGER = logging.getLogger(__name__)
_DOTENV_LOADED = False


@dataclass(frozen=True)
class MatrixSettings:
    """Runtime settings for auth matrix probes."""

    username: str
    password: str


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


def _is_matrix_enabled() -> bool:
    _load_local_dotenv()
    return os.getenv("FORTUM_E2E_AUTH_MATRIX", "0") == "1"


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@pytest.fixture
def matrix_settings() -> MatrixSettings:
    """Load and validate matrix settings from environment variables."""
    if not _is_enabled():
        pytest.skip("Set FORTUM_E2E=1 to run live E2E tests")
    if not _is_matrix_enabled():
        pytest.skip("Set FORTUM_E2E_AUTH_MATRIX=1 to run auth-init matrix probes")

    return MatrixSettings(
        username=_required_env("FORTUM_USERNAME"),
        password=_required_env("FORTUM_PASSWORD"),
    )


@pytest.fixture
def live_hass() -> HomeAssistant:
    """Create a lightweight Home Assistant mock for httpx client helpers."""
    hass = MagicMock(spec=HomeAssistant)
    hass.data = {}
    hass.bus = MagicMock()
    hass.bus.async_listen_once = MagicMock()
    return hass


@pytest.mark.e2e
async def test_live_auth_init_matrix_across_regions(
    live_hass: HomeAssistant,
    matrix_settings: MatrixSettings,
):
    """Probe all locale/authIndex auth-init combinations for each region."""
    failures: list[str] = []

    for region in SUPPORTED_REGIONS:
        endpoints = APIEndpoints.for_region(region)

        auth_client = OAuth2AuthClient(
            hass=live_hass,
            username=matrix_settings.username,
            password=matrix_settings.password,
            region=region,
        )

        async with get_async_client(live_hass) as client:
            providers_resp = await client.get(endpoints.providers)
            csrf_resp = await client.get(endpoints.csrf)

            if providers_resp.status_code != 200 or csrf_resp.status_code != 200:
                failures.append(
                    " | ".join(
                        [
                            f"region={region}",
                            f"providers={providers_resp.status_code}",
                            f"csrf={csrf_resp.status_code}",
                            "error=bootstrap_failed",
                        ]
                    )
                )
                continue

            csrf_data = csrf_resp.json()
            csrf_token = csrf_data.get("csrfToken")
            if not csrf_token:
                failures.append(
                    f"region={region} | csrf=200 | error=missing_csrf_token"
                )
                continue

            signin_resp = await client.post(
                endpoints.signin,
                json={
                    "csrfToken": csrf_token,
                    "callbackUrl": endpoints.callback_page,
                    "json": "true",
                },
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
            if signin_resp.status_code != 200:
                failures.append(
                    f"region={region} | signin={signin_resp.status_code} "
                    "| error=signin_failed"
                )
                continue

            signin_data = signin_resp.json()
            oauth_url = signin_data.get("url")
            if not isinstance(oauth_url, str) or not oauth_url:
                failures.append(
                    f"region={region} | signin=200 | error=missing_oauth_url"
                )
                continue

            await client.get(oauth_url, timeout=30.0)

            attempts = auth_client._preferred_sso_attempts(oauth_url)  # noqa: SLF001
            auth_url = (
                "https://sso.fortum.com/am/json/realms/root/realms/alpha/authenticate"
            )

            results: list[str] = []
            ok_count = 0
            for locale, auth_index_value in attempts:
                auth_params = {
                    "locale": locale,
                    "authIndexType": "service",
                    "authIndexValue": auth_index_value,
                    "goto": oauth_url,
                }
                auth_full_url = f"{auth_url}?{urlencode(auth_params)}"
                response = await client.post(
                    auth_full_url,
                    headers={
                        "accept-api-version": "protocol=1.0,resource=2.1",
                        "content-type": "application/json",
                    },
                    json={},
                    timeout=30.0,
                )

                message = ""
                try:
                    payload = response.json()
                    if isinstance(payload, dict):
                        message = str(payload.get("message", "")).strip()
                except ValueError:
                    message = ""

                if response.status_code == 200:
                    ok_count += 1
                results.append(
                    f"{locale}/{auth_index_value}: {response.status_code}"
                    + (f" {message}" if message else "")
                )

            _LOGGER.info("region=%s auth_init_matrix", region)
            for row in results:
                _LOGGER.info("  - %s", row)

            if ok_count == 0:
                failures.append(
                    f"region={region} | error=no_successful_auth_init | "
                    f"results={'; '.join(results)}"
                )

    assert not failures, "Auth-init matrix failures:\n" + "\n".join(failures)
