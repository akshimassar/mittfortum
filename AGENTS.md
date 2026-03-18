# Project Structure

```
custom_components/mittfortum/
├── __init__.py              # Integration setup and teardown
├── api/                     # API client modules
│   ├── __init__.py
│   ├── auth.py              # OAuth2 authentication client
│   ├── client.py            # Main API client
│   └── endpoints.py         # API endpoint definitions
├── sensors/                 # Sensor entity modules
│   ├── __init__.py
│   ├── energy.py            # Energy consumption sensor
│   └── cost.py              # Cost sensor
├── button.py                # Debug button entities
├── config_flow.py           # Configuration flow and options
├── const.py                 # Constants and configuration
├── coordinator.py           # Data update coordinator
├── device.py                # Device representation
├── entity.py                # Base entity class
├── exceptions.py            # Custom exceptions
├── models.py                # Data models
├── sensor.py                # Sensor platform setup
├── manifest.json            # Integration manifest
├── strings.json             # UI strings
└── translations/            # Localization files
    └── en.json
```

## Architecture Notes

This integration follows modern Home Assistant development practices.

### Key Components

#### OAuth2 Authentication (`api/auth.py`)
- Handles the complete OAuth2 flow with Fortum's SSO system
- Manages token lifecycle (access, refresh, ID tokens)
- Implements PKCE (Proof Key for Code Exchange) for security

#### API Client (`api/client.py`)
- Provides high-level API for consuming Fortum services
- Handles authentication headers and token refresh
- Implements proper error handling and retry logic

#### Data Coordinator (`coordinator.py`)
- Manages data updates from the API
- Implements efficient polling with configurable intervals
- Handles API errors gracefully

#### Data Models (`models.py`)
- Type-safe data structures for API responses
- Consistent data validation and transformation
- Easy serialization/deserialization

#### Custom Exceptions (`exceptions.py`)
- Comprehensive error hierarchy
- Clear error messages for debugging
- Proper exception chaining

### Engineering Features

- **Type Safety**: Full type annotations with pyrefly support
- **Error Handling**: Comprehensive exception handling with proper error messages
- **Testing**: Unit and integration tests with high coverage
- **Code Quality**: Pre-commit hooks with black, isort, flake8, and pyrefly
- **Documentation**: Comprehensive docstrings and README
- **Logging**: Structured logging for debugging and monitoring

## Development

### Setup Development Environment

```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Install pre-commit hooks
pre-commit install

# Run tests
pytest

# Run type checking
pyrefly check

# Format code
black custom_components/mittfortum
isort custom_components/mittfortum
```

### Testing

```bash
# Run all tests
pytest

# Run unit tests only
pytest tests/unit

# Run integration tests only
pytest tests/integration

# Run with coverage
pytest --cov=custom_components.mittfortum
```

### Live E2E test (manual)

Use this when validating real Fortum account/API behavior (for example while adapting region-specific flows):

```bash
FORTUM_E2E=1 \
FORTUM_USERNAME="your_username" \
FORTUM_PASSWORD="your_password" \
FORTUM_REGION="fi" \
uv run pytest tests/e2e/test_live_api.py -v
```

Notes:
- This test is opt-in and skipped unless `FORTUM_E2E=1` is set.
- It performs real authentication and API calls.
- Avoid running it in CI unless secrets are configured.

## Troubleshooting

### Common Issues

1. **Authentication Failed**: Verify your Fortum credentials.
2. **No Data**: Check that you have energy consumption data in your Fortum account for the selected region.
3. **Connection Issues**: Verify your internet connection and Home Assistant's network access.

### Debug Logging

Add the following to your `configuration.yaml` to enable debug logging:

```yaml
logger:
  default: info
  logs:
    custom_components.mittfortum: debug
```

## Contributing

1. Fork the repository.
2. Create a feature branch.
3. Make your changes.
4. Add tests for new functionality.
5. Run the test suite and ensure all tests pass.
6. Submit a pull request.

## Support

- [GitHub Issues](https://github.com/selleronom/mittfortum/issues)
- [Home Assistant Community Forum](https://community.home-assistant.io/)

## Acknowledgments

- Fortum for providing the API
- Home Assistant community for guidance and best practices

## Usage

Once the integration is set up, you can start monitoring your energy usage from Home Assistant.
This integration requires a Fortum account in a supported region.
