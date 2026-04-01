"""Unit tests for Fortum button entities."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.fortum.button import (
    FortumClearStatisticsButton,
    FortumForceRecreateMultipointDashboardButton,
    FortumForceRecreateSingleDashboardButton,
    _build_multipoint_dashboard_strategy_config,
    _build_single_dashboard_strategy_config,
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


async def test_clear_statistics_button_triggers_clear() -> None:
    """Button press should clear imported statistics."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {}
    coordinator.async_clear_statistics = AsyncMock(return_value=3)

    button = FortumClearStatisticsButton(coordinator, _mock_device(), Mock())
    with (
        patch("custom_components.fortum.button.pause_all_sync_schedules") as mock_pause,
        patch(
            "custom_components.fortum.button.resume_all_sync_schedules"
        ) as mock_resume,
    ):
        await button.async_press()

    mock_pause.assert_called_once_with(coordinator.hass)
    mock_resume.assert_called_once_with(coordinator.hass)
    coordinator.async_clear_statistics.assert_awaited_once_with()


async def test_clear_statistics_button_surfaces_api_errors() -> None:
    """Button press should raise HomeAssistantError when clear fails."""
    coordinator = Mock()
    coordinator.last_update_success = True
    coordinator.data = []
    coordinator.hass = Mock()
    coordinator.hass.data = {}
    coordinator.async_clear_statistics = AsyncMock(side_effect=APIError("boom"))

    button = FortumClearStatisticsButton(coordinator, _mock_device(), Mock())

    with (
        patch("custom_components.fortum.button.pause_all_sync_schedules") as mock_pause,
        patch(
            "custom_components.fortum.button.resume_all_sync_schedules"
        ) as mock_resume,
        pytest.raises(HomeAssistantError, match="Clear statistics failed"),
    ):
        await button.async_press()

    mock_pause.assert_called_once_with(coordinator.hass)
    mock_resume.assert_called_once_with(coordinator.hass)


def test_buttons_available_with_authenticated_session() -> None:
    """Debug buttons should be available with valid session/auth."""
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
                            metering_points=(),
                        )
                    )
                )
            }
        }
    }
    entry = Mock(entry_id="entry_1")

    clear_stats = FortumClearStatisticsButton(coordinator, _mock_device(), entry)

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

    clear_stats = FortumClearStatisticsButton(coordinator, _mock_device(), entry)

    assert clear_stats.available is False


def test_build_multipoint_dashboard_strategy_config_defaults_name_to_address() -> None:
    """Multipoint config should default name to address and include itemization."""
    config = _build_multipoint_dashboard_strategy_config(
        [
            MeteringPoint(
                metering_point_no="7000222",
                address="Street 2, City",
            ),
            MeteringPoint(
                metering_point_no="6094111",
                address=None,
            ),
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
    config = _build_multipoint_dashboard_strategy_config(
        [
            MeteringPoint(
                metering_point_no="6094111",
                address="Street 1, City",
            )
        ]
    )

    strategy = config["strategy"]
    assert "version" not in strategy
    point = strategy["metering_points"][0]
    assert set(point.keys()) == {"number", "name", "itemization"}


def test_build_single_dashboard_strategy_config_requires_single_metering_point() -> (
    None
):
    """Single dashboard config requires exactly one metering point."""
    with pytest.raises(
        HomeAssistantError,
        match="Multiple metering points found",
    ):
        _build_single_dashboard_strategy_config(
            [
                MeteringPoint(metering_point_no="111"),
                MeteringPoint(metering_point_no="222"),
            ]
        )


def test_build_single_dashboard_strategy_config_builds_expected_payload() -> None:
    """Single dashboard config should include explicit metering point override."""
    config = _build_single_dashboard_strategy_config(
        [MeteringPoint(metering_point_no="6094111")]
    )
    assert config == {
        "strategy": {
            "type": "custom:fortum-energy-single",
            "metering_point": {
                "number": "6094111",
            },
        }
    }


async def test_force_recreate_multipoint_dashboard_button_recreates_dashboard() -> None:
    """Button press should force-write multipoint strategy dashboard config."""
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
    button = FortumForceRecreateMultipointDashboardButton(
        coordinator, _mock_device(), entry
    )

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
                    }
                ],
            }
        },
    )


async def test_force_recreate_multipoint_dashboard_button_requires_metering_points():
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
    button = FortumForceRecreateMultipointDashboardButton(
        coordinator, _mock_device(), entry
    )

    with pytest.raises(
        HomeAssistantError,
        match="No metering points found for dashboard generation",
    ):
        await button.async_press()


async def test_force_recreate_single_dashboard_button_recreates_dashboard() -> None:
    """Single button press should force-write single strategy dashboard config."""
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
    button = FortumForceRecreateSingleDashboardButton(
        coordinator, _mock_device(), entry
    )

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


async def test_force_recreate_single_dashboard_button_requires_single_point() -> None:
    """Single button press should fail when multiple metering points exist."""
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
                                MeteringPoint(metering_point_no="111"),
                                MeteringPoint(metering_point_no="222"),
                            ),
                        )
                    ),
                )
            }
        }
    }
    entry = Mock(entry_id="entry_1")
    button = FortumForceRecreateSingleDashboardButton(
        coordinator, _mock_device(), entry
    )

    with pytest.raises(
        HomeAssistantError,
        match="Multiple metering points found",
    ):
        await button.async_press()
