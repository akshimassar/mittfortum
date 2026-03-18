"""Constants for the MittFortum integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform

# Integration domain
DOMAIN = "mittfortum"
CONF_REGION = "region"
CONF_DEBUG_ENTITIES = "debug_entities"
CONF_DEBUG_LOGGING = "debug_logging"
CONF_FORCE_SHORT_TOKEN_LIFETIME = "force_short_token_lifetime"
DEFAULT_REGION = "se"
DEFAULT_DEBUG_ENTITIES = False
DEFAULT_DEBUG_LOGGING = False
DEFAULT_FORCE_SHORT_TOKEN_LIFETIME = False
SUPPORTED_REGIONS = ["se", "fi"]
REGION_CURRENCY = {
    "se": "SEK",
    "fi": "EUR",
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

# Device information
MANUFACTURER = "Fortum"
MODEL = "MittFortum"

# Sensor configuration
PRICE_SENSOR_KEY = "price_per_kwh"
STATS_SYNC_SENSOR_KEY = "statistics_last_sync"
FULL_SYNC_BUTTON_KEY = "statistics_full_sync"
CLEAR_STATS_BUTTON_KEY = "statistics_clear_all"

# Statistics backfill configuration
STATISTICS_BACKFILL_DAYS = 14

# Statistics sync request configuration
STATISTICS_REQUEST_TIMEOUT_SECONDS = 30.0

# Data storage keys
CONF_CUSTOMER_ID = "customer_id"
CONF_METERING_POINTS = "metering_points"


def get_currency_for_region(region: str | None) -> str:
    """Get currency code for region."""
    code = (region or DEFAULT_REGION).strip().lower()
    return REGION_CURRENCY.get(code, REGION_CURRENCY[DEFAULT_REGION])
