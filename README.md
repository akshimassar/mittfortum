# Fortum Home Assistant Integration

A Home Assistant custom integration for accessing energy consumption data from Fortum for supported regions (currently Sweden and Finland).

## Features

- **Current electricity price**: Imports Fortum 15-minute spot price data and updates it in Home Assistant every 5 minutes.
- **Hourly historical statistics**: Imports hourly consumption, cost, price, and temperature and backfills missing history on a regular interval.
- **Long-term visibility**: Stores imported data as Home Assistant long-term statistics, so it can be charted over long periods.
- **Multi-meter support**: Creates separate statistics series for each metering point found in your Fortum account.

## Installation

### HACS (Recommended)

 This integration is not yet available in the default HACS repositories, but you can add it as a custom repository:

1. Open HACS in Home Assistant
2. Click on the 3 dots in the top right corner
3. Select "Custom repositories"
4. Add the repository URL: `https://github.com/selleronom/mittfortum`
5. Select "Integration" as the category
6. Click the "ADD" button
7. Search for "MittFortum" in HACS and install it
8. Restart Home Assistant

### Manual Installation

1. Download the latest release from the [releases page](https://github.com/selleronom/mittfortum/releases)
2. Copy the `custom_components/mittfortum` directory to your Home Assistant `custom_components` directory
3. Restart Home Assistant

## Configuration

1. Go to Configuration > Integrations
2. Click "Add Integration"
3. Search for "MittFortum"
4. Enter your Fortum username and password
5. Select your region and complete setup

## Entities

The integration creates these regular entities:

- **Energy Consumption Sensor** (`sensor`): Current/aggregated consumption view from the main data coordinator.
- **Total Cost Sensor** (`sensor`): Current/aggregated cost view from the main data coordinator.
- **Price per kWh Sensor** (`sensor`): Latest spot price, refreshed by the price coordinator every 5 minutes.
- **Statistics Last Sync** (`sensor`, timestamp): Last successful statistics import time.

Additionally, it imports hourly Recorder statistics for each available metering point:

- `mittfortum:hourly_consumption_<metering_point_no>`
- `mittfortum:hourly_cost_<metering_point_no>`
- `mittfortum:hourly_price_<metering_point_no>`
- `mittfortum:hourly_temperature_<metering_point_no>`

If `Debug entities` is enabled in integration options, two debug buttons are exposed:

- **Full Statistics Sync** (`button`): Runs a forced full historical sync.
- **Clear Statistics (available metering points only)** (`button`): Clears imported statistics series for currently discovered metering points.

## Architecture

For architecture details, project layout, and contributor-focused development notes, see `AGENTS.md`.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
