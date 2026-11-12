# __init__.py
"""The AUX AC integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.storage import Store

from .aux_cloud import AuxCloudAPI
from .const import (
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_REGION,
    DOMAIN,
)
from .coordinator import AuxCloudDataUpdateCoordinator
from .settings import TornadoSettings
from .usage import CloudUsage

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.SENSOR, Platform.NUMBER, Platform.SELECT]
_LOGGER = logging.getLogger(__name__)
_USAGE_SAVE_DELAY = 60  # seconds; usage is persisted so "today" survives restarts


def _usage_store(hass: HomeAssistant, entry: ConfigEntry) -> Store:
    return Store(hass, 1, f"{DOMAIN}_usage_{entry.entry_id}")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up AUX AC from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})

    settings = TornadoSettings.from_mapping(entry.options, entry.data)
    await AuxCloudAPI.configure_keepalive(keepalive=settings.keepalive)

    usage = CloudUsage()
    store = _usage_store(hass, entry)
    if stored := await store.async_load():
        usage.restore(stored)

    # No private aiohttp session here: the client uses the shared, traced one.
    client = AuxCloudAPI(
        email=entry.data[CONF_EMAIL],
        password=entry.data[CONF_PASSWORD],
        # The options flow may change the region; it takes precedence over the data
        region=entry.options.get(CONF_REGION, entry.data[CONF_REGION]),
        usage=usage,
        ambient_interval=settings.ambient_interval,
        shared_every=settings.shared_every,
        keepalive=settings.keepalive,
    )

    try:
        await client.login()
    except Exception as err:
        await client.cleanup()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        msg = f"Failed to connect to AUX AC: {err}"
        raise ConfigEntryNotReady(msg) from err

    # ONE coordinator per entry, created before the platforms are set up. Its first
    # refresh is the only initial fetch; platforms read coordinator.data instead of
    # calling the cloud themselves.
    coordinator = AuxCloudDataUpdateCoordinator(hass, client, settings, entry)
    await coordinator.async_refresh()
    if not coordinator.last_update_success:
        await client.cleanup()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        msg = "Failed to fetch AUX AC data"
        raise ConfigEntryNotReady(msg)

    client.on_command = coordinator.boost
    hass.data[DOMAIN][entry.entry_id].update(
        {"client": client, "coordinator": coordinator, "usage": usage, "store": store}
    )
    entry.async_on_unload(coordinator.async_shutdown)
    entry.async_on_unload(
        coordinator.async_add_listener(
            lambda: store.async_delay_save(usage.as_dict, _USAGE_SAVE_DELAY)
        )
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_reload_on_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, {})
        if (store := entry_data.get("store")) and (usage := entry_data.get("usage")):
            await store.async_save(usage.as_dict())
        if client := entry_data.get("client"):
            await client.cleanup()
        # The pool is shared by all entries; close it with the last one
        if not hass.data[DOMAIN]:
            await AuxCloudAPI.cleanup_shared_resources()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the persisted usage counters together with the entry."""
    await _usage_store(hass, entry).async_remove()
