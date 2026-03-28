"""Session state manager for Fortum integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import SESSION_REFRESH_INTERVAL
from .exceptions import APIError, InvalidResponseError
from .models import CustomerDetails, MeteringPoint
from .sensors.metering_point import MeteringPointEntityManager
from .sensors.price import PriceAreaEntityManager
from .sensors.stats_last_sync import StaticEntityManager

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .api import FortumAPIClient
    from .coordinators.hourly_consumption import HourlyConsumptionSyncCoordinator
    from .coordinators.spot_price import SpotPriceSyncCoordinator
    from .device import FortumDevice

_LOGGER = logging.getLogger(__name__)

STATE_WAITING_FOR_SETUP = "waiting_for_setup"
STATE_RUNNING = "running"
STATE_STOPPED = "stopped"


@dataclass(frozen=True)
class SessionSnapshot:
    """Parsed session snapshot used by integration runtime."""

    customer_id: str | None
    customer_details: CustomerDetails | None
    metering_points: tuple[MeteringPoint, ...]
    price_areas: tuple[str, ...]
    updated_at_utc: datetime


@dataclass
class RuntimeEntityManagers:
    """Runtime state for sensor-platform entity managers."""

    metering_points: MeteringPointEntityManager
    price_areas: PriceAreaEntityManager
    static_entities: StaticEntityManager


class SessionManager:
    """Manage parsed Fortum session state and refresh scheduling."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        api_client: FortumAPIClient,
        *,
        refresh_interval: timedelta = SESSION_REFRESH_INTERVAL,
    ) -> None:
        """Initialize session manager."""
        self._hass = hass
        self._entry_id = entry_id
        self._api_client = api_client
        self._refresh_interval = refresh_interval

        self._snapshot: SessionSnapshot | None = None
        self._setup_waiting_payload: dict[str, Any] | None = None
        self._state = STATE_WAITING_FOR_SETUP
        self._lock = asyncio.Lock()
        self._enabled = False
        self._refresh_handle: asyncio.TimerHandle | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._sensor_platform: RuntimeEntityManagers | None = None

    def start(self) -> None:
        """Start periodic session refresh scheduling."""
        self._enabled = True
        _LOGGER.debug("session manager started entry_id=%s", self._entry_id)
        self._schedule_next_refresh()

    async def stop(self) -> None:
        """Stop periodic session refresh scheduling and active task."""
        _LOGGER.debug("session manager stopping entry_id=%s", self._entry_id)
        self._state = STATE_STOPPED
        self._enabled = False
        self._cancel_next_refresh()
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        self._refresh_task = None
        _LOGGER.debug("session manager stopped entry_id=%s", self._entry_id)

    def get_snapshot(self) -> SessionSnapshot | None:
        """Return latest parsed session snapshot."""
        return self._snapshot

    async def async_setup_sensor_platform(
        self,
        async_add_entities: AddEntitiesCallback,
        *,
        coordinator: HourlyConsumptionSyncCoordinator,
        price_coordinator: SpotPriceSyncCoordinator,
        device: FortumDevice,
        region: str,
        create_current_month_sensors: bool,
    ) -> None:
        """Register sensor platform runtime and add initial entities."""
        if self._state == STATE_STOPPED:
            raise InvalidResponseError("Session manager is stopped")
        if self._state == STATE_RUNNING:
            raise InvalidResponseError("Sensor platform already initialized")
        if self._setup_waiting_payload is None:
            raise InvalidResponseError(
                "Session snapshot missing during sensor platform setup"
            )

        runtime = RuntimeEntityManagers(
            metering_points=MeteringPointEntityManager(
                async_add_entities,
                coordinator,
                device,
                region,
                create_current_month_sensors,
                (),
            ),
            price_areas=PriceAreaEntityManager(
                async_add_entities,
                price_coordinator,
                device,
                region,
                (),
            ),
            static_entities=StaticEntityManager(
                async_add_entities,
                coordinator,
                device,
            ),
        )
        self._sensor_platform = runtime

        payload = self._setup_waiting_payload
        self._setup_waiting_payload = None
        self._state = STATE_RUNNING
        await self.async_update_from_payload(payload, source="setup")

    async def async_update_from_payload(
        self, payload: dict[str, Any], source: str
    ) -> None:
        """Parse and store session payload, then reschedule refresh."""
        if self._state == STATE_STOPPED:
            _LOGGER.info(
                "ignoring session update for stopped manager entry_id=%s source=%s",
                self._entry_id,
                source,
            )
            return

        if self._state == STATE_WAITING_FOR_SETUP:
            if self._setup_waiting_payload is not None:
                raise InvalidResponseError(
                    "Received additional session payload before sensor platform setup"
                )
            _LOGGER.debug(
                "buffered initial session payload before sensor setup "
                "entry_id=%s source=%s",
                self._entry_id,
                source,
            )
            self._setup_waiting_payload = payload
            return

        async with self._lock:
            snapshot = self._parse_session_snapshot(payload)
            self._snapshot = snapshot

        runtime = self._sensor_platform
        if runtime is not None:
            runtime.metering_points.refresh_all(snapshot.metering_points)
            runtime.price_areas.refresh_all(snapshot.price_areas)
            _LOGGER.debug("session update applied to live entities source=%s", source)
        else:
            _LOGGER.debug(
                "session update stored without sensor runtime entry_id=%s source=%s",
                self._entry_id,
                source,
            )

        self._schedule_next_refresh()

    async def _async_refresh_from_api(self) -> None:
        """Refresh session from API and update parsed snapshot."""
        _LOGGER.debug("scheduled session refresh started entry_id=%s", self._entry_id)
        try:
            payload = await self._api_client.get_session_payload()
        except (APIError, InvalidResponseError) as exc:
            _LOGGER.warning("session refresh failed for %s: %s", self._entry_id, exc)
            self._schedule_next_refresh()
            return

        await self.async_update_from_payload(payload, source="scheduled")

    def _schedule_next_refresh(self) -> None:
        """Schedule next session refresh at configured interval."""
        if not self._enabled:
            _LOGGER.debug(
                "skipping session refresh scheduling because manager disabled "
                "entry_id=%s",
                self._entry_id,
            )
            return

        self._cancel_next_refresh()
        loop = getattr(self._hass, "loop", None) or asyncio.get_running_loop()
        self._refresh_handle = loop.call_later(
            self._refresh_interval.total_seconds(),
            self._run_refresh_if_enabled,
        )
        _LOGGER.debug(
            "scheduled next session refresh entry_id=%s in_seconds=%.0f",
            self._entry_id,
            self._refresh_interval.total_seconds(),
        )

    def _cancel_next_refresh(self) -> None:
        """Cancel pending session refresh callback."""
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None

    def _run_refresh_if_enabled(self) -> None:
        """Run scheduled refresh when manager is enabled and idle."""
        self._refresh_handle = None
        if not self._enabled:
            _LOGGER.debug(
                "ignoring scheduled refresh because manager disabled entry_id=%s",
                self._entry_id,
            )
            return
        if self._refresh_task and not self._refresh_task.done():
            _LOGGER.warning(
                "ignoring scheduled refresh because task already running entry_id=%s",
                self._entry_id,
            )
            return

        async def _refresh() -> None:
            try:
                await self._async_refresh_from_api()
            finally:
                self._refresh_task = None

        self._refresh_task = self._hass.async_create_task(_refresh())

    @classmethod
    def _parse_session_snapshot(cls, payload: dict[str, Any]) -> SessionSnapshot:
        """Parse raw Fortum session payload into typed snapshot."""
        user_data = payload.get("user") if isinstance(payload, dict) else None
        if not isinstance(user_data, dict):
            raise InvalidResponseError("Session payload missing user object")

        delivery_sites = user_data.get("deliverySites")
        points: list[MeteringPoint] = []
        areas: list[str] = []
        if isinstance(delivery_sites, list):
            for index, site in enumerate(delivery_sites):
                if not isinstance(site, dict):
                    _LOGGER.warning(
                        "skipping invalid delivery site index=%s: expected object",
                        index,
                    )
                    continue
                try:
                    point = MeteringPoint.from_api_response(site)
                except (TypeError, ValueError) as exc:
                    _LOGGER.warning(
                        "skipping invalid delivery site index=%s: %s",
                        index,
                        exc,
                    )
                    continue
                points.append(point)

                price_area = point.price_area
                if price_area and price_area not in areas:
                    areas.append(price_area)

                nested_area = cls._extract_nested_price_area(site)
                if nested_area and nested_area not in areas:
                    areas.append(nested_area)

        customer_id_raw = user_data.get("customerId")
        customer_id = str(customer_id_raw) if customer_id_raw else None
        customer_details: CustomerDetails | None = None
        try:
            customer_details = CustomerDetails.from_api_response(payload)
        except (KeyError, TypeError, ValueError):
            customer_details = None

        return SessionSnapshot(
            customer_id=customer_id,
            customer_details=customer_details,
            metering_points=tuple(points),
            price_areas=tuple(areas),
            updated_at_utc=dt_util.utcnow(),
        )

    @staticmethod
    def _extract_nested_price_area(site: dict[str, Any]) -> str | None:
        """Extract fallback nested price-area value from delivery-site payload."""
        consumption = site.get("consumption")
        if not isinstance(consumption, dict):
            return None

        raw = consumption.get("priceArea")
        if not isinstance(raw, str) or not raw.strip():
            return None
        return raw.strip().upper()
