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
- `VEEPEAK`
- `Veepeak`

Supported BLE service UUIDs:

- `0000ffe0-0000-1000-8000-00805f9b34fb`
- `0000fff0-0000-1000-8000-00805f9b34fb`
- `000018f0-0000-1000-8000-00805f9b34fb`

Known device profiles:

- Generic OBD-II BLE adapters using service `FFF0` and characteristics `FFF1`/`FFF2`
- Vgate/V-LINK-style adapters using service `18F0` and characteristics `18F1`/`18F2`
- Veepeak OBDCheck BLE/BLE+ adapters advertising as `VEEPEAK` using service `FFF0` and characteristics `FFF1`/`FFF2`

## Enhanced PIDs

The integration includes opt-in enhanced commands that may not appear in the
standard OBD-II supported PID list.

Experimental Honda/Acura automatic transmission fluid temperature candidates:

- `HONDA_ATF_TEMP_8220`: request `22 82 20`, Celsius equation `A - 40`
- `HONDA_ATF_TEMP_9023`: request `22 90 23`, Celsius equation `A - 40`

Internally these commands use the equivalent `B - 40` formula because
`py-obdii` keeps the second service-22 PID byte in the decoded payload.

These are not guaranteed for every Honda/Acura model. Validate by comparing a
cold start and warm-up curve: ATF temperature should start near ambient and rise
more slowly than engine coolant. If it exactly mirrors coolant temperature,
treat the selected PID as invalid for the vehicle.

## Troubleshooting

- Ensure your OBD2 BLE device is in range and Bluetooth is enabled.
- Check Home Assistant logs for detailed error messages.
- Verify the device appears in Home Assistant Bluetooth discovery.

## Support

For issues and feature requests, please visit the [GitHub repository](https://github.com/dala318/obd2_ble/issues)
