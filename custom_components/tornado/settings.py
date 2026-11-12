"""Runtime settings of a config entry (options override data)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .const import (
    CONF_AMBIENT_INTERVAL,
    CONF_BOOST_DURATION,
    CONF_BOOST_ENABLED,
    CONF_BOOST_INTERVAL,
    CONF_KEEPALIVE,
    CONF_LOW_DATA_MODE,
    CONF_SCAN_INTERVAL,
    CONF_SHARED_EVERY,
    DEFAULT_AMBIENT_INTERVAL,
    DEFAULT_BOOST_DURATION,
    DEFAULT_BOOST_ENABLED,
    DEFAULT_BOOST_INTERVAL,
    DEFAULT_KEEPALIVE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SHARED_EVERY,
    LOW_DATA_SCAN_INTERVAL,
)


@dataclass(frozen=True)
class TornadoSettings:
    """Polling behaviour."""

    scan_interval: int = DEFAULT_SCAN_INTERVAL
    boost_enabled: bool = DEFAULT_BOOST_ENABLED
    boost_interval: int = DEFAULT_BOOST_INTERVAL
    boost_duration: int = DEFAULT_BOOST_DURATION
    ambient_interval: int = DEFAULT_AMBIENT_INTERVAL
    keepalive: bool = DEFAULT_KEEPALIVE
    shared_every: int = DEFAULT_SHARED_EVERY

    @classmethod
    def from_mapping(
        cls, options: Any, data: Any = None
    ) -> TornadoSettings:
        """Build settings; low data mode forces slow polling and no boost."""
        merged = {**(data or {}), **(options or {})}
        scan = int(merged.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
        boost = bool(merged.get(CONF_BOOST_ENABLED, DEFAULT_BOOST_ENABLED))
        if merged.get(CONF_LOW_DATA_MODE):
            scan = max(scan, LOW_DATA_SCAN_INTERVAL)
            boost = False
        return cls(
            scan_interval=scan,
            boost_enabled=boost,
            boost_interval=int(merged.get(CONF_BOOST_INTERVAL, DEFAULT_BOOST_INTERVAL)),
            boost_duration=int(merged.get(CONF_BOOST_DURATION, DEFAULT_BOOST_DURATION)),
            ambient_interval=int(
                merged.get(CONF_AMBIENT_INTERVAL, DEFAULT_AMBIENT_INTERVAL)
            ),
            keepalive=bool(merged.get(CONF_KEEPALIVE, DEFAULT_KEEPALIVE)),
            shared_every=int(merged.get(CONF_SHARED_EVERY, DEFAULT_SHARED_EVERY)),
        )
