"""Unit tests for Fortum button entities."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.fortum.button import (
    FortumBackfillHistoricalGapsButton,
    FortumForceRecreateDashboardButton,
    FortumResyncHistoricalStatsButton,
)
from custom_components.fortum.dashboard_strategy import (
    build_multipoint_dashboard_strategy_config,
    build_single_dashboard_strategy_config,
)
from custom_components.fortum.device import FortumDevice
from custom_components.fortum.exceptions import APIError
from custom_components.fortum.models import MeteringPoint


def _mock_device() -> Mock:
    device = Mock(spec=FortumDevice)
    device.device_info = {
        "identifiers": {("fortum", "123456")},
        "name": "Fortum Energy Meter",
        "manufacturer": "Fortum",
        "model": "Energy Meter",
    }
    return device


async def test_resync_historical_stats_button_triggers_resync() -> None:
    """Button press should trigger full historical re-sync."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {}
    coordinator.async_resync_historical_stats = AsyncMock(return_value=24)

    button = FortumResyncHistoricalStatsButton(coordinator, _mock_device(), Mock())
    with (
        patch("custom_components.fortum.button.pause_all_sync_schedules") as mock_pause,
        patch(
            "custom_components.fortum.button.resume_all_sync_schedules"
        ) as mock_resume,
    ):
        await button.async_press()

    mock_pause.assert_called_once_with(coordinator.hass)
    mock_resume.assert_called_once_with(coordinator.hass)
    coordinator.async_resync_historical_stats.assert_awaited_once_with()


async def test_resync_historical_stats_button_surfaces_api_errors() -> None:
    """Button press should raise HomeAssistantError when re-sync fails."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {}
    coordinator.async_resync_historical_stats = AsyncMock(side_effect=APIError("boom"))

    button = FortumResyncHistoricalStatsButton(coordinator, _mock_device(), Mock())

    with (
        patch("custom_components.fortum.button.pause_all_sync_schedules") as mock_pause,
        patch(
            "custom_components.fortum.button.resume_all_sync_schedules"
        ) as mock_resume,
        pytest.raises(HomeAssistantError, match="Historical re-sync failed"),
    ):
        await button.async_press()

    mock_pause.assert_called_once_with(coordinator.hass)
    mock_resume.assert_called_once_with(coordinator.hass)


async def test_backfill_historical_gaps_button_triggers_backfill() -> None:
    """Button press should trigger manual historical gap backfill."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {}
    coordinator.async_backfill_historical_gaps = AsyncMock(return_value=12)

    button = FortumBackfillHistoricalGapsButton(coordinator, _mock_device(), Mock())
    with (
        patch("custom_components.fortum.button.pause_all_sync_schedules") as mock_pause,
        patch(
            "custom_components.fortum.button.resume_all_sync_schedules"
        ) as mock_resume,
    ):
        await button.async_press()

    mock_pause.assert_called_once_with(coordinator.hass)
    mock_resume.assert_called_once_with(coordinator.hass)
    coordinator.async_backfill_historical_gaps.assert_awaited_once_with()


