"""Platform for Tornado AC sensor integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfInformation, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .climate import AuxCloudDataUpdateCoordinator
    from .usage import CloudUsage

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class TornadoUsageDescription(SensorEntityDescription):
    """Describes a cloud-usage diagnostic sensor."""

    value_fn: Callable[[CloudUsage], float | int | None]
    # Counters reset at local midnight; the others are plain measurements
    resets_daily: bool = False


USAGE_SENSORS: tuple[TornadoUsageDescription, ...] = (
    TornadoUsageDescription(
        key="requests_today",
        translation_key="requests_today",
        native_unit_of_measurement="requests",
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda usage: usage.requests_today,
        resets_daily=True,
    ),
    TornadoUsageDescription(
        key="data_today",
        translation_key="data_today",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.MEGABYTES,
        suggested_display_precision=2,
        state_class=SensorStateClass.TOTAL,
        # Approximate: request/response bytes + an estimate per TLS handshake
        value_fn=lambda usage: round(usage.bytes_today / 1_000_000, 3),
        resets_daily=True,
    ),
    TornadoUsageDescription(
        key="requests_per_refresh",
        translation_key="requests_per_refresh",
        native_unit_of_measurement="requests",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda usage: usage.requests_last_refresh,
    ),
    TornadoUsageDescription(
        key="last_refresh_duration",
        translation_key="last_refresh_duration",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_display_precision=1,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda usage: (
            None
            if usage.last_refresh_duration is None
            else round(usage.last_refresh_duration, 2)
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tornado sensor platform."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = entry_data["coordinator"]

    entities: list[SensorEntity] = [
        TornadoUsageSensor(coordinator, config_entry.entry_id, description)
        for description in USAGE_SENSORS
    ]

    # Devices come from the coordinator's first refresh - no extra cloud request
    for device in (coordinator.data or {}).values():
        # Room temperature sensor
        entities.append(TornadoTemperatureSensor(coordinator, device, "current"))
        # Target temperature sensor
        entities.append(TornadoTemperatureSensor(coordinator, device, "target"))

    async_add_entities(entities)


class TornadoUsageSensor(CoordinatorEntity, SensorEntity):
    """Measured cloud traffic of this config entry (diagnostic)."""

    entity_description: TornadoUsageDescription
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: AuxCloudDataUpdateCoordinator,
        entry_id: str,
        description: TornadoUsageDescription,
    ) -> None:
        """Initialize the usage sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._usage: CloudUsage = coordinator.api.usage
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry_id)},
            "name": "Tornado AC cloud connection",
            "manufacturer": "Tornado",
            "model": "AUX Cloud",
        }

    @property
    def native_value(self) -> float | int | None:
        """Return the measured value."""
        return self.entity_description.value_fn(self._usage)

    @property
    def last_reset(self) -> datetime | None:
        """Daily counters restart at local midnight (keeps long-term statistics correct)."""
        return self._usage.last_reset if self.entity_description.resets_daily else None

    @property
    def available(self) -> bool:
        """Usage is known even while the cloud is failing."""
        return True


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

    async def async_added_to_hass(self) -> None:
        """Show the current state at once instead of waiting for the next poll."""
        await super().async_added_to_hass()
        self._handle_coordinator_update()

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
