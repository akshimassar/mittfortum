# Development Notes

This document contains contributor-focused architecture and development notes for the Fortum integration.

## Project Structure

```
custom_components/fortum/
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
│   ├── stats_last_sync.py   # Debug-only statistics last-sync sensor
│   └── tomorrow_price.py    # Tomorrow max price + timestamp sensors
├── button.py                # Debug button entities
├── config_flow.py           # Configuration flow and options
├── const.py                 # Constants and configuration
├── coordinators.py          # Data update coordinators
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
- Writes `fortum:price_forecast` statistics from fetched spot-price windows.
- Uses a 14-day recent window and 14-day chunks for historical catch-up.
- Fortum API can return GraphQL errors or take over 30 seconds for larger windows (observed even around 30 days).

### Coordinators (`coordinators.py`)
- Main coordinator runs statistics sync cycle.
- Price coordinator updates near-real-time spot prices.

### Sensors (`sensors/*`)
- `Statistics Last Sync` is diagnostic and only created when `Debug entities` is enabled.
- `Tomorrow Max Price` and `Tomorrow Max Price Time` are based on tomorrow points in spot-price coordinator data and remain unavailable until tomorrow prices are published.

### Models (`models.py`)
- Typed models for API payloads.
- Parsing helpers for metering point and time-series payload variants.

## TODO

- Persist pending historical sync per metering point across statistics sync failures (for both `force_resync=True` and auto-historical when existing statistics are missing), retry those points on subsequent sync cycles, and clear each pending state only after that metering point completes a successful historical sync.

## Development

### Setup

```bash
uv sync
uv run pre-commit install
```

### Checks

```bash
uv run ruff check custom_components/fortum tests
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
    custom_components.fortum: debug
```

### Common issues

1. Authentication failure: verify Fortum credentials and region.
2. Missing history: verify metering point has available data for the requested period.
3. Network issues: verify Home Assistant can reach Fortum endpoints.
