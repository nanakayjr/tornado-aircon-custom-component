"""Config flow for Tornado AC integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback

from .aux_cloud import AuxCloudAPI
from .const import (
    CONF_AMBIENT_INTERVAL,
    CONF_BOOST_DURATION,
    CONF_BOOST_ENABLED,
    CONF_BOOST_INTERVAL,
    CONF_KEEPALIVE,
    CONF_LOW_DATA_MODE,
    CONF_REGION,
    CONF_SCAN_INTERVAL,
    CONF_SHARED_EVERY,
    DEFAULT_AMBIENT_INTERVAL,
    DEFAULT_BOOST_DURATION,
    DEFAULT_BOOST_ENABLED,
    DEFAULT_BOOST_INTERVAL,
    DEFAULT_KEEPALIVE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SHARED_EVERY,
    DOMAIN,
    MAX_AMBIENT_INTERVAL,
    MAX_BOOST_DURATION,
    MAX_BOOST_INTERVAL,
    MAX_SCAN_INTERVAL,
    MAX_SHARED_EVERY,
    MIN_BOOST_DURATION,
    MIN_BOOST_INTERVAL,
    MIN_SCAN_INTERVAL,
    REGIONS,
)

if TYPE_CHECKING:
    from homeassistant.data_entry_flow import FlowResult

_LOGGER = logging.getLogger(__name__)


class TornadoConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Tornado AC."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
        config_entry: config_entries.ConfigEntry | None = None,
    ) -> FlowResult:
        """Handle the initial step."""
        errors = {}

        if config_entry:
            self._config_entry = config_entry

        if user_input is not None:
            try:
                client = AuxCloudAPI(
                    email=user_input[CONF_EMAIL],
                    password=user_input[CONF_PASSWORD],
                    region=user_input[CONF_REGION],
                )
                await client.login()

                await self.async_set_unique_id(user_input[CONF_EMAIL])
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=user_input[CONF_EMAIL], data=user_input
                )
            except Exception:
                _LOGGER.exception("Failed to connect to Tornado AC")
                errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_REGION, default="eu"): vol.In(REGIONS),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TornadoOptionsFlow:
        """Get the options flow for this handler."""
        return TornadoOptionsFlow(config_entry)


class TornadoOptionsFlow(config_entries.OptionsFlow):
    """Handle options flow for Tornado AC."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        errors = {}

        if user_input is not None:
            try:
                # Test the new configuration
                client = AuxCloudAPI(
                    email=self.config_entry.data[CONF_EMAIL],
                    password=self.config_entry.data[CONF_PASSWORD],
                    region=user_input[CONF_REGION],
                )
                await client.login()

                return self.async_create_entry(title="", data=user_input)
            except Exception:
                _LOGGER.exception("Failed to connect to Tornado AC")
                errors["base"] = "cannot_connect"

        options = self.config_entry.options
        # Re-show what the user just typed when validation failed
        current = {**options, **(user_input or {})}

        def _interval(key: str, default: int, low: int, high: int) -> tuple[Any, Any]:
            return (
                vol.Optional(key, default=current.get(key, default)),
                vol.All(vol.Coerce(int), vol.Range(min=low, max=high)),
            )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                dict(
                    [
                        (
                            vol.Required(
                                CONF_REGION,
                                default=current.get(
                                    CONF_REGION,
                                    self.config_entry.data.get(CONF_REGION, "eu"),
                                ),
                            ),
                            vol.In(REGIONS),
                        ),
                        (
                            vol.Optional(
                                CONF_LOW_DATA_MODE,
                                default=current.get(CONF_LOW_DATA_MODE, False),
                            ),
                            bool,
                        ),
                        _interval(
                            CONF_SCAN_INTERVAL,
                            DEFAULT_SCAN_INTERVAL,
                            MIN_SCAN_INTERVAL,
                            MAX_SCAN_INTERVAL,
                        ),
                        (
                            vol.Optional(
                                CONF_BOOST_ENABLED,
                                default=current.get(
                                    CONF_BOOST_ENABLED, DEFAULT_BOOST_ENABLED
                                ),
                            ),
                            bool,
                        ),
                        _interval(
                            CONF_BOOST_INTERVAL,
                            DEFAULT_BOOST_INTERVAL,
                            MIN_BOOST_INTERVAL,
                            MAX_BOOST_INTERVAL,
                        ),
                        _interval(
                            CONF_BOOST_DURATION,
                            DEFAULT_BOOST_DURATION,
                            MIN_BOOST_DURATION,
                            MAX_BOOST_DURATION,
                        ),
                        _interval(
                            CONF_AMBIENT_INTERVAL,
                            DEFAULT_AMBIENT_INTERVAL,
                            0,
                            MAX_AMBIENT_INTERVAL,
                        ),
                        (
                            vol.Optional(
                                CONF_KEEPALIVE,
                                default=current.get(CONF_KEEPALIVE, DEFAULT_KEEPALIVE),
                            ),
                            bool,
                        ),
                        _interval(
                            CONF_SHARED_EVERY,
                            DEFAULT_SHARED_EVERY,
                            1,
                            MAX_SHARED_EVERY,
                        ),
                    ]
                )
            ),
            errors=errors,
        )
