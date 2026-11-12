"""AuxCloud API client for Tornado AC."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import socket
import time
from typing import TYPE_CHECKING, Any, ClassVar

import aiohttp
from async_lru import alru_cache
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

if TYPE_CHECKING:
    from collections.abc import Callable

from ..const import KEEPALIVE_TIMEOUT, MIN_RELOGIN_GAP
from ..usage import CloudUsage, RequestTrace, build_trace_config
from .util import encrypt_aes_cbc_zero_padding

_LOGGER = logging.getLogger(__name__)

# Constants from original implementation
TIMESTAMP_TOKEN_ENCRYPT_KEY = "kdixkdqp54545^#*"  # noqa: S105
PASSWORD_ENCRYPT_KEY = "4969fj#k23#"  # noqa: S105
BODY_ENCRYPT_KEY = "xgx3d*fe3478$ukx"
AES_INITIAL_VECTOR = bytes(
    [
        (b + 256) % 256
        for b in [
            -22,
            -86,
            -86,
            58,
            -69,
            88,
            98,
            -94,
            25,
            24,
            -75,
            119,
            29,
            22,
            21,
            -86,
        ]
    ]
)
LICENSE = (
    "PAFbJJ3WbvDxH5vvWezXN5BujETtH/iuTtIIW5CE/SeHN7oNKqnEajgljTcL0fBQQWM0XAAAAAAnBh"
    "JyhMi7zIQMsUcwR/PEwGA3uB5HLOnr+xRrci+FwHMkUtK7v4yo0ZHa+jPvb6djelPP893k7SagmffZ"
    "mOkLSOsbNs8CAqsu8HuIDs2mDQAAAAA="
)
LICENSE_ID = "3c015b249dd66ef0f11f9bef59ecd737"
COMPANY_ID = "48eb1b36cf0202ab2ef07b880ecda60d"
SPOOF_APP_VERSION = "2.2.10.456537160"
SPOOF_USER_AGENT = "Dalvik/2.1.0 (Linux; U; Android 12; SM-G991B Build/SP1A.210812.016)"
SPOOF_SYSTEM = "android"
SPOOF_APP_PLATFORM = "android"
API_SERVER_URL_EU = "https://app-service-deu-f0e9ebbb.smarthomecs.de"
API_SERVER_URL_USA = "https://app-service-usa-fd7cc04c.smarthomecs.com"


class AuxCloudError(Exception):
    """Base exception for AuxCloud API."""


class AuxCloudAuthError(AuxCloudError):
    """Authentication error occurred."""


class AuxCloudSessionExpiredError(AuxCloudAuthError):
    """The cloud rejected our login session (not retried by the HTTP retry layer)."""


class AuxCloudApiError(AuxCloudError):
    """API error occurred."""


class AuxCloudConnectionError(AuxCloudError):
    """Connection error occurred."""


# Add retry decorator configuration
def create_retry_decorator(
    max_attempts: int = 3,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Create a retry decorator with specified parameters."""
    return retry(
        retry=retry_if_exception_type(
            (
                aiohttp.ClientError,
                TimeoutError,
                AuxCloudConnectionError,
                AuxCloudApiError,
            )
        ),
        wait=wait_exponential(multiplier=1, min=4, max=10),
        stop=stop_after_attempt(max_attempts),
        before_sleep=lambda retry_state: _LOGGER.warning(
            "Request failed, retrying in %s seconds...",
            retry_state.next_action.sleep,
        ),
    )


class AuxCloudAPI:
    """API Client for AuxCloud."""

    LOGIN_VALIDATION_FAILED = -30129

    _shared_connector: ClassVar[aiohttp.TCPConnector | None] = None
    _shared_connector_lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _shared_session: ClassVar[aiohttp.ClientSession | None] = None
    _shared_session_lock: ClassVar[asyncio.Lock] = asyncio.Lock()
    _shared_keepalive: ClassVar[bool] = False

    @classmethod
    async def configure_keepalive(cls, *, keepalive: bool) -> None:
        """Select keep-alive mode; switching drops the shared pool so it is rebuilt."""
        if cls._shared_keepalive != keepalive:
            cls._shared_keepalive = keepalive
            await cls.cleanup_shared_resources()

    @classmethod
    async def get_shared_connector(cls) -> aiohttp.TCPConnector:
        """Get or create shared connector."""
        async with cls._shared_connector_lock:
            if cls._shared_connector is None or cls._shared_connector.closed:
                # Default force_close=True: never reuse a pooled connection. AUX
                # Cloud's idle-connection timeout is shorter than the poll interval,
                # so a connection kept across polls is stale and can be "poisoned"
                # (see #29). With the opt-in keep-alive mode, connections are kept
                # only KEEPALIVE_TIMEOUT seconds - long enough to be reused by the
                # requests of one refresh burst, never long enough to go stale
                # between polls.
                options: dict[str, Any] = (
                    {"keepalive_timeout": KEEPALIVE_TIMEOUT}
                    if cls._shared_keepalive
                    else {"force_close": True}
                )
                cls._shared_connector = aiohttp.TCPConnector(
                    limit=10,
                    ttl_dns_cache=300,  # Cache DNS results for 5 minutes
                    use_dns_cache=True,
                    family=socket.AF_INET,
                    **options,
                )
                _LOGGER.debug(
                    "Created new connector %s (keepalive=%s)",
                    id(cls._shared_connector),
                    cls._shared_keepalive,
                )
            return cls._shared_connector

    @classmethod
    async def get_shared_session(cls) -> aiohttp.ClientSession:
        """Get or create shared session."""
        async with cls._shared_session_lock:
            if cls._shared_session is None or cls._shared_session.closed:
                connector = await cls.get_shared_connector()
                cls._shared_session = aiohttp.ClientSession(
                    connector=connector,
                    timeout=aiohttp.ClientTimeout(
                        total=30, connect=10, sock_connect=10, sock_read=10
                    ),
                    raise_for_status=True,
                    # One session serves every entry; requests carry their own
                    # CloudUsage through trace_request_ctx.
                    trace_configs=[build_trace_config()],
                )
                _LOGGER.info(
                    "Created new shared aiohttp session: %s", id(cls._shared_session)
                )
            return cls._shared_session

    def __init__(
        self,
        email: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
        region: str = "eu",
        *,
        usage: CloudUsage | None = None,
        ambient_interval: int = 0,
        shared_every: int = 1,
        keepalive: bool = False,
    ) -> None:
        """
        Initialize the API client.

        Args:
            email: Account e-mail
            password: Account password
            session: Optional external session (the shared session is used otherwise)
            region: "eu" or "usa"
            usage: Traffic counters fed by the session's trace config
            ambient_interval: Seconds to cache room temperature/state (0 = never)
            shared_every: Poll shared devices only every Nth get_devices() cycle
            keepalive: Run a device's requests one after another to reuse a connection

        """
        self.url = API_SERVER_URL_EU if region == "eu" else API_SERVER_URL_USA
        self.email = email
        self.password = password
        self.session = session
        # If session is provided externally, we don't own it
        self._session_owner = session is None
        self.data: dict[str, Any] = {}
        self.usage = usage or CloudUsage()
        self.ambient_interval = ambient_interval
        self.shared_every = max(1, shared_every)
        self.keepalive = keepalive
        self._cycle = 0
        # endpointId -> (monotonic time, {"state": ..., "envtemp": ...})
        self._slow_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._shared_cache: dict[str, list[dict[str, Any]]] = {}
        # endpointId -> last known params; reused while the unit is off
        self._last_params: dict[str, dict[str, Any]] = {}
        # Shared devices already fetched by the has-shared check this cycle
        self._prefetched_shared: dict[str, list[dict[str, Any]]] = {}
        self._last_login = 0.0
        self._login_lock = asyncio.Lock()
        # Optional callback (set by the integration) fired after a successful command
        self.on_command: Any = None
        self.timeout = aiohttp.ClientTimeout(
            total=30, connect=10, sock_connect=10, sock_read=10
        )
        _LOGGER.info(
            "Initialized AuxCloudAPI with email: %s, region: %s", email, region
        )

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get aiohttp client session."""
        if self._session_owner:
            if self.session is None or self.session.closed:
                if getattr(self, "_cleaned_up", False):
                    msg = "Cannot create new session after cleanup"
                    raise RuntimeError(msg)
                self.session = await self.get_shared_session()
                _LOGGER.debug("Using shared session: %s", id(self.session))
            return self.session

        return await self.get_shared_session()

    def _post(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        data: str | bytes | None = None,
        **kwargs: Any,
    ) -> Any:
        """POST with per-entry traffic accounting (see usage.CloudUsage)."""
        body_bytes = len(data) if isinstance(data, str | bytes) else 0
        return session.post(
            url,
            data=data,
            trace_request_ctx=RequestTrace(self.usage, body_bytes),
            **kwargs,
        )

    async def cleanup(self) -> None:
        """Clean up resources."""
        # We never close the session in instance cleanup
        # External sessions are managed by their owners
        # Shared sessions are managed by cleanup_shared_resources

        # Just clear the reference and mark as cleaned up
        self.session = None
        self._cleaned_up = True

    @classmethod
    async def cleanup_shared_resources(cls) -> None:
        """Cleanup shared resources."""
        # First cleanup session since it depends on the connector
        session = None
        async with cls._shared_session_lock:
            if cls._shared_session and not cls._shared_session.closed:
                session = cls._shared_session
                cls._shared_session = None

        if session:
            await session.close()

        # Then cleanup connector
        connector = None
        async with cls._shared_connector_lock:
            if cls._shared_connector and not cls._shared_connector.closed:
                connector = cls._shared_connector
                cls._shared_connector = None

        if connector:
            await connector.close()

        # Ensure the shared resources are fully cleaned up
        async with cls._shared_session_lock:
            cls._shared_session = None

        async with cls._shared_connector_lock:
            cls._shared_connector = None

    def _get_headers(self, **kwargs: str) -> dict[str, str]:
        """
        Get request headers.

        Args:
            **kwargs: Additional header key-value pairs

        Returns:
            Dictionary of headers

        """
        headers = {
            "Content-Type": "application/x-java-serialized-object",
            "licenseId": LICENSE_ID,
            "lid": LICENSE_ID,
            "language": "en",
            "appVersion": SPOOF_APP_VERSION,
            "User-Agent": SPOOF_USER_AGENT,
            "system": SPOOF_SYSTEM,
            "appPlatform": SPOOF_APP_PLATFORM,
            "loginsession": getattr(self, "loginsession", ""),
            "userid": getattr(self, "userid", ""),
            **kwargs,
        }
        return headers

    def _get_directive_header(
        self, namespace: str, name: str, message_id_prefix: str, **kwargs: str
    ) -> dict[str, str]:
        """
        Get directive header for device control.

        Args:
            namespace: Directive namespace
            name: Directive name
            message_id_prefix: Prefix for message ID
            **kwargs: Additional header key-value pairs

        Returns:
            Dictionary containing directive header

        """
        timestamp = int(time.time())
        header = {
            "namespace": namespace,
            "name": name,
            "interfaceVersion": "2",
            "senderId": "sdk",
            "messageId": f"{message_id_prefix}-{timestamp}",
            **kwargs,
        }
        _LOGGER.debug("Generated directive header: %s", header)
        return header

    async def login(
        self, email: str | None = None, password: str | None = None
    ) -> bool:
        """Login to AuxCloud."""
        try:
            success = await self._perform_login(email, password)
        except (aiohttp.ClientError, TimeoutError) as ex:
            self._log_and_raise_auth_error(ex)
            return False  # Unreachable, but satisfies type checker
        except json.JSONDecodeError as ex:
            self._log_and_raise_auth_error(ex)
            return False  # Unreachable, but satisfies type checker

        if not success:
            msg = "Login failed: Invalid credentials"
            raise AuxCloudAuthError(msg)
        # Wait a brief moment after successful login before making other requests
        await asyncio.sleep(1)
        self._last_login = time.monotonic()
        return success

    async def relogin(self) -> None:
        """
        Re-login after the cloud rejected our session.

        Rate limited so a persistently rejected session cannot turn into a login loop;
        the caller then falls back to the coordinator's exponential backoff.
        """
        async with self._login_lock:
            since = time.monotonic() - self._last_login
            if self._last_login and since < MIN_RELOGIN_GAP:
                msg = f"Re-login suppressed, last login was {since:.0f}s ago"
                raise AuxCloudAuthError(msg)
            await self.login()

    def _log_and_raise_auth_error(self, ex: Exception) -> None:
        """Log the exception and raises an AuxCloudAuthError."""
        _LOGGER.exception("Login error: %s", str(ex))
        msg = f"Login failed: {ex!s}"
        raise AuxCloudAuthError(msg) from ex

    @create_retry_decorator()
    async def _perform_login(
        self, email: str | None = None, password: str | None = None
    ) -> bool:
        """Perform the actual login operation with retry."""
        email = email if email is not None else self.email
        password = password if password is not None else self.password
        _LOGGER.info("Attempting to login with email: %s", email)

        current_time = time.time()
        # Note: SHA1 is used here to match the original API implementation
        sha_password = hashlib.sha1(  # noqa: S324
            f"{password}{PASSWORD_ENCRYPT_KEY}".encode()
        ).hexdigest()
        payload = {
            "email": email,
            "password": sha_password,
            "companyid": COMPANY_ID,
            "lid": LICENSE_ID,
        }
        json_payload = json.dumps(payload, separators=(",", ":"))
        # Note: MD5 is used here to match the original API implementation
        token = hashlib.md5(  # noqa: S324
            f"{json_payload}{BODY_ENCRYPT_KEY}".encode()
        ).hexdigest()
        md5_hash = hashlib.md5(  # noqa: S324
            f"{current_time}{TIMESTAMP_TOKEN_ENCRYPT_KEY}".encode()
        ).digest()

        session = await self._get_session()
        async with self._post(
            session,
            f"{self.url}/account/login",
            data=encrypt_aes_cbc_zero_padding(
                AES_INITIAL_VECTOR, md5_hash, json_payload.encode()
            ),
            headers=self._get_headers(timestamp=f"{current_time}", token=token),
        ) as resp:
            data = await resp.text()
            json_data = json.loads(data)

            if "status" in json_data and json_data["status"] == 0:
                self.loginsession = json_data["loginsession"]
                self.userid = json_data["userid"]
                _LOGGER.info("Login successful for email: %s", email)
                return True

            error_msg = f"Login failed: {json_data.get('msg', data)}"
            raise AuxCloudAuthError(error_msg)

    async def get_devices(self, *, count_cycle: bool = True) -> list[dict[str, Any]]:
        """
        Get all devices across all families.

        Args:
            count_cycle: Advance the shared-devices "every Nth refresh" counter. Boost
                polls pass False so the option stays a count of normal refreshes.

        """
        _LOGGER.debug("Fetching all devices")
        all_devices = []
        cycle = self._cycle
        if count_cycle:
            self._cycle += 1
        try:
            # First get all families
            families = await self.list_families()

            # Then get devices for each family
            for family in families:
                family_id = family["familyid"]
                # Get regular devices
                devices = await self.list_devices(family_id)
                if devices:
                    all_devices.extend(devices)

                # Check for shared devices using cached method
                has_shared = await self._has_shared_devices(family_id)
                prefetched = self._prefetched_shared.pop(family_id, None)
                if not has_shared:
                    _LOGGER.debug(
                        "No shared devices found for family %s (cached)", family_id
                    )
                    continue

                if prefetched is not None:
                    shared_devices = prefetched
                elif cycle % self.shared_every == 0 or family_id not in self._shared_cache:
                    shared_devices = await self.list_devices(family_id, shared=True)
                else:
                    # Skipped cycle: reuse the last shared state so entities stay available
                    shared_devices = self._shared_cache[family_id]
                self._shared_cache[family_id] = shared_devices
                all_devices.extend(shared_devices or [])

        except Exception:
            _LOGGER.exception("Error getting devices")
            raise

        _LOGGER.debug("Fetched %d devices", len(all_devices))
        return all_devices

    @alru_cache(maxsize=1, ttl=3600)  # Cache 1 result for 1 hour
    @create_retry_decorator()
    async def list_families(self, retry_count: int = 0) -> list[dict[str, Any]]:
        """Get list of all families with retry."""
        _LOGGER.debug("Fetching list of families")
        max_retries = 3

        # Check if we're logged in
        if not hasattr(self, "loginsession") or not self.loginsession:
            _LOGGER.debug("No login session found, attempting to login")
            await self.login()

        session = await self._get_session()
        async with self._post(
            session,
            f"{self.url}/appsync/group/member/getfamilylist",
            headers=self._get_headers(),
        ) as response:
            data = await response.text()
            try:
                json_data = json.loads(data)
            except json.JSONDecodeError as ex:
                msg = f"Failed to decode JSON: {ex}"
                _LOGGER.exception(msg)
                json_data = {"status": -1, "msg": data}

            if "status" in json_data:
                if json_data["status"] == 0:
                    self.data = {}
                    for family in json_data["data"]["familyList"]:
                        self.data[family["familyid"]] = {
                            "id": family["familyid"],
                            "name": family["name"],
                            "rooms": [],
                            "devices": [],
                        }
                    _LOGGER.debug(
                        "Fetched family list: %s", json_data["data"]["familyList"]
                    )
                    return json_data["data"]["familyList"]
                if json_data["status"] == self.LOGIN_VALIDATION_FAILED:
                    if retry_count >= max_retries:
                        msg = "Login validation failed after retries"
                        _LOGGER.error(msg)
                        raise AuxCloudAuthError(msg)
                    # Login validation failed, re-login and retry the request
                    _LOGGER.warning("Login validation failed, attempting to re-login")
                    await self.login()
                    # Retry the request after re-login
                    return await self.list_families(retry_count + 1)

            msg = f"Failed to get families list: {data}"
            _LOGGER.error(msg)
            raise AuxCloudApiError(msg)

    @alru_cache(maxsize=32, ttl=3600)  # Cache per family for 1 hour
    async def _has_shared_devices(self, family_id: str) -> bool:
        """Check if family has any shared devices with 1-hour cache."""
        _LOGGER.debug("Checking for shared devices in family: %s", family_id)
        try:
            shared_devices = await self.list_devices(family_id, shared=True)
            # Let get_devices() reuse this result instead of fetching it again
            self._prefetched_shared[family_id] = shared_devices
            return len(shared_devices) > 0
        except AuxCloudApiError as ex:
            _LOGGER.warning("API error checking shared devices: %s", ex)
            return False
        except AuxCloudAuthError as ex:
            _LOGGER.warning("Authentication error checking shared devices: %s", ex)
            return False
        except aiohttp.ClientError as ex:
            _LOGGER.warning("Network error checking shared devices: %s", ex)
            return False

    async def list_devices(
        self, family_id: str, *, shared: bool = False
    ) -> list[dict[str, Any]]:
        """Get devices for a specific family."""
        _LOGGER.debug(
            "Fetching devices for family_id: %s, shared: %s", family_id, shared
        )
        devices = await self._fetch_device_list(family_id, shared=shared)

        # Initialize family data structure if needed
        if family_id not in self.data:
            self.data[family_id] = {
                "id": family_id,
                "name": "",  # Could be populated from family data
                "rooms": [],
                "devices": [],
            }

        # The per-device requests retry on their own; no retry around the whole list
        processed_devices = [await self._enrich_device(dev) for dev in devices]

        # Update internal cache - replace existing devices for this family
        if not shared:
            self.data[family_id]["devices"] = processed_devices
        else:
            # For shared devices, append to existing devices
            existing_ids = {d["endpointId"] for d in self.data[family_id]["devices"]}
            new_devices = [
                d for d in processed_devices if d["endpointId"] not in existing_ids
            ]
            self.data[family_id]["devices"].extend(new_devices)

        return processed_devices

    @create_retry_decorator()
    async def _fetch_device_list(
        self, family_id: str, *, shared: bool
    ) -> list[dict[str, Any]]:
        """Request the raw device list of a family with retry."""
        session = await self._get_session()
        device_endpoint = (
            "dev/query?action=select"
            if not shared
            else "sharedev/querylist?querytype=shared"
        )
        # The response is fully read before the per-device requests start, so the
        # connection is back in the pool (when keep-alive is on) for them to reuse.
        async with self._post(
            session,
            f"{self.url}/appsync/group/{device_endpoint}",
            data='{"pids":[]}' if not shared else '{"endpointId":""}',
            headers=self._get_headers(familyid=family_id),
        ) as response:
            data = await response.text()
        json_data = json.loads(data)

        if json_data.get("status") == self.LOGIN_VALIDATION_FAILED:
            msg = "Login session rejected by the cloud"
            raise AuxCloudSessionExpiredError(msg)
        if "status" not in json_data or json_data["status"] != 0:
            msg = f"Failed to get devices: {data}"
            raise AuxCloudApiError(msg)

        if "endpoints" in json_data["data"]:
            return json_data["data"]["endpoints"]
        if "shareFromOther" in json_data["data"]:
            return [dev["devinfo"] for dev in json_data["data"]["shareFromOther"]]
        return []

    async def _enrich_device(self, dev: dict[str, Any]) -> dict[str, Any]:
        """
        Add state, params and room temperature to a listed device.

        Only the power flag is polled first. A unit that is off keeps its last known
        params (1 request); the full data is fetched only when it is on or unknown.
        A failed power query raises so the coordinator backs off.
        """
        endpoint_id = dev["endpointId"]
        last = self._last_params.get(endpoint_id)
        power = await self.get_device_params(dev, ["pwr"])
        if last is not None and not power.get("pwr"):
            dev["params"] = {**last, "pwr": 0}
            cached = self._slow_cache.get(endpoint_id)
            if cached and "state" in cached[1]:
                dev["state"] = cached[1]["state"]
            return dev

        dev = await self._fetch_full_device(dev)
        if "params" in dev:
            self._last_params[endpoint_id] = dict(dev["params"])
        return dev

    async def _fetch_full_device(self, dev: dict[str, Any]) -> dict[str, Any]:
        """
        Fetch state, params and room temperature of a unit that is on.

        State and room temperature are re-fetched only every `ambient_interval`
        seconds (0 = every refresh); in between a refresh costs one request.
        """
        endpoint_id = dev["endpointId"]
        cached = self._slow_cache.get(endpoint_id)
        use_cache = (
            cached is not None
            and self.ambient_interval > 0
            and time.monotonic() - cached[0] < self.ambient_interval
        )

        # Creation order matters: state, params, ambient
        if use_cache:
            results: list[Any] = [None, await self._safe(self.get_device_params(dev)), None]
        elif self.keepalive:
            # One after another so the pooled connection is reused, not re-opened
            results = [
                await self._safe(self.query_device_state(endpoint_id, dev["devSession"])),
                await self._safe(self.get_device_params(dev)),
                await self._safe(self.get_device_params(dev, ["mode"])),
            ]
        else:
            results = await asyncio.gather(
                self.query_device_state(endpoint_id, dev["devSession"]),
                self.get_device_params(dev),
                self.get_device_params(dev, ["mode"]),
                return_exceptions=True,
            )
        state_result, params_result, ambient_result = results
        if isinstance(params_result, Exception):
            raise params_result

        slow: dict[str, Any] = dict(cached[1]) if cached else {}
        slow_ok = True
        if not use_cache:
            if isinstance(state_result, Exception):
                slow_ok = False
            else:
                slow["state"] = state_result["data"][0]["state"]
            if isinstance(ambient_result, Exception) or "envtemp" not in ambient_result:
                slow_ok = False
            else:
                slow["envtemp"] = ambient_result["envtemp"]
            if slow_ok:
                self._slow_cache[endpoint_id] = (time.monotonic(), slow)

        if "state" in slow:
            dev["state"] = slow["state"]
        dev["params"] = params_result
        if "envtemp" in slow:
            dev["params"]["envtemp"] = slow["envtemp"]
        _LOGGER.debug("Processed device %s", endpoint_id)
        return dev

    @staticmethod
    async def _safe(awaitable: Any) -> Any:
        """Await and return an exception instead of raising it (gather-style)."""
        try:
            return await awaitable
        except Exception as ex:  # noqa: BLE001
            return ex

    async def get_device_params(
        self, device: dict[str, Any], params: list[str] | None = None
    ) -> dict[str, Any]:
        """
        Get device parameters.

        Args:
            device: Device information dictionary
            params: List of parameter names to get

        Returns:
            Dictionary of parameter values

        """
        if params is None:
            params = []
        _LOGGER.debug(
            "Fetching device parameters for device: %s, params = %s",
            device.get("endpointId"),
            params,
        )
        return await self._act_device_params(device, "get", params)

    async def set_device_params(
        self, device: dict[str, Any], values: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Set device parameters.

        Args:
            device: Device information dictionary
            values: Dictionary of parameter names and values to set

        Returns:
            Dictionary of updated parameter values

        """
        _LOGGER.info(
            "Setting device parameters for device: %s with values: %s",
            device.get("endpointId"),
            values,
        )
        params = list(values.keys())
        vals = [[{"val": val, "idx": 1}] for val in values.values()]
        result = await self._act_device_params(device, "set", params, vals)
        # Keep the off-unit cache current so the next power-only poll does not undo it
        if (cached := self._last_params.get(device["endpointId"])) is not None:
            cached.update(result or values)
        if self.on_command is not None:
            self.on_command()
        return result

    @create_retry_decorator()
    async def query_device_state(
        self, device_id: str, dev_session: str
    ) -> dict[str, Any]:
        """Query device state with retry."""
        _LOGGER.debug("Querying device state for device_id: %s", device_id)
        session = await self._get_session()
        timestamp = int(time.time())
        data = {
            "directive": {
                "header": self._get_directive_header(
                    namespace="DNA.QueryState",
                    name="queryState",
                    messageType="controlgw.batch",
                    message_id_prefix=self.userid,
                    timestamp=f"{timestamp}",
                ),
                "payload": {
                    "studata": [{"did": device_id, "devSession": dev_session}],
                    "msgtype": "batch",
                },
            }
        }

        _LOGGER.debug("Sending query state request with data: %s", data)

        async with self._post(
            session,
            f"{self.url}/device/control/v2/querystate",
            data=json.dumps(data, separators=(",", ":")),
            headers=self._get_headers(),
        ) as response:
            data = await response.text()
            _LOGGER.debug("Received response: %s", data)
            json_data = json.loads(data)

            if (
                "event" in json_data
                and "payload" in json_data["event"]
                and json_data["event"]["payload"]["status"] == 0
            ):
                _LOGGER.debug(
                    "Queried device state for device_id %s: %s",
                    device_id,
                    json_data["event"]["payload"],
                )
                return json_data["event"]["payload"]

            _LOGGER.error("Failed to query device state: %s", data)
            msg = f"Failed to query device state: {data}"
            raise AuxCloudApiError(msg)

    @create_retry_decorator()
    async def query_device_temperature(
        self, device_id: str, dev_session: str
    ) -> dict[str, Any]:
        """Query device temperature with retry."""
        _LOGGER.debug("Querying device temperature for device_id: %s", device_id)
        session = await self._get_session()
        async with self._post(
            session,
            f"{self.url}/device/control/v2/temperaturesensor",
            data=json.dumps(
                self._build_temperature_query_data(device_id, dev_session),
                separators=(",", ":"),
            ),
            headers=self._get_headers(),
        ) as resp:
            data = await resp.text()
            _LOGGER.debug("Received response: %s", data)
            json_data = json.loads(data)

            if (
                "event" in json_data
                and "payload" in json_data["event"]
                and json_data["event"]["payload"]["status"] == 0
            ):
                _LOGGER.debug(
                    "Queried device temperature for device_id %s: %s",
                    device_id,
                    json_data["event"]["payload"],
                )
                return json_data["event"]["payload"]

            error_msg = f"Failed to query device temperature: {data}"
            _LOGGER.error(error_msg)
            raise AuxCloudApiError(error_msg)

    def _build_temperature_query_data(
        self, device_id: str, dev_session: str
    ) -> dict[str, Any]:
        """Build the data payload for temperature query."""
        timestamp = int(time.time())
        return {
            "directive": {
                "header": self._get_directive_header(
                    namespace="DNA.TemperatureSensor",
                    name="ReportState",
                    message_id_prefix=self.userid,
                    timestamp=f"{timestamp}",
                ),
                "endpoint": {
                    "endpointId": device_id,
                    "devicePairedInfo": {"did": device_id, "devSession": dev_session},
                    "cookie": {},
                },
                "payload": {},
            }
        }

    @create_retry_decorator()
    async def _act_device_params(
        self,
        device: dict[str, Any],
        act: str,
        params: list[str] | None = None,
        vals: list[list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        """Act on device parameters with retry."""
        params = params or []
        vals = vals or []
        _LOGGER.debug(
            "Acting on device parameters for device: %s, action: %s",
            device.get("endpointId"),
            act,
        )

        if act == "set" and len(params) != len(vals):
            msg = "Params and Vals must have the same length"
            raise ValueError(msg)

        session = await self._get_session()
        cookie = json.loads(base64.b64decode(device["cookie"].encode()))
        mapped_cookie = base64.b64encode(
            json.dumps(
                {
                    "device": {
                        "id": cookie["terminalid"],
                        "key": cookie["aeskey"],
                        "devSession": device["devSession"],
                        "aeskey": cookie["aeskey"],
                        "did": device["endpointId"],
                        "pid": device["productId"],
                        "mac": device["mac"],
                    }
                },
                separators=(",", ":"),
            ).encode()
        ).decode()

        data = {
            "directive": {
                "header": self._get_directive_header(
                    namespace="DNA.KeyValueControl",
                    name="KeyValueControl",
                    message_id_prefix=device["endpointId"],
                ),
                "endpoint": {
                    "devicePairedInfo": {
                        "did": device["endpointId"],
                        "pid": device["productId"],
                        "mac": device["mac"],
                        "devicetypeflag": device["devicetypeFlag"],
                        "cookie": mapped_cookie,
                    },
                    "endpointId": device["endpointId"],
                    "cookie": {},
                    "devSession": device["devSession"],
                },
                "payload": {
                    "act": act,
                    "params": params,
                    "vals": vals,
                },
            }
        }

        if self._is_ambient_mode(params):
            data["directive"]["payload"]["did"] = device["endpointId"]
            data["directive"]["payload"]["vals"] = [[{"val": 0, "idx": 1}]]

        async with self._post(
            session,
            f"{self.url}/device/control/v2/sdkcontrol",
            params={"license": LICENSE},
            data=json.dumps(data, separators=(",", ":")),
            headers=self._get_headers(),
        ) as resp:
            response_text = await resp.text()
            json_data = json.loads(response_text)

            if all(
                key in json_data.get("event", {}).get("payload", {})
                for key in ("data",)
            ):
                response = json.loads(json_data["event"]["payload"]["data"])
                _LOGGER.debug(
                    "Acted on device parameters for device %s: %s",
                    device.get("endpointId"),
                    response,
                )
                return {
                    response["params"][i]: response["vals"][i][0]["val"]
                    for i in range(len(response["params"]))
                }

            msg = f"Failed to {act} device parameters: {response_text}"
            raise ValueError(msg)

    def _is_ambient_mode(self, params: list[str]) -> bool:
        """
        Check if the parameters indicate ambient mode.

        Args:
            params: List of parameter names

        Returns:
            True if parameters indicate ambient mode, False otherwise

        """
        return len(params) == 1 and params[0] == "mode"

    async def refresh(self) -> None:
        """
        Refresh all family and device data.

        Raises:
            Exception: If refresh operation fails

        """
        _LOGGER.debug("Refreshing all data")
        try:
            family_data = await self.list_families()
            tasks = [
                self.list_devices(family["familyid"]) for family in family_data
            ] + [
                self.list_devices(family["familyid"], shared=True)
                for family in family_data
            ]
            # return_exceptions=True: one family/group failing must not cancel
            # its siblings' still-in-flight requests on the shared connector
            # (see #29 — a cancelled read can leave a broken connection in the
            # shared keep-alive pool for the next poll to trip over).
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    _LOGGER.warning(
                        "Error refreshing a family/device group: %s", result
                    )
        except Exception:
            _LOGGER.exception("Error refreshing data")
            raise
