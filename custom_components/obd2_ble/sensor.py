"""Sensor platform for OBD2 BLE."""

import logging
# from typing import Any
from collections.abc import Iterable

from obdii import Command, Response, commands as veh_commands

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant

from . import Obd2BleConfigEntry
from .const import CONF_COMMANDS, ICON_KEYWORDS
from .coordinator import Obd2BleDataUpdateCoordinator
from .entity import ObdBleEntity

_LOGGER = logging.getLogger(__name__)


def propose_icon_from_command(command: Command) -> str:
    """Propose an mdi icon string by checking token suffixes backwards."""
    tokens = command.name.lower().split("_")
    # Iterate backwards so the modifier (like 'speed' or 'temp') wins over 'engine'
    for token in tokens[::-1]:
        if token in ICON_KEYWORDS:
            return ICON_KEYWORDS[token]
    return "mdi:car-diagnostic"


def propose_sensor_state_class(command: Command) -> SensorStateClass | None:
    """Analyze OBD2 metrics using normalized unit collections."""

    if isinstance(command.units, Iterable) and not isinstance(command.units, (str, bytes)):
        raw_units = list(command.units)
    else:
        raw_units = [command.units]
    primary_unit = raw_units[0] if raw_units else None
    tokens = command.name.lower().split("_")
    last_token = tokens[-1] if tokens else ""

    if primary_unit is None or primary_unit in ("string", "bool"):
        return None
    elif last_token in ("count", "distance", "time", "odometer"):
        return SensorStateClass.TOTAL_INCREASING
    return SensorStateClass.MEASUREMENT


def propose_sensor_device_class(command: Command) -> SensorDeviceClass | None:
    """Analyze OBD2 metrics using normalized unit collections."""

    if isinstance(command.units, Iterable) and not isinstance(command.units, (str, bytes)):
        raw_units = list(command.units)
    else:
        raw_units = [command.units]
    primary_unit = raw_units[0] if raw_units else None
    tokens = command.name.lower().split("_")

    if primary_unit == "°C":
        return SensorDeviceClass.TEMPERATURE
    elif primary_unit in ("kPa", "bar", "psi"):
        return SensorDeviceClass.PRESSURE
    elif primary_unit in ("V", "v"):
        return SensorDeviceClass.VOLTAGE
    elif primary_unit in ("km/h", "mph"):
        return SensorDeviceClass.SPEED
    elif primary_unit in ("s", "seconds", "min", "h"):
        return SensorDeviceClass.DURATION
    elif "temp" in tokens or "temperature" in tokens:
        return SensorDeviceClass.TEMPERATURE
    elif "speed" in tokens or "velocity" in tokens or "rpm" in tokens:
        return SensorDeviceClass.SPEED
    return None


class Obd2BleSensorEntityConfig:
    def __init__(self, command: Command, name: str="", icon: str = "", unit: str = "", **kwargs) -> None:
        self.command = command
        self.description = SensorEntityDescription(
            key=command.name,
            name=name or " ".join(command.name.replace("_", " ").split()).capitalize(),
            icon = icon or propose_icon_from_command(command),
            native_unit_of_measurement=unit or command.units.__str__(),
            **kwargs,
        )

# SENSOR_TYPES: list[Obd2BleSensorEntityConfig] = [
#     Obd2BleSensorEntityConfig(
#         command=veh_commands.FUEL_STATUS,
#         # name="Fuel Status",
#         # icon="mdi:gas-station",
#         device_class=SensorDeviceClass.VOLUME_STORAGE,
#         state_class=SensorStateClass.MEASUREMENT,
#     ),
#     Obd2BleSensorEntityConfig(
#         command=veh_commands.ENGINE_RUN_TIME,
#         # name="Engine run time",
#         # icon="mdi:car-shift-pattern",
#         device_class=SensorDeviceClass.DURATION,
#         state_class=SensorStateClass.TOTAL_INCREASING,
#     ),
#     Obd2BleSensorEntityConfig(
#         command=veh_commands.ENGINE_SPEED,
#         # name="Engine speed",
#         # icon="mdi:gauge",
#         suggested_display_precision=1,
#         # device_class=SensorDeviceClass.REVOLUTION_PER_MINUTE,
#         state_class=SensorStateClass.MEASUREMENT,
#     ),
#     # Obd2BleSensorEntityConfig(
#     #     command=veh_commands.CATALYST_TEMP_BANK_1_SENSOR_1,
#     #     name="Catalyst Temperature Bank 1 Sensor 1",
#     #     icon="mdi:gauge",
#     #     device_class=SensorDeviceClass.TEMPERATURE,
#     #     state_class=SensorStateClass.MEASUREMENT,
#     # ),
#     # Obd2BleSensorEntityConfig(
#     #     command=veh_commands.VEHICLE_VOLTAGE,
#     #     name="Vehicle Voltage",
#     #     icon="mdi:gauge",
#     #     device_class=SensorDeviceClass.VOLTAGE,
#     #     state_class=SensorStateClass.MEASUREMENT,
#     # ),
#     # Obd2BleSensorEntityConfig(
#     #     command=veh_commands.ACCELERATOR_POSITION_RELATIVE,
#     #     name="Accelerator Position Relative",
#     #     icon="mdi:gauge",
#     #     device_class=SensorDeviceClass.POWER_FACTOR,
#     #     state_class=SensorStateClass.MEASUREMENT,
#     # ),
# ]


async def async_setup_entry(
    hass: HomeAssistant, entry: Obd2BleConfigEntry, async_add_entities
):
    """Set up sensor platform."""

    _LOGGER.debug("Configured commands %s", entry.options.get(CONF_COMMANDS))

    sensor_commands: list[Obd2BleSensorEntityConfig] = []
    for command_config in entry.options.get(CONF_COMMANDS, []):
        try:
            command = veh_commands[command_config.get("command")]
        except KeyError:
            _LOGGER.error(f"Command {command_config.get('command')} not found in obdii.commands, skipping")
        else:
            sensor_commands.append(Obd2BleSensorEntityConfig(
                command=command,
                icon=command_config.get("icon"),
                unit=command_config.get("unit"),
                device_class=command_config.get("device_class"),
                state_class=command_config.get("state_class")
            ))
    coordinator = entry.runtime_data
    entities = [
        ObdBleSensor(coordinator, entry, sensor)
        # for sensor in SENSOR_TYPES
        for sensor in sensor_commands
    ]
    async_add_entities(entities)

class ObdBleSensor(ObdBleEntity, SensorEntity):
    """Config entry for obd2_ble sensors."""

    def __init__(
        self,
        coordinator: Obd2BleDataUpdateCoordinator,
        config_entry,
        config: Obd2BleSensorEntityConfig,
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


# class ObdBleDiagSensor(ObdBleEntity, SensorEntity):
#     """Config entry for obd2_ble diagnostic sensors."""

#     def __init__(
#         self,
#         coordinator: Obd2BleDataUpdateCoordinator,
#         config_entry,
#         id: str,
#         description: SensorEntityDescription,
#     ) -> None:
#         """Initialize the sensor."""
#         super().__init__(coordinator, config_entry, id, description.icon, id, DOMAIN)
#         self._id = id
#         self._description = description
#         self._attr_name = f"{NAME} {description.name}"
#         self._attr_entity_category = EntityCategory.DIAGNOSTIC
    

