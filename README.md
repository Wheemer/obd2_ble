# OBD2 BLE

A Home Assistant integration for reading OBD2 vehicle data via Bluetooth Low Energy (BLE).

Uses [PaulMarisOUMary OBDII](https://github.com/PaulMarisOUMary/OBDII) as library for interfacing the OBD2.

## Features

- Real-time OBD2 data reading from compatible BLE devices
- Automatic device discovery via Bluetooth
- Configurable polling intervals for different vehicle states
- Support for caching sensor values
- Automatic reconnection when the device comes back in range

## Installation

### Via HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=dala318&repository=obd2_ble&category=Integration)

1. Add this repository to HACS as a custom repository.
2. Search for "OBD2 BLE" in HACS and install it.
3. Restart Home Assistant.

### Manual Installation

1. Copy the `custom_components/obd2_ble` folder to your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Go to Settings → Devices & Services → Add Integration.
4. Search for `OBD2 BLE` and add it.
5. Restart Home Assistant if prompted.

## Configuration

The integration is configured via the UI. No YAML configuration is required.

1. Supported OBD2 BLE devices should be discovered automatically and offered during setup.
2. If your device is not found but is within range, it is likely not supported.
3. After adding the integration, open the integration options and choose "Configure commands".
4. On the Commands selection page, choose the supported commands you want to enable.
5. The command configuration page will repeat for each selected command.
6. When finished, the integration reloads and the selected sensors should appear.

### Setup Requirements

- A compatible OBD2 BLE device
- Home Assistant with Bluetooth support (ESPHome Bluetooth Proxy)

## Supported Devices

This integration supports OBD2 BLE dongles that advertise as:

- `OBD2`
- `OBDII`

Supported BLE service UUIDs:

- `0000ffe0-0000-1000-8000-00805f9b34fb`
- `0000fff0-0000-1000-8000-00805f9b34fb`
- `000018f0-0000-1000-8000-00805f9b34fb`

## Troubleshooting

- Ensure your OBD2 BLE device is in range and Bluetooth is enabled.
- Check Home Assistant logs for detailed error messages.
- Verify the device appears in Home Assistant Bluetooth discovery.

## Support

For issues and feature requests, please visit the [GitHub repository](https://github.com/dala318/obd2_ble/issues)
