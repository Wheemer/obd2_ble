"""Coordinator for OBD2 BLE."""

from datetime import timedelta
import logging
from typing import Any

from bleak.backends.device import BLEDevice

from obdii import Command, Connection, Protocol, Response, at_commands, commands as veh_commands, __version__
from obdii.basetypes import MISSING

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.api import async_address_present
from homeassistant.components.bluetooth.const import DOMAIN as BLUETOOTH_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConditionError, ConfigEntryNotReady, ConfigEntryNotReady
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CACHED_VALUES,
    CONF_FAST_POLL,
    CONF_SLOW_POLL,
    CONF_XS_POLL,
    CONF_CHARACTERISTIC_UUID_READ,
    CONF_CHARACTERISTIC_UUID_WRITE,
    CONF_PROTOCOL,
    DOMAIN,
    FAST_POLL_INTERVAL,
    DEFAULT_FAST_POLL,
    DEFAULT_SLOW_POLL,
    DEFAULT_XS_POLL,
    DEFAULT_CACHED_VALUES,
    DEFAULT_CHARACTERISTIC_UUID_READ,
    DEFAULT_CHARACTERISTIC_UUID_WRITE,
)
from .obdii.transport_ble import TransportBLE

_LOGGER = logging.getLogger(__name__)

type Obd2BleConfigEntry = ConfigEntry[Obd2BleDataUpdateCoordinator]


