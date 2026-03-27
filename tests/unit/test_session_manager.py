"""Unit tests for SessionManager."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.fortum.exceptions import APIError
from custom_components.fortum.session_manager import SessionManager


def _session_payload(*metering_point_numbers: str) -> dict:
    """Build a minimal Fortum session payload."""
    return {
        "user": {
            "customerId": "55650898",
            "deliverySites": [
                {
                    "consumption": {
                        "meteringPointNo": number,
                        "priceArea": "NO1",
                    }
                }
                for number in metering_point_numbers
            ],
        }
    }


@pytest.mark.asyncio
async def test_update_from_payload_parses_snapshot_and_reschedules(mock_hass) -> None:
    """Session update should parse data and schedule next refresh."""
    api_client = Mock()
    manager = SessionManager(mock_hass, "entry-id", api_client)
    manager.start()

    await manager.async_update_from_payload(_session_payload("6094111"), source="setup")

    snapshot = manager.get_snapshot()
    assert snapshot is not None
    assert snapshot.customer_id == "55650898"
    assert [point.metering_point_no for point in snapshot.metering_points] == [
        "6094111"
    ]
    assert list(snapshot.price_areas) == ["NO1"]
    assert manager._refresh_handle is not None  # noqa: SLF001

    await manager.stop()


@pytest.mark.asyncio
async def test_update_with_changes_triggers_reload_each_time(mock_hass) -> None:
    """Session update should reload entry on every semantic session change."""
    mock_hass.config_entries.async_reload = AsyncMock(return_value=True)
    api_client = Mock()
    manager = SessionManager(mock_hass, "entry-id", api_client)
    manager.start()

    await manager.async_update_from_payload(_session_payload("6094111"), source="setup")
    await manager.async_update_from_payload(
        _session_payload("6094111", "6094222"),
        source="scheduled",
    )
    await manager.async_update_from_payload(
        _session_payload("6094111", "6094222", "6094333"),
        source="scheduled",
    )

    await asyncio.sleep(0)

    assert mock_hass.config_entries.async_reload.await_count == 2
    mock_hass.config_entries.async_reload.assert_awaited_with("entry-id")

    await manager.stop()


@pytest.mark.asyncio
async def test_update_ignores_delivery_site_ordering(mock_hass) -> None:
    """Session update should not reload when only delivery-site order changes."""
    mock_hass.config_entries.async_reload = AsyncMock(return_value=True)
    api_client = Mock()
    manager = SessionManager(mock_hass, "entry-id", api_client)
    manager.start()

    await manager.async_update_from_payload(
        _session_payload("6094111", "6094222"),
        source="setup",
    )
    await manager.async_update_from_payload(
        _session_payload("6094222", "6094111"),
        source="scheduled",
    )

    await asyncio.sleep(0)

    assert mock_hass.config_entries.async_reload.await_count == 0

    await manager.stop()


@pytest.mark.asyncio
async def test_refresh_from_api_failure_keeps_previous_snapshot(mock_hass) -> None:
    """Refresh failure should preserve snapshot and still reschedule."""
    api_client = Mock()
    api_client.get_session_payload = AsyncMock(side_effect=APIError("boom"))
    manager = SessionManager(mock_hass, "entry-id", api_client)
    manager.start()
    await manager.async_update_from_payload(_session_payload("6094111"), source="setup")
    previous_snapshot = manager.get_snapshot()

    await manager._async_refresh_from_api()  # noqa: SLF001

    assert manager.get_snapshot() == previous_snapshot
    assert manager._refresh_handle is not None  # noqa: SLF001

    await manager.stop()
