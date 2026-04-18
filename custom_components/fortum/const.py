"""Constants for the Fortum integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

# Integration domain
DOMAIN = "fortum"
CONF_REGION = "region"
CONF_DEBUG_ENTITIES = "debug_entities"
CONF_DEBUG_LOGGING = "debug_logging"
CONF_FORCE_SHORT_TOKEN_LIFETIME = "force_short_token_lifetime"
CONF_CREATE_DASHBOARD = "create_dashboard"
CONF_CREATE_CURRENT_MONTH_SENSORS = "create_current_month_sensors"
DEFAULT_REGION = "se"
DEFAULT_DEBUG_ENTITIES = False
DEFAULT_DEBUG_LOGGING = False
DEFAULT_FORCE_SHORT_TOKEN_LIFETIME = False
DEFAULT_CREATE_DASHBOARD = False
DEFAULT_CREATE_CURRENT_MONTH_SENSORS = False
SUPPORTED_REGIONS = ["se", "fi", "no"]
REGION_CURRENCY = {
    "se": "SEK",
    "fi": "EUR",
    "no": "NOK",
}

# Platforms
PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BUTTON]

# API endpoints
FORTUM_BASE_URL = "https://www.fortum.com/se/el"
API_BASE_URL = f"{FORTUM_BASE_URL}/api"
TRPC_BASE_URL = f"{API_BASE_URL}/trpc"
OAUTH_BASE_URL = "https://sso.fortum.com"

# Session endpoint (for customer details and metering points)
SESSION_URL = f"{API_BASE_URL}/auth/session"

# tRPC endpoints (only for time series data)
TIME_SERIES_URL = f"{TRPC_BASE_URL}/loggedIn.timeSeries.listTimeSeries"

# API request configuration
TRPC_BATCH_PARAM = "1"
DEFAULT_RESOLUTION = "MONTH"
AVAILABLE_RESOLUTIONS = ["HOUR", "DAY", "MONTH", "YEAR"]
PRICE_RESOLUTIONS = ["PER_15_MIN", "HOUR"]

# Energy data types
ENERGY_DATA_TYPE = "ENERGY"

# Cost component types
COST_TYPES = {
    "ELCERT_AMOUNT": "Certificate costs",
    "FIXED_FEE_AMOUNT": "Fixed fees",
    "SPOT_VARIABLE_AMOUNT": "Variable spot price",
    "VAR_AMOUNT": "Variable amount",
    "VAR_DISCOUNT_AMOUNT": "Discounts",
}

# OAuth2 configuration
OAUTH_CLIENT_ID = "globalwebprod"
OAUTH_REDIRECT_URI = "https://www.fortum.com/se/el/api/auth/callback/ciamprod"
OAUTH_SECRET_KEY = "shared_secret"
OAUTH_SCOPE = ["openid", "profile", "crmdata"]

# OAuth2 endpoints
OAUTH_CONFIG_URL = f"{OAUTH_BASE_URL}/.well-known/openid-configuration"
OAUTH_TOKEN_URL = f"{OAUTH_BASE_URL}/am/oauth2/access_token"
OAUTH_AUTH_URL = f"{OAUTH_BASE_URL}/am/json/realms/root/realms/alpha/authenticate"

# Update intervals
DEFAULT_UPDATE_INTERVAL = timedelta(minutes=30)
PRICE_UPDATE_INTERVAL = timedelta(minutes=5)
TOKEN_REFRESH_INTERVAL = timedelta(minutes=5)
SESSION_REFRESH_INTERVAL = timedelta(hours=3)

# Device information
MANUFACTURER = "@akshimassar"
MODEL = "Fortum Integration"

# Sensor configuration
PRICE_SENSOR_KEY = "price_per_kwh"
TOMORROW_MAX_PRICE_SENSOR_KEY = "tomorrow_max_price"
TOMORROW_MAX_PRICE_TIME_SENSOR_KEY = "tomorrow_max_price_time"
NORGESPRIS_CONSUMPTION_LIMIT_SENSOR_KEY = "norgespris_consumption_limit"
CURRENT_MONTH_CONSUMPTION_SENSOR_KEY = "current_month_consumption"
CURRENT_MONTH_COST_SENSOR_KEY = "current_month_cost"
STATS_LAST_SYNC_SENSOR_KEY = "statistics_last_sync"
RESYNC_HISTORICAL_STATS_BUTTON_KEY = "statistics_resync_historical"
RECREATE_DASHBOARD_BUTTON_KEY = "dashboard_recreate"
BACKFILL_HISTORICAL_GAPS_BUTTON_KEY = "statistics_backfill_historical_gaps"

# What is considered non-historical data and requested from Fortum regularly
HOURLY_DATA_RECENT_WINDOW_DAYS = 14

# Historical data sync will request all available data in specified chunks
HOURLY_DATA_HISTORICAL_CHUNK_DAYS = 14

# Timeout for Fortum hourly-data API requests (seconds)
HOURLY_DATA_REQUEST_TIMEOUT_SECONDS = 30.0

# Default timeout for Fortum HTTP requests when a call does not override it
API_DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0

# Data storage keys
CONF_CUSTOMER_ID = "customer_id"
CONF_METERING_POINTS = "metering_points"


def get_currency_for_region(region: str | None) -> str:
    """Get currency code for region."""
    code = (region or DEFAULT_REGION).strip().lower()
    return REGION_CURRENCY.get(code, REGION_CURRENCY[DEFAULT_REGION])
