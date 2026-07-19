import asyncio
import logging

from bleak import BleakClient
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.service import BleakGATTServiceCollection
from bleak_retry_connector import establish_connection, BleakClientWithServiceCache

from threading import Lock, Event
from time import monotonic
from typing import Optional, Dict, Any, Coroutine

from obdii.transports.transport_base import TransportBase
from obdii.basetypes import MISSING

_LOGGER: logging.Logger = logging.getLogger(__package__)
BLE_WRITE_CHUNK_SIZE = 17
BLE_CONNECT_ATTEMPTS = 3

class TransportBLE(TransportBase):
    def __init__(
        self,
        ble_device: BLEDevice = MISSING,
        uuid_write: str = MISSING,
        uuid_read: str = MISSING,
        timeout: float = 8.0,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        **kwargs,
    ) -> None:
        if ble_device is MISSING or uuid_write is MISSING or uuid_read is MISSING:
            raise ValueError(
                "ble_device (%s), uuid_write (%s) and uuid_read (%s) must be specified for TransportBLE.",
                ble_device, uuid_write, uuid_read
            )

        self.config: Dict[str, Any] = {
            "uuid_write": uuid_write,
            "uuid_read": uuid_read,
            "timeout": timeout,
            **kwargs,
        }

        self._ble_device = ble_device
        self._ble_conn: Optional[BleakClient] = None
        self._read_char: BleakGATTCharacteristic | None = None
        self._write_char: BleakGATTCharacteristic | None = None
        self._buffer = bytearray()
        self._lock = Lock()
        self._data_ready = Event()
        self._loop = loop

    def __repr__(self) -> str:
        return f"<TransportBLE {self._ble_device}>"

    def _run_coro(self, coro: Coroutine, timeout: float | None = None) -> Any:
        if self._loop is None:
            raise RuntimeError("Event loop is not running.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout or self.config["timeout"])

    def _connect_timeout(self) -> float:
        return (float(self.config["timeout"]) * BLE_CONNECT_ATTEMPTS) + 5.0
    
    def _notify_callback(self, _, data: bytearray) -> None:
        with self._lock:
            self._buffer.extend(data)
        self._data_ready.set()

    def _resolve_characteristic(
        self,
        uuid: str,
        preferred_properties: set[str],
    ) -> BleakGATTCharacteristic:
        if self._ble_conn is None:
            raise RuntimeError("BLE connection is not established.")

        matches: list[BleakGATTCharacteristic] = []
        for service in self._ble_conn.services:
            matches.extend(
                characteristic
                for characteristic in service.characteristics
                if characteristic.uuid.lower() == uuid.lower()
            )

        if not matches:
            raise ValueError(f"Characteristic {uuid} was not found.")

        _LOGGER.debug(
            "Found %s BLE characteristic candidate(s) for %s: %s",
            len(matches),
            uuid,
            ", ".join(self._format_characteristic(characteristic) for characteristic in matches),
        )
        for characteristic in matches:
            if preferred_properties.intersection(characteristic.properties):
                _LOGGER.debug(
                    "Selected BLE characteristic %s for preferred properties %s",
                    self._format_characteristic(characteristic),
                    sorted(preferred_properties),
                )
                return characteristic

        if len(matches) == 1:
            _LOGGER.debug(
                "Selected only BLE characteristic %s despite missing preferred properties %s",
                self._format_characteristic(matches[0]),
                sorted(preferred_properties),
            )
            return matches[0]

        raise ValueError(
            f"Multiple characteristics found for {uuid}, but none have "
            f"any of the required properties: {sorted(preferred_properties)}"
        )

    @staticmethod
    def _format_characteristic(characteristic: BleakGATTCharacteristic) -> str:
        handle = getattr(characteristic, "handle", "unknown")
        properties = ",".join(characteristic.properties) or "none"
        return f"{characteristic.uuid} handle={handle} properties={properties}"

    async def async_connect(self) -> None:
        started = monotonic()
        _LOGGER.info(
            "Attempting BLE connect to %s (%s), attempts=%s per_attempt_timeout=%ss total_wrapper_timeout=%ss",
            self._ble_device.name,
            self._ble_device.address,
            BLE_CONNECT_ATTEMPTS,
            self.config["timeout"],
            self._connect_timeout(),
        )
        last_error: Exception | None = None
        for attempt in range(1, BLE_CONNECT_ATTEMPTS + 1):
            attempt_started = monotonic()
            try:
                _LOGGER.info(
                    "BLE connect attempt %s/%s to %s (%s)",
                    attempt,
                    BLE_CONNECT_ATTEMPTS,
                    self._ble_device.name,
                    self._ble_device.address,
                )
                self._ble_conn = await asyncio.wait_for(
                    establish_connection(
                        BleakClientWithServiceCache,
                        self._ble_device,
                        self._ble_device.name or "Unknown Device",
                        max_attempts=1,
                    ),
                    timeout=self.config["timeout"],
                )
            except Exception as err:
                last_error = err
                _LOGGER.info(
                    "BLE connect attempt %s/%s to %s (%s) failed after %.0fms: %r",
                    attempt,
                    BLE_CONNECT_ATTEMPTS,
                    self._ble_device.name,
                    self._ble_device.address,
                    (monotonic() - attempt_started) * 1000,
                    err,
                )
                if attempt < BLE_CONNECT_ATTEMPTS:
                    await asyncio.sleep(0.5)
                continue
            _LOGGER.info(
                "BLE connect attempt %s/%s to %s (%s) succeeded after %.0fms",
                attempt,
                BLE_CONNECT_ATTEMPTS,
                self._ble_device.name,
                self._ble_device.address,
                (monotonic() - attempt_started) * 1000,
            )
            break
        else:
            assert last_error is not None
            raise last_error

        _LOGGER.info(
            "Connected to BLE device %s (%s) in %.2fs",
            self._ble_device.name,
            self._ble_device.address,
            monotonic() - started,
        )
        self._read_char = self._resolve_characteristic(
            self.config["uuid_read"],
            {"notify", "indicate"},
        )
        self._write_char = self._resolve_characteristic(
            self.config["uuid_write"],
            {"write", "write-without-response"},
        )
        await self._ble_conn.start_notify(self._read_char, self._notify_callback)

    async def async_close(self) -> None:
        if self._ble_conn and self._ble_conn.is_connected:
            await self._ble_conn.stop_notify(self._read_char or self.config["uuid_read"])
            await self._ble_conn.disconnect()
        self._read_char = None
        self._write_char = None
        self._ble_conn = None

    async def _write(self, query: bytes) -> None:
        if self._ble_conn is None:
            raise RuntimeError("BLE connection is not established.")
        if self._write_char is None:
            raise RuntimeError("BLE write characteristic is not resolved.")
        for offset in range(0, len(query), BLE_WRITE_CHUNK_SIZE):
            await self._ble_conn.write_gatt_char(
                self._write_char,
                query[offset : offset + BLE_WRITE_CHUNK_SIZE],
            )

    def get_service_collection(self) -> BleakGATTServiceCollection:
        if self._ble_conn is None:
            raise RuntimeError("BLE connection is not established.")
        return self._ble_conn.services

    def connect(self, loop: Optional[asyncio.AbstractEventLoop] = None, **kwargs) -> None:
        self.config.update(kwargs)

        if loop is not None:
            self._loop = loop

        try:
            self._run_coro(self.async_connect(), timeout=self._connect_timeout())
        except Exception:
            self.close() # Cleanup on failure
            raise

    def close(self) -> None:
        if self.is_connected():
            try:
                self._run_coro(self.async_close())
            except Exception:
                pass # Already disconnecting or loop is dead

    def is_connected(self) -> bool:
        if self._ble_conn is None:
            return False
        return self._ble_conn.is_connected

    def write_bytes(self, query: bytes) -> None:
        if not self.is_connected():
            raise RuntimeError("BLE is not connected.")
        with self._lock:
            self._buffer.clear()
        self._data_ready.clear()
        self._run_coro(self._write(query), timeout=self.config["timeout"])

    def read_bytes(self, expected_seq: bytes = b'>', size: int = MISSING) -> bytes:
        lenterm = len(expected_seq)
        deadline = monotonic() + self.config["timeout"]

        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                with self._lock:
                    snapshot = bytes(self._buffer)
                raise TimeoutError(
                    f"read timed out waiting for {expected_seq!r}; received {snapshot!r}"
                )

            with self._lock:
                snapshot = bytes(self._buffer)

            if snapshot[-lenterm:] == expected_seq or (
                expected_seq == b">" and expected_seq in snapshot
            ):
                break
            if size is not MISSING and len(snapshot) >= size:
                break

            self._data_ready.wait(timeout=remaining)
            self._data_ready.clear()

        return snapshot
    
    def __enter__(self) -> "TransportBLE":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    async def __aenter__(self) -> "TransportBLE":
        await self.async_connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.async_close()
