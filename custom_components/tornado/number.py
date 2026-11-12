"""Platform for Tornado AC number integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import NumberEntity, NumberMode
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
    """Set up Tornado number platform."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    client = entry_data["client"]
    coordinator = entry_data["coordinator"]

    # Devices come from the coordinator's first refresh - no extra cloud request
    async_add_entities(
        TornadoPowerLimitNumber(coordinator, device, client)
        for device in (coordinator.data or {}).values()
    )


class TornadoPowerLimitNumber(CoordinatorEntity, NumberEntity):
    """Representation of a Tornado AC power limit number entity."""

    def __init__(
        self,
        coordinator: AuxCloudDataUpdateCoordinator,
        device: dict,
        client: Any,
    ) -> None:
        """Initialize the power limit number."""
        super().__init__(coordinator)
        self._device_id = device["endpointId"]
        self._client = client
        self._attr_name = f"Tornado AC {device.get('friendlyName')} Power Limit"
        self._attr_unique_id = f"{device['endpointId']}_power_limit"
        self._attr_native_min_value = 30
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_mode = NumberMode.SLIDER
        self._attr_native_unit_of_measurement = "%"
        self._attr_icon = "mdi:speedometer"
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
        
        # If power limit switch is off, show 100% (no limit)
        if not device_params.get("pwrlimitswitch", 0):
            self._attr_native_value = 100
        else:
            # Otherwise show the actual limit value from device
            self._attr_native_value = device_params.get("pwrlimit", 100)
        
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Set new power limit value.
        
        This will update both the slider and trigger eco mode selector update.
        When slider is moved to 30-35, eco_30 is selected; 36-45 -> eco_40, etc.
        """
        _LOGGER.info(
            "Setting power limit to %s%% for %s",
            value,
            self._device_id,
        )
        
        try:
            # Determine the params to send
            if value >= 100:
                params = {"pwrlimitswitch": 0}
            else:
                params = {"pwrlimitswitch": 1, "pwrlimit": int(value)}
            
            # Send to device
            await self._client.set_device_params(self._device, params)

            # Instant UI feedback for all entities, no cloud request
            self.coordinator.apply_optimistic(self._device_id, params)
        except Exception:
            _LOGGER.exception(
                "Error setting power limit for %s",
                self._device_id,
            )
