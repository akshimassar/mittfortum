"""Unit tests for OAuth2AuthClient."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.fortum.api.auth import OAuth2AuthClient
from custom_components.fortum.exceptions import AuthenticationError, OAuth2Error


class TestOAuth2AuthClient:
    """Test OAuth2AuthClient."""

    def test_init(self, mock_hass):
        """Test initialization."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        assert client._username == "test@example.com"
        assert client._password == "test_password"
        assert client._hass == mock_hass

    def test_is_token_expired_no_expiry(self, mock_hass):
        """Test token expiry check with no expiry set."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        assert client.is_token_expired() is True

    @patch("time.time", return_value=1000)
    def test_is_token_expired_not_expired(self, mock_time, mock_hass):
        """Test token expiry check when token is not expired."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )
        client._token_expiry = 2000

        assert client.is_token_expired() is False

    @patch("time.time", return_value=2000)
    def test_is_token_expired_expired(self, mock_time, mock_hass):
        """Test token expiry check when token is expired."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )
        client._token_expiry = 1000

        assert client.is_token_expired() is True

    def test_process_token_expiry_uses_server_value_by_default(self, mock_hass):
        """Default mode should trust server expiry timestamps."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )
        future = (datetime.now(UTC) + timedelta(minutes=45)).isoformat()

        expires_in = client._process_token_expiry(future)

        assert 2500 <= expires_in <= 2800

    def test_process_token_expiry_force_short_only_reduces(self, mock_hass):
        """Force short mode should reduce lifetime but never extend it."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
            force_short_token_lifetime=True,
        )

        # Long server lifetime gets reduced to 900s
        long_future = (datetime.now(UTC) + timedelta(minutes=45)).isoformat()
        long_expires_in = client._process_token_expiry(long_future)
        assert long_expires_in == 900

        # Short server lifetime is not extended
        short_future = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        short_expires_in = client._process_token_expiry(short_future)
        assert 250 <= short_expires_in <= 320

    async def test_authenticate_success(self, mock_hass, sample_auth_tokens):
        """Test successful authentication."""
        # Set up mock_hass.data for get_async_client
        mock_hass.data = {}

        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        with (
            patch(
                "custom_components.fortum.api.auth.get_async_client"
            ) as mock_get_client,
            patch.object(client, "_initialize_fortum_session") as mock_init_session,
            patch.object(client, "_initiate_oauth_signin") as mock_oauth_signin,
            patch.object(client, "_perform_sso_authentication") as mock_sso_auth,
            patch.object(client, "_complete_oauth_authorization") as mock_complete_auth,
            patch.object(client, "_verify_session_established") as mock_verify_session,
        ):
            # Mock the async context manager for the client
            mock_client = AsyncMock()
            mock_client.cookies.jar = []  # Empty cookie jar
            mock_get_client.return_value.__aenter__.return_value = mock_client

            # Set up method return values
            mock_init_session.return_value = "csrf_token_123"
            mock_oauth_signin.return_value = "https://oauth.url"
            mock_sso_auth.return_value = None
            mock_complete_auth.return_value = None
            mock_verify_session.return_value = {
                "user": {
                    "accessToken": "test_access_token",
                    "idToken": "test_id_token",
                    "expires": "2024-12-31T23:59:59Z",
                }
            }

            result = await client.authenticate()

            assert result.access_token == "test_access_token"
            assert result.id_token == "test_id_token"
            assert client._tokens is not None
            assert client._tokens.access_token == "test_access_token"

    async def test_authenticate_failure(self, mock_hass):
        """Test authentication failure."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        with (
            patch.object(
                client,
                "_authenticate_once",
                side_effect=AuthenticationError("Test error"),
            ),
            patch("custom_components.fortum.api.auth.asyncio.sleep", new=AsyncMock()),
        ):
            with pytest.raises(AuthenticationError):
                await client.authenticate()

    async def test_authenticate_fails_fast_on_authentication_error(self, mock_hass):
        """Initial authenticate should not retry AuthenticationError."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        client._authenticate_once = AsyncMock(side_effect=AuthenticationError("temp"))

        with patch(
            "custom_components.fortum.api.auth.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            with pytest.raises(AuthenticationError, match="temp"):
                await client.authenticate()

        mock_sleep.assert_not_awaited()

    async def test_refresh_access_token_session_based(
        self, mock_hass, sample_auth_tokens
    ):
        """Session-based auth should not use refresh-token exchange."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        # Set up session-based tokens
        session_tokens = sample_auth_tokens
        session_tokens.refresh_token = "session_based"
        client._tokens = session_tokens

        with pytest.raises(
            AuthenticationError,
            match="Refresh token exchange unavailable for session-based authentication",
        ):
            await client.refresh_access_token()

    def test_preferred_sso_attempts_prioritizes_oauth_url_values(self, mock_hass):
        """SSO attempts should prioritize locale/authIndex embedded in OAuth URL."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
            region="no",
        )

        attempts = client._preferred_sso_attempts(
            "https://oauth.test?locale=nb&authIndexValue=CustomLogin"
        )

        assert attempts[0] == ("nb", "CustomLogin")
        assert ("no", "NoB2COGWLogin") in attempts
        assert ("nb", "NOB2COGWLogin") in attempts

    async def test_perform_sso_authentication_stores_token_id(self, mock_hass):
        """SSO login should retain tokenId for cookie-based continuation."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
            region="no",
        )

        init_resp = Mock(status_code=200)
        init_resp.json.return_value = {
            "authId": "auth-1",
            "callbacks": [
                {"type": "StringAttributeInputCallback"},
                {"type": "PasswordCallback"},
            ],
        }
        login_resp = Mock(status_code=200)
        login_resp.json.return_value = {
            "tokenId": "token-1",
            "successUrl": "https://success.test",
        }

        mock_client = AsyncMock()
        mock_client.get.return_value = Mock(status_code=200)
        mock_client.post.side_effect = [init_resp, login_resp]

        result = await client._perform_sso_authentication(
            mock_client,
            "https://oauth.test?locale=nb&authIndexValue=CustomLogin",
        )

        assert result is None
        assert client._sso_token_id == "token-1"
        assert client._sso_success_url == "https://success.test"
        first_auth_url = mock_client.post.call_args_list[0].args[0]
        assert "authIndexValue=CustomLogin" in first_auth_url

    async def test_complete_oauth_authorization_injects_sso_cookie(self, mock_hass):
        """OAuth completion should inject iPlanet cookie when tokenId exists."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )
        client._sso_token_id = "token-1"
        client._sso_success_url = "https://success.test"

        mock_http_client = AsyncMock()
        mock_http_client.cookies = Mock()
        mock_http_client.cookies.set = Mock()
        mock_http_client.get.return_value = Mock(url="https://final.test")

        await client._complete_oauth_authorization(
            mock_http_client,
            "https://oauth.test",
        )

        assert mock_http_client.cookies.set.call_count == 2
        cookie_domains = {
            call.kwargs["domain"]
            for call in mock_http_client.cookies.set.call_args_list
        }
        assert cookie_domains == {"sso.fortum.com", ".sso.fortum.com"}
        mock_http_client.get.assert_awaited_once_with(
            "https://success.test",
            follow_redirects=True,
            timeout=30.0,
        )

    async def test_complete_oauth_authorization_uses_oauth_url_without_success_url(
        self, mock_hass
    ):
        """OAuth completion should fall back to oauth_url when no successUrl exists."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        mock_http_client = AsyncMock()
        mock_http_client.cookies = Mock()
        mock_http_client.cookies.set = Mock()
        mock_http_client.get.return_value = Mock(url="https://final.test")

        await client._complete_oauth_authorization(
            mock_http_client,
            "https://oauth.test",
        )

        mock_http_client.get.assert_awaited_once_with(
            "https://oauth.test",
            follow_redirects=True,
            timeout=30.0,
        )

    async def test_refresh_access_token_no_refresh_token(self, mock_hass):
        """Test refresh access token without refresh token raises error."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        with pytest.raises(AuthenticationError, match="No refresh token available"):
            await client.refresh_access_token()

    async def test_refresh_access_token_real_oauth_token(
        self, mock_hass, sample_auth_tokens
    ):
        """Test refresh access token with real OAuth2 token."""
        # Set up mock_hass.data for get_async_client
        mock_hass.data = {}

        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        # Set up real OAuth tokens
        real_tokens = sample_auth_tokens
        real_tokens.refresh_token = "real_refresh_token"
        client._tokens = real_tokens

        # Mock the HTTP client and response
        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.json = Mock(
            return_value={
                "access_token": "new_access_token",
                "refresh_token": "new_refresh_token",
                "token_type": "Bearer",
                "expires_in": 3600,
                "id_token": "new_id_token",
            }
        )

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with patch(
            "custom_components.fortum.api.auth.get_async_client"
        ) as mock_get_client:
            mock_get_client.return_value.__aenter__.return_value = mock_client

            result = await client.refresh_access_token()

            # Verify the token exchange call was made
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[1]["data"]["grant_type"] == "refresh_token"
            assert call_args[1]["data"]["refresh_token"] == "real_refresh_token"

            # Verify tokens were updated
            assert result.access_token == "new_access_token"
            assert result.refresh_token == "new_refresh_token"

    async def test_session_verification_has_no_unconditional_delay(self, mock_hass):
        """Successful first attempt should not sleep for propagation."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        # Mock session data response
        mock_session_data = {
            "user": {
                "id": "test_user",
                "accessToken": "test_access_token",
                "customerId": "12345",
            }
        }

        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = mock_session_data

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response

        with patch(
            "custom_components.fortum.api.auth.get_async_client"
        ) as mock_get_client:
            mock_get_client.return_value.__aenter__.return_value = mock_client
            mock_get_client.return_value.__aexit__.return_value = None

            # Mock asyncio.sleep to verify it's called
            with patch("custom_components.fortum.api.auth.asyncio.sleep") as mock_sleep:
                mock_sleep.return_value = None

                # Mock the session validation method (non-blocking now)
                with patch.object(
                    client, "_validate_session_against_api", return_value=False
                ):
                    # Call _verify_session_established
                    result = await client._verify_session_established(mock_client)

                    # No propagation delay should be added on successful first attempt
                    assert mock_sleep.call_count == 0

                # Verify session data was returned correctly
                assert result == mock_session_data
                assert result["user"]["id"] == "test_user"

    async def test_verify_session_retries_until_user_available(self, mock_hass):
        """Test session verification retries when user data is delayed."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        empty_session_response = Mock()
        empty_session_response.status_code = 200
        empty_session_response.json.return_value = {}

        valid_session = {
            "user": {
                "id": "test_user",
                "accessToken": "test_access_token",
            }
        }
        valid_session_response = Mock()
        valid_session_response.status_code = 200
        valid_session_response.json.return_value = valid_session

        mock_client = AsyncMock()
        mock_client.get.side_effect = [
            empty_session_response,
            empty_session_response,
            valid_session_response,
        ]

        with patch("custom_components.fortum.api.auth.asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            with patch.object(
                client, "_validate_session_against_api", return_value=True
            ):
                result = await client._verify_session_established(mock_client)

        assert result == valid_session
        assert mock_client.get.call_count == 3
        assert mock_sleep.call_args_list[0][0][0] == 0.5

    async def test_verify_session_logs_error_when_first_attempt_has_no_user(
        self, mock_hass
    ):
        """Log an error when first session verification misses user data."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        empty_session_response = Mock()
        empty_session_response.status_code = 200
        empty_session_response.json.return_value = {}

        valid_session = {
            "user": {
                "id": "test_user",
                "accessToken": "test_access_token",
            }
        }
        valid_session_response = Mock()
        valid_session_response.status_code = 200
        valid_session_response.json.return_value = valid_session

        mock_client = AsyncMock()
        mock_client.get.side_effect = [empty_session_response, valid_session_response]

        with (
            patch("custom_components.fortum.api.auth.asyncio.sleep") as mock_sleep,
            patch("custom_components.fortum.api.auth._LOGGER.error") as mock_error,
            patch.object(client, "_validate_session_against_api", return_value=True),
        ):
            mock_sleep.return_value = None
            result = await client._verify_session_established(mock_client)

        assert result == valid_session
        mock_error.assert_called_once()
        assert mock_sleep.call_count == 0

    async def test_verify_session_raises_when_user_never_available(self, mock_hass):
        """Test session verification fails after all retries are exhausted."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        empty_session_response = Mock()
        empty_session_response.status_code = 200
        empty_session_response.json.return_value = {}

        mock_client = AsyncMock()
        mock_client.get.return_value = empty_session_response

        with patch("custom_components.fortum.api.auth.asyncio.sleep") as mock_sleep:
            mock_sleep.return_value = None
            with pytest.raises(OAuth2Error, match="No user data in session"):
                await client._verify_session_established(mock_client)

        assert mock_client.get.call_count == 5
        assert [call[0][0] for call in mock_sleep.call_args_list] == [
            0.5,
            1.0,
            2.0,
            3.0,
        ]

    async def test_scheduler_retries_refresh_before_token_expiry(self, mock_hass):
        """Scheduler should retry refresh with exponential backoff before expiry."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )
        client._renewal_scheduler_enabled = True

        attempts = 0

        async def _refresh_once_then_succeed() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("transient refresh failure")

        client.refresh_access_token = AsyncMock(side_effect=_refresh_once_then_succeed)
        client.time_until_expiry = Mock(side_effect=[120.0, 120.0, 120.0])

        with patch(
            "custom_components.fortum.api.auth.asyncio.sleep",
            new=AsyncMock(),
        ) as mock_sleep:
            await client._run_scheduled_refresh()

        assert client.refresh_access_token.await_count == 2
        mock_sleep.assert_awaited_once_with(5.0)

    async def test_scheduler_switches_to_reauth_after_expiry(self, mock_hass):
        """Scheduler should re-authenticate when token is already expired."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )
        client._renewal_scheduler_enabled = True

        client.time_until_expiry = Mock(return_value=0.0)
        client.refresh_access_token = AsyncMock()
        client._authenticate_with_backoff = AsyncMock(return_value=Mock())

        await client._run_scheduled_refresh()

        client.refresh_access_token.assert_not_called()
        client._authenticate_with_backoff.assert_awaited_once_with(
            retry_forever=True,
        )

    async def test_scheduler_skips_refresh_for_session_based_auth(self, mock_hass):
        """Session-based mode should go directly to re-auth stage."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )
        client._renewal_scheduler_enabled = True
        client._auth_mode = "session_based"
        client.refresh_access_token = AsyncMock()
        client._authenticate_with_backoff = AsyncMock(return_value=Mock())

        await client._run_scheduled_refresh()

        client.refresh_access_token.assert_not_awaited()
        client._authenticate_with_backoff.assert_awaited_once_with(
            retry_forever=True,
        )

    async def test_scheduler_switches_to_reauth_on_refresh_auth_error(self, mock_hass):
        """Refresh auth errors should immediately transition to re-auth stage."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )
        client._renewal_scheduler_enabled = True

        client.time_until_expiry = Mock(return_value=120.0)
        client.refresh_access_token = AsyncMock(
            side_effect=AuthenticationError("refresh auth failed")
        )
        client._authenticate_with_backoff = AsyncMock(return_value=Mock())

        with patch(
            "custom_components.fortum.api.auth.asyncio.sleep", new=AsyncMock()
        ) as mock_sleep:
            await client._run_scheduled_refresh()

        client.refresh_access_token.assert_awaited_once()
        client._authenticate_with_backoff.assert_awaited_once_with(
            retry_forever=True,
        )
        assert mock_sleep.await_count == 0

    async def test_authenticate_with_backoff_caps_delay_to_8_hours(self, mock_hass):
        """Authentication retry helper should cap exponential backoff delay."""
        client = OAuth2AuthClient(
            hass=mock_hass,
            username="test@example.com",
            password="test_password",
        )

        reauth_attempts = {"count": 0}

        async def _fail_then_succeed() -> Mock:
            reauth_attempts["count"] += 1
            if reauth_attempts["count"] <= 15:
                raise AuthenticationError("reauth failed")
            return Mock()

        client._authenticate_once = AsyncMock(side_effect=_fail_then_succeed)

        with patch(
            "custom_components.fortum.api.auth.asyncio.sleep",
            new=AsyncMock(),
        ) as mock_sleep:
            await client._authenticate_with_backoff(
                retry_forever=True,
            )

        delays = [call.args[0] for call in mock_sleep.await_args_list]
        assert delays[:6] == [5.0, 10.0, 20.0, 40.0, 80.0, 160.0]
        assert delays[-1] == 28800.0
        assert max(delays) == 28800.0
