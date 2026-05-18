"""Sensor platform for OBD2 BLE."""

import logging
# from typing import Any

from obdii import Command, Response, commands

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant

from . import Obd2BleConfigEntry
from .const import CONF_COMMANDS
from .coordinator import Obd2BleDataUpdateCoordinator
from .entity import ObdBleEntity

_LOGGER = logging.getLogger(__name__)

class ObdSensorEntityConfig:
    def __init__(self, command: Command, name: str, **kwargs):
        self.command = command
        self.description = SensorEntityDescription(
            key=command.name,
            name=name,
            native_unit_of_measurement=command.units.__str__(),
            **kwargs,
        )

SENSOR_TYPES: list[ObdSensorEntityConfig] = [
    ObdSensorEntityConfig(
        command=commands.FUEL_STATUS,
        name="Fuel Status",
        icon="mdi:gas-station",
        device_class=SensorDeviceClass.VOLUME_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ObdSensorEntityConfig(
        command=commands.ENGINE_RUN_TIME,
        name="Engine run time",
        icon="mdi:car-shift-pattern",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    ObdSensorEntityConfig(
        command=commands.ENGINE_SPEED,
        name="Engine speed",
        icon="mdi:gauge",
        suggested_display_precision=1,
        # device_class=SensorDeviceClass.REVOLUTION_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # ObdSensorEntityConfig(
    #     command=commands.CATALYST_TEMP_BANK_1_SENSOR_1,
    #     name="Catalyst Temperature Bank 1 Sensor 1",
    #     icon="mdi:gauge",
    #     device_class=SensorDeviceClass.TEMPERATURE,
    #     state_class=SensorStateClass.MEASUREMENT,
    # ),
    # ObdSensorEntityConfig(
    #     command=commands.VEHICLE_VOLTAGE,
    #     name="Vehicle Voltage",
    #     icon="mdi:gauge",
    #     device_class=SensorDeviceClass.VOLTAGE,
    #     state_class=SensorStateClass.MEASUREMENT,
    # ),
    # ObdSensorEntityConfig(
    #     command=commands.ACCELERATOR_POSITION_RELATIVE,
    #     name="Accelerator Position Relative",
    #     icon="mdi:gauge",
    #     device_class=SensorDeviceClass.POWER_FACTOR,
    #     state_class=SensorStateClass.MEASUREMENT,
    # ),
]


async def async_setup_entry(
    hass: HomeAssistant, entry: Obd2BleConfigEntry, async_add_entities
):
    """Set up sensor platform."""

    _LOGGER.debug("Configured commands %s", entry.options.get(CONF_COMMANDS))

    coordinator = entry.runtime_data
    entities = [
        ObdBleSensor(coordinator, entry, sensor)
        for sensor in SENSOR_TYPES
    ]
    async_add_entities(entities)

class ObdBleSensor(ObdBleEntity, SensorEntity):
    """Config entry for obd2_ble sensors."""

    def __init__(
        self,
        coordinator: Obd2BleDataUpdateCoordinator,
        config_entry,
        config: ObdSensorEntityConfig,
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
    

