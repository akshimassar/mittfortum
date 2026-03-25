"""Exceptions for the Fortum integration."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError


class FortumError(HomeAssistantError):
    """Base exception for Fortum integration."""

    def __init__(
        self,
        message: str = "An error occurred",
        *,
        status_code: int | None = None,
    ) -> None:
        """Initialize the exception."""
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class AuthenticationError(FortumError):
    """Exception raised for authentication errors."""

    def __init__(
        self,
        message: str = "Authentication failed",
        *,
        status_code: int | None = None,
    ) -> None:
        """Initialize the exception."""
        super().__init__(message, status_code=status_code)


class APIError(FortumError):
    """Exception raised for API-related errors."""

    def __init__(
        self,
        message: str = "API error occurred",
        *,
        status_code: int | None = None,
    ) -> None:
        """Initialize the exception."""
        super().__init__(message, status_code=status_code)


class ConfigurationError(FortumError):
    """Exception raised for configuration errors."""

    def __init__(
        self,
        message: str = "Configuration error",
        *,
        status_code: int | None = None,
    ) -> None:
        """Initialize the exception."""
        super().__init__(message, status_code=status_code)


class ConnectionError(FortumError):
    """Exception raised for connection errors."""

    def __init__(
        self,
        message: str = "Connection error",
        *,
        status_code: int | None = None,
    ) -> None:
        """Initialize the exception."""
        super().__init__(message, status_code=status_code)


class InvalidResponseError(APIError):
    """Exception raised when API response is invalid."""

    def __init__(
        self,
        message: str = "Invalid API response",
        *,
        status_code: int | None = None,
    ) -> None:
        """Initialize the exception."""
        super().__init__(message, status_code=status_code)


class UnexpectedStatusCodeError(APIError):
    """Exception raised when API returns unexpected status code."""

    def __init__(
        self,
        message: str = "Unexpected status code",
        *,
        status_code: int | None = None,
    ) -> None:
        """Initialize the exception."""
        super().__init__(message, status_code=status_code)


class TokenExpiredError(AuthenticationError):
    """Exception raised when authentication token has expired."""

    def __init__(
        self,
        message: str = "Token has expired",
        *,
        status_code: int | None = None,
    ) -> None:
        """Initialize the exception."""
        super().__init__(message, status_code=status_code)


class OAuth2Error(AuthenticationError):
    """Exception raised for OAuth2-related errors."""

    def __init__(
        self,
        message: str = "OAuth2 error occurred",
        *,
        status_code: int | None = None,
    ) -> None:
        """Initialize the exception."""
        super().__init__(message, status_code=status_code)