async def test_backfill_historical_gaps_button_surfaces_api_errors() -> None:
    """Button press should raise HomeAssistantError when backfill fails."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {}
    coordinator.async_backfill_historical_gaps = AsyncMock(side_effect=APIError("boom"))

    button = FortumBackfillHistoricalGapsButton(coordinator, _mock_device(), Mock())

    with (
        patch("custom_components.fortum.button.pause_all_sync_schedules") as mock_pause,
        patch(
            "custom_components.fortum.button.resume_all_sync_schedules"
        ) as mock_resume,
        pytest.raises(HomeAssistantError, match="Historical gap backfill failed"),
    ):
        await button.async_press()

    mock_pause.assert_called_once_with(coordinator.hass)
    mock_resume.assert_called_once_with(coordinator.hass)


def test_buttons_available_with_metering_points() -> None:
    """Debug buttons should be available with metering points."""
    coordinator = Mock()
    coordinator.last_update_success = False
    coordinator.data = None
    coordinator.hass = Mock()
    coordinator.hass.data = {
        "fortum": {
            "entry_1": {
                "session_manager": Mock(
                    get_snapshot=Mock(
                        return_value=Mock(
                            customer_id="123",
                            metering_points=(MeteringPoint(metering_point_no="111"),),
                        )
                    )
                )
            }
        }
    }
    entry = Mock(entry_id="entry_1")

    clear_stats = FortumResyncHistoricalStatsButton(coordinator, _mock_device(), entry)

    assert clear_stats.available is True


def test_buttons_unavailable_without_session_snapshot() -> None:
    """Debug buttons should be unavailable without SessionManager snapshot."""
    coordinator = Mock()
    coordinator.last_update_success = False
    coordinator.data = None
    coordinator.hass = Mock()
    coordinator.hass.data = {
        "fortum": {
            "entry_1": {
                "session_manager": Mock(get_snapshot=Mock(return_value=None)),
            }
        }
    }
    entry = Mock(entry_id="entry_1")

    clear_stats = FortumResyncHistoricalStatsButton(coordinator, _mock_device(), entry)

    assert clear_stats.available is False


def test_debug_buttons_are_hidden_by_default() -> None:
    """Debug buttons should be hidden, not disabled, by default."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {}
    entry = Mock(entry_id="entry_1")

    clear_stats = FortumResyncHistoricalStatsButton(coordinator, _mock_device(), entry)
    backfill = FortumBackfillHistoricalGapsButton(coordinator, _mock_device(), entry)
    recreate_dashboard = FortumForceRecreateDashboardButton(
        coordinator,
        _mock_device(),
        entry,
    )

    assert clear_stats.entity_registry_visible_default is False
    assert backfill.entity_registry_visible_default is False
    assert recreate_dashboard.entity_registry_visible_default is False

    assert clear_stats.entity_registry_enabled_default is True
    assert backfill.entity_registry_enabled_default is True
    assert recreate_dashboard.entity_registry_enabled_default is True


def test_build_multipoint_dashboard_strategy_config_defaults_name_to_address() -> None:
    """Multipoint config should default name to address and include itemization."""
    config = build_multipoint_dashboard_strategy_config(
        [
            {"number": "7000222", "address": "Street 2, City"},
            {"number": "6094111", "address": ""},
        ]
    )

    assert config == {
        "strategy": {
            "type": "custom:fortum-energy-multipoint",
            "metering_points": [
                {
                    "number": "6094111",
                    "name": "6094111",
                    "itemization": [],
                },
                {
                    "number": "7000222",
                    "name": "Street 2, City",
                    "itemization": [],
                },
            ],
        }
    }


def test_build_multipoint_dashboard_strategy_config_uses_canonical_shape() -> None:
    """Generated multipoint config should keep canonical keys only."""
    config = build_multipoint_dashboard_strategy_config(
        [{"number": "6094111", "address": "Street 1, City"}]
    )

    strategy = config["strategy"]
    assert "version" not in strategy
    point = strategy["metering_points"][0]
    assert set(point.keys()) == {"number", "name", "itemization"}


def test_build_single_dashboard_strategy_config_uses_first_sorted_number() -> None:
    """Single dashboard config should use first sorted metering point number."""
    config = build_single_dashboard_strategy_config(
        [
            {"number": "7000222", "address": "Street 2, City"},
            {"number": "6094111", "address": "Street 1, City"},
        ]
    )

    assert config == {
        "strategy": {
            "type": "custom:fortum-energy-single",
            "metering_point": {
                "number": "6094111",
            },
        }
    }


def test_build_single_dashboard_strategy_config_builds_expected_payload() -> None:
    """Single dashboard config should include explicit metering point override."""
    config = build_single_dashboard_strategy_config(
        [{"number": "6094111", "address": "Street 1, City"}]
    )
    assert config == {
        "strategy": {
            "type": "custom:fortum-energy-single",
            "metering_point": {
                "number": "6094111",
            },
        }
    }


