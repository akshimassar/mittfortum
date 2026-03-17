"""API endpoints configuration."""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass
from datetime import datetime

FORTUM_SITE_BASE = "https://www.fortum.com"
SSO_BASE = "https://sso.fortum.com"


@dataclass(frozen=True)
class RegionProfile:
    """Region-specific endpoint and locale configuration."""

    code: str
    market_path: str
    locale: str
    ui_locale: str
    auth_index_values: tuple[str, ...]
    signin_provider: str = "ciamprod"
    callback_page_path: str = ""
    referer_path: str = ""
    timezone: str = "UTC"


REGION_PROFILES: dict[str, RegionProfile] = {
    "se": RegionProfile(
        code="se",
        market_path="se/el",
        locale="sv",
        ui_locale="sv",
        auth_index_values=("SeB2COGWLogin",),
        callback_page_path="inloggad/oversikt",
        referer_path="inloggad/el",
        timezone="Europe/Stockholm",
    ),
    "fi": RegionProfile(
        code="fi",
        market_path="fi/sahkoa",
        locale="fi",
        ui_locale="fi",
        auth_index_values=("FIB2CLogin", "FiB2COGWLogin", "SeB2COGWLogin"),
        callback_page_path="",
        referer_path="",
        timezone="Europe/Helsinki",
    ),
}


class APIEndpoints:
    """API endpoints configuration."""

    def __init__(self, profile: RegionProfile) -> None:
        """Initialize region-specific endpoints."""
        self.profile = profile

        # Site endpoints
        self.base_url = f"{FORTUM_SITE_BASE}/{profile.market_path}"
        self.api_base = f"{self.base_url}/api"
        self.trpc_base = f"{self.api_base}/trpc"

        self.providers = f"{self.api_base}/auth/providers"
        self.csrf = f"{self.api_base}/auth/csrf"
        self.signin = f"{self.api_base}/auth/signin/{profile.signin_provider}"
        self.session = f"{self.api_base}/auth/session"
        self.session_username = f"{self.api_base}/get-session-username"
        self.callback_url = f"{self.api_base}/auth/callback/{profile.signin_provider}"
        self.callback_page = self._join_path(self.base_url, profile.callback_page_path)
        self.referer = self._join_path(self.base_url, profile.referer_path)
        self.time_series = f"{self.trpc_base}/loggedIn.timeSeries.listTimeSeries"

        # SSO / OAuth endpoints
        self.openid_config = f"{SSO_BASE}/.well-known/openid-configuration"
        self.token_exchange = f"{SSO_BASE}/am/oauth2/access_token"
        self.auth_init = (
            f"{SSO_BASE}/am/json/realms/root/realms/alpha/authenticate?"
            f"locale={profile.locale}&authIndexType=service&"
            f"authIndexValue={profile.auth_index_values[0]}"
        )
        self.user_session = f"{SSO_BASE}/am/json/users?_action=idFromSession"
        self.theme_realm = f"{SSO_BASE}/openidm/config/ui/themerealm"
        self.user_details = (
            f"{SSO_BASE}/am/json/realms/root/realms/alpha/users/{{user_id}}"
        )
        self.validate_goto = (
            f"{SSO_BASE}/am/json/realms/root/realms/alpha/users?_action=validateGoto"
        )

    @classmethod
    def for_region(cls, region: str | None) -> APIEndpoints:
        """Build endpoints for the selected region."""
        code = (region or "se").strip().lower()
        profile = REGION_PROFILES.get(code, REGION_PROFILES["se"])
        return cls(profile)

    @staticmethod
    def _join_path(base: str, subpath: str) -> str:
        if not subpath:
            return base
        return f"{base}/{subpath.strip('/')}"

    def get_time_series_url(
        self,
        metering_point_nos: list[str],
        from_date: datetime,
        to_date: datetime,
        resolution: str = "MONTH",
    ) -> str:
        """Get time series URL with tRPC format."""
        input_data = {
            "0": {
                "json": {
                    "meteringPointNo": metering_point_nos,
                    "fromDate": from_date.isoformat() + "Z",
                    "toDate": to_date.isoformat() + "Z",
                    "resolution": resolution,
                }
            }
        }

        input_json = json.dumps(input_data, separators=(",", ":"))
        input_encoded = urllib.parse.quote(input_json)

        return f"{self.time_series}?batch=1&input={input_encoded}"

    def get_user_details_url(self, user_id: str) -> str:
        """Get user details URL."""
        return self.user_details.format(user_id=user_id)
