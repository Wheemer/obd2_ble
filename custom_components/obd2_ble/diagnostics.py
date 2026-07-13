"""Diagnostics support for OBD2 BLE."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant

from .const import (
    CONF_CHARACTERISTIC_UUID_READ,
    CONF_CHARACTERISTIC_UUID_WRITE,
    CONF_HW_VERSION,
)
from .coordinator import Obd2BleConfigEntry

TO_REDACT = {
    CONF_ADDRESS,
    CONF_CHARACTERISTIC_UUID_READ,
    CONF_CHARACTERISTIC_UUID_WRITE,
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: Obd2BleConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "ble_found": coordinator.ble_found(),
            "ble_reachable": coordinator.ble_reachable(),
            "ble_connected": coordinator.ble_connected(),
            "car_connected": coordinator.car_connected(),
            "active_command_count": coordinator.active_command_count(),
            "active_commands": sorted(command.name for command in coordinator.active_commands),
            "polling_interval_seconds": coordinator.update_interval_seconds(),
            "last_successful_update": (
                coordinator.last_successful_update.isoformat()
                if coordinator.last_successful_update
                else None
            ),
            "last_error": coordinator.last_error,
            "cached_response_count": len(coordinator._cache_data),
            "supported_pids": coordinator._supported_pids,
            "supported_commands": sorted(command.name for command in coordinator._supported_cmds),
            "hw_version": entry.data.get(CONF_HW_VERSION),
        },
    }