async def test_force_recreate_dashboard_button_creates_multipoint_when_many() -> None:
    """Button press should force-write multipoint strategy when many points exist."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {
        "fortum": {
            "entry_1": {
                "session_manager": Mock(
                    get_snapshot=Mock(
                        return_value=Mock(
                            metering_points=(
                                MeteringPoint(
                                    metering_point_no="6094111",
                                    address="Street 1, City",
                                ),
                                MeteringPoint(
                                    metering_point_no="7000222",
                                    address="Street 2, City",
                                ),
                            ),
                        )
                    )
                )
            }
        }
    }
    entry = Mock(entry_id="entry_1")
    button = FortumForceRecreateDashboardButton(coordinator, _mock_device(), entry)

    with (
        patch(
            "custom_components.fortum.button._async_ensure_dashboard_strategy_lovelace_resource",
            AsyncMock(),
        ) as mock_resource,
        patch(
            "custom_components.fortum.button._async_force_recreate_dashboard_strategy_dashboard",
            AsyncMock(),
        ) as mock_recreate,
    ):
        await button.async_press()

    mock_resource.assert_awaited_once_with(coordinator.hass)
    mock_recreate.assert_awaited_once_with(
        coordinator.hass,
        {
            "strategy": {
                "type": "custom:fortum-energy-multipoint",
                "metering_points": [
                    {
                        "number": "6094111",
                        "name": "Street 1, City",
                        "itemization": [],
                    },
                    {
                        "number": "7000222",
                        "name": "Street 2, City",
                        "itemization": [],
                    },
                ],
            }
        },
    )


async def test_force_recreate_dashboard_button_requires_metering_points():
    """Button press should fail when no metering points are available."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {
        "fortum": {
            "entry_1": {
                "session_manager": Mock(
                    get_snapshot=Mock(return_value=Mock(metering_points=())),
                )
            }
        }
    }
    entry = Mock(entry_id="entry_1")
    button = FortumForceRecreateDashboardButton(coordinator, _mock_device(), entry)

    with pytest.raises(
        HomeAssistantError,
        match="No metering points found for dashboard generation",
    ):
        await button.async_press()


async def test_force_recreate_dashboard_button_creates_single_when_one() -> None:
    """Button press should force-write single strategy when one point exists."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {
        "fortum": {
            "entry_1": {
                "session_manager": Mock(
                    get_snapshot=Mock(
                        return_value=Mock(
                            metering_points=(
                                MeteringPoint(
                                    metering_point_no="6094111",
                                    address="Street 1, City",
                                ),
                            ),
                        )
                    )
                )
            }
        }
    }
    entry = Mock(entry_id="entry_1")
    button = FortumForceRecreateDashboardButton(coordinator, _mock_device(), entry)

    with (
        patch(
            "custom_components.fortum.button._async_ensure_dashboard_strategy_lovelace_resource",
            AsyncMock(),
        ) as mock_resource,
        patch(
            "custom_components.fortum.button._async_force_recreate_dashboard_strategy_dashboard",
            AsyncMock(),
        ) as mock_recreate,
    ):
        await button.async_press()

    mock_resource.assert_awaited_once_with(coordinator.hass)
    mock_recreate.assert_awaited_once_with(
        coordinator.hass,
        {
            "strategy": {
                "type": "custom:fortum-energy-single",
                "metering_point": {
                    "number": "6094111",
                },
            }
        },
    )


async def test_force_recreate_dashboard_button_available_across_entries() -> None:
    """Dashboard button should be available when any Fortum entry has points."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {
        "fortum": {
            "entry_1": {
                "session_manager": Mock(get_snapshot=Mock(return_value=None)),
            },
            "entry_2": {
                "session_manager": Mock(
                    get_snapshot=Mock(
                        return_value=Mock(
                            metering_points=(MeteringPoint(metering_point_no="111"),),
                        )
                    )
                ),
            },
        }
    }
    entry = Mock(entry_id="entry_1")
    button = FortumForceRecreateDashboardButton(coordinator, _mock_device(), entry)

    assert button.available is True
