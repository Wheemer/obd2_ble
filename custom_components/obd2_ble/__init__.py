"""Custom integration to integrate OBD2 BLE with Home Assistant.

For more details about this integration, please refer to
https://github.com/dala318/obd2_ble
"""

import logging
from typing_extensions import Final
import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import (
    CoreState,
    Event,
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ConfigEntryError, ServiceValidationError
from homeassistant.helpers.config_validation import config_entry_only_config_schema

from .const import (
    DOMAIN,
    ACTION_ATTEMPT_CONNECT,
    ACTION_PROBE_RAW,
    PLATFORMS,
    STARTUP_MESSAGE
)
from .coordinator import Obd2BleDataUpdateCoordinator, Obd2BleConfigEntry
from .obdii.transport_ble_identifiers import AVAILABLE_OBD2_CLASSES

_LOGGER: logging.Logger = logging.getLogger(__package__)

CONFIG_SCHEMA = config_entry_only_config_schema(DOMAIN)
SERVICE_ATTEMPT_CONNECT_SCHEMA = vol.Schema({vol.Optional("entry_id"): str})
SERVICE_PROBE_RAW_SCHEMA = vol.Schema(
    {
        vol.Required("command"): str,
        vol.Optional("entry_id"): str,
    }
)
POST_START_REDISCOVERY_MATCHERS = tuple(
    matcher
    for obd2_class in AVAILABLE_OBD2_CLASSES
    for matcher in obd2_class.matcher_dict_list()
)


async def _async_handle_attempt_to_connect(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Attempt a connection to one configured vehicle and return a boolean status."""
    coordinators: dict[str, Obd2BleDataUpdateCoordinator] = hass.data.get(DOMAIN, {})
    entry_id = call.data.get("entry_id")

    if entry_id is None:
        if not coordinators:
            raise ServiceValidationError("No OBD2 BLE entries are configured.")
        if len(coordinators) > 1:
            raise ServiceValidationError(
                "Provide entry_id when multiple OBD2 BLE entries are configured."
            )
        coordinator = next(iter(coordinators.values()))
    else:
        coordinator = coordinators.get(entry_id)
        if coordinator is None:
            raise ServiceValidationError(f"No OBD2 BLE entry found for entry_id {entry_id}.")

    _LOGGER.info("Firing action 'attempt_to_connect' via user trigger")
    try:
        connected_successfully = await coordinator.async_force_update()
        return {"connected": bool(connected_successfully)}
    except Exception as err:
        raise ServiceValidationError(
            f"Communication failed while targeting the OBD2 device: {err}"
        ) from err


async def _async_handle_probe_raw(
    hass: HomeAssistant,
    call: ServiceCall,
) -> ServiceResponse:
    """Send one raw ELM command through a configured OBD2 BLE connection."""
    coordinators: dict[str, Obd2BleDataUpdateCoordinator] = hass.data.get(DOMAIN, {})
    entry_id = call.data.get("entry_id")

    if entry_id is None:
        if not coordinators:
            raise ServiceValidationError("No OBD2 BLE entries are configured.")
        if len(coordinators) > 1:
            raise ServiceValidationError(
                "Provide entry_id when multiple OBD2 BLE entries are configured."
            )
        coordinator = next(iter(coordinators.values()))
    else:
        coordinator = coordinators.get(entry_id)
        if coordinator is None:
            raise ServiceValidationError(f"No OBD2 BLE entry found for entry_id {entry_id}.")

    command = call.data["command"]
    _LOGGER.info("Firing action 'probe_raw' with command %s", command)
    try:
        response = await coordinator.async_probe_raw(command)
        return {"response": response}
    except Exception as err:
        raise ServiceValidationError(
            f"Raw OBD2 probe failed while targeting the OBD2 device: {err}"
        ) from err


async def async_setup_entry(hass: HomeAssistant, entry: Obd2BleConfigEntry) -> bool:
    """Set up this integration using UI."""
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
        _LOGGER.info(STARTUP_MESSAGE)

    if entry.unique_id is None:
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key="missing_unique_id",
        )

    coordinator = Obd2BleDataUpdateCoordinator(
        hass, entry=entry
    )

    # await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _async_specific_device_found(
        service_info: bluetooth.BluetoothServiceInfoBleak,
        change: bluetooth.BluetoothChange,
    ) -> None:
        """Handle re-discovery of the device."""
        _LOGGER.debug("New service_info: %s - %s", service_info, change)
        coordinator.request_refresh_from_bluetooth()

    @callback
    def _async_register_post_start_rediscovery() -> None:
        """Register rediscovery only after HA startup is complete."""
        matchers = (
            {"address": entry.data.get(CONF_ADDRESS, entry.unique_id)},
            *POST_START_REDISCOVERY_MATCHERS,
        )
        for matcher in matchers:
            entry.async_on_unload(
                bluetooth.async_register_callback(
                    hass,
                    _async_specific_device_found,
                    matcher,
                    bluetooth.BluetoothScanningMode.ACTIVE,
                )
            )

    @callback
    def _async_refresh_after_started(_event: Event) -> None:
        """Wake the coordinator after HA startup has completed."""
        _async_register_post_start_rediscovery()
        coordinator.request_refresh_after_setup()

    if hass.state is CoreState.running:
        _async_register_post_start_rediscovery()
        coordinator.request_refresh_after_setup()
    else:
        entry.async_on_unload(
            hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                _async_refresh_after_started,
            )
        )

    async def _handle_attempt_to_connect(call: ServiceCall) -> ServiceResponse:
        return await _async_handle_attempt_to_connect(hass, call)

    async def _handle_probe_raw(call: ServiceCall) -> ServiceResponse:
        return await _async_handle_probe_raw(hass, call)

    if not hass.services.has_service(DOMAIN, ACTION_ATTEMPT_CONNECT):
        hass.services.async_register(
            domain=DOMAIN,
            service=ACTION_ATTEMPT_CONNECT,
            service_func=_handle_attempt_to_connect,
            schema=SERVICE_ATTEMPT_CONNECT_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    if not hass.services.has_service(DOMAIN, ACTION_PROBE_RAW):
        hass.services.async_register(
            domain=DOMAIN,
            service=ACTION_PROBE_RAW,
            service_func=_handle_probe_raw,
            schema=SERVICE_PROBE_RAW_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Obd2BleConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded: Final = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS
    )
    _LOGGER.debug("Unloaded config entry: %s, ok? %s!", entry.unique_id, unloaded)
    if unloaded and getattr(entry, "runtime_data", None) is not None:
        await entry.runtime_data.async_shutdown()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, ACTION_ATTEMPT_CONNECT)
            hass.services.async_remove(DOMAIN, ACTION_PROBE_RAW)
    return unloaded
