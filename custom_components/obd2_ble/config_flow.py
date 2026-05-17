"""Adds config flow for OBD2 BLE."""

import logging
from typing import Any

try:
    from bluetooth_data_tools import human_readable_name # type: ignore
except ImportError:  # pragma: no cover - fallback for missing dependency
    def human_readable_name(_manufacturer: str | None, name: str | None, address: str):
        """Fallback if bluetooth_data_tools is unavailable."""
        return name or address
from dataclasses import dataclass
import voluptuous as vol

from bleak.backends.device import BLEDevice
from bleak.backends.characteristic import BleakGATTCharacteristic

from obdii import commands as obdii_commands
from obdii.protocol import Protocol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_ble_device_from_address,
    async_discovered_service_info,
)
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers import device_registry, selector

from . import Obd2BleConfigEntry
from .coordinator import DEFAULT_SLOW_POLL, DEFAULT_XS_POLL, Obd2BleDataUpdateCoordinator
from .const import (
    CONF_AUTO_CONFIGURE,
    CONF_CACHED_VALUES,
    CONF_COMMANDS,
    CONF_FAST_POLL,
    CONF_SLOW_POLL,
    CONF_XS_POLL,
    DEFAULT_CACHED_VALUES,
    DEFAULT_FAST_POLL,
    DOMAIN,
    CONF_CHARACTERISTIC_UUID_READ,
    CONF_CHARACTERISTIC_UUID_WRITE,
    CONF_PROTOCOL,
    DEFAULT_CHARACTERISTIC_UUID_READ,
    DEFAULT_CHARACTERISTIC_UUID_WRITE,
)
from .obdii.transport_ble import TransportBLE
from .obdii.transport_ble_identifiers import AVAILABLE_OBD2_CLASSES, BaseOBD2, advertisement_matches

_LOGGER = logging.getLogger(__name__)


