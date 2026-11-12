"""Shared data coordinator for the Tornado AC integration."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .aux_cloud import AuxCloudAPI, AuxCloudSessionExpiredError
from .const import MAX_BACKOFF_INTERVAL
from .settings import TornadoSettings

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


class AuxCloudDataUpdateCoordinator(DataUpdateCoordinator):
    """
    Fetch AuxCloud data at a sane rate (one coordinator per config entry).

    The poll interval is chosen after every refresh:
    failing cloud (exponential backoff) > boost after a command > normal.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: AuxCloudAPI,
        settings: TornadoSettings | None = None,
        config_entry: ConfigEntry | None = None,
    ) -> None:
        """Initialize the coordinator."""
        self.api = api
        self.settings = settings or TornadoSettings()
        self._boost_until = 0.0
        self._failures = 0
        super().__init__(
            hass,
            _LOGGER,
            name="AuxCloud",
            config_entry=config_entry,
            update_interval=timedelta(seconds=self.settings.scan_interval),
        )

    @property
    def consecutive_failures(self) -> int:
        """Number of failed refreshes in a row."""
        return self._failures

    def _boosting(self) -> bool:
        return time.monotonic() < self._boost_until

    def _compute_interval(self, _data: dict[str, Any] | None = None) -> timedelta:
        """Pick the next poll interval; see the class docstring for the priority."""
        s = self.settings
        nominal = s.scan_interval
        if self._failures:
            cap = max(MAX_BACKOFF_INTERVAL, nominal)
            return timedelta(seconds=min(cap, nominal * 2**self._failures))
        if self._boosting():
            nominal = min(nominal, s.boost_interval)
        return timedelta(seconds=nominal)

    def boost(self) -> None:
        """
        Poll faster for a short time after a command, then go back to normal.

        No immediate refresh: the entity already shows the optimistic state, and the
        cloud usually has not applied the change yet. The first confirmation poll
        comes after the boost interval.
        """
        # A running boost is not extended, so repeated commands cannot keep it alive
        if not self.settings.boost_enabled or self._boosting():
            return
        self._boost_until = time.monotonic() + self.settings.boost_duration
        self.update_interval = self._compute_interval(self.data)
        self._schedule_refresh()

    @callback
    def apply_optimistic(self, device_id: str, params: dict[str, Any]) -> None:
        """Show a just-sent command at once, without any cloud request."""
        if not self.data or device_id not in self.data:
            return
        device = self.data[device_id]
        new_data = {
            **self.data,
            device_id: {**device, "params": {**device.get("params", {}), **params}},
        }
        self.async_set_updated_data(new_data)

    async def _async_update_data(self) -> dict:
        """Fetch data from AuxCloud."""
        usage = self.api.usage
        started = time.monotonic()
        usage.begin_refresh()
        try:
            devices = await self._fetch_devices()
        except Exception as err:
            self._failures += 1
            self.update_interval = self._compute_interval(self.data)
            _LOGGER.warning(
                "Error fetching data (%d in a row), retrying in %s: %s",
                self._failures,
                self.update_interval,
                err,
            )
            error_msg = f"Error fetching data: {err}"
            raise UpdateFailed(error_msg) from err
        finally:
            usage.end_refresh(time.monotonic() - started)

        data = {device["endpointId"]: device for device in devices}
        self._failures = 0
        self.update_interval = self._compute_interval(data)
        _LOGGER.debug("Coordinator fetched %d devices", len(data))
        return data

    async def _fetch_devices(self) -> list[dict[str, Any]]:
        """Fetch devices, logging in first / re-logging in when needed."""
        if not getattr(self.api, "loginsession", None):
            _LOGGER.info("No valid login session, attempting to login")
            await self.api.login()

        # Boost polls must not advance the "shared devices every Nth refresh" counter
        count_cycle = not self._boosting()
        try:
            return await self.api.get_devices(count_cycle=count_cycle)
        except AuxCloudSessionExpiredError:
            _LOGGER.info("Login session expired, logging in again")
            await self.api.relogin()
            return await self.api.get_devices(count_cycle=count_cycle)
