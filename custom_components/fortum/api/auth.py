"""OAuth2 authentication client for Fortum."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode, urlsplit

import httpx
from homeassistant.helpers.httpx_client import get_async_client

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

from ..const import OAUTH_CLIENT_ID, OAUTH_SECRET_KEY
from ..exceptions import (
    AuthenticationError,
    OAuth2Error,
)
from ..exceptions import (
    ConnectionError as FortumConnectionError,
)
from ..models import AuthTokens
from .endpoints import APIEndpoints

_LOGGER = logging.getLogger(__name__)

# Constants
CONTENT_TYPE_JSON = "application/json"
AUTHORIZATION_CODE_PARAM = "code="
SESSION_BASED_TOKEN = "session_based"
BEARER_TOKEN_TYPE = "Bearer"
REQUEST_TIMEOUT_SECONDS = 30.0
DEFAULT_TOKEN_EXPIRY_HOURS = 1  # 1 hour default expiry fallback
FIXED_TOKEN_LIFETIME_SECONDS = 900  # 15 minutes
SESSION_VERIFICATION_RETRY_DELAYS = (0.5, 1.0, 2.0, 3.0)
TOKEN_RENEWAL_RETRY_INITIAL_SECONDS = 5.0
REAUTH_RETRY_MAX_DELAY_SECONDS = 1800.0
INITIAL_AUTH_MAX_ATTEMPTS = 3
MAX_LOG_EXCERPT_LENGTH = 240


class OAuth2AuthClient:
    """OAuth2 authentication client for Fortum API."""

    def __init__(
        self,
        hass: HomeAssistant,
        username: str,
        password: str,
        region: str = "se",
        client_id: str = OAUTH_CLIENT_ID,
        redirect_uri: str | None = None,
        secret_key: str = OAUTH_SECRET_KEY,
        force_short_token_lifetime: bool = False,
    ) -> None:
        """Initialize OAuth2 client."""
        self._hass = hass
        self._username = username
        self._password = password
        self._region = region
        self._endpoints = APIEndpoints.for_region(region)
        self._client_id = client_id
        self._redirect_uri = redirect_uri or self._endpoints.callback_url
        self._secret_key = secret_key
        self._force_short_token_lifetime = force_short_token_lifetime

        # Token storage
        self._tokens: AuthTokens | None = None
        self._token_expiry: float | None = None
        self._session_data: dict[str, Any] | None = None
        self._session_cookies: dict[str, str] = {}
        self._auth_mode: str | None = None

        # Background token renewal scheduling
        self._token_refresh_task: asyncio.Task | None = None
        self._token_refresh_handle: asyncio.TimerHandle | None = None
        self._renewal_scheduler_enabled: bool = False

    @property
    def access_token(self) -> str | None:
        """Get current access token."""
        return self._tokens.access_token if self._tokens else None

    @property
    def refresh_token(self) -> str | None:
        """Get current refresh token."""
        return self._tokens.refresh_token if self._tokens else None

    @property
    def id_token(self) -> str | None:
        """Get current ID token."""
        return self._tokens.id_token if self._tokens else None

    @property
    def session_data(self) -> dict[str, Any] | None:
        """Get current session data."""
        return self._session_data

    @property
    def session_cookies(self) -> dict[str, str]:
        """Get current session cookies."""
        return self._session_cookies

    @property
    def region(self) -> str:
        """Get configured market region."""
        return self._region

    def is_token_expired(self, buffer_seconds: int = 0) -> bool:
        """Check if the current token is expired or will expire soon.

        Args:
            buffer_seconds: Number of seconds before actual expiry to consider
                the token expired. This allows for proactive renewal.
                Default is 0 for backwards compatibility.
        """
        if not self._token_expiry:
            _LOGGER.debug(
                "Token expiry check: No token expiry set, considering expired"
            )
            return True

        current_time = time.time()
        # Add buffer time for proactive renewal
        effective_expiry = self._token_expiry - buffer_seconds
        is_expired = current_time >= effective_expiry
        seconds_until_refresh = effective_expiry - current_time

        if is_expired:
            _LOGGER.debug(
                "Token refresh required: refresh_required=%s "
                "seconds_until_refresh=%.2f",
                True,
                seconds_until_refresh,
            )
        return is_expired

    def _renewal_buffer_seconds(self) -> int:
        """Return proactive renewal buffer: 10% of TTL, at least 15 seconds."""
        if not self._tokens:
            return 15
        return max(15, int(self._tokens.expires_in * 0.1))

    def time_until_expiry(self) -> float:
        """Get time in seconds until token expires. Returns 0 if already expired."""
        if not self._token_expiry:
            return 0
        return max(0, self._token_expiry - time.time())

    def _process_token_expiry(self, expires_str: str | None) -> int:
        """Process token expiry string and return validated expires_in seconds.

        Args:
            expires_str: The expiry string from the server.

        Returns:
            Token lifetime in seconds.
        """
        server_expires_in: int
        if expires_str:
            try:
                expires_dt = self._parse_server_datetime(expires_str)
                current_time_utc = datetime.now(UTC)
                time_diff = expires_dt - current_time_utc
                server_expires_in = max(0, int(time_diff.total_seconds()))
            except Exception as exc:
                server_expires_in = DEFAULT_TOKEN_EXPIRY_HOURS * 3600
                _LOGGER.debug(
                    "Failed to parse server expiry '%s': %s. Using fallback %d seconds",
                    expires_str,
                    exc,
                    server_expires_in,
                )
        else:
            server_expires_in = DEFAULT_TOKEN_EXPIRY_HOURS * 3600
            _LOGGER.debug(
                "No server expiry provided. Using fallback %d seconds",
                server_expires_in,
            )

        return self._apply_token_lifetime_policy(server_expires_in)

    def _apply_token_lifetime_policy(self, server_expires_in: int) -> int:
        """Apply configured token lifetime policy.

        If force-short mode is enabled, only reduce lifetime (never extend).
        """
        if not self._force_short_token_lifetime:
            return server_expires_in

        effective_lifetime = min(server_expires_in, FIXED_TOKEN_LIFETIME_SECONDS)
        _LOGGER.info(
            "Force short token lifetime enabled: using %d seconds (server=%d seconds)",
            effective_lifetime,
            server_expires_in,
        )
        return effective_lifetime

    async def authenticate(self) -> AuthTokens:
        """Perform OAuth authentication with bounded retry backoff."""
        return await self._authenticate_with_backoff(
            retry_forever=False,
            max_attempts=INITIAL_AUTH_MAX_ATTEMPTS,
        )

    async def _authenticate_once(self) -> AuthTokens:
        """Perform a single complete OAuth2 authentication flow."""
        try:
            async with get_async_client(self._hass) as client:
                _LOGGER.debug("Starting working OAuth flow...")

                # Step 1: Initialize Fortum session
                csrf_token = await self._initialize_fortum_session(client)

                # Step 2: Get OAuth URL from signin
                oauth_url = await self._initiate_oauth_signin(client, csrf_token)

                # Step 3: Perform SSO authentication
                updated_oauth_url = await self._perform_sso_authentication(
                    client, oauth_url
                )

                # Use updated OAuth URL if provided, otherwise use original
                final_oauth_url = updated_oauth_url if updated_oauth_url else oauth_url

                # Step 4: Complete OAuth authorization flow
                await self._complete_oauth_authorization(client, final_oauth_url)

                # Step 5: Verify session is established
                session_data = await self._verify_session_established(client)

                _LOGGER.info("OAuth flow completed successfully")

                # Store session cookies with domain prioritization to fix conflicts
                self._session_cookies = self._extract_prioritized_cookies(client)

                # Extract real tokens from session data
                user_data = session_data.get("user", {})
                access_token = user_data.get("accessToken", SESSION_BASED_TOKEN)
                id_token = user_data.get("idToken", SESSION_BASED_TOKEN)

                _LOGGER.debug(
                    "Session established: customer_id=%s delivery_sites=%d",
                    user_data.get("customerId"),
                    len(user_data.get("deliverySites", [])),
                )

                expires_str = user_data.get("expires")

                # Calculate token expiry with proper timezone handling
                expires_in = self._process_token_expiry(expires_str)

                # Create tokens with real access token
                self._tokens = AuthTokens(
                    access_token=access_token,
                    refresh_token=SESSION_BASED_TOKEN,  # No refresh token in this flow
                    token_type=BEARER_TOKEN_TYPE,
                    expires_in=expires_in,
                    id_token=id_token,
                )
                self._token_expiry = time.time() + expires_in
                self._auth_mode = SESSION_BASED_TOKEN

                # Store session data for later use
                self._session_data = session_data

                # Start background token monitoring for proactive renewal
                self.start_token_renewal_scheduler()

                return self._tokens

        except FortumConnectionError:
            raise
        except httpx.HTTPError as exc:
            _LOGGER.exception("Network error while authenticating")
            raise FortumConnectionError("Network error connecting to Fortum") from exc
        except Exception as exc:
            _LOGGER.exception("Authentication failed")
            raise AuthenticationError(f"Authentication failed: {exc}") from exc

    def _is_unauthorized_error(self, exc: Exception) -> bool:
        """Return True if exception indicates unauthorized credentials (401)."""
        message = str(exc).lower()
        return "401" in message or "unauthorized" in message

    def _format_exception(self, exc: Exception) -> str:
        """Return a stable exception string, even for empty exception messages."""
        exc_type = type(exc).__name__
        message = str(exc).strip()
        if message:
            return f"{exc_type}: {message}"

        if exc.args:
            return f"{exc_type}: {exc.args!r}"

        return exc_type

    def _redact_url_for_log(self, url: str) -> str:
        """Return URL without query/fragment to avoid leaking auth artifacts."""
        parsed = urlsplit(url)
        if not parsed.scheme or not parsed.netloc:
            return "<invalid-url>"

        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"

    def _safe_response_excerpt(self, text: str | None) -> str:
        """Return bounded response body excerpt for diagnostics."""
        if not text:
            return "<empty>"

        compact = " ".join(text.split())
        if len(compact) <= MAX_LOG_EXCERPT_LENGTH:
            return compact

        return f"{compact[:MAX_LOG_EXCERPT_LENGTH]}..."

    async def _authenticate_with_backoff(
        self,
        *,
        retry_forever: bool,
        stop_on_unauthorized: bool = True,
        max_attempts: int | None = None,
    ) -> AuthTokens:
        """Authenticate with exponential backoff and optional attempt cap."""
        attempts = 0
        delay = TOKEN_RENEWAL_RETRY_INITIAL_SECONDS

        while True:
            attempts += 1
            try:
                return await self._authenticate_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._is_unauthorized_error(exc):
                    _LOGGER.warning("Authentication unauthorized (401)")
                    if stop_on_unauthorized:
                        raise AuthenticationError("Unauthorized (401)") from exc

                if (
                    not retry_forever
                    and max_attempts is not None
                    and attempts >= max_attempts
                ):
                    raise

                sleep_for = min(delay, REAUTH_RETRY_MAX_DELAY_SECONDS)
                _LOGGER.warning(
                    "Authentication attempt failed: %s. Retrying in %.1fs",
                    self._format_exception(exc),
                    sleep_for,
                )
                await asyncio.sleep(sleep_for)
                delay = min(delay * 2, REAUTH_RETRY_MAX_DELAY_SECONDS)

    async def _initialize_fortum_session(self, client) -> str:
        """Initialize Fortum session and get CSRF token."""
        # Get providers
        providers_resp = await client.get(
            self._endpoints.providers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if providers_resp.status_code != 200:
            _LOGGER.error(
                "Providers fetch failed with status=%d",
                providers_resp.status_code,
            )
            raise OAuth2Error(f"Providers fetch failed: {providers_resp.status_code}")

        # Get CSRF token
        csrf_resp = await client.get(
            self._endpoints.csrf,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if csrf_resp.status_code != 200:
            _LOGGER.error(
                "CSRF fetch failed with status=%d",
                csrf_resp.status_code,
            )
            raise OAuth2Error(f"CSRF fetch failed: {csrf_resp.status_code}")

        csrf_data = csrf_resp.json()
        csrf_token = csrf_data.get("csrfToken")
        if not csrf_token:
            raise OAuth2Error("No CSRF token received")

        _LOGGER.debug("Got CSRF token from Fortum auth endpoint")
        return csrf_token

    async def _initiate_oauth_signin(self, client, csrf_token: str) -> str:
        """Initiate OAuth signin and get OAuth URL."""
        signin_data = {
            "csrfToken": csrf_token,
            "callbackUrl": self._endpoints.callback_page,
            "json": "true",
        }

        signin_resp = await client.post(
            self._endpoints.signin,
            json=signin_data,
            headers={"Content-Type": CONTENT_TYPE_JSON},
        )

        if signin_resp.status_code != 200:
            _LOGGER.error(
                "Signin initiation failed with status=%d response=%s",
                signin_resp.status_code,
                self._safe_response_excerpt(signin_resp.text),
            )
            raise OAuth2Error(f"Signin initiation failed: {signin_resp.status_code}")

        signin_result = signin_resp.json()
        oauth_url = signin_result.get("url")
        if not oauth_url:
            _LOGGER.error(
                "Signin response missing OAuth URL (keys=%s)",
                sorted(signin_result.keys()),
            )
            raise OAuth2Error("No OAuth URL received from signin")

        _LOGGER.debug("Got OAuth redirect URL from signin response")
        return oauth_url

    async def _perform_sso_authentication(self, client, oauth_url: str) -> str | None:
        """Perform SSO authentication with credentials.

        Returns:
            Updated OAuth URL if provided by the SSO response, otherwise None.
        """
        try:
            # Step 1: Navigate to OAuth URL to establish session
            _LOGGER.debug("Navigating to OAuth URL to establish session")
            response = await client.get(oauth_url)
            _LOGGER.debug("OAuth page status: %d", response.status_code)

            if response.status_code == 302:
                _LOGGER.debug("OAuth page returned 302 redirect (expected in SSO flow)")
            elif response.status_code != 200:
                _LOGGER.warning("OAuth page returned %d", response.status_code)
                # Continue anyway, as authentication might still work

            # Step 2: Use ForgeRock JSON API for authentication
            _LOGGER.debug("Using ForgeRock JSON API for SSO authentication")

            auth_url = (
                "https://sso.fortum.com/am/json/realms/root/realms/alpha/authenticate"
            )

            init_data: dict[str, Any] | None = None
            last_status = None
            auth_full_url = ""

            for auth_index_value in self._endpoints.profile.auth_index_values:
                auth_params = {
                    "locale": self._endpoints.profile.locale,
                    "authIndexType": "service",
                    "authIndexValue": auth_index_value,
                    "goto": oauth_url,
                }
                auth_full_url = f"{auth_url}?{urlencode(auth_params)}"

                _LOGGER.debug(
                    "Initializing ForgeRock authentication with authIndexValue=%s",
                    auth_index_value,
                )
                init_resp = await client.post(
                    auth_full_url,
                    headers={
                        "accept-api-version": "protocol=1.0,resource=2.1",
                        "content-type": CONTENT_TYPE_JSON,
                    },
                    json={},
                )

                last_status = init_resp.status_code
                if init_resp.status_code == 200:
                    init_data = init_resp.json()
                    break

                _LOGGER.warning(
                    "Auth init failed with authIndexValue=%s status=%s",
                    auth_index_value,
                    init_resp.status_code,
                )

            if init_data is None:
                _LOGGER.error(
                    "Auth init failed for all authIndex values %s (last_status=%s)",
                    self._endpoints.profile.auth_index_values,
                    last_status,
                )
                raise OAuth2Error(f"Auth init failed: {last_status}")

            _LOGGER.debug("Authentication init succeeded, processing callbacks")

            # Check if authId is present
            auth_id = init_data.get("authId")
            if not auth_id:
                # If no authId, check for successUrl which indicates we should
                # proceed directly
                success_url = init_data.get("successUrl")
                if success_url:
                    _LOGGER.debug(
                        "No authId found, but successUrl present. "
                        "Using provided successUrl for OAuth completion",
                    )
                    return success_url  # Return the successUrl to use as OAuth URL
                else:
                    _LOGGER.error(
                        "Auth init response missing authId and successUrl (keys=%s)",
                        sorted(init_data.keys()),
                    )
                    raise OAuth2Error(
                        f"No authId or successUrl in init response: {init_data}"
                    )

            callbacks = init_data.get("callbacks", [])

            # Submit credentials using callback structure
            _LOGGER.debug("Submitting credentials via ForgeRock API")
            for callback in callbacks:
                if callback.get("type") == "StringAttributeInputCallback":
                    callback["input"] = [
                        {"name": "IDToken1", "value": self._username},
                        {"name": "IDToken1validateOnly", "value": False},
                    ]
                elif callback.get("type") == "PasswordCallback":
                    callback["input"] = [{"name": "IDToken2", "value": self._password}]

            login_payload = {"authId": auth_id, "callbacks": callbacks}

            login_resp = await client.post(
                auth_full_url,
                headers={
                    "accept-api-version": "protocol=1.0,resource=2.1",
                    "content-type": CONTENT_TYPE_JSON,
                },
                json=login_payload,
            )

            if login_resp.status_code != 200:
                _LOGGER.error(
                    "SSO login failed with status=%d response=%s",
                    login_resp.status_code,
                    self._safe_response_excerpt(login_resp.text),
                )
                raise OAuth2Error(f"Login failed: {login_resp.status_code}")

            login_data = login_resp.json()
            _LOGGER.debug("SSO login successful")

            success_url = login_data.get("successUrl")
            if success_url:
                _LOGGER.debug(
                    "SSO login returned successUrl. Using it for OAuth completion",
                )
                return success_url

            # Return None to indicate using the original OAuth URL
            return None

        except Exception as exc:
            exc_text = self._format_exception(exc)
            _LOGGER.error("SSO authentication failed: %s", exc_text)
            raise OAuth2Error(f"SSO authentication failed: {exc_text}") from exc

    async def _complete_oauth_authorization(self, client, oauth_url: str) -> None:
        """Complete OAuth authorization flow."""
        current_step = "initial_oauth_completion"
        oauth_url_for_log = self._redact_url_for_log(oauth_url)

        try:
            oauth_completion_resp = await client.get(
                oauth_url,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            if oauth_completion_resp.status_code != 302:
                _LOGGER.warning(
                    "OAuth completion returned status=%d instead of redirect "
                    "(oauth_url=%s)",
                    oauth_completion_resp.status_code,
                    oauth_url_for_log,
                )
                # Try a full redirect-following request to finalize session.
                current_step = "fallback_follow_redirects"
                fallback_resp = await client.get(
                    oauth_url,
                    follow_redirects=True,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                _LOGGER.debug(
                    "OAuth completion fallback finished with status=%d",
                    fallback_resp.status_code,
                )
                return

            # Follow the callback redirect chain
            callback_url = oauth_completion_resp.headers.get("location")
            if not callback_url or AUTHORIZATION_CODE_PARAM not in callback_url:
                _LOGGER.warning("No authorization code in callback URL")
                # Different regions may not expose code in first redirect.
                # Follow full redirect chain to establish authenticated session.
                current_step = "missing_code_follow_redirects"
                fallback_resp = await client.get(
                    oauth_url,
                    follow_redirects=True,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
                _LOGGER.debug(
                    "OAuth missing-code fallback finished with status=%d",
                    fallback_resp.status_code,
                )
                return

            _LOGGER.debug("Following callback URL...")

            # Follow callback to complete flow
            current_step = "callback_redirect"
            callback_resp = await client.get(
                callback_url,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )

            # May get additional redirects
            if callback_resp.status_code == 302:
                final_redirect = callback_resp.headers.get("location")
                if final_redirect:
                    _LOGGER.debug("Following final redirect...")
                    current_step = "final_redirect"
                    await client.get(
                        final_redirect,
                        timeout=REQUEST_TIMEOUT_SECONDS,
                    )

            _LOGGER.debug("OAuth authorization flow completed")

        except Exception as exc:
            exc_text = self._format_exception(exc)
            _LOGGER.error(
                "OAuth authorization completion failed at step=%s (oauth_url=%s): %s",
                current_step,
                oauth_url_for_log,
                exc_text,
            )
            raise OAuth2Error(f"OAuth authorization failed: {exc_text}") from exc

    async def _verify_session_established(self, client) -> dict[str, Any]:
        """Verify that session is properly established."""
        session_data: dict[str, Any] | None = None

        for attempt, delay in enumerate(
            (0.0, *SESSION_VERIFICATION_RETRY_DELAYS), start=1
        ):
            session_resp = await client.get(self._endpoints.session)

            if session_resp.status_code != 200:
                raise OAuth2Error(
                    f"Session verification failed: {session_resp.status_code}"
                )

            raw_session_data = session_resp.json()
            if isinstance(raw_session_data, dict):
                session_data = raw_session_data
            else:
                session_data = {}

            if session_data.get("user"):
                break

            if attempt == 1:
                _LOGGER.error(
                    "Session user data missing on first verification attempt; "
                    "retrying with propagation backoff"
                )

            if delay > 0:
                _LOGGER.info(
                    "Session user data not available yet "
                    "(attempt %d), retrying in %.1fs",
                    attempt,
                    delay,
                )
                await asyncio.sleep(delay)
        else:
            raise OAuth2Error("No user data in session")

        if session_data is None:
            raise OAuth2Error("Invalid session data response")

        _LOGGER.debug("Session verified successfully")

        # Perform a non-blocking session validation check for informational purposes
        # This is purely for logging and won't fail authentication if it doesn't work
        # since the session often takes additional time to propagate across endpoints
        validation_success = await self._validate_session_against_api(client)
        if validation_success:
            _LOGGER.debug("Session validation check passed - session is ready")
        else:
            _LOGGER.info(
                "Session validation check failed during authentication, but this is "
                "normal due to session propagation delays. Session will be available "
                "for API calls shortly."
            )

        return session_data

    async def refresh_access_token(self) -> AuthTokens:
        """Refresh the access token using refresh token."""
        if not self._tokens or not self._tokens.refresh_token:
            raise AuthenticationError("No refresh token available")

        # Session-based auth cannot use refresh-token exchange.
        if self._tokens.refresh_token == SESSION_BASED_TOKEN:
            raise AuthenticationError(
                "Refresh token exchange unavailable for session-based authentication"
            )

        try:
            _LOGGER.debug("Attempting to refresh access token")
            async with get_async_client(self._hass) as client:
                response = await client.post(
                    self._endpoints.token_exchange,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._tokens.refresh_token,
                        "client_id": self._client_id,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )

                if response.status_code != 200:
                    if response.status_code == 401:
                        raise AuthenticationError("Unauthorized (401)")

                    _LOGGER.error(
                        "Token refresh failed with status %d: %s",
                        response.status_code,
                        response.text,
                    )
                    raise OAuth2Error(
                        f"Token refresh failed: {response.status_code} {response.text}"
                    )

                token_data = response.json()
                self._tokens = AuthTokens.from_api_response(token_data)
                effective_lifetime = self._apply_token_lifetime_policy(
                    self._tokens.expires_in
                )
                self._tokens.expires_in = effective_lifetime
                self._token_expiry = time.time() + effective_lifetime
                self._auth_mode = "oauth_refresh"
                _LOGGER.debug("Successfully refreshed access token")

                # Restart token monitoring with new expiry time
                self.start_token_renewal_scheduler()

                return self._tokens

        except AuthenticationError:
            raise
        except Exception as exc:
            _LOGGER.exception("Token refresh failed")
            raise AuthenticationError(f"Token refresh failed: {exc}") from exc

    async def _validate_session_against_api(self, client) -> bool:
        """Validate that the session works against actual API endpoints."""
        try:
            # Test against the session endpoint that the client actually uses
            test_url = self._endpoints.session
            response = await client.get(test_url)

            if response.status_code == 200:
                _LOGGER.debug("Session validation against API successful")
                return True
            elif response.status_code == 401:
                _LOGGER.warning(
                    "Session validation failed with 401 - session not ready"
                )
                return False
            else:
                _LOGGER.warning(
                    "Session validation returned status %d", response.status_code
                )
                return False
        except Exception as exc:
            _LOGGER.warning(
                "Session validation failed with exception: %s",
                self._format_exception(exc),
            )
            return False

    def _extract_prioritized_cookies(self, client) -> dict[str, str]:
        """Extract cookies with domain prioritization to prevent stale cookie usage.

        Domain-specific cookies are prioritized over empty domain cookies to ensure
        fresh session tokens are used instead of stale ones.
        """
        domain_cookies = {}
        empty_domain_cookies = {}

        for cookie in client.cookies.jar:
            if cookie.value is None:
                continue

            domain = getattr(cookie, "domain", "")

            if domain:
                # Domain-specific cookie (prioritized)
                domain_cookies[cookie.name] = cookie.value
            elif cookie.name not in domain_cookies:
                # Empty domain cookie only if no domain version exists
                empty_domain_cookies[cookie.name] = cookie.value
            else:
                _LOGGER.debug(
                    "Skipped empty-domain cookie %s - domain version exists",
                    cookie.name,
                )

        # Combine with domain cookies taking priority
        result_cookies = {}
        result_cookies.update(empty_domain_cookies)
        result_cookies.update(domain_cookies)  # Domain cookies override empty ones

        _LOGGER.debug("Stored %d session cookies for API calls", len(result_cookies))
        return result_cookies

    def _parse_server_datetime(self, expires_str: str) -> datetime:
        """Parse server datetime with robust timezone handling.

        Args:
            expires_str: Datetime string from server

        Returns:
            Parsed datetime object in UTC

        Raises:
            ValueError: If datetime string cannot be parsed
        """
        try:
            # Handle common server datetime formats
            if expires_str.endswith("Z"):
                # ISO format with Z (UTC)
                return datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
            elif "+00:00" in expires_str:
                # ISO format with explicit UTC timezone
                return datetime.fromisoformat(expires_str)
            elif "+" in expires_str or expires_str.count("-") > 2:
                # ISO format with timezone offset
                return datetime.fromisoformat(expires_str)
            else:
                # Assume UTC if no timezone info
                dt = datetime.fromisoformat(expires_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                return dt
        except ValueError as exc:
            raise ValueError(
                f"Cannot parse datetime string '{expires_str}': {exc}"
            ) from exc

    def start_token_renewal_scheduler(self) -> None:
        """Start one-shot token renewal scheduling."""
        self._renewal_scheduler_enabled = True
        self._schedule_next_token_refresh()
        _LOGGER.debug("Started token renewal scheduling")

    async def stop_token_renewal_scheduler(self) -> None:
        """Stop token renewal scheduling and active refresh task."""
        self._renewal_scheduler_enabled = False
        if self._token_refresh_handle is not None:
            self._token_refresh_handle.cancel()
            self._token_refresh_handle = None

        if self._token_refresh_task and not self._token_refresh_task.done():
            self._token_refresh_task.cancel()
            try:
                await self._token_refresh_task
            except asyncio.CancelledError:
                pass
        self._token_refresh_task = None
        _LOGGER.debug("Stopped token renewal scheduling")

    def _calculate_refresh_delay(self) -> float:
        """Calculate one-shot delay until proactive token renewal."""
        if not self._tokens or not self._token_expiry:
            return 15.0

        ttl_seconds = self.time_until_expiry()
        renewal_buffer = float(self._renewal_buffer_seconds())
        return max(1.0, ttl_seconds - renewal_buffer)

    def _schedule_next_token_refresh(self) -> None:
        """Schedule the next proactive refresh as a one-shot callback."""
        if not self._renewal_scheduler_enabled:
            return

        if self._token_refresh_handle is not None:
            self._token_refresh_handle.cancel()
            self._token_refresh_handle = None

        delay = self._calculate_refresh_delay()
        loop = getattr(self._hass, "loop", None) or asyncio.get_running_loop()
        self._token_refresh_handle = loop.call_later(
            delay,
            self._start_scheduled_refresh,
        )
        _LOGGER.debug("Scheduled token renewal in %.1fs", delay)

    def _start_scheduled_refresh(self) -> None:
        """Start scheduled token refresh task."""
        self._token_refresh_handle = None
        if not self._renewal_scheduler_enabled:
            return

        if self._token_refresh_task and not self._token_refresh_task.done():
            return

        self._token_refresh_task = asyncio.create_task(self._run_scheduled_refresh())

    async def _run_scheduled_refresh(self) -> None:
        """Refresh token with expiry-limited retries, then re-auth if needed."""
        try:
            if self._auth_mode == SESSION_BASED_TOKEN:
                await self._authenticate_with_backoff(
                    retry_forever=True,
                    stop_on_unauthorized=False,
                )
                _LOGGER.info("Token re-authentication successful")
                return

            refresh_delay = TOKEN_RENEWAL_RETRY_INITIAL_SECONDS

            while self._renewal_scheduler_enabled and self.time_until_expiry() > 0:
                try:
                    await self.refresh_access_token()
                    _LOGGER.info("Proactive token renewal successful")
                    return
                except asyncio.CancelledError:
                    raise
                except AuthenticationError as exc:
                    if self._is_unauthorized_error(exc):
                        _LOGGER.warning(
                            "Token refresh unauthorized (401); switching to re-auth"
                        )
                        break

                    remaining = self.time_until_expiry()
                    if remaining <= 0:
                        break
                    sleep_for = min(refresh_delay, remaining)
                    _LOGGER.warning(
                        "Proactive token refresh failed: %s. Retrying in %.1fs "
                        "(token expires in %.1fs)",
                        exc,
                        sleep_for,
                        remaining,
                    )
                    await asyncio.sleep(sleep_for)
                    refresh_delay *= 2
                except Exception as exc:
                    remaining = self.time_until_expiry()
                    if remaining <= 0:
                        break
                    sleep_for = min(refresh_delay, remaining)
                    _LOGGER.warning(
                        "Proactive token refresh failed: %s. Retrying in %.1fs "
                        "(token expires in %.1fs)",
                        exc,
                        sleep_for,
                        remaining,
                    )
                    await asyncio.sleep(sleep_for)
                    refresh_delay *= 2

            await self._authenticate_with_backoff(
                retry_forever=True, stop_on_unauthorized=False
            )
            _LOGGER.info("Token re-authentication successful")
            return
        except asyncio.CancelledError:
            raise
        finally:
            self._token_refresh_task = None
