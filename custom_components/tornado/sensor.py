"""Platform for Tornado AC sensor integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .climate import AuxCloudDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tornado sensor platform."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    client = entry_data["client"]
    
    # Import here to avoid circular dependency
    from .climate import AuxCloudDataUpdateCoordinator
    
    # Get or create coordinator
    if "coordinator" not in entry_data:
        coordinator = AuxCloudDataUpdateCoordinator(hass, client)
        await coordinator.async_config_entry_first_refresh()
        entry_data["coordinator"] = coordinator
    else:
        coordinator = entry_data["coordinator"]

    try:
        devices = await client.get_devices()
        entities = []

        for device in devices:
            # Room temperature sensor
            entities.append(
                TornadoTemperatureSensor(
                    coordinator,
                    device,
                    "current",
                )
            )
            # Target temperature sensor
            entities.append(
                TornadoTemperatureSensor(
                    coordinator,
                    device,
                    "target",
                )
            )

        async_add_entities(entities)

    except Exception:
        _LOGGER.exception("Error setting up Tornado sensor platform")


class TornadoTemperatureSensor(CoordinatorEntity, SensorEntity):
    """Representation of a Tornado AC temperature sensor."""

    def __init__(
        self,
        coordinator: AuxCloudDataUpdateCoordinator,
        device: dict,
        sensor_type: str,
    ) -> None:
        """Initialize the temperature sensor."""
        super().__init__(coordinator)
        self._device_id = device["endpointId"]
        self._sensor_type = sensor_type
        
        if sensor_type == "current":
            self._attr_name = f"Tornado AC {device.get('friendlyName')} Room Temperature"
            self._attr_unique_id = f"{device['endpointId']}_current_temperature"
        else:
            self._attr_name = f"Tornado AC {device.get('friendlyName')} Target Temperature"
            self._attr_unique_id = f"{device['endpointId']}_target_temperature"
        
        self._attr_device_class = SensorDeviceClass.TEMPERATURE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device["endpointId"])},
            "name": f"Tornado AC {device.get('friendlyName')}",
            "manufacturer": "Tornado",
            "model": "AUX Cloud",
        }

    @property
    def _device(self) -> dict | None:
        """Get current device data from coordinator."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._device_id)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return self.coordinator.last_update_success and self._device is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        if not self._device:
            return

        device_params = self._device.get("params", {})
        
        if self._sensor_type == "current":
            # Room temperature (envtemp)
            self._attr_native_value = device_params.get("envtemp", 0) / 10
        else:
            # Target temperature (temp)
            self._attr_native_value = device_params.get("temp", 0) / 10
        
        self.async_write_ha_state()