@dataclass
class DiscoveredDevice:
    """A discovered Bluetooth device."""

    name: str
    discovery_info: BluetoothServiceInfoBleak
    type: str

    def model(self) -> str:
        """Return BMS type in capital letters, e.g. 'DUMMY OBDII'."""
        return self.type.rsplit(".", 1)[1].replace("_", " ").upper()


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow handler."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    def __init__(self) -> None:
        """Initialize."""
        self._errors = {}
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        # self._discovered_devices: dict[str, DiscoveredDevice] = {}
        self._characteristic_uuid_read: str = DEFAULT_CHARACTERISTIC_UUID_READ
        self._characteristic_uuid_write: str = DEFAULT_CHARACTERISTIC_UUID_WRITE
        self._protocol: Protocol = Protocol.AUTO

        self._obdii_dev: type[BaseOBD2] | None = None
        self._transport: TransportBLE | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: Obd2BleConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the options flow."""
        return Obd2BleOptionsFlowHandler()

    async def _async_device_supported(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> type[BaseOBD2] | None:
        """Check if device is supported by an available OBD2 BLE class."""
        for obd2_class in AVAILABLE_OBD2_CLASSES:
            if all([advertisement_matches(matcher, discovery_info.advertisement, discovery_info.address) for matcher in obd2_class.matcher_dict_list()]):
                _LOGGER.debug(
                    "Device %s (%s) detected as '%s'",
                    discovery_info.name,
                    device_registry.format_mac(discovery_info.address),
                    # obd2_class.obd2_id(),
                    obd2_class.__name__,
                )
                return obd2_class
        return None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> config_entries.ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        # if not (obd2_class := await self._async_device_supported(discovery_info)):
        if not (await self._async_device_supported(discovery_info)):
            return self.async_abort(reason="not_supported")

        self._discovery_info = discovery_info
        self.context["title_placeholders"] = {
            "name": human_readable_name(
                None, discovery_info.name, discovery_info.address
            )
        }
        return await self.async_step_user()

    async def async_step_user(self, user_input: dict | None = None) -> config_entries.ConfigFlowResult:
        """Handle the user step to pick discovered device."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._discovery_info = self._discovered_devices[user_input[CONF_ADDRESS]]
            await self.async_set_unique_id(
                self._discovery_info.address, raise_on_progress=False
            )
            self._abort_if_unique_id_configured()

            if user_input.get(CONF_AUTO_CONFIGURE, True):
                if obdii_dev := await self._async_device_supported(self._discovery_info):
                    _LOGGER.debug("Auto-configuring device %s using class %s", self._discovery_info.name, obdii_dev.__name__)
                    self._obdii_dev = obdii_dev
                    self._characteristic_uuid_read = obdii_dev.uuid_rx()
                    self._characteristic_uuid_write = obdii_dev.uuid_tx()
                else:
                    _LOGGER.warning("Device %s does not match any known OBD2 classes, auto-configuration may fail", self._discovery_info.name)

            ble_device: BLEDevice | None = async_ble_device_from_address(
                self.hass, self._discovery_info.address, True
            )
            assert ble_device is not None, "Device disappeared after selection - this should not happen"
            self._transport = TransportBLE(
                ble_device=ble_device,
                uuid_write=self._characteristic_uuid_write,
                uuid_read=self._characteristic_uuid_read,
                loop=self.hass.loop,
            )
            await self._transport.async_connect()

            return await self.async_step_connection()

        if discovery := self._discovery_info:
            self._discovered_devices[discovery.address] = discovery
        else:
            current_addresses = self._async_current_ids()
            for discovery in async_discovered_service_info(self.hass):
                if (
                    discovery.address in current_addresses
                    or discovery.address in self._discovered_devices
                    or not (await self._async_device_supported(discovery))
                    # or not (obd2_class := await self._async_device_supported(discovery))
                ):
                    continue
                self._discovered_devices[discovery.address] = discovery

        if not self._discovered_devices:
            return self.async_abort(reason="no_unconfigured_devices")

        data_schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS): vol.In(
                    {
                        service_info.address: f"{service_info.name} ({service_info.address})"
                        for service_info in self._discovered_devices.values()
                    }
                ),
                vol.Required(
                    CONF_AUTO_CONFIGURE, default=True
                ): bool,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    async def async_step_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        # if not self.options:
        #     self.options = dict(self.config_entry.options)

        if user_input is not None:
            self._characteristic_uuid_read = user_input[CONF_CHARACTERISTIC_UUID_READ]
            self._characteristic_uuid_write = user_input[CONF_CHARACTERISTIC_UUID_WRITE]
            self._protocol = Protocol(int(user_input[CONF_PROTOCOL]))
            if self._transport is not None and self._transport.is_connected():
                await self._transport.async_close()

            if self.source == config_entries.SOURCE_RECONFIGURE:
                reconfigure_entry = self._get_reconfigure_entry()
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data={
                        **reconfigure_entry.data,
                        CONF_CHARACTERISTIC_UUID_READ: self._characteristic_uuid_read,
                        CONF_CHARACTERISTIC_UUID_WRITE: self._characteristic_uuid_write,
                        CONF_PROTOCOL: self._protocol,
                    },
                )

            assert self._discovery_info is not None, "Discovery info should have been set by now"
            return self.async_create_entry(
                title= self._discovery_info.name,
                data={
                    CONF_ADDRESS: self._discovery_info.address,
                    CONF_CHARACTERISTIC_UUID_READ: self._characteristic_uuid_read,
                    CONF_CHARACTERISTIC_UUID_WRITE: self._characteristic_uuid_write,
                    CONF_PROTOCOL: self._protocol,
                },
            )
        
        assert self._transport is not None and self._transport.is_connected(), "Transport should have been initialized and connected by now"
        characteristics: list[BleakGATTCharacteristic] = []
        for service in self._transport.get_service_collection():
            characteristics.extend(service.characteristics)
        if not characteristics:
            raise ValueError(f"No characteristics found")

        return self.async_show_form(
            step_id="connection",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CHARACTERISTIC_UUID_READ, default=self._characteristic_uuid_read): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": characteristic.uuid,
                                    "label": f"{characteristic.description} {characteristic.uuid.split('-')[0]}"
                                } for characteristic in characteristics],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key="ble_read_characteristics",
                        )
                    ),
                    vol.Required(CONF_CHARACTERISTIC_UUID_WRITE, default=self._characteristic_uuid_write): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": characteristic.uuid,
                                    "label": f"{characteristic.description} {characteristic.uuid.split('-')[0]}"
                                } for characteristic in characteristics],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key="ble_write_characteristics",
                        )
                    ),
                    vol.Required(CONF_PROTOCOL, default=str(self._protocol.value)): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": str(protocol.value),
                                    "label": f"{protocol.name} ({protocol.value})"
                                } for protocol in Protocol if protocol != Protocol.UNKNOWN],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key="obdii_protocol",
                        ),
                    ),
                }
            )
        )

    async def async_step_reconfigure(self, user_input: dict | None = None) -> config_entries.ConfigFlowResult:
        self._transport = self._get_reconfigure_entry().runtime_data.api.transport
        assert self._transport is not None, "Transport should have been initialized by now"
        if not self._transport.is_connected():
            await self._transport.async_connect()
        self._characteristic_uuid_read = self._get_reconfigure_entry().data.get(CONF_CHARACTERISTIC_UUID_READ, DEFAULT_CHARACTERISTIC_UUID_READ)
        self._characteristic_uuid_write = self._get_reconfigure_entry().data.get(CONF_CHARACTERISTIC_UUID_WRITE, DEFAULT_CHARACTERISTIC_UUID_WRITE)
        self._protocol = self._get_reconfigure_entry().data.get(CONF_PROTOCOL, Protocol.AUTO)
        return await self.async_step_connection(user_input)

    @callback
    def async_remove(self) -> None:
        """Handle flow removal/cancellation."""
        if self._transport and self._transport.is_connected():
            _LOGGER.debug("Config flow cancelled/removed. Forcing BLE disconnect.")
            self._transport.close()
        super().async_remove()

