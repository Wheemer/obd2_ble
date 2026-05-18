"""Coordinator for OBD2 BLE."""

from datetime import timedelta
import logging
from typing import Any

from bleak.backends.device import BLEDevice

from obdii import Command, Connection, Response, at_commands, commands, __version__

from homeassistant.components.bluetooth.api import async_address_present
from homeassistant.components.bluetooth.const import DOMAIN as BLUETOOTH_DOMAIN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConditionError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CACHED_VALUES,
    CONF_FAST_POLL,
    CONF_SLOW_POLL,
    CONF_XS_POLL,
    DOMAIN,
    FAST_POLL_INTERVAL,
    DEFAULT_FAST_POLL,
    DEFAULT_SLOW_POLL,
    DEFAULT_XS_POLL,
    DEFAULT_CACHED_VALUES
)
from .obdii.transport_ble import TransportBLE

_LOGGER = logging.getLogger(__name__)

class Obd2BleDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(
        self, hass: HomeAssistant, device: BLEDevice, api: Connection, options
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=FAST_POLL_INTERVAL,
            always_update=True,
        )
        self._device: BLEDevice = device
        self.api = api
        if not isinstance(api.transport, TransportBLE):
            raise ConditionError("API transport is not of type TransportBLE")
        self.transport: TransportBLE = api.transport  # Shortcut to typed instance
        self._cache_data: dict[str, Any] = {}
        self.options = options

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, device.address), (BLUETOOTH_DOMAIN, device.address)},
            connections={(CONNECTION_BLUETOOTH, device.address)},
            # name=device.name,
            model_id=self.api.protocol.name,
            sw_version=__version__,
        )

        self._supported_pids = []
        self._supported_cmds = []

        # Track which commands are active to avoid unnecessary polling of inactive commands
        self.active_commands: set[Command] = set()

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

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library."""

        # Check if the device is still available
        _LOGGER.debug("Check if the device is still available")
        available = async_address_present(self.hass, self._device.address, connectable=True)
        if not available:
            _LOGGER.debug("Car out of range? Switch to extra slow polling")
            self.update_interval = timedelta(seconds=self.options.get(CONF_XS_POLL, DEFAULT_XS_POLL))
            _LOGGER.debug(
                "Car out of range? Switch to ultra slow polling: interval = %s",
                self.update_interval,
            )
            if self.options.get(CONF_CACHED_VALUES, DEFAULT_CACHED_VALUES):
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

            await self.async_get_all_pid_commands()

            new_data = {}
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
                self.update_interval = timedelta(seconds=self.options.get(CONF_SLOW_POLL, DEFAULT_SLOW_POLL))
                _LOGGER.debug(
                    "Car is probably off, switch to slow polling: interval = %s",
                    self.update_interval,
                )
            else:
                self.update_interval = timedelta(seconds=self.options.get(CONF_FAST_POLL, DEFAULT_FAST_POLL))
                _LOGGER.debug(
                    "Car is on, polling: interval = %s",
                    self.update_interval,
                )
        except Exception as err:
            raise UpdateFailed(f"Unable to fetch data: {err}") from err
        else:
            if self.options.get(CONF_CACHED_VALUES, DEFAULT_CACHED_VALUES):
                self._cache_data.update(new_data)
                return self._cache_data
            return new_data

    # async def _get_command_by_name(self, name: str) -> Command | None:
    #     """Find an obdii Command object matching a string name."""
    #     _, commands = await self.async_get_all_pid_commands()
    #     for cmd in commands:
    #         if getattr(cmd, "name", "") == name:
    #             return cmd
    #     return None

    async def async_get_all_pid_commands(self, force_refresh=False) -> tuple[list[Any], list[Any]]:
        if self._supported_pids and self._supported_cmds and not force_refresh:
            return self._supported_pids, self._supported_cmds
        
        if not self.api.is_connected():
            raise UpdateFailed("No connection to OBD2 to get supported PIDs and Commands")
        
        self._supported_pids = []
        self._supported_cmds = []
        for cmd in range(0x00, 0xE0, 0x20):
            try:
                response: Response = await self.hass.async_add_executor_job(self.api.query, commands[1][cmd])
                if isinstance(response.value, list):
                    self._supported_pids.extend(response.value)
                    for pid in response.value:
                        try:
                            self._supported_cmds.append(commands[1][pid])
                        except KeyError:
                            _LOGGER.warning(f"PID {pid} is supported but no command found in library")
            except Exception:
                _LOGGER.warning(f"Failed to query supported PIDs for command {commands[1][cmd]}")

        _LOGGER.info(f"Supported PIDs: {self._supported_pids}")
        _LOGGER.info(f"Supported Commands: {self._supported_cmds}")

        return self._supported_pids, self._supported_cmds
