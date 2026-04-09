from __future__ import annotations

DOMAIN = "ternopil_grid"
ENTITY_PREFIX = "Ternopil Grid"

# PowerOn API: fixed city (Ternopil)
DEFAULT_TERNOPIL_CITY_ID = 1032

# Config keys
CONF_CITY_ID = "city_id"
CONF_STREET_ID = "street_id"
CONF_GROUP = "group"

# Optional informational keys (if used in titles/UI)
CONF_STREET_NAME = "street_name"
CONF_HOUSE_NUMBER = "house_number"

# Entity naming
DEFAULT_NAME = "Ternopil Grid"
DEFAULT_POWER_SENSOR_NAME = "Ternopil Grid Power"
CONF_POWER_SENSOR_NAME = "power_sensor_name"

# Schedule update interval (seconds)
DEFAULT_UPDATE_INTERVAL = 1800  # 30 min

# Ping methods exposed in UI
PING_METHOD_OPTIONS = ["icmp", "tcp", "http", "entity"]

# Ping / connectivity options keys
CONF_PING_ENABLED = "ping_enabled"
CONF_PING_IP = "ping_ip"
CONF_PING_INTERVAL = "ping_interval"
CONF_PING_METHOD = "ping_method"
CONF_PING_PORT = "ping_port"
CONF_PING_TIMEOUT = "ping_timeout"
CONF_PING_ENTITY_ID = "ping_entity_id"
CONF_PING_HTTP_SSL = "ping_http_ssl"
CONF_PING_HTTP_PATH = "ping_http_path"
CONF_PING_HISTORY_HOURS = "ping_history_hours"
CONF_PING_DHCP_SOURCE = "ping_dhcp_source"
CONF_PING_DHCP_LOOKUP = "ping_dhcp_lookup"
CONF_PING_DHCP_TARGET = "ping_dhcp_target"
CONF_PING_DHCP_URL = "ping_dhcp_url"
CONF_PING_DHCP_FILE = "ping_dhcp_file"
CONF_DEBUG_LOGGING = "debug_logging"

# Ping defaults
DEFAULT_PING_ENABLED = False
DEFAULT_PING_IP = "1.1.1.1"
DEFAULT_PING_INTERVAL = 10  # seconds
DEFAULT_PING_METHOD = "icmp"
DEFAULT_PING_PORT = 80
DEFAULT_PING_TIMEOUT = 1.0  # seconds
DEFAULT_PING_HTTP_SSL = False
DEFAULT_PING_HTTP_PATH = "/"
DEFAULT_PING_HISTORY_HOURS = 24
DEFAULT_PING_DHCP_SOURCE = "disabled"
DEFAULT_PING_DHCP_LOOKUP = "auto"
DEFAULT_PING_DHCP_TARGET = ""
DEFAULT_PING_DHCP_URL = ""
DEFAULT_PING_DHCP_FILE = ""
DEFAULT_DEBUG_LOGGING = False
MAX_PING_HISTORY_HOURS = 24
DEFAULT_DHCP_CACHE_TTL = 60
DEFAULT_DHCP_REQUEST_TIMEOUT = 5.0
PING_DHCP_SOURCE_OPTIONS = ["disabled", "json_url", "json_file"]
PING_DHCP_LOOKUP_OPTIONS = ["auto", "hostname", "mac", "entity_id"]

# hass.data keys
STORE_SCHEDULE_COORDINATOR = "schedule_coordinator"
STORE_PING_COORDINATOR = "ping_coordinator"
STORE_LEGACY_SCHEDULE = "schedule"
STORE_LEGACY_PING = "ping"
