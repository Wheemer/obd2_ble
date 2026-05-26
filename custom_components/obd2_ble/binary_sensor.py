"""Sensor platform for OBD2 BLE."""

import logging
# from typing import Any
from collections.abc import Iterable

from obdii import Command, Response, commands as veh_commands

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry

from . import Obd2BleConfigEntry
from .const import (
    CONF_COMMANDS,
    CONF_ICON,
    CONF_UNIT,
    CONF_DEVICE_CLASS,
    CONF_STATE_CLASS,
)
from .coordinator import Obd2BleDataUpdateCoordinator
from .entity import ObdBleEntity

_LOGGER = logging.getLogger(__name__)


class Obd2BleStatusBinarySensorEntityConfig:
    def __init__(self, function, name: str, icon: str, **kwargs) -> None:
        self.function = function
        self.description = BinarySensorEntityDescription(
            name=name,
            icon=icon,
            **kwargs,
        )


class Obd2BleBinarySensorEntityConfig:
    def __init__(self, command: Command, name: str|None=None, icon: str|None=None, unit: str|None=None, **kwargs) -> None:
        self.command = command
        self.description = BinarySensorEntityDescription(
            key=command.name,
            name=name or " ".join(command.name.replace("_", " ").split()).capitalize(),
            icon=icon,
            **kwargs,
        )


async def async_setup_entry(
    hass: HomeAssistant, entry: Obd2BleConfigEntry, async_add_entities
):
    """Set up sensor platform."""

    _LOGGER.debug("Configured commands %s", entry.options.get(CONF_COMMANDS))

    active_command_names: set[str] = set()
    sensor_commands: list[Obd2BleBinarySensorEntityConfig] = []
    # for command_config in entry.options.get(CONF_COMMANDS, []):
    #     try:
    #         command = veh_commands[command_config.get("command")]
    #     except KeyError:
    #         _LOGGER.error(f"Command {command_config.get('command')} not found in obdii.commands, skipping")
    #     else:
    #         active_command_names.add(command.name)
    #         sensor_commands.append(Obd2BleBinarySensorEntityConfig(
    #             command=command,
    #             icon=command_config.get(CONF_ICON) or None,
    #             unit=command_config.get(CONF_UNIT) or None,
    #             device_class=command_config.get(CONF_DEVICE_CLASS) or None,
    #             state_class=command_config.get(CONF_STATE_CLASS) or None,
    #         ))

    # ent_reg = entity_registry.async_get(hass)
    # existing_registry_entries = entity_registry.async_entries_for_config_entry(ent_reg, entry.entry_id)
    # _LOGGER.debug("Existing registry entries for this config entry: %s", existing_registry_entries)
    # for registered_entity in existing_registry_entries:
    #     # Unique ID signature from entity.py: f"{address}-sensor-{command.name}"
    #     unique_id_parts = registered_entity.unique_id.split("-sensor-")
    #     if len(unique_id_parts) < 2:
    #         continue
    #     registered_command_name = unique_id_parts[1]
    #     # If the tracking metric is not present in user options anymore, purge it entirely!
    #     if registered_command_name not in active_command_names:
    #         _LOGGER.info("Evicting unselected tracking sensor: %s", registered_entity.entity_id)
    #         ent_reg.async_remove(registered_entity.entity_id)

    coordinator = entry.runtime_data
    entities = [
        ObdBleBinarySensor(coordinator, entry, sensor)
        for sensor in sensor_commands
    ]
    async_add_entities(entities)

class ObdBleBinarySensor(ObdBleEntity, BinarySensorEntity):
    """Config entry for obd2_ble binary sensors."""

    def __init__(
        self,
        coordinator: Obd2BleDataUpdateCoordinator,
        config_entry,
        config: Obd2BleBinarySensorEntityConfig,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, config_entry, config.command, "sensor")
        self._config = config
        self.entity_description = config.description
        # self._description = config.description
        # self._attr_name = f"{NAME} {config.description.name}"
        # self._attr_device_class = config.description.device_class
        # self._attr_native_unit_of_measurement = config.description.native_unit_of_measurement
        # self._attr_state_class = config.description.state_class

    # async def async_update(self) -> None:
    def _handle_coordinator_update(self) -> None:
        try:
            data: Response | None = self.coordinator.data.get(str(self._command))
            _LOGGER.debug("Updating sensor %s with data: %s", str(self._command), data)
        except Exception as ex:
            _LOGGER.error(f"Error updating sensor {str(self._command)}: {ex}")
            self._attr_available = False
        else:
            if data is None:
                _LOGGER.warning(f"No data available for sensor {str(self._command)}")
                self._attr_available = False
            elif isinstance(data, Response):
                self._attr_available = True
                self._attr_native_value = data.value

        super()._handle_coordinator_update()
