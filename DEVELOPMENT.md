# Development Notes

This document contains contributor-focused architecture and development notes for the MittFortum integration.

## Project Structure

```
custom_components/mittfortum/
├── __init__.py              # Integration setup and teardown
├── api/                     # API client modules
│   ├── __init__.py
│   ├── auth.py              # OAuth2 authentication client
│   ├── client.py            # Main API/statistics client
│   └── endpoints.py         # API endpoint definitions
├── sensors/                 # Sensor entity modules
│   ├── __init__.py
│   ├── metering_point.py    # Per-metering-point diagnostic sensor
│   ├── price.py             # Near-real-time price sensor
│   └── stats_sync.py        # Statistics last sync sensor
├── button.py                # Debug button entities
├── config_flow.py           # Configuration flow and options
├── const.py                 # Constants and configuration
├── coordinator.py           # Data update coordinators
├── device.py                # Device representation
├── entity.py                # Base entity class
├── exceptions.py            # Custom exceptions
├── models.py                # Data models
├── sensor.py                # Sensor platform setup
├── manifest.json            # Integration manifest
├── strings.json             # UI strings
└── translations/            # Localization files
```

## Architecture Notes

### OAuth2 Authentication (`api/auth.py`)
- Handles Fortum SSO login flow.
- Manages token lifecycle and refresh.
- Uses retry/backoff when session user data has not propagated yet.

### API + Statistics Client (`api/client.py`)
- Handles authenticated API calls and retry/error handling.
- Imports hourly external statistics per metering point.
- Maintains cumulative `sum` for hourly consumption/cost statistics.
- Supports incremental sync and historical catch-up in 2-week chunks.

### Coordinators (`coordinator.py`)
- Main coordinator runs statistics sync cycle.
- Price coordinator updates near-real-time spot prices.

### Models (`models.py`)
- Typed models for API payloads.
- Parsing helpers for metering point and time-series payload variants.

## Development

### Setup

```bash
uv sync
uv run pre-commit install
```

### Checks

```bash
uv run ruff check custom_components/mittfortum tests
uv run pytest
```

### Targeted checks

```bash
uv run pytest tests/unit
uv run pytest tests/integration
```

### Live E2E test (manual)

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

### Debug logging

```yaml
logger:
  default: info
  logs:
    custom_components.mittfortum: debug
```

### Common issues

1. Authentication failure: verify Fortum credentials and region.
2. Missing history: verify metering point has available data for the requested period.
3. Network issues: verify Home Assistant can reach Fortum endpoints.