class Obd2BleDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Response]]):
    """Class to manage fetching data from the API."""

    _cache_data: dict[str, Response]
    
    active_commands: set[Command]

    def __init__(
        self, hass: HomeAssistant, entry: Obd2BleConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=FAST_POLL_INTERVAL,
            always_update=True,
            config_entry=entry,
        )

        address = entry.unique_id

        if address is None:
            raise ConditionError("No unique_id found in config entry")

        ble_device: BLEDevice | None = bluetooth.async_ble_device_from_address(
            hass, address, True
        )

        if ble_device is None:
            _LOGGER.debug("Failed to discover device %s via Bluetooth", address)
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="device_not_found",
                translation_placeholders={
                    "mac": address,
                },
            )

        self.transport = TransportBLE(
            ble_device=ble_device,
            uuid_write=entry.data.get(CONF_CHARACTERISTIC_UUID_WRITE, DEFAULT_CHARACTERISTIC_UUID_WRITE),
            uuid_read=entry.data.get(CONF_CHARACTERISTIC_UUID_READ, DEFAULT_CHARACTERISTIC_UUID_READ),
            # timeout=entry.options.get("timeout", 10.0),
            loop = hass.loop,
        )

        self.api = Connection(
            transport=self.transport,
            auto_connect=False,
            protocol=Protocol(entry.data.get(CONF_PROTOCOL, Protocol.AUTO.value)),
            log_handler=MISSING,
            log_formatter=MISSING,
            log_level=MISSING,
        )

        self._supported_pids = []
        self._supported_cmds = []
        self.active_commands: set[Command] = set()

        if not self.config_entry or not address:
            raise ConditionError("No address found in config entry data")
        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, address), (BLUETOOTH_DOMAIN, address)},
            connections={(CONNECTION_BLUETOOTH, address)},
            # name=device.name,
            model_id=self.api.protocol.name,
            sw_version=__version__,
        )

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and any connection."""
        _LOGGER.debug("Shutting down BMS (%s)", self.name)
        await super().async_shutdown()
        try:
            await self.hass.async_add_executor_job(self.api.close)
        except Exception as err:
            _LOGGER.warning(f"Error occurred while closing API connection: {err}")
        else:
            _LOGGER.debug("API connection closed successfully")

    async def _async_update_data(self) -> dict[str, Response]:
        """Update data via library."""

        if not self.config_entry or not self.config_entry.unique_id:
            _LOGGER.error("No config entry available for coordinator")
            return {}

        _LOGGER.debug("Check if the device is still available")
        available = async_address_present(self.hass, self.config_entry.unique_id, connectable=True)
        if not available:
            _LOGGER.debug("Car out of range? Switch to extra slow polling")
            self.update_interval = timedelta(seconds=self.config_entry.options.get(CONF_XS_POLL, DEFAULT_XS_POLL))
            _LOGGER.debug(
                "Car out of range? Switch to ultra slow polling: interval = %s",
                self.update_interval,
            )
            if self.config_entry.options.get(CONF_CACHED_VALUES, DEFAULT_CACHED_VALUES):
                return self._cache_data
            return {}

        _LOGGER.debug("Device is available, check if connected")
        if not self.api.is_connected():
            try:
                _LOGGER.info("Device is available but not connected, attempt to connect")
                await self.hass.async_add_executor_job(self.api.connect)
                if not self.api.is_connected():
                    raise UpdateFailed("No connection to OBD2 after connect attempt")
            except Exception as err:
                raise UpdateFailed(f"Error connecting with OBD2: {err}")

        _LOGGER.debug("Device is connected, proceed to query data")
        try:
            response = await self.hass.async_add_executor_job(self.api.query, at_commands.VERSION_ID)
            self.device_info["hw_version"] = response.value if response else None
            # response = await self.hass.async_add_executor_job(self.api.query, at_commands.IDENTIFIER)
            # if response and response.value and self.device_info and "identifiers" in self.device_info:
            #     self.device_info["identifiers"].add(("identifier", response.value))
            # response = await self.hass.async_add_executor_job(self.api.query, at_commands.DESCRIPTION)
            # if response and response.value and self.device_info and "identifiers" in self.device_info:
            #     self.device_info["identifiers"].add(("description", response.value))

            new_data: dict[str, Response] = {}
            for command in self.active_commands:
                if command is None:
                    _LOGGER.warning("Skipping invalid command: %s", command)
                    continue
                try:
                    _LOGGER.debug("Querying OBD2 for command %s", command)
                    response: Response = await self.hass.async_add_executor_job(self.api.query, command)
                    _LOGGER.debug("Received response for command %s: %s", command, response)
                    new_data[str(command)] = response
                except Exception as err:
                    _LOGGER.error(f"Error occurred while querying command {command}: {err}")
            if new_data is None:
                raise UpdateFailed("Failed to connect to OBD device")
            if len(new_data) == 0:
                self.update_interval = timedelta(seconds=self.config_entry.options.get(CONF_SLOW_POLL, DEFAULT_SLOW_POLL))
                _LOGGER.debug(
                    "Car is probably off, switch to slow polling: interval = %s",
                    self.update_interval,
                )
            else:
                self.update_interval = timedelta(seconds=self.config_entry.options.get(CONF_FAST_POLL, DEFAULT_FAST_POLL))
                _LOGGER.debug(
                    "Car is on, polling: interval = %s",
                    self.update_interval,
                )
        except Exception as err:
            raise UpdateFailed(f"Unable to fetch data: {err}") from err
        else:
            if self.config_entry.options.get(CONF_CACHED_VALUES, DEFAULT_CACHED_VALUES):
                self._cache_data.update(new_data)
                return self._cache_data
            return new_data

    async def async_get_all_pid_commands(self, force_refresh=False) -> tuple[list[Any], list[Any]]:
        if self._supported_pids and self._supported_cmds and not force_refresh:
            return self._supported_pids, self._supported_cmds
        
        if not self.api.is_connected():
            raise UpdateFailed("No connection to OBD2 to get supported PIDs and Commands")
        
        self._supported_pids = []
        self._supported_cmds = [veh_commands.ENGINE_SPEED, veh_commands.VEHICLE_SPEED, veh_commands.FUEL_STATUS, veh_commands.ENGINE_RUN_TIME]  # TOOD: added for debugging, remove after testing
        for cmd in range(0x00, 0xE0, 0x20):
            try:
                response: Response = await self.hass.async_add_executor_job(self.api.query, veh_commands[1][cmd])
                if isinstance(response.value, list):
                    self._supported_pids.extend(response.value)
                    for pid in response.value:
                        try:
                            self._supported_cmds.append(veh_commands[1][pid])
                        except KeyError:
                            _LOGGER.warning(f"PID {pid} is supported but no command found in library")
            except Exception:
                _LOGGER.warning(f"Failed to query supported PIDs for command {veh_commands[1][cmd]}")

        _LOGGER.info(f"Supported PIDs: {self._supported_pids}")
        _LOGGER.info(f"Supported Commands: {self._supported_cmds}")

        return self._supported_pids, self._supported_cmds
