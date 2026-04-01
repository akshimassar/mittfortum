"""Shared dashboard strategy config builders."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _metering_point_sort_key(number: str) -> tuple[int, int | str]:
    """Return stable sort key prioritizing numeric order."""
    if number.isdigit():
        return (0, int(number))
    return (1, number)


def normalize_metering_points(metering_points: list[Any]) -> list[dict[str, str]]:
    """Normalize and sort metering points by number."""
    by_number: dict[str, dict[str, str]] = {}
    for point in metering_points:
        metering_point_no_raw: Any
        address_raw: Any
        if isinstance(point, dict):
            metering_point_no_raw = point.get("number")
            address_raw = point.get("address")
        else:
            metering_point_no_raw = getattr(point, "metering_point_no", None)
            address_raw = getattr(point, "address", None)

        if (
            not isinstance(metering_point_no_raw, str)
            or not metering_point_no_raw.strip()
        ):
            continue

        number = metering_point_no_raw.strip()
        address = address_raw.strip() if isinstance(address_raw, str) else ""
        existing = by_number.get(number)
        if existing is None:
            by_number[number] = {
                "number": number,
                "address": address,
            }
            continue

        if not existing["address"] and address:
            existing["address"] = address

    return sorted(
        by_number.values(), key=lambda point: _metering_point_sort_key(point["number"])
    )


def collect_available_metering_points(hass: HomeAssistant) -> list[dict[str, str]]:
    """Collect normalized metering points from all loaded Fortum entries."""
    domain_data = hass.data.get(DOMAIN, {})
    all_points: list[Any] = []
    for entry_data in domain_data.values():
        if not isinstance(entry_data, dict):
            continue
        session_manager = entry_data.get("session_manager")
        snapshot = (
            session_manager.get_snapshot() if session_manager is not None else None
        )
        if snapshot is None:
            continue
        all_points.extend(snapshot.metering_points)

    return normalize_metering_points(all_points)


def build_single_dashboard_strategy_config(
    metering_points: list[dict[str, str]],
) -> dict[str, Any]:
    """Build strategy config for a single metering point dashboard."""
    normalized_points = normalize_metering_points(cast("list[Any]", metering_points))
    if not normalized_points:
        raise ValueError("No metering points found for dashboard generation")

    return {
        "strategy": {
            "type": "custom:fortum-energy-single",
            "metering_point": {
                "number": normalized_points[0]["number"],
            },
        }
    }


def build_multipoint_dashboard_strategy_config(
    metering_points: list[dict[str, str]],
) -> dict[str, Any]:
    """Build strategy config for a multipoint dashboard."""
    normalized_points = normalize_metering_points(cast("list[Any]", metering_points))
    if not normalized_points:
        raise ValueError("No metering points found for dashboard generation")

    return {
        "strategy": {
            "type": "custom:fortum-energy-multipoint",
            "metering_points": [
                {
                    "number": point["number"],
                    "name": point["address"] or point["number"],
                    "itemization": [],
                }
                for point in normalized_points
            ],
        }
    }


def build_auto_dashboard_strategy_config(
    metering_points: list[dict[str, str]],
) -> dict[str, Any]:
    """Build single or multipoint strategy config based on point count."""
    normalized_points = normalize_metering_points(cast("list[Any]", metering_points))
    if len(normalized_points) == 1:
        return build_single_dashboard_strategy_config(normalized_points)
    return build_multipoint_dashboard_strategy_config(normalized_points)
