"""Diagnostic binary sensors for OBD2 BLE."""

import logging
from collections.abc import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import Obd2BleConfigEntry
from .coordinator import Obd2BleDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class Obd2BleStatusBinarySensorEntityConfig:
    """Configuration for a diagnostic status binary sensor."""

    def __init__(
        self,
        function: Callable[[], bool],
        icon: str,
        name: str | None = None,
        **kwargs,
    ) -> None:
        self.function = function
        self.description = BinarySensorEntityDescription(
            key=function.__name__,
            name=name or " ".join(function.__name__.replace("_", " ").split()).capitalize(),
            icon=icon,
            entity_category=EntityCategory.DIAGNOSTIC,
            **kwargs,
        )


async def async_setup_entry(
    hass: HomeAssistant, entry: Obd2BleConfigEntry, async_add_entities
) -> None:
    """Set up diagnostic binary sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            ObdBleStatusBinarySensor(
                coordinator,
                entry,
                Obd2BleStatusBinarySensorEntityConfig(
                    function=coordinator.ble_found,
                    name="BLE Found",
                    icon="mdi:bluetooth-searching",
                    device_class=BinarySensorDeviceClass.CONNECTIVITY,
                ),
            ),
            ObdBleStatusBinarySensor(
                coordinator,
                entry,
                Obd2BleStatusBinarySensorEntityConfig(
                    function=coordinator.ble_reachable,
                    name="BLE Reachable",
                    icon="mdi:bluetooth-connect",
                    device_class=BinarySensorDeviceClass.CONNECTIVITY,
                ),
            ),
            ObdBleStatusBinarySensor(
                coordinator,
                entry,
                Obd2BleStatusBinarySensorEntityConfig(
                    function=coordinator.ble_connected,
                    name="BLE Connected",
                    icon="mdi:bluetooth",
                    device_class=BinarySensorDeviceClass.CONNECTIVITY,
                ),
            ),
            ObdBleStatusBinarySensor(
                coordinator,
                entry,
                Obd2BleStatusBinarySensorEntityConfig(
                    function=coordinator.car_connected,
                    icon="mdi:car",
                    device_class=BinarySensorDeviceClass.CONNECTIVITY,
                ),
            ),
        ]
    )


class ObdBleStatusBinarySensor(
    CoordinatorEntity[Obd2BleDataUpdateCoordinator], BinarySensorEntity
):
    """Diagnostic binary sensor for OBD2 BLE connection state."""

    def __init__(
        self,
        coordinator: Obd2BleDataUpdateCoordinator,
        config_entry,
        config: Obd2BleStatusBinarySensorEntityConfig,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_device_info = coordinator.device_info
        self._attr_unique_id = (
            f"{config_entry.unique_id}-binary_sensor-{config.function.__name__}"
        )
        self._config = config
        self.entity_description = config.description

    def _handle_coordinator_update(self) -> None:
        try:
            self._attr_is_on = self._config.function()
            _LOGGER.debug(
                "Updating sensor %s with data: %s",
                self._config.function.__name__,
                self._attr_is_on,
            )
        except Exception as ex:
            _LOGGER.error("Error updating sensor %s: %s", self._config.function.__name__, ex)
            self._attr_available = False
        else:
            self._attr_available = True
        super()._handle_coordinator_update()
