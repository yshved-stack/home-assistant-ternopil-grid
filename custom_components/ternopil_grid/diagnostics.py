from __future__ import annotations

from .const import (
    DOMAIN,
    CONF_GROUP,
    CONF_STREET_ID,
    CONF_PING_ENABLED,
    CONF_PING_IP,
    CONF_PING_METHOD,
    CONF_PING_PORT,
    CONF_PING_TIMEOUT,
    CONF_PING_INTERVAL,
    CONF_PING_ENTITY_ID,
    STORE_LEGACY_PING,
    STORE_LEGACY_SCHEDULE,
    STORE_PING_COORDINATOR,
    STORE_SCHEDULE_COORDINATOR,
)

async def async_get_config_entry_diagnostics(hass, entry):
    def get(k):
        return entry.options.get(k, entry.data.get(k))

    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    schedule = bucket.get(STORE_SCHEDULE_COORDINATOR) or bucket.get(STORE_LEGACY_SCHEDULE)
    ping = bucket.get(STORE_PING_COORDINATOR) or bucket.get(STORE_LEGACY_PING)

    segs = schedule.data if schedule else None
    ping_data = ping.data if ping else None

    return {
        "street_id": get(CONF_STREET_ID),
        "group": get(CONF_GROUP),
        "schedule_segments_count": len(segs) if isinstance(segs, list) else None,
        "ping_enabled": get(CONF_PING_ENABLED),
        "ping_ip": get(CONF_PING_IP),
        "ping_method": get(CONF_PING_METHOD),
        "ping_port": get(CONF_PING_PORT),
        "ping_timeout": get(CONF_PING_TIMEOUT),
        "ping_interval": get(CONF_PING_INTERVAL),
        "ping_entity_id": get(CONF_PING_ENTITY_ID),
        "ping_target_display": (ping_data or {}).get("target_display") if isinstance(ping_data, dict) else None,
        "ping_ok": (ping_data or {}).get("ok") if isinstance(ping_data, dict) else None,
        "ping_disabled": (ping_data or {}).get("disabled") if isinstance(ping_data, dict) else None,
    }
