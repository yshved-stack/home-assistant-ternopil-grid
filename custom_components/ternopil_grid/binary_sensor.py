from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    ENTITY_PREFIX,
    STORE_LEGACY_PING,
    STORE_LEGACY_SCHEDULE,
    STORE_PING_COORDINATOR,
    STORE_SCHEDULE_COORDINATOR,
)
from .sensor import _build_context

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class TGBinaryDescription(BinarySensorEntityDescription):
    kind: str


DESCRIPTIONS: tuple[TGBinaryDescription, ...] = (
    TGBinaryDescription(
        key="planned_outage",
        name=f"{ENTITY_PREFIX} Planned Outage",
        icon="mdi:calendar-alert",
        kind="planned",
    ),
    TGBinaryDescription(
        key="power_ping",
        name=f"{ENTITY_PREFIX} Power Ping",
        icon="mdi:lan-check",
        kind="ping",
    ),
)


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _current_planned_color(segs: list[dict[str, Any]]) -> str | None:
    ts = _now_ts()
    for seg in segs or []:
        try:
            if float(seg["start_ts"]) <= ts < float(seg["end_ts"]):
                return str(seg.get("color"))
        except Exception:
            continue
    return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    schedule = data.get(STORE_SCHEDULE_COORDINATOR) or data.get(STORE_LEGACY_SCHEDULE)
    ping = data.get(STORE_PING_COORDINATOR) or data.get(STORE_LEGACY_PING)

    entities: list[BinarySensorEntity] = []
    for description in DESCRIPTIONS:
        if description.kind == "planned" and schedule is not None:
            entities.append(TGPlannedOutageBinary(schedule, ping, entry, description))
        if description.kind == "ping" and ping is not None:
            entities.append(TGPingBinary(ping, entry, description))
    async_add_entities(entities)


class TGPlannedOutageBinary(CoordinatorEntity, BinarySensorEntity):
    def __init__(self, coordinator, ping_coordinator, entry: ConfigEntry, description: TGBinaryDescription) -> None:
        super().__init__(coordinator)
        self._ping = ping_coordinator
        self.entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def is_on(self) -> bool:
        return _current_planned_color(self.coordinator.data or []) == "red"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        context = _build_context(self)
        return {
            "current_group": context.get("current_group"),
            "current_color": context.get("current_color"),
            "next_off_start": context.get("next_off_start"),
            "next_off_end": context.get("next_off_end"),
            "next_on_start": context.get("next_on_start"),
            "source_updated_at": context.get("source_updated_at"),
            "api_ok": context.get("api_ok"),
        }


class TGPingBinary(CoordinatorEntity, BinarySensorEntity):
    def __init__(self, coordinator, entry: ConfigEntry, description: TGBinaryDescription) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data or {}
        if data.get("disabled"):
            return None
        return bool(data.get("ok"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data or {}
        updated_at = None
        if getattr(self.coordinator, "last_update_success_time", None) is not None:
            updated_at = self.coordinator.last_update_success_time.isoformat()
        return {
            "ip": data.get("ip"),
            "configured_ip": data.get("configured_ip"),
            "port": data.get("port"),
            "method": data.get("method"),
            "timeout": data.get("timeout"),
            "target_entity_id": data.get("target_entity_id"),
            "target_name": data.get("target_name"),
            "target_display": data.get("target_display"),
            "history_hours": data.get("history_hours"),
            "disabled": data.get("disabled"),
            "history_slots": data.get("history_slots"),
            "source_updated_at": updated_at,
            "api_ok": bool(getattr(self.coordinator, "last_update_success", False)),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()
