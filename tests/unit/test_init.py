"""Test __init__.py module."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from custom_components.fortum import (
    _apply_debug_logging,
    _async_bootstrap_energy_preferences,
    _async_ensure_dashboard_strategy_dashboard,
    _async_register_dashboard_strategy_static_path,
    _build_hourly_statistic_id,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.fortum.const import (
    CONF_CREATE_DASHBOARD,
    CONF_DEBUG_LOGGING,
    DOMAIN,
)
from custom_components.fortum.models import MeteringPoint


class TestInit:
    """Test integration setup and teardown."""

    async def test_async_setup_entry_success(self, mock_hass):
        """Test successful setup."""
        entry = AsyncMock(spec=ConfigEntry)
        entry.data = {
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "test_password",
        }
        entry.entry_id = "test_entry_id"
        entry.options = {}
        entry.add_update_listener = Mock(return_value=Mock())
        entry.async_on_unload = Mock()

        mock_hass.data = {DOMAIN: {}}

        with (
            patch("custom_components.fortum.OAuth2AuthClient") as mock_auth,
            patch("custom_components.fortum.FortumAPIClient") as mock_api,
            patch("custom_components.fortum.MittFortumDevice") as mock_device,
            patch(
                "custom_components.fortum.HourlyConsumptionSyncCoordinator"
            ) as mock_coordinator,
            patch(
                "custom_components.fortum.SpotPriceSyncCoordinator"
            ) as mock_price_coordinator,
            patch(
                "custom_components.fortum._schedule_dashboard_strategy_dashboard_creation"
            ) as mock_schedule_dashboard_creation,
        ):
            mock_auth_instance = AsyncMock()
            mock_auth_instance.session_data = {}
            mock_auth.return_value = mock_auth_instance

            mock_api_instance = AsyncMock()
            mock_api_instance.get_customer_id.return_value = "customer_123"
            mock_api.return_value = mock_api_instance

            mock_device_instance = AsyncMock()
            mock_device.return_value = mock_device_instance

            mock_coordinator_instance = AsyncMock()
            mock_coordinator.return_value = mock_coordinator_instance

            mock_price_coordinator_instance = AsyncMock()
            mock_price_coordinator.return_value = mock_price_coordinator_instance

            mock_hass.config_entries.async_forward_entry_setups = AsyncMock(
                return_value=True
            )

            result = await async_setup_entry(mock_hass, entry)

            assert result is True
            assert DOMAIN in mock_hass.data
            assert entry.entry_id in mock_hass.data[DOMAIN]
            mock_schedule_dashboard_creation.assert_not_called()

    async def test_async_setup_entry_schedules_dashboard_creation_when_enabled(
        self,
        mock_hass,
    ):
        """Dashboard creation should be scheduled only when option is enabled."""
        entry = AsyncMock(spec=ConfigEntry)
        entry.data = {
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "test_password",
        }
        entry.entry_id = "test_entry_id"
        entry.options = {CONF_CREATE_DASHBOARD: True}
        entry.add_update_listener = Mock(return_value=Mock())
        entry.async_on_unload = Mock()

        mock_hass.data = {DOMAIN: {}}

        with (
            patch("custom_components.fortum.OAuth2AuthClient") as mock_auth,
            patch("custom_components.fortum.FortumAPIClient") as mock_api,
            patch("custom_components.fortum.MittFortumDevice") as mock_device,
            patch(
                "custom_components.fortum.HourlyConsumptionSyncCoordinator"
            ) as mock_coordinator,
            patch(
                "custom_components.fortum.SpotPriceSyncCoordinator"
            ) as mock_price_coordinator,
            patch(
                "custom_components.fortum._schedule_dashboard_strategy_dashboard_creation"
            ) as mock_schedule_dashboard_creation,
        ):
            mock_auth_instance = AsyncMock()
            mock_auth_instance.session_data = {}
            mock_auth.return_value = mock_auth_instance

            mock_api_instance = AsyncMock()
            mock_api_instance.get_customer_id.return_value = "customer_123"
            mock_api.return_value = mock_api_instance

            mock_device.return_value = AsyncMock()
            mock_coordinator.return_value = AsyncMock()
            mock_price_coordinator.return_value = AsyncMock()
            mock_hass.config_entries.async_forward_entry_setups = AsyncMock(
                return_value=True
            )

            result = await async_setup_entry(mock_hass, entry)

            assert result is True
            mock_schedule_dashboard_creation.assert_called_once_with(
                mock_hass,
                entry.entry_id,
            )

    async def test_async_setup_entry_auth_failure(self, mock_hass):
        """Test setup with authentication failure."""
        entry = AsyncMock(spec=ConfigEntry)
        entry.data = {
            CONF_USERNAME: "test@example.com",
            CONF_PASSWORD: "wrong_password",
        }
        entry.entry_id = "test_entry_id"
        entry.options = {}
        entry.add_update_listener = Mock(return_value=Mock())
        entry.async_on_unload = Mock()

        mock_hass.data = {DOMAIN: {}}

        with patch("custom_components.fortum.OAuth2AuthClient") as mock_auth:
            mock_auth_instance = AsyncMock()
            mock_auth_instance.authenticate.side_effect = Exception("Auth failed")
            mock_auth.return_value = mock_auth_instance

            result = await async_setup_entry(mock_hass, entry)

            assert result is False

    async def test_async_unload_entry_success(self, mock_hass):
        """Test successful unload."""
        entry = AsyncMock(spec=ConfigEntry)
        entry.entry_id = "test_entry_id"

        mock_hass.data = {DOMAIN: {entry.entry_id: {"test": "data"}}}
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

        result = await async_unload_entry(mock_hass, entry)

        assert result is True
        assert entry.entry_id not in mock_hass.data[DOMAIN]

    async def test_async_unload_entry_failure(self, mock_hass):
        """Test unload failure."""
        entry = AsyncMock(spec=ConfigEntry)
        entry.entry_id = "test_entry_id"

        mock_hass.data = {DOMAIN: {entry.entry_id: {"test": "data"}}}
        mock_hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)

        result = await async_unload_entry(mock_hass, entry)

        assert result is False
        assert entry.entry_id in mock_hass.data[DOMAIN]  # Should still be there

    def test_apply_debug_logging_uses_options_toggle(self):
        """Test debug logging level is applied from options."""
        entry = Mock(spec=ConfigEntry)
        entry.options = {CONF_DEBUG_LOGGING: True}

        with patch("custom_components.fortum.logging.getLogger") as mock_get_logger:
            logger = Mock()
            mock_get_logger.return_value = logger

            _apply_debug_logging(entry)

        logger.setLevel.assert_called_once_with(logging.DEBUG)

    async def test_static_strategy_registration_awaits_http(self, mock_hass, tmp_path):
        """Static strategy registration should await HTTP path setup."""
        strategy_path = tmp_path / "fortum-energy-strategy.js"
        strategy_path.write_text("export default {};", encoding="utf-8")

        mock_hass.data = {}
        mock_hass.http = Mock()
        mock_hass.http.async_register_static_paths = AsyncMock()

        with patch(
            "custom_components.fortum._dashboard_strategy_path",
            return_value=strategy_path,
        ):
            await _async_register_dashboard_strategy_static_path(mock_hass)

        mock_hass.http.async_register_static_paths.assert_awaited_once()

    async def test_auto_dashboard_creation_creates_strategy_dashboard(self, mock_hass):
        """Auto dashboard creation should create Fortum strategy dashboard once."""
        strategy_storage = AsyncMock()
        strategy_storage.async_save = AsyncMock()
        runtime_storage = Mock()
        lovelace_data = SimpleNamespace(
            dashboards={},
            yaml_dashboards={},
            resources=Mock(),
        )
        mock_hass.data = {LOVELACE_DATA: lovelace_data}

        with (
            patch("custom_components.fortum.DashboardsCollection") as mock_collection,
            patch(
                "custom_components.fortum.LovelaceStorage",
                side_effect=[strategy_storage, runtime_storage],
            ) as mock_lovelace_storage,
            patch(
                "custom_components.fortum.ha_frontend.async_register_built_in_panel"
            ) as mock_register_panel,
        ):
            collection = mock_collection.return_value
            collection.async_load = AsyncMock()
            collection.async_items.return_value = []
            collection.async_create_item = AsyncMock(
                return_value={
                    "id": "fortum-energy",
                    "url_path": "fortum-energy",
                    "title": "Fortum",
                    "icon": "mdi:transmission-tower",
                    "show_in_sidebar": True,
                    "require_admin": False,
                    "mode": "storage",
                }
            )

            await _async_ensure_dashboard_strategy_dashboard(mock_hass)

        collection.async_create_item.assert_awaited_once()
        mock_lovelace_storage.assert_any_call(
            mock_hass,
            {
                "id": "fortum-energy",
                "url_path": "fortum-energy",
                "title": "Fortum",
                "icon": "mdi:transmission-tower",
                "show_in_sidebar": True,
                "require_admin": False,
                "mode": "storage",
            },
        )
        strategy_storage.async_save.assert_awaited_once_with(
            {"strategy": {"type": "custom:fortum-energy"}}
        )
        assert lovelace_data.dashboards["fortum-energy"] is runtime_storage
        mock_register_panel.assert_called_once()

    async def test_auto_dashboard_creation_skips_existing_dashboard(self, mock_hass):
        """Auto dashboard creation should not touch existing dashboards."""
        lovelace_data = SimpleNamespace(
            dashboards={"fortum-energy": Mock()},
            yaml_dashboards={},
            resources=Mock(),
        )
        mock_hass.data = {LOVELACE_DATA: lovelace_data}

        with patch("custom_components.fortum.DashboardsCollection") as mock_collection:
            await _async_ensure_dashboard_strategy_dashboard(mock_hass)

        mock_collection.assert_not_called()

    async def test_auto_dashboard_creation_skips_existing_storage_dashboard(
        self,
        mock_hass,
    ):
        """Existing storage dashboard should be left untouched."""
        lovelace_data = SimpleNamespace(
            dashboards={},
            yaml_dashboards={},
            resources=Mock(),
        )
        mock_hass.data = {LOVELACE_DATA: lovelace_data}

        with (
            patch("custom_components.fortum.DashboardsCollection") as mock_collection,
            patch(
                "custom_components.fortum.LovelaceStorage",
            ) as mock_lovelace_storage,
            patch(
                "custom_components.fortum.ha_frontend.async_register_built_in_panel"
            ) as mock_register_panel,
        ):
            collection = mock_collection.return_value
            collection.async_load = AsyncMock()
            collection.async_items.return_value = [
                {
                    "id": "fortum-energy",
                    "url_path": "fortum-energy",
                    "title": "Fortum",
                    "icon": "mdi:transmission-tower",
                    "show_in_sidebar": True,
                    "require_admin": False,
                    "mode": "storage",
                }
            ]
            collection.async_create_item = AsyncMock()

            await _async_ensure_dashboard_strategy_dashboard(mock_hass)

        collection.async_create_item.assert_not_awaited()
        mock_lovelace_storage.assert_not_called()
        mock_register_panel.assert_not_called()
        assert "fortum-energy" not in lovelace_data.dashboards

    async def test_energy_bootstrap_runs_only_when_energy_sources_empty(
        self, mock_hass
    ):
        """Bootstrap should add Fortum sources only for empty energy setup."""
        point = MeteringPoint(
            metering_point_no="6094111",
            metering_point_id="id-1",
            address="Test",
        )
        mock_hass.data = {DOMAIN: {"entry": {"metering_points": [point]}}}

        manager = Mock()
        manager.data = {"energy_sources": []}
        manager.async_update = AsyncMock()

        with (
            patch(
                "custom_components.fortum.async_get_manager",
                AsyncMock(return_value=manager),
            ),
            patch(
                "custom_components.fortum._energy_bootstrap_schema_mode",
                return_value="unified",
            ),
        ):
            await _async_bootstrap_energy_preferences(mock_hass, "entry")

        manager.async_update.assert_awaited_once()
        update_payload = manager.async_update.await_args.args[0]
        assert update_payload["energy_sources"][0]["stat_energy_from"] == (
            "fortum:hourly_consumption_6094111"
        )
        assert update_payload["energy_sources"][0]["stat_cost"] == (
            "fortum:hourly_cost_6094111"
        )

    async def test_energy_bootstrap_uses_legacy_flow_schema_when_required(
        self,
        mock_hass,
    ):
        """Bootstrap should use legacy flow_from schema on older HA cores."""
        point = MeteringPoint(
            metering_point_no="6094111",
            metering_point_id="id-1",
            address="Test",
        )
        mock_hass.data = {DOMAIN: {"entry": {"metering_points": [point]}}}

        manager = Mock()
        manager.data = {"energy_sources": []}
        manager.async_update = AsyncMock()

        with (
            patch(
                "custom_components.fortum.async_get_manager",
                AsyncMock(return_value=manager),
            ),
            patch(
                "custom_components.fortum._energy_bootstrap_schema_mode",
                return_value="legacy",
            ),
        ):
            await _async_bootstrap_energy_preferences(mock_hass, "entry")

        manager.async_update.assert_awaited_once()
        update_payload = manager.async_update.await_args.args[0]
        grid_source = update_payload["energy_sources"][0]
        assert grid_source["type"] == "grid"
        assert grid_source["flow_to"] == []
        assert grid_source["flow_from"][0]["stat_energy_from"] == (
            "fortum:hourly_consumption_6094111"
        )
        assert grid_source["flow_from"][0]["stat_cost"] == "fortum:hourly_cost_6094111"

    async def test_energy_bootstrap_skips_when_energy_already_configured(
        self,
        mock_hass,
    ):
        """Bootstrap should not modify existing energy preferences."""
        mock_hass.data = {DOMAIN: {"entry": {"metering_points": []}}}
        manager = Mock()
        manager.data = {"energy_sources": [{"type": "grid"}]}
        manager.async_update = AsyncMock()

        with patch(
            "custom_components.fortum.async_get_manager",
            AsyncMock(return_value=manager),
        ):
            await _async_bootstrap_energy_preferences(mock_hass, "entry")

        manager.async_update.assert_not_awaited()

    def test_build_hourly_statistic_id_sanitizes_metering_point(self):
        """Statistic id builder should sanitize metering point number."""
        assert (
            _build_hourly_statistic_id("cost", "MP-12/34")
            == "fortum:hourly_cost_mp_12_34"
        )
