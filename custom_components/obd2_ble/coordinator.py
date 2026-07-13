"""Coordinator for OBD2 BLE."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import logging
from time import monotonic
from typing import Any, Iterator

from bleak.backends.device import BLEDevice

from obdii import Command, Connection, Protocol, Response, at_commands, commands as veh_commands, __version__ as obdii_version
from obdii.basetypes import MISSING
from obdii.errors import CanError, MissingDataError, ProtocolConnectionError, ResponseError

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
from homeassistant.components.bluetooth.api import async_address_present
from homeassistant.components.bluetooth.const import DOMAIN as BLUETOOTH_DOMAIN
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import ConditionError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CACHED_VALUES,
    CONF_FAST_POLL,
    CONF_HW_VERSION,
    CONF_SLOW_POLL,
    CONF_XS_POLL,
    CONF_BLE_TIMEOUT,
    CONF_CHARACTERISTIC_UUID_READ,
    CONF_CHARACTERISTIC_UUID_WRITE,
    CONF_PROTOCOL,
    DOMAIN,
    DEFAULT_FAST_POLL,
    DEFAULT_SLOW_POLL,
    DEFAULT_XS_POLL,
    DEFAULT_BLE_TIMEOUT,
    DEFAULT_CACHED_VALUES,
    DEFAULT_CHARACTERISTIC_UUID_READ,
    DEFAULT_CHARACTERISTIC_UUID_WRITE,
)
from .obdii.transport_ble import TransportBLE
from .obdii.transport_ble_identifiers import AVAILABLE_OBD2_CLASSES, advertisement_matches
try:
    from .debug import FAKE_COMMANDS
except ImportError:
    FAKE_COMMANDS: list[Command] = []

_LOGGER = logging.getLogger(__name__)
logging.getLogger("obdii.connection").setLevel(logging.WARNING)
ECU_HEALTH_COMMANDS = tuple(
    command
    for command in (
        veh_commands[1][0x00],  # supported PIDs 01-20
        veh_commands["ENGINE_SPEED"],
        veh_commands[1][0x1F],  # run time since engine start
        veh_commands[1][0x42],  # vehicle/control module voltage
    )
    if command is not None
)
AUTO_PROTOCOL_CANDIDATES = (
    Protocol.ISO_15765_4_CAN,
)
EXPECTED_PROBE_LOGGERS = (
    "obdii.protocols.protocol_can",
    "obdii.protocols.protocol_kwp",
    "obdii.protocols.protocol_base",
    "obdii.protocols.mixins",
)
DEFAULT_FUNCTIONAL_CAN_HEADER = "7DF"
MISSING_ADAPTER_FAST_RETRIES = 12
MISSING_ADAPTER_CLOSE_RETRIES = 3
CONNECT_FAILURE_PROTOCOL_RETRIES = 3
STATE_STARTUP_DEFERRED = "startup_deferred"
STATE_ADAPTER_MISSING = "adapter_missing"
STATE_ADAPTER_VISIBLE = "adapter_visible"
STATE_BLE_CONNECTED = "ble_connected"
STATE_ECU_LIVE = "ecu_live"


@contextmanager
def _suppress_expected_probe_logs() -> Iterator[None]:
    """Mute noisy protocol-level logs for expected car-off probe failures."""
    loggers = [logging.getLogger(name) for name in EXPECTED_PROBE_LOGGERS]
    previous_levels = [logger.level for logger in loggers]
    try:
        for logger in loggers:
            logger.setLevel(logging.CRITICAL + 1)
        yield
    finally:
        for logger, level in zip(loggers, previous_levels, strict=True):
            logger.setLevel(level)

type Obd2BleConfigEntry = ConfigEntry[Obd2BleDataUpdateCoordinator]


class Obd2BleDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Response]]):
    """Class to manage fetching data from the API."""

    def __init__(
        self, hass: HomeAssistant, entry: Obd2BleConfigEntry
    ) -> None:
        """Initialize."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=entry.options.get(CONF_FAST_POLL, DEFAULT_FAST_POLL)),
            always_update=True,
            config_entry=entry,
        )

        self._cache_data: dict[str, Response] = {}
        self._supported_pids = []
        self._supported_cmds = []
        self._last_fetch_successful = False
        self.last_successful_update: datetime | None = None
        self.last_error: str | None = None
        self.last_connect_duration_ms: int | None = None
        self.connection_state = STATE_STARTUP_DEFERRED

        self.transport: TransportBLE | None = None
        self.api: Connection | None = None
        self.active_commands: set[Command] = set()
        self._protocol_candidate_index = 0
        self._last_requested_protocol: Protocol | None = None
        self._last_active_protocol: Protocol | None = None
        self._active_obd_header: str | None = None
        self._active_ble_address: str | None = None
        self._ecu_seen = False
        self._connect_failures = 0
        self._ecu_failures = 0
        self._missing_adapter_retries = 0
        self._last_bluetooth_refresh_request = 0.0
        self._resolved_address: str = entry.data.get(CONF_ADDRESS, entry.unique_id)
        self._last_resolved_service_info: BluetoothServiceInfoBleak | None = None

        if not entry or not entry.unique_id:
            raise ConditionError("No unique_id found in config entry")

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id), (BLUETOOTH_DOMAIN, entry.unique_id)},
            connections={(CONNECTION_BLUETOOTH, entry.unique_id)},
            hw_version=entry.data.get(CONF_HW_VERSION),
            model_id=Protocol(entry.data.get(CONF_PROTOCOL, Protocol.AUTO.value)).name,
            sw_version=obdii_version,
        )

        # self._connect_ble()

    def _configured_protocol(self) -> Protocol:
        """Return the user-configured protocol."""
        return Protocol(self.config_entry.data.get(CONF_PROTOCOL, Protocol.AUTO.value))

    def _configured_address(self) -> str:
        """Return the current configured BLE address."""
        return self.config_entry.data.get(CONF_ADDRESS, self.config_entry.unique_id)

    def _protocol_candidates(self) -> tuple[Protocol, ...]:
        """Return protocol candidates for the current config entry."""
        configured = self._configured_protocol()
        if configured != Protocol.AUTO:
            return (configured,)
        return AUTO_PROTOCOL_CANDIDATES

    def _current_protocol_candidate(self) -> Protocol:
        """Return the protocol candidate to try on the next connection."""
        candidates = self._protocol_candidates()
        return candidates[self._protocol_candidate_index % len(candidates)]

    def _advance_protocol_candidate(self) -> None:
        """Advance to the next protocol candidate when AUTO probing fails."""
        if self._ecu_seen:
            _LOGGER.debug(
                "Keeping OBD2 protocol pinned to %s after prior ECU success",
                self._current_protocol_candidate().name,
            )
            return
        candidates = self._protocol_candidates()
        if len(candidates) <= 1:
            return
        self._protocol_candidate_index = (self._protocol_candidate_index + 1) % len(candidates)
        _LOGGER.debug(
            "Advancing OBD2 AUTO protocol probe to %s",
            candidates[self._protocol_candidate_index].name,
        )

    def _reset_protocol_candidate(self) -> None:
        """Keep using the current protocol after a successful ECU response."""
        candidates = self._protocol_candidates()
        if self._last_requested_protocol in candidates:
            self._protocol_candidate_index = candidates.index(self._last_requested_protocol)

    def _close_api(self) -> None:
        """Close and clear the active OBD connection."""
        if self.api is not None:
            try:
                self.api.close()
            finally:
                self.api = None
                self.transport = None
                self._active_obd_header = None
                self._active_ble_address = None
                self._ecu_failures = 0

    def _mark_car_disconnected(self, error: str) -> None:
        """Clear live-car state after an adapter or ECU disconnect."""
        self._last_fetch_successful = False
        self.last_error = error

    def _mark_connection_state(self, state: str) -> None:
        """Update the coarse connection state diagnostic."""
        if self.connection_state != state:
            _LOGGER.info("OBD2 BLE connection state: %s -> %s", self.connection_state, state)
        self.connection_state = state

    def _missing_adapter_interval(self) -> timedelta:
        """Return retry interval for a missing adapter before long backoff."""
        self._missing_adapter_retries += 1
        if self._missing_adapter_retries <= MISSING_ADAPTER_FAST_RETRIES:
            return timedelta(
                seconds=self.config_entry.options.get(CONF_SLOW_POLL, DEFAULT_SLOW_POLL)
            )
        return timedelta(
            seconds=self.config_entry.options.get(CONF_XS_POLL, DEFAULT_XS_POLL)
        )

    def _slow_poll_interval(self) -> timedelta:
        """Return the configured slow polling interval."""
        return timedelta(
            seconds=self.config_entry.options.get(CONF_SLOW_POLL, DEFAULT_SLOW_POLL)
        )

    def _reset_missing_adapter_retries(self) -> None:
        """Reset missing-adapter backoff once HA can see the adapter again."""
        self._missing_adapter_retries = 0

    def _service_info_matches_obd2(self, service_info: BluetoothServiceInfoBleak) -> bool:
        """Return whether a discovered advertisement looks like an OBD2 adapter."""
        if any(
            advertisement_matches(matcher, service_info.advertisement, service_info.address)
            for obd2_class in AVAILABLE_OBD2_CLASSES
            for matcher in obd2_class.matcher_dict_list()
        ):
            return True

        return False

    def _resolve_ble_address(self, configured_address: str) -> str:
        """Return the safest BLE address to try.

        During HA startup, stay pinned to the configured address. Once HA is
        running, allow a temporary OBD-like candidate so rotating BLE addresses
        can recover, but only a successful ECU health probe persists the change.
        """
        if self.hass.state is not CoreState.running:
            self._resolved_address = configured_address
            self._last_resolved_service_info = None
            return configured_address

        configured_connectable = async_address_present(
            self.hass,
            configured_address,
            connectable=True,
        )
        if configured_connectable:
            self._resolved_address = configured_address
            self._last_resolved_service_info = None
            return configured_address

        discovered = [
            service_info
            for service_info in bluetooth.async_discovered_service_info(self.hass)
            if self._service_info_matches_obd2(service_info)
        ]
        best_by_address: dict[str, BluetoothServiceInfoBleak] = {}
        for service_info in discovered:
            current = best_by_address.get(service_info.address)
            if current is None or service_info.rssi > current.rssi:
                best_by_address[service_info.address] = service_info

        if not best_by_address:
            self._resolved_address = configured_address
            self._last_resolved_service_info = None
            return configured_address

        configured_info = best_by_address.get(configured_address)
        if configured_info is not None and async_address_present(
            self.hass,
            configured_address,
            connectable=False,
        ):
            self._resolved_address = configured_address
            self._last_resolved_service_info = configured_info
            return configured_address

        connectable_candidates = [
            service_info
            for service_info in best_by_address.values()
            if async_address_present(self.hass, service_info.address, connectable=True)
        ]
        if not connectable_candidates:
            self._resolved_address = configured_address
            self._last_resolved_service_info = configured_info
            return configured_address

        best_info = max(connectable_candidates, key=lambda service_info: service_info.rssi)
        if best_info.address != self._resolved_address:
            _LOGGER.warning(
                "Configured OBD2 adapter %s is not connectable; trying discovered OBD-like adapter %s (%s, RSSI=%s) after startup",
                configured_address,
                best_info.address,
                best_info.name or best_info.advertisement.local_name,
                best_info.rssi,
            )
        self._resolved_address = best_info.address
        self._last_resolved_service_info = best_info
        return best_info.address

    def _get_ble_device(self, address: str) -> BLEDevice | None:
        """Return a BLEDevice for an address, falling back to discovery records."""
        ble_device = bluetooth.async_ble_device_from_address(self.hass, address, True)
        if ble_device is not None:
            return ble_device

        if (
            self._last_resolved_service_info is not None
            and self._last_resolved_service_info.address == address
        ):
            _LOGGER.info(
                "Using cached discovery BLEDevice for OBD2 adapter %s (%s)",
                address,
                self._last_resolved_service_info.name
                or self._last_resolved_service_info.advertisement.local_name,
            )
            return self._last_resolved_service_info.device

        discovered = [
            service_info
            for service_info in bluetooth.async_discovered_service_info(self.hass)
            if self._service_info_matches_obd2(service_info)
        ]
        if not discovered:
            return None

        same_address = [
            service_info for service_info in discovered if service_info.address == address
        ]
        candidates = same_address or discovered
        best_info = max(candidates, key=lambda service_info: service_info.rssi)
        self._last_resolved_service_info = best_info
        if best_info.address != self._resolved_address:
            self._resolved_address = best_info.address
        _LOGGER.info(
            "Using discovered OBD2 BLEDevice %s (%s, RSSI=%s) after direct address lookup for %s returned none",
            best_info.address,
            best_info.name or best_info.advertisement.local_name,
            best_info.rssi,
            address,
        )
        return best_info.device

    async def _async_persist_resolved_address(self, address: str) -> None:
        """Persist a proven current BLE address without changing stable identity."""
        if address == self.config_entry.data.get(CONF_ADDRESS):
            return

        old_address = self.config_entry.data.get(CONF_ADDRESS, self.config_entry.unique_id)
        data = dict(self.config_entry.data)
        data[CONF_ADDRESS] = address
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)
        _LOGGER.warning(
            "Updated OBD2 BLE adapter address from %s to %s after successful ECU health probe",
            old_address,
            address,
        )

    def _connect_ble(self, ble_device: BLEDevice, protocol: Protocol) -> bool:
        """Connect to the BLE device."""

        if not self.config_entry or not self.config_entry.unique_id:
            _LOGGER.error("No config entry available for coordinator")
            return False

        entry_data = dict(self.config_entry.data)
        timeout = float(
            self.config_entry.options.get(CONF_BLE_TIMEOUT, DEFAULT_BLE_TIMEOUT)
        )

        self.transport = TransportBLE(
            ble_device=ble_device,
            uuid_write=entry_data.get(CONF_CHARACTERISTIC_UUID_WRITE, DEFAULT_CHARACTERISTIC_UUID_WRITE),
            uuid_read=entry_data.get(CONF_CHARACTERISTIC_UUID_READ, DEFAULT_CHARACTERISTIC_UUID_READ),
            timeout=timeout,
            loop = self.hass.loop,
        )

        self.api = Connection(
            transport=self.transport,
            auto_connect=False,
            protocol=protocol,
            log_handler=None,
            log_formatter=MISSING,
            log_level=MISSING,
        )

        self._last_requested_protocol = protocol
        started = monotonic()
        self.api.connect()
        self.last_connect_duration_ms = int((monotonic() - started) * 1000)
        self._active_ble_address = ble_device.address
        self._active_obd_header = None
        self._last_active_protocol = self.api.protocol
        _LOGGER.info(
            "Connected to OBD2 adapter over BLE in %sms using requested=%s active=%s",
            self.last_connect_duration_ms,
            protocol.name,
            self.api.protocol.name,
        )
        hw_version = self.api.query(at_commands.VERSION_ID)
        if hw_version is not None:
            entry_data[CONF_HW_VERSION] = hw_version.value if hw_version else None
            self.device_info["hw_version"] = hw_version.value if hw_version else None

        return True

    def _persist_successful_protocol(self) -> None:
        """Persist the working protocol after a real ECU response."""
        if self.api is None:
            return
        active_protocol = self.api.protocol
        if active_protocol in (Protocol.AUTO, Protocol.UNKNOWN):
            return
        if self._configured_protocol() != Protocol.AUTO:
            return

        data = dict(self.config_entry.data)
        data[CONF_PROTOCOL] = active_protocol.value
        self.hass.config_entries.async_update_entry(self.config_entry, data=data)
        self.device_info["model_id"] = active_protocol.name
        _LOGGER.warning(
            "Persisted successful OBD2 protocol %s after ECU health probe",
            active_protocol.name,
        )

    def _query_command(self, command: Command) -> Response:
        """Query a command while muting noisy expected protocol failures."""
        if self.api is None:
            raise ConnectionError("No OBD2 API connection")
        with _suppress_expected_probe_logs():
            requested_header = getattr(command, "obd_header", None)
            if requested_header is not None:
                self._set_obd_header(requested_header)
            elif self._active_obd_header is not None:
                self._set_obd_header(DEFAULT_FUNCTIONAL_CAN_HEADER)
            return self.api.query(command)

    def _set_obd_header(self, header: str) -> None:
        """Set the ELM request header when an enhanced PID needs a module target."""
        if self.api is None:
            raise ConnectionError("No OBD2 API connection")

        normalized = header.replace(" ", "").upper()
        if normalized == self._active_obd_header:
            return

        if len(normalized) == 3:
            command = at_commands.SET_HEADER_11(
                x=normalized[0],
                y=normalized[1],
                z=normalized[2],
            )
        elif len(normalized) == 6:
            command = at_commands.SET_HEADER(
                xx=normalized[0:2],
                yy=normalized[2:4],
                zz=normalized[4:6],
            )
        elif len(normalized) == 8:
            command = at_commands.SET_HEADER_29(
                ww=normalized[0:2],
                xx=normalized[2:4],
                yy=normalized[4:6],
                zz=normalized[6:8],
            )
        else:
            raise ValueError(f"Unsupported OBD header length: {header}")

        _LOGGER.debug("Setting OBD request header to %s", normalized)
        self.api.query(command)
        self._active_obd_header = normalized

    def _query_ecu_health(self) -> tuple[Command | None, Response | None]:
        """Probe for a live ECU using a few standard commands."""
        last_err: Exception | None = None
        for command in ECU_HEALTH_COMMANDS:
            try:
                response = self._query_command(command)
            except (CanError, MissingDataError, ProtocolConnectionError, TimeoutError) as err:
                last_err = err
                _LOGGER.debug("ECU health probe %s did not get data: %s", command, err)
                continue
            except ResponseError as err:
                last_err = err
                _LOGGER.debug("ECU health probe %s returned protocol error: %s", command, err)
                continue
            if response is not None and response.value is not None:
                return command, response
        if last_err is not None:
            raise last_err
        return None, None

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and any connection."""
        _LOGGER.debug("Shutting down BMS (%s)", self.name)
        await super().async_shutdown()
        try:
            if self.api is not None and self.api.is_connected():
                await self.hass.async_add_executor_job(self.api.close)
        except Exception as err:
            self.last_error = f"Error occurred while closing API connection: {err}"
            _LOGGER.warning(self.last_error)
        else:
            _LOGGER.debug("API connection closed successfully")

    async def _async_update_data(self) -> dict[str, Response]:
        """Update data via library."""

        if not self.config_entry or not self.config_entry.unique_id:
            _LOGGER.error("No config entry available for coordinator")
            return {}

        if self.hass.state is not CoreState.running:
            self._mark_car_disconnected("Home Assistant startup is not complete")
            self._mark_connection_state(STATE_STARTUP_DEFERRED)
            self.update_interval = None
            _LOGGER.debug("Deferring OBD2 BLE update until Home Assistant startup completes")
            if self.config_entry.options.get(CONF_CACHED_VALUES, DEFAULT_CACHED_VALUES):
                return self._cache_data
            return {}

        configured_address = self._configured_address()
        address = self._resolve_ble_address(configured_address)
        connected = self.api is not None and self.api.is_connected()
        if self.api is not None and self._active_ble_address != address:
            _LOGGER.info(
                "Resolved OBD2 BLE address changed from %s to %s; rebuilding BLE session",
                self._active_ble_address,
                address,
            )
            await self.hass.async_add_executor_job(self._close_api)
            connected = False
        present = connected or async_address_present(self.hass, address, connectable=False)
        connectable = connected or async_address_present(self.hass, address, connectable=True)

        _LOGGER.debug(
            "Bluetooth state for %s: present=%s connectable=%s connected=%s",
            address,
            present,
            connectable,
            connected,
        )
        if not present:
            self._mark_car_disconnected("Bluetooth device is not currently present")
            self._mark_connection_state(STATE_ADAPTER_MISSING)
            self.update_interval = self._missing_adapter_interval()
            if self._missing_adapter_retries >= MISSING_ADAPTER_CLOSE_RETRIES:
                await self.hass.async_add_executor_job(self._close_api)
            _LOGGER.info(
                "Bluetooth adapter is missing; retry %s/%s in %s",
                self._missing_adapter_retries,
                MISSING_ADAPTER_FAST_RETRIES,
                self.update_interval,
            )
            if self.config_entry.options.get(CONF_CACHED_VALUES, DEFAULT_CACHED_VALUES):
                return self._cache_data
            return {}

        self._reset_missing_adapter_retries()

        if not connectable:
            self._mark_connection_state(STATE_ADAPTER_VISIBLE)
            _LOGGER.info(
                "OBD2 adapter %s is present but HA reports connectable=False; attempting direct GATT anyway",
                address,
            )
        
        if self.api is None:
            ble_device = self._get_ble_device(address)
            if ble_device is None:
                self._mark_car_disconnected(f"Failed to discover device {address} via Bluetooth")
                self._mark_connection_state(STATE_ADAPTER_VISIBLE)
                self.update_interval = self._slow_poll_interval()
                _LOGGER.info(self.last_error)
                if self.config_entry.options.get(CONF_CACHED_VALUES, DEFAULT_CACHED_VALUES):
                    return self._cache_data
                return {}
            protocol = self._current_protocol_candidate()
            connect_started = monotonic()
            try:
                _LOGGER.info("Connecting to OBD2 adapter using protocol candidate %s", protocol.name)
                connected = await self.hass.async_add_executor_job(
                    self._connect_ble, ble_device, protocol
                )
            except Exception as err:
                self.last_connect_duration_ms = int((monotonic() - connect_started) * 1000)
                self._mark_car_disconnected(f"Error connecting with OBD2: {err!r}")
                self._mark_connection_state(STATE_ADAPTER_VISIBLE)
                await self.hass.async_add_executor_job(self._close_api)
                self._connect_failures += 1
                if self._connect_failures >= CONNECT_FAILURE_PROTOCOL_RETRIES:
                    self._connect_failures = 0
                    _LOGGER.info(
                        "Keeping OBD2 protocol candidate at %s after BLE connect failures",
                        self._current_protocol_candidate().name,
                    )
                self.update_interval = self._slow_poll_interval()
                _LOGGER.info(
                    "OBD2 adapter is visible but not responding; retrying in %s: %r",
                    self.update_interval,
                    err,
                )
                if self.config_entry.options.get(CONF_CACHED_VALUES, DEFAULT_CACHED_VALUES):
                    return self._cache_data
                return {}
            if not connected:
                self._mark_car_disconnected("OBD2 adapter did not connect")
                self._mark_connection_state(STATE_ADAPTER_VISIBLE)
                await self.hass.async_add_executor_job(self._close_api)
                self.update_interval = self._slow_poll_interval()
                return {}

        assert self.api is not None, "API should be initialized at this point"
        _LOGGER.debug("Device is available, check if connected")
        if not self.api.is_connected():
            connect_started = monotonic()
            try:
                _LOGGER.info("Device is available but not connected, attempt to connect")
                await self.hass.async_add_executor_job(self.api.connect)
                self.last_connect_duration_ms = int((monotonic() - connect_started) * 1000)
                if not self.api.is_connected():
                    raise UpdateFailed("No connection to OBD2 after connect attempt")
            except Exception as err:
                self.last_connect_duration_ms = int((monotonic() - connect_started) * 1000)
                self._mark_car_disconnected(f"Error connecting with OBD2: {err!r}")
                self._mark_connection_state(STATE_ADAPTER_VISIBLE)
                self._connect_failures += 1
                if self._connect_failures >= CONNECT_FAILURE_PROTOCOL_RETRIES:
                    await self.hass.async_add_executor_job(self._close_api)
                    self._connect_failures = 0
                    _LOGGER.info(
                        "Keeping OBD2 protocol candidate at %s after reconnect failures",
                        self._current_protocol_candidate().name,
                    )
                self.update_interval = self._slow_poll_interval()
                _LOGGER.debug(
                    "OBD2 adapter is visible but not responding; retrying in %s: %r",
                    self.update_interval,
                    err,
                )
                if self.config_entry.options.get(CONF_CACHED_VALUES, DEFAULT_CACHED_VALUES):
                    return self._cache_data
                return {}

        _LOGGER.debug("OBD2 BLE GATT session is connected; querying ECU data")
        self._mark_connection_state(STATE_BLE_CONNECTED)
        try:
            self._last_fetch_successful = False
            new_data: dict[str, Response] = {}
            ecu_command: Command | None = None
            ecu_response: Response | None = None
            try:
                _LOGGER.debug(
                    "Querying OBD2 health probes over requested=%s active=%s",
                    self._last_requested_protocol.name if self._last_requested_protocol else None,
                    self.api.protocol.name if self.api else None,
                )
                ecu_command, ecu_response = await self.hass.async_add_executor_job(
                    self._query_ecu_health
                )
                _LOGGER.debug(
                    "Received health probe response for %s: %s",
                    ecu_command,
                    ecu_response,
                )
            except Exception as err:
                self.last_error = f"Error occurred while querying ECU health probe: {err}"
                _LOGGER.debug("ECU health probe failed: %s", err)

            ecu_detected = ecu_response is not None and ecu_response.value is not None
            if ecu_detected:
                self._ecu_seen = True
                self._connect_failures = 0
                self._ecu_failures = 0
                self._mark_connection_state(STATE_ECU_LIVE)
                self._reset_protocol_candidate()
                self._persist_successful_protocol()
                await self._async_persist_resolved_address(address)
            for command in self.active_commands:
                if command is None:
                    _LOGGER.warning("Skipping invalid command: %s", command)
                    continue
                if command == ecu_command and ecu_response is not None:
                    new_data[command.name] = ecu_response
                    continue
                if not ecu_detected:
                    continue
                try:
                    _LOGGER.debug("Querying OBD2 for command %s", command)
                    response: Response = await self.hass.async_add_executor_job(self._query_command, command)
                    _LOGGER.debug("Received response for command %s: %s", command, response)
                    if response is not None and response.value is not None:
                        new_data[command.name] = response
                    else:
                        _LOGGER.debug("Received empty response for command %s", command)
                except (ResponseError, TimeoutError) as err:
                    self.last_error = f"Command {command} did not return data: {err}"
                    _LOGGER.debug("Command %s did not return data: %s", command, err)
                except Exception as err:
                    self.last_error = f"Error occurred while querying command {command}: {err}"
                    _LOGGER.warning("Error occurred while querying command %s: %s", command, err)
            if not ecu_detected and len(new_data) == 0:
                self._mark_car_disconnected(
                    self.last_error or "ECU did not respond to OBD2 health probes"
                )
                ble_session_connected = self.api is not None and self.api.is_connected()
                if ble_session_connected:
                    self._mark_connection_state(STATE_BLE_CONNECTED)
                    self._ecu_failures += 1
                    _LOGGER.debug(
                        "ECU did not respond over healthy BLE session; keeping GATT open for wake retry (failure %s/%s)",
                        self._ecu_failures,
                        CONNECT_FAILURE_PROTOCOL_RETRIES,
                    )
                else:
                    self._mark_connection_state(STATE_ADAPTER_VISIBLE)
                    await self.hass.async_add_executor_job(self._close_api)
                if self._ecu_failures >= CONNECT_FAILURE_PROTOCOL_RETRIES:
                    _LOGGER.info(
                        "ECU still has not responded after %s probes; keeping BLE open on protocol %s",
                        self._ecu_failures,
                        self._current_protocol_candidate().name,
                    )
                    self._ecu_failures = 0
                self.update_interval = timedelta(
                    seconds=self.config_entry.options.get(CONF_FAST_POLL, DEFAULT_FAST_POLL)
                ) if ble_session_connected else self._slow_poll_interval()
                _LOGGER.debug(
                    "ECU did not respond; retrying in %s",
                    self.update_interval,
                )
            else:
                self._last_fetch_successful = True
                self.last_error = None
                self.last_successful_update = datetime.now(UTC)
                self.update_interval = timedelta(seconds=self.config_entry.options.get(CONF_FAST_POLL, DEFAULT_FAST_POLL))
                _LOGGER.info(
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

        if self.api is None or not self.api.is_connected():
            raise UpdateFailed("No connection to OBD2 to get supported PIDs and Commands")
        
        self._supported_pids = []
        self._supported_cmds = FAKE_COMMANDS.copy()
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
    
    def ble_found(self) -> bool:
        if self.ble_reachable():
            return True
        if not self.config_entry or not self.config_entry.unique_id:
            return False
        address = self._configured_address()
        address = self._resolve_ble_address(address)
        return async_address_present(self.hass, address, connectable=False)

    def ble_reachable(self) -> bool:
        """Return whether HA currently sees a connectable BLE adapter."""
        if self.ble_connected():
            return True
        if not self.config_entry or not self.config_entry.unique_id:
            return False
        address = self._resolve_ble_address(self._configured_address())
        return async_address_present(
            self.hass,
            address,
            connectable=True,
        )

    def ble_connected(self) -> bool:
        """Return whether the integration currently holds an active GATT session."""
        if self.api and self.api.is_connected():
            return True
        return False

    def car_connected(self) -> bool:
        return self._last_fetch_successful

    def update_interval_seconds(self) -> int | None:
        """Return the current coordinator polling interval in seconds."""
        if self.update_interval is None:
            return None
        return int(self.update_interval.total_seconds())

    def active_command_count(self) -> int:
        """Return the number of commands currently registered by entities."""
        return len(self.active_commands)

    def connection_state_value(self) -> str:
        """Return the coarse connection state diagnostic."""
        return self.connection_state

    def last_connect_duration_ms_value(self) -> int | None:
        """Return the last BLE connect duration in milliseconds."""
        return self.last_connect_duration_ms

    def _raw_probe(self, command: str) -> str:
        """Send a raw ELM command over the active transport and return raw text."""
        if self.transport is None or not self.transport.is_connected():
            raise ConnectionError("BLE transport is not connected")
        normalized = "".join(command.strip().upper().split())
        if not normalized:
            raise ValueError("Command must not be empty")
        self.transport.write_bytes(f"{normalized}\r".encode())
        response = self.transport.read_bytes()
        return response.decode(errors="replace").strip()

    async def async_probe_raw(self, command: str) -> str:
        """Send a raw ELM command over the active connection."""
        return await self.hass.async_add_executor_job(self._raw_probe, command)

    def request_refresh_from_bluetooth(self) -> None:
        """Request a refresh from a Bluetooth rediscovery callback with debounce."""
        if self.hass.state is not CoreState.running:
            _LOGGER.debug("Skipping OBD2 BLE rediscovery refresh during HA startup")
            return
        if self.update_interval is None:
            self.update_interval = self._slow_poll_interval()
        now = self.hass.loop.time()
        if now - self._last_bluetooth_refresh_request < 2:
            return
        self._last_bluetooth_refresh_request = now
        self.hass.async_create_task(self.async_request_refresh())

    def request_refresh_after_setup(self) -> None:
        """Start coordinator polling after setup or HA startup completes."""
        if self.hass.state is not CoreState.running:
            _LOGGER.debug("Skipping OBD2 BLE setup refresh during HA startup")
            return
        if self.update_interval is None:
            self.update_interval = self._slow_poll_interval()
        _LOGGER.info("Starting OBD2 BLE coordinator polling after setup")
        self.hass.async_create_task(self.async_request_refresh())

    async def async_force_update(self) -> bool:
        """Force an update of the coordinator data by calling the update method directly."""
        try:
            await self._async_update_data()
            if self.car_connected():
                _LOGGER.info("Successfully connected to the car during forced update")
                return True
        except Exception as err:
            self.last_error = f"Error during forced update: {err}"
            _LOGGER.error(f"Error during forced update: {err}")
        return False
