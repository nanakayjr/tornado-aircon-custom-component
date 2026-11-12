"""Measured cloud traffic of one config entry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import aiohttp

# Rough per-request/handshake overheads; the counters are estimates, not wire data.
HEADER_BYTES_ESTIMATE = 600
TLS_HANDSHAKE_BYTES_ESTIMATE = 5000


def _local_midnight() -> datetime:
    return datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)


class CloudUsage:
    """Request and byte counters; the daily ones reset at local midnight."""

    def __init__(self) -> None:
        """Initialize empty counters."""
        self.requests_today = 0
        self.bytes_today = 0
        self.requests_last_refresh = 0
        self.last_refresh_duration: float | None = None
        self.last_reset: datetime = _local_midnight()
        self._refresh_start_requests = 0

    def _roll_over(self) -> None:
        midnight = _local_midnight()
        if midnight > self.last_reset:
            self.requests_today = 0
            self.bytes_today = 0
            self.last_reset = midnight

    def add_request(self, request_bytes: int) -> None:
        """Count one request."""
        self._roll_over()
        self.requests_today += 1
        self.bytes_today += request_bytes + HEADER_BYTES_ESTIMATE

    def add_bytes(self, size: int) -> None:
        """Count extra traffic (response body, TLS handshake)."""
        self._roll_over()
        self.bytes_today += size

    def begin_refresh(self) -> None:
        """Mark the start of a coordinator refresh."""
        self._refresh_start_requests = self.requests_today

    def end_refresh(self, duration: float) -> None:
        """Record how many requests and how long the refresh took."""
        self.requests_last_refresh = max(
            0, self.requests_today - self._refresh_start_requests
        )
        self.last_refresh_duration = duration

    def as_dict(self) -> dict[str, Any]:
        """Serialize for persistence."""
        return {
            "requests_today": self.requests_today,
            "bytes_today": self.bytes_today,
            "last_reset": self.last_reset.isoformat(),
        }

    def restore(self, stored: dict[str, Any]) -> None:
        """Restore persisted counters; stale ones from a previous day are dropped."""
        try:
            last_reset = datetime.fromisoformat(stored["last_reset"])
            if last_reset.tzinfo is None:
                last_reset = last_reset.astimezone()
            requests = int(stored["requests_today"])
            size = int(stored["bytes_today"])
        except (KeyError, TypeError, ValueError):
            return
        self.requests_today = requests
        self.bytes_today = size
        self.last_reset = last_reset
        self._roll_over()


class RequestTrace:
    """Per-request context carrying the usage object and the request body size."""

    def __init__(self, usage: CloudUsage, body_bytes: int) -> None:
        """Store the counters to feed."""
        self.usage = usage
        self.body_bytes = body_bytes


def build_trace_config() -> aiohttp.TraceConfig:
    """Feed each request's CloudUsage through the session's trace hooks."""
    trace = aiohttp.TraceConfig()

    async def on_start(_s: Any, ctx: Any, _p: Any) -> None:
        req = ctx.trace_request_ctx
        if isinstance(req, RequestTrace):
            req.usage.add_request(req.body_bytes)

    async def on_end(_s: Any, ctx: Any, params: Any) -> None:
        req = ctx.trace_request_ctx
        if isinstance(req, RequestTrace):
            length = params.response.headers.get("Content-Length", "0")
            req.usage.add_bytes(int(length) if length.isdigit() else 0)

    async def on_connect(_s: Any, ctx: Any, _p: Any) -> None:
        req = ctx.trace_request_ctx
        if isinstance(req, RequestTrace):
            req.usage.add_bytes(TLS_HANDSHAKE_BYTES_ESTIMATE)

    trace.on_request_start.append(on_start)
    trace.on_request_end.append(on_end)
    trace.on_connection_create_end.append(on_connect)
    return trace