class Obd2BleOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """Config flow options handler for obd2_ble."""

    def __init__(self) -> None:
        """Initialize options flow."""
        self._options: dict = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """First step: Display the built-in option menu selection."""
        if not self._options:
            self._options = dict(self.config_entry.options)

        # Home Assistant takes a list of step IDs and renders them as a menu.
        # Clicking a button automatically calls async_step_<step_id>
        return self.async_show_menu(
            step_id="init",
            menu_options=["polling", "commands"],
            description_placeholders={
                "polling": "Configure polling intervals for different device states",
                "commands": "Configure custom OBD-II commands"
            }
        )

    async def async_step_polling(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:

        if user_input is not None:
            self._options.update(user_input)
            return self.async_create_entry(
                title=self.config_entry.data.get(CONF_ADDRESS),
                data=self._options,
            )
        
        return self.async_show_form(
            step_id="polling",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CACHED_VALUES, default=self._options.get(CONF_CACHED_VALUES, DEFAULT_CACHED_VALUES)
                    ): bool,
                    vol.Required(
                        CONF_FAST_POLL, default=self._options.get(CONF_FAST_POLL, DEFAULT_FAST_POLL)
                    ): int,
                    vol.Required(
                        CONF_SLOW_POLL, default=self._options.get(CONF_SLOW_POLL, DEFAULT_SLOW_POLL)
                    ): int,
                    vol.Required(
                        CONF_XS_POLL, default=self._options.get(CONF_XS_POLL, DEFAULT_XS_POLL)
                    ): int,
                }
            ),
        )

    async def async_step_commands(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:

        if user_input is not None:
            # self._options.update(user_input)
            self._options[CONF_COMMANDS] = [obdii_commands[cmd_name] for cmd_name in user_input[CONF_COMMANDS]]
            return self.async_create_entry(
                title=self.config_entry.data.get(CONF_ADDRESS),
                data=self._options,
            )
        
        _, commands = await self.config_entry.runtime_data.async_get_all_pid_commands(force_refresh=True)

        return self.async_show_form(
            step_id="commands",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_COMMANDS, default=[cmd.name for cmd in self._options.get(CONF_COMMANDS, [])]): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=[
                                {
                                    "value": command.name,
                                    "label": f"{command.name} ({command.mode} {command.pid})"
                                } for command in commands],
                            mode=selector.SelectSelectorMode.DROPDOWN,
                            translation_key="ble_services",
                            multiple=True,
                        )
                    ),
                }
            ),
        )
