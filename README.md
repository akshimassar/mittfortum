# Fortum Home Assistant Integration

A Home Assistant custom integration for accessing energy consumption data from Fortum for supported regions (currently Sweden and Finland).

## Features

- **Energy Consumption Monitoring**: Track your energy usage over time
- **Cost Tracking**: Monitor energy costs in your local Fortum currency (SEK/EUR)
- **Secure OAuth2 Authentication**: Uses Fortum's official authentication system
- **Automatic Token Refresh**: Handles token expiration automatically
- **Device Integration**: Creates a device in Home Assistant for easy management

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

The integration creates the following entities:

- **Energy Consumption Sensor**: Total energy consumption in kWh
- **Total Cost Sensor**: Total energy cost in local currency (SEK/EUR)
- **Price per kWh Sensor**: Current/latest energy price per kWh, refreshed every 5 minutes (uses 15-minute resolution when available, otherwise hourly)

## Architecture

For architecture details, project layout, and contributor-focused development notes, see `AGENTS.md`.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
