from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    STORE_LEGACY_PING,
    STORE_LEGACY_SCHEDULE,
    STORE_PING_COORDINATOR,
    STORE_SCHEDULE_COORDINATOR,
)
from .coordinator import TernopilPingCoordinator, TernopilScheduleCoordinator

PLATFORMS = ["sensor", "binary_sensor", "select"]

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    store = hass.data[DOMAIN].setdefault(entry.entry_id, {})

    schedule = TernopilScheduleCoordinator(hass, entry)
    ping = TernopilPingCoordinator(hass, entry)

    store[STORE_SCHEDULE_COORDINATOR] = schedule
    store[STORE_PING_COORDINATOR] = ping
    store[STORE_LEGACY_SCHEDULE] = schedule
    store[STORE_LEGACY_PING] = ping

    await schedule.async_config_entry_first_refresh()
    await ping.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok and DOMAIN in hass.data:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok
