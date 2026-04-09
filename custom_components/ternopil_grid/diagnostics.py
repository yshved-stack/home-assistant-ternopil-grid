from __future__ import annotations

from .const import (
    CONF_DEBUG_LOGGING,
    DOMAIN,
    CONF_GROUP,
    CONF_PING_DHCP_FILE,
    CONF_PING_DHCP_LOOKUP,
    CONF_PING_DHCP_SOURCE,
    CONF_PING_DHCP_TARGET,
    CONF_PING_DHCP_URL,
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
        "schedule_last_success_at": getattr(schedule, "_tg_last_success_at", None).isoformat() if getattr(schedule, "_tg_last_success_at", None) is not None else None,
        "schedule_last_failure_at": getattr(schedule, "_tg_last_failure_at", None).isoformat() if getattr(schedule, "_tg_last_failure_at", None) is not None else None,
        "schedule_last_error": str(getattr(schedule, "_tg_last_error", "") or "") if schedule else "",
        "schedule_refresh_count": int(getattr(schedule, "_tg_refresh_count", 0) or 0) if schedule else 0,
        "schedule_empty_count": int(getattr(schedule, "_tg_empty_count", 0) or 0) if schedule else 0,
        "ping_enabled": get(CONF_PING_ENABLED),
        "debug_logging": get(CONF_DEBUG_LOGGING),
        "ping_dhcp_source": get(CONF_PING_DHCP_SOURCE),
        "ping_dhcp_lookup": get(CONF_PING_DHCP_LOOKUP),
        "ping_dhcp_target": get(CONF_PING_DHCP_TARGET),
        "ping_dhcp_url": get(CONF_PING_DHCP_URL),
        "ping_dhcp_file": get(CONF_PING_DHCP_FILE),
        "ping_ip": get(CONF_PING_IP),
        "ping_method": get(CONF_PING_METHOD),
        "ping_port": get(CONF_PING_PORT),
        "ping_timeout": get(CONF_PING_TIMEOUT),
        "ping_interval": get(CONF_PING_INTERVAL),
        "ping_entity_id": get(CONF_PING_ENTITY_ID),
        "ping_target_display": (ping_data or {}).get("target_display") if isinstance(ping_data, dict) else None,
        "ping_effective_ip": (ping_data or {}).get("ip") if isinstance(ping_data, dict) else None,
        "ping_resolved_target_ip": (ping_data or {}).get("resolved_target_ip") if isinstance(ping_data, dict) else None,
        "ping_dhcp_target_ip": (ping_data or {}).get("dhcp_target_ip") if isinstance(ping_data, dict) else None,
        "ping_dhcp_match_mode": (ping_data or {}).get("dhcp_match_mode") if isinstance(ping_data, dict) else None,
        "ping_target_ip_source": (ping_data or {}).get("target_ip_source") if isinstance(ping_data, dict) else None,
        "ping_ok": (ping_data or {}).get("ok") if isinstance(ping_data, dict) else None,
        "ping_disabled": (ping_data or {}).get("disabled") if isinstance(ping_data, dict) else None,
        "ping_last_success_at": getattr(ping, "_tg_last_success_at", None).isoformat() if getattr(ping, "_tg_last_success_at", None) is not None else None,
        "ping_last_failure_at": getattr(ping, "_tg_last_failure_at", None).isoformat() if getattr(ping, "_tg_last_failure_at", None) is not None else None,
        "ping_last_error": str(getattr(ping, "_tg_last_error", "") or "") if ping else "",
        "ping_success_count": int(getattr(ping, "_tg_success_count", 0) or 0) if ping else 0,
        "ping_failure_count": int(getattr(ping, "_tg_failure_count", 0) or 0) if ping else 0,
        "ping_consecutive_failures": int(getattr(ping, "_tg_consecutive_failures", 0) or 0) if ping else 0,
        "ping_dhcp_last_success_at": getattr(ping, "_tg_dhcp_last_success_at", None).isoformat() if getattr(ping, "_tg_dhcp_last_success_at", None) is not None else None,
        "ping_dhcp_last_failure_at": getattr(ping, "_tg_dhcp_last_failure_at", None).isoformat() if getattr(ping, "_tg_dhcp_last_failure_at", None) is not None else None,
        "ping_dhcp_last_error": str(getattr(ping, "_tg_dhcp_last_error", "") or "") if ping else "",
        "ping_dhcp_fetch_count": int(getattr(ping, "_tg_dhcp_fetch_count", 0) or 0) if ping else 0,
    }
