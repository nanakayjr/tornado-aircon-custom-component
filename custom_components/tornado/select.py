"""Platform for Tornado AC select integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .climate import AuxCloudDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# HVAC Mode options
HVAC_MODE_OPTIONS = ["off", "cool", "heat", "dry", "fan_only", "auto"]
HVAC_MODE_MAP = {
    "off": {"pwr": 0},
    "cool": {"pwr": 1, "ac_mode": 0},
    "heat": {"pwr": 1, "ac_mode": 1},
    "dry": {"pwr": 1, "ac_mode": 2},
    "fan_only": {"pwr": 1, "ac_mode": 3},
    "auto": {"pwr": 1, "ac_mode": 4},
}

# Eco mode (preset) options
ECO_MODE_OPTIONS = ["normal", "eco_30", "eco_40", "eco_50", "eco_60", "eco_70", "eco_80", "eco_90"]
ECO_MODE_PARAMS = {
    "normal": {"pwrlimitswitch": 0},
    "eco_30": {"pwrlimitswitch": 1, "pwrlimit": 30},
    "eco_40": {"pwrlimitswitch": 1, "pwrlimit": 40},
    "eco_50": {"pwrlimitswitch": 1, "pwrlimit": 50},
    "eco_60": {"pwrlimitswitch": 1, "pwrlimit": 60},
    "eco_70": {"pwrlimitswitch": 1, "pwrlimit": 70},
    "eco_80": {"pwrlimitswitch": 1, "pwrlimit": 80},
    "eco_90": {"pwrlimitswitch": 1, "pwrlimit": 90},
}


def get_eco_mode_from_power_limit(pwrlimitswitch: int, pwrlimit: int) -> str:
    """Determine eco mode based on power limit value.
    
    Uses ranges: 30-35 -> eco_30, 36-45 -> eco_40, 46-55 -> eco_50, etc.
    """
    if not pwrlimitswitch:
        return "normal"
    
    # Map ranges to eco modes (centered around preset values)
    if 30 <= pwrlimit <= 35:
        return "eco_30"
    elif 36 <= pwrlimit <= 45:
        return "eco_40"
    elif 46 <= pwrlimit <= 55:
        return "eco_50"
    elif 56 <= pwrlimit <= 65:
        return "eco_60"
    elif 66 <= pwrlimit <= 75:
        return "eco_70"
    elif 76 <= pwrlimit <= 85:
        return "eco_80"
    elif 86 <= pwrlimit <= 100:
        return "eco_90"
    else:
        return "normal"


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Tornado select platform."""
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
            # HVAC mode selector
            entities.append(
                TornadoHVACModeSelect(
                    coordinator,
                    device,
                    client,
                )
            )
            # Eco mode selector
            entities.append(
                TornadoEcoModeSelect(
                    coordinator,
                    device,
                    client,
                )
            )

        async_add_entities(entities)

    except Exception:
        _LOGGER.exception("Error setting up Tornado select platform")


class TornadoHVACModeSelect(CoordinatorEntity, SelectEntity):
    """Representation of a Tornado AC HVAC mode selector."""

    def __init__(
        self,
        coordinator: AuxCloudDataUpdateCoordinator,
        device: dict,
        client: Any,
    ) -> None:
        """Initialize the HVAC mode selector."""
        super().__init__(coordinator)
        self._device_id = device["endpointId"]
        self._client = client
        self._attr_name = f"Tornado AC {device.get('friendlyName')} Mode"
        self._attr_unique_id = f"{device['endpointId']}_hvac_mode"
        self._attr_options = HVAC_MODE_OPTIONS
        self._attr_icon = "mdi:air-conditioner"
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
        
        # Determine current HVAC mode
        if not device_params.get("pwr", 0):
            self._attr_current_option = "off"
        else:
            ac_mode = device_params.get("ac_mode", 0)
            mode_map = {0: "cool", 1: "heat", 2: "dry", 3: "fan_only", 4: "auto"}
            self._attr_current_option = mode_map.get(ac_mode, "cool")
        
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Change the HVAC mode."""
        _LOGGER.info(
            "Setting HVAC mode to %s for %s",
            option,
            self._device_id,
        )
        
        try:
            params = HVAC_MODE_MAP.get(option, HVAC_MODE_MAP["off"])
            await self._client.set_device_params(self._device, params)
            
            # Immediately update coordinator data for instant UI feedback
            if self.coordinator.data and self._device_id in self.coordinator.data:
                device_data = self.coordinator.data[self._device_id]
                if "params" in device_data:
                    device_data["params"].update(params)
                    # Trigger coordinator update to notify all entities
                    self.coordinator.async_set_updated_data(self.coordinator.data)
                    
        except Exception:
            _LOGGER.exception(
                "Error setting HVAC mode for %s",
                self._device_id,
            )


class TornadoEcoModeSelect(CoordinatorEntity, SelectEntity):
    """Representation of a Tornado AC eco mode selector."""

    def __init__(
        self,
        coordinator: AuxCloudDataUpdateCoordinator,
        device: dict,
        client: Any,
    ) -> None:
        """Initialize the eco mode selector."""
        super().__init__(coordinator)
        self._device_id = device["endpointId"]
        self._client = client
        self._attr_name = f"Tornado AC {device.get('friendlyName')} Eco Mode"
        self._attr_unique_id = f"{device['endpointId']}_eco_mode"
        self._attr_options = ECO_MODE_OPTIONS
        self._attr_icon = "mdi:leaf"
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
        
        # Determine current eco mode based on power limit parameters
        pwrlimitswitch = device_params.get("pwrlimitswitch", 0)
        pwrlimit = device_params.get("pwrlimit", 0)
        
        self._attr_current_option = get_eco_mode_from_power_limit(pwrlimitswitch, pwrlimit)
        
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Change the eco mode."""
        _LOGGER.info(
            "Setting eco mode to %s for %s",
            option,
            self._device_id,
        )
        
        try:
            params = ECO_MODE_PARAMS.get(option, ECO_MODE_PARAMS["normal"])
            await self._client.set_device_params(self._device, params)
            
            # Immediately update coordinator data for instant UI feedback
            if self.coordinator.data and self._device_id in self.coordinator.data:
                device_data = self.coordinator.data[self._device_id]
                if "params" in device_data:
                    device_data["params"].update(params)
                    # Trigger coordinator update to notify all entities
                    self.coordinator.async_set_updated_data(self.coordinator.data)
                    
        except Exception:
            _LOGGER.exception(
                "Error setting eco mode for %s",
                self._device_id,
            )
