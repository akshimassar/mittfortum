"""Data models for the MittFortum integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


@dataclass
class EnergyDataPoint:
    """Represents an energy data point."""

    value: float
    type: str

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> EnergyDataPoint:
        """Create instance from API response data."""
        return cls(
            value=float(data["value"]),
            type=data["type"],
        )


@dataclass
class CostDataPoint:
    """Represents a cost data point."""

    total: float
    value: float
    type: str

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> CostDataPoint:
        """Create instance from API response data."""
        return cls(
            total=float(data["total"]),
            value=float(data["value"]),
            type=data["type"],
        )


@dataclass
class Price:
    """Represents price information."""

    total: float
    value: float
    vat_amount: float
    vat_percentage: float

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> Price:
        """Create instance from API response data."""
        return cls(
            total=float(data["total"]),
            value=float(data["value"]),
            vat_amount=float(data["vatAmount"]),
            vat_percentage=float(data["vatPercentage"]),
        )


@dataclass
class TemperatureReading:
    """Represents temperature reading."""

    temperature: float

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> TemperatureReading:
        """Create instance from API response data."""
        return cls(
            temperature=float(data["temperature"]),
        )


@dataclass
class TimeSeriesDataPoint:
    """Represents a time series data point."""

    at_utc: datetime
    energy: list[EnergyDataPoint]
    cost: list[CostDataPoint] | None
    price: Price | None
    temperature_reading: TemperatureReading | None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> TimeSeriesDataPoint:
        """Create instance from API response data."""
        energy_points = [EnergyDataPoint.from_api_response(e) for e in data["energy"]]

        cost_points = None
        if data.get("cost"):
            cost_points = [CostDataPoint.from_api_response(c) for c in data["cost"]]

        price = None
        if data.get("price"):
            price = Price.from_api_response(data["price"])

        temperature = None
        if data.get("temperatureReading"):
            temperature = TemperatureReading.from_api_response(
                data["temperatureReading"]
            )

        return cls(
            at_utc=datetime.fromisoformat(data["atUTC"].replace("Z", "+00:00")),
            energy=energy_points,
            cost=cost_points,
            price=price,
            temperature_reading=temperature,
        )

    @property
    def total_energy(self) -> float:
        """Get total energy value."""
        return sum(point.value for point in self.energy if point.type == "ENERGY")

    @property
    def total_cost(self) -> float:
        """Get total cost value."""
        if not self.cost:
            return 0.0
        return sum(point.total for point in self.cost)


@dataclass
class TimeSeries:
    """Represents time series data."""

    delivery_site_category: str
    measurement_unit: str
    metering_point_no: str
    price_unit: str
    cost_unit: str
    temperature_unit: str
    series: list[TimeSeriesDataPoint]
    earliest_available_at_utc: datetime | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> TimeSeries:
        """Create instance from API response data."""
        series_points = [
            TimeSeriesDataPoint.from_api_response(s) for s in data["series"]
        ]

        earliest_available_at_utc = cls._extract_earliest_available_datetime(data)

        return cls(
            delivery_site_category=data["deliverySiteCategory"],
            measurement_unit=data["measurementUnit"],
            metering_point_no=data["meteringPointNo"],
            price_unit=data["priceUnit"],
            cost_unit=data["costUnit"],
            temperature_unit=data["temperatureUnit"],
            series=series_points,
            earliest_available_at_utc=earliest_available_at_utc,
        )

    @classmethod
    def _extract_earliest_available_datetime(
        cls,
        data: dict[str, Any],
    ) -> datetime | None:
        """Extract API-reported earliest available timestamp when present."""
        candidates: list[datetime] = []

        def _visit(value: Any, key: str = "", parent_key: str = "") -> None:
            key_l = key.lower()
            parent_l = parent_key.lower()

            if isinstance(value, dict):
                for child_key, child_value in value.items():
                    _visit(child_value, child_key, key)
                return

            if isinstance(value, list):
                for child in value:
                    _visit(child, key, parent_key)
                return

            if not isinstance(value, str):
                return

            if not cls._looks_like_earliest_key(key_l, parent_l):
                return

            parsed = cls._parse_api_datetime(value)
            if parsed is not None:
                candidates.append(parsed)

        _visit(data)
        if not candidates:
            return None
        return min(candidates)

    @staticmethod
    def _looks_like_earliest_key(key: str, parent_key: str) -> bool:
        """Return True when key likely represents earliest-available metadata."""
        if "earliest" in key or "oldest" in key:
            return True
        if "available" in parent_key and "from" in key:
            return True
        if key in {"availablefrom", "available_from", "fromavailable"}:
            return True
        return False

    @staticmethod
    def _parse_api_datetime(value: str) -> datetime | None:
        """Parse Fortum datetime/date strings into timezone-aware UTC datetimes."""
        try:
            if value.endswith("Z"):
                return datetime.fromisoformat(value.replace("Z", "+00:00"))

            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=ZoneInfo("UTC"))
            return parsed
        except ValueError:
            try:
                parsed_date = datetime.fromisoformat(f"{value}T00:00:00")
                return parsed_date.replace(tzinfo=ZoneInfo("UTC"))
            except ValueError:
                return None

    @property
    def total_energy_consumption(self) -> float:
        """Get total energy consumption across all data points."""
        return sum(point.total_energy for point in self.series)

    @property
    def total_cost(self) -> float:
        """Get total cost across all data points."""
        return sum(point.total_cost for point in self.series)

    @property
    def latest_data_point(self) -> TimeSeriesDataPoint | None:
        """Get the latest data point with energy data."""
        for point in reversed(self.series):
            if point.energy and any(e.value > 0 for e in point.energy):
                return point
        return None


@dataclass
class ConsumptionData:
    """Legacy model for backward compatibility."""

    date_time: datetime
    value: float
    cost: float | None = None
    price: float | None = None
    price_unit: str | None = None
    unit: str = "kWh"

    @classmethod
    def from_time_series(
        cls, time_series: TimeSeries, timezone: str | None = None
    ) -> list[ConsumptionData]:
        """Create consumption data list from time series."""
        consumption_data = []
        local_timezone = ZoneInfo(timezone) if timezone else None

        for point in time_series.series:
            has_energy = point.energy and any(e.value > 0 for e in point.energy)
            has_price = point.price is not None

            if has_energy or has_price:
                point_datetime = point.at_utc
                if local_timezone is not None:
                    point_datetime = point_datetime.astimezone(local_timezone)

                consumption_data.append(
                    cls(
                        date_time=point_datetime,
                        value=point.total_energy,
                        cost=point.total_cost if point.cost else None,
                        price=point.price.total if point.price else None,
                        price_unit=time_series.price_unit,
                        unit=time_series.measurement_unit,
                    )
                )

        return consumption_data

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> ConsumptionData:
        """Create instance from legacy API response data."""
        return cls(
            date_time=datetime.fromisoformat(data["dateTime"]),
            value=float(data["value"]),
            cost=float(data.get("cost", 0)) if data.get("cost") is not None else None,
            unit=data.get("unit", "kWh"),
        )


@dataclass
class CustomerDetails:
    """Represents customer details."""

    customer_id: str
    postal_address: str
    post_office: str
    name: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> CustomerDetails:
        """Create instance from API response data."""
        # Handle session endpoint format
        if "user" in data:
            user_data = data["user"]
            return cls(
                customer_id=user_data["customerId"],
                postal_address=user_data.get("postalAddress", ""),
                post_office=user_data.get("postOffice", ""),
                name=user_data.get("name"),
            )

        # Handle legacy/direct format
        return cls(
            customer_id=data["customerId"],
            postal_address=data["postalAddress"],
            post_office=data["postOffice"],
            name=data.get("name"),
        )


@dataclass
class MeteringPoint:
    """Represents a metering point."""

    metering_point_no: str
    metering_point_id: str | None = None
    address: str | None = None

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> MeteringPoint:
        """Create instance from API response data."""
        # Handle the new API structure where meteringPointNo is nested in consumption
        consumption = data.get("consumption", {})
        metering_point_no = consumption.get("meteringPointNo")

        # Fallback to old structure for backward compatibility
        if not metering_point_no:
            metering_point_no = data.get("meteringPointNo")

        # Ensure we have a valid metering point number
        if not metering_point_no:
            raise ValueError("No meteringPointNo found in data")

        metering_point_id = (
            consumption.get("meteringPointId")
            or data.get("meteringPointId")
            or data.get("id")
        )

        address = data.get("address")
        if isinstance(address, dict):
            address = cls._format_address(address)

        return cls(
            metering_point_no=str(metering_point_no),
            metering_point_id=str(metering_point_id) if metering_point_id else None,
            address=address,
        )

    @staticmethod
    def _format_address(address: dict[str, Any]) -> str | None:
        """Format nested address payload into display string."""
        street = " ".join(
            part
            for part in [
                address.get("streetName"),
                address.get("houseNumber"),
                address.get("houseLetter"),
            ]
            if part
        ).strip()
        city = " ".join(
            part
            for part in [
                address.get("zipCode"),
                address.get("cityName"),
            ]
            if part
        ).strip()
        formatted = ", ".join(part for part in [street, city] if part)
        return formatted or None


@dataclass
class AuthTokens:
    """Represents OAuth2 authentication tokens."""

    access_token: str
    refresh_token: str
    id_token: str
    expires_in: int
    token_type: str = "Bearer"

    @classmethod
    def from_api_response(cls, data: dict[str, Any]) -> AuthTokens:
        """Create instance from API response data."""
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            id_token=data["id_token"],
            expires_in=int(data["expires_in"]),
            token_type=data.get("token_type", "Bearer"),
        )
