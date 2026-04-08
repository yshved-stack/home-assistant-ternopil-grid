from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_DEBUG_LOGGING,
    DOMAIN,
    STORE_LEGACY_PING,
    STORE_LEGACY_SCHEDULE,
    STORE_PING_COORDINATOR,
    STORE_SCHEDULE_COORDINATOR,
)
from .coordinator import TernopilPingCoordinator, TernopilScheduleCoordinator

PLATFORMS = ["sensor", "binary_sensor", "select"]

_LOGGER = logging.getLogger(__name__)
_PACKAGE_LOGGER = logging.getLogger("custom_components.ternopil_grid")

_LEGACY_ENTITY_IDS: dict[str, str] = {
    "sensor.schedule_rolling_24h": "sensor.ternopil_grid_schedule_rolling_24h",
    "sensor.next_change": "sensor.ternopil_grid_next_change",
    "sensor.countdown": "sensor.ternopil_grid_countdown",
    "sensor.off_today": "sensor.ternopil_grid_off_today",
    "sensor.off_tomorrow": "sensor.ternopil_grid_off_tomorrow",
}


@callback
def _async_normalize_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    entity_registry = er.async_get(hass)
    for registry_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        new_entity_id = _LEGACY_ENTITY_IDS.get(registry_entry.entity_id)
        if not new_entity_id:
            continue
        try:
            entity_registry.async_update_entity(
                registry_entry.entity_id,
                new_entity_id=new_entity_id,
            )
        except ValueError as err:
            _LOGGER.warning(
                "Unable to normalize entity_id %s -> %s: %s",
                registry_entry.entity_id,
                new_entity_id,
                err,
            )


@callback
def _apply_logging_mode(entry: ConfigEntry) -> None:
    debug_enabled = bool(entry.options.get(CONF_DEBUG_LOGGING, False))
    _PACKAGE_LOGGER.setLevel(logging.DEBUG if debug_enabled else logging.WARNING)


async def _async_handle_entry_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
    _apply_logging_mode(entry)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    store = hass.data[DOMAIN].setdefault(entry.entry_id, {})
    _apply_logging_mode(entry)
    entry.async_on_unload(entry.add_update_listener(_async_handle_entry_update))

    schedule = TernopilScheduleCoordinator(hass, entry)
    ping = TernopilPingCoordinator(hass, entry)

    store[STORE_SCHEDULE_COORDINATOR] = schedule
    store[STORE_PING_COORDINATOR] = ping
    store[STORE_LEGACY_SCHEDULE] = schedule
    store[STORE_LEGACY_PING] = ping

    _async_normalize_entity_ids(hass, entry)

    await schedule.async_config_entry_first_refresh()
    await ping.async_config_entry_first_refresh()

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok and DOMAIN in hass.data:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return ok
