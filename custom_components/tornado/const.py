# custom_components/tornado/const.py
"""Constants for the Tornado AC integration."""

DOMAIN = "tornado"
CONF_EMAIL = "email"
# Configuration key for password field - not the actual password value
CONF_PASSWORD = "password"  # noqa: S105
CONF_REGION = "region"

# Supported regions
REGIONS = ["eu", "usa"]

# Polling: a refresh costs the device list plus one power query per AC unit. Only
# units that are on additionally fetch state, params and ambient temperature.
CONF_SCAN_INTERVAL = "scan_interval"
DEFAULT_SCAN_INTERVAL = 60  # seconds
MIN_SCAN_INTERVAL = 15
MAX_SCAN_INTERVAL = 600
LOW_DATA_SCAN_INTERVAL = 300

# After a command poll faster for a short while to confirm the real device state.
CONF_BOOST_ENABLED = "boost_enabled"
CONF_BOOST_INTERVAL = "boost_interval"
CONF_BOOST_DURATION = "boost_duration"
DEFAULT_BOOST_ENABLED = True
DEFAULT_BOOST_INTERVAL = 10
DEFAULT_BOOST_DURATION = 45
MIN_BOOST_INTERVAL = 5
MAX_BOOST_INTERVAL = 60
MIN_BOOST_DURATION = 10
MAX_BOOST_DURATION = 300

# Seconds to reuse room temperature/state of an ON unit (0 = every refresh)
CONF_AMBIENT_INTERVAL = "ambient_interval"
DEFAULT_AMBIENT_INTERVAL = 0
MAX_AMBIENT_INTERVAL = 3600

CONF_SHARED_EVERY = "shared_every"
DEFAULT_SHARED_EVERY = 1
MAX_SHARED_EVERY = 60

CONF_KEEPALIVE = "keepalive"
DEFAULT_KEEPALIVE = False
KEEPALIVE_TIMEOUT = 5  # seconds; shorter than the cloud's idle timeout

CONF_LOW_DATA_MODE = "low_data_mode"

MAX_BACKOFF_INTERVAL = 600  # seconds
MIN_RELOGIN_GAP = 60  # seconds
