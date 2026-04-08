from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Iterable

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_GROUP,
    CONF_HOUSE_NUMBER,
    CONF_STREET_ID,
    DEFAULT_PING_HISTORY_HOURS,
    CONF_STREET_NAME,
    DOMAIN,
    ENTITY_PREFIX,
    STORE_LEGACY_PING,
    STORE_LEGACY_SCHEDULE,
    STORE_PING_COORDINATOR,
    STORE_SCHEDULE_COORDINATOR,
)

_LOGGER = logging.getLogger(__name__)

SLOT_SECONDS = 1800
SLOTS_24H = 48
CHART_PAST_SLOTS = 4
CHART_TOTAL_SLOTS = 48

C_GREEN = "g"
C_RED = "r"
C_YELLOW = "y"
C_UNKNOWN = "u"

RGBA_UNKNOWN = "rgba(255,255,255,0.25)"
RGBA_GREEN = "rgba(74,201,106,0.75)"
RGBA_RED = "rgba(235,90,70,0.88)"
RGBA_YELLOW = "rgba(214,193,67,0.85)"
RGBA_TRANSPARENT = "rgba(0,0,0,0)"


def _bucket_ts(ts: float) -> int:
    return int(ts // SLOT_SECONDS) * SLOT_SECONDS


def _color_to_char(color: str | None) -> str:
    if color == "green":
        return C_GREEN
    if color == "red":
        return C_RED
    if color == "yellow":
        return C_YELLOW
    return C_UNKNOWN


def _char_to_rgba(ch: str, *, transparent_for_unknown: bool = False) -> str:
    if ch == C_GREEN:
        return RGBA_GREEN
    if ch == C_RED:
        return RGBA_RED
    if ch == C_YELLOW:
        return RGBA_YELLOW
    return RGBA_TRANSPARENT if transparent_for_unknown else RGBA_UNKNOWN


def _bins_from_segments(
    segments: Iterable[dict[str, Any]],
    start_ts: int,
    end_ts: int,
    *,
    slots: int = SLOTS_24H,
) -> list[str]:
    bins: list[str] = []
    segs = list(segments)

    for index in range(slots):
        bucket_start = start_ts + index * SLOT_SECONDS
        bucket_end = bucket_start + SLOT_SECONDS
        if bucket_start >= end_ts:
            break

        char = C_UNKNOWN
        for seg in segs:
            seg_start = float(seg.get("start_ts", 0))
            seg_end = float(seg.get("end_ts", 0))
            if seg_end <= bucket_start or seg_start >= bucket_end:
                continue
            char = _color_to_char(str(seg.get("color")) if seg.get("color") is not None else None)
            break
        bins.append(char)

    if len(bins) < slots:
        bins.extend([C_UNKNOWN] * (slots - len(bins)))
    return bins[:slots]


def _bins_from_ping_history(
    history_slots: list[dict[str, Any]],
    start_ts: int,
    *,
    slots: int = SLOTS_24H,
) -> list[str]:
    latest: dict[int, bool] = {}
    for slot in history_slots:
        try:
            ts = int(float(slot.get("ts")))
            ok = bool(slot.get("ok"))
        except Exception:
            continue
        latest[_bucket_ts(ts)] = ok

    bins: list[str] = []
    for index in range(slots):
        bucket_start = start_ts + index * SLOT_SECONDS
        ok = latest.get(_bucket_ts(bucket_start))
        if ok is True:
            bins.append(C_GREEN)
        elif ok is False:
            bins.append(C_RED)
        else:
            bins.append(C_UNKNOWN)
    return bins


def _infer_actual_bins(planned_bins: list[str], ping_bins: list[str]) -> list[str]:
    actual: list[str] = []
    for planned, ping in zip(planned_bins, ping_bins):
        if ping == C_UNKNOWN:
            actual.append(C_UNKNOWN)
        elif planned in (C_UNKNOWN, C_YELLOW):
            actual.append(ping)
        elif planned == ping:
            actual.append(planned)
        else:
            actual.append(C_YELLOW)
    return actual


def _next_change(planned_bins: list[str], now_ts: int, start_ts: int) -> tuple[int | None, int]:
    idx_now = int((now_ts - start_ts) // SLOT_SECONDS)
    if idx_now < 0:
        idx_now = 0
    if idx_now >= len(planned_bins):
        return (None, 0)

    current = planned_bins[idx_now]
    for index in range(idx_now + 1, len(planned_bins)):
        if planned_bins[index] != current:
            next_ts = start_ts + index * SLOT_SECONDS
            return (next_ts, max(0, next_ts - now_ts))
    return (None, 0)


def _next_change_from_segments(segs: list[dict[str, Any]], now_ts: int) -> tuple[int | None, int]:
    for seg in segs:
        try:
            start_ts = int(float(seg.get("start_ts")))
            end_ts = int(float(seg.get("end_ts")))
        except Exception:
            continue

        if start_ts <= now_ts < end_ts:
            return (end_ts, max(0, end_ts - now_ts))
        if start_ts > now_ts:
            return (start_ts, max(0, start_ts - now_ts))

    return (None, 0)


def _dt(ts: float | int | None) -> datetime | None:
    if ts is None:
        return None
    return dt_util.utc_from_timestamp(float(ts))


def _iso(ts: float | int | None) -> str | None:
    value = _dt(ts)
    return value.isoformat() if value else None


def _segment_window(segs: list[dict[str, Any]], now_ts: int, color: str) -> tuple[float | None, float | None]:
    current_match: tuple[float | None, float | None] | None = None
    future_match: tuple[float | None, float | None] | None = None

    for seg in segs:
        try:
            start_ts = float(seg.get("start_ts"))
            end_ts = float(seg.get("end_ts"))
        except Exception:
            continue
        if str(seg.get("color")) != color:
            continue
        if start_ts <= now_ts < end_ts:
            current_match = (start_ts, end_ts)
            break
        if start_ts > now_ts and future_match is None:
            future_match = (start_ts, end_ts)

    return current_match or future_match or (None, None)


def _next_on_start(segs: list[dict[str, Any]], now_ts: int) -> float | None:
    for seg in segs:
        try:
            start_ts = float(seg.get("start_ts"))
            end_ts = float(seg.get("end_ts"))
        except Exception:
            continue
        color = str(seg.get("color"))
        if color == "red" and start_ts <= now_ts < end_ts:
            return end_ts
        if color == "green" and start_ts > now_ts:
            return start_ts
    return None


def _current_segment(segs: list[dict[str, Any]], now_ts: int) -> dict[str, Any] | None:
    for seg in segs:
        try:
            if float(seg.get("start_ts")) <= now_ts < float(seg.get("end_ts")):
                return seg
        except Exception:
            continue
    return None


def _overlap_minutes(segs: list[dict[str, Any]], start_ts: int, end_ts: int, *, color: str) -> int:
    overlap_seconds = 0.0
    for seg in segs:
        try:
            seg_start = float(seg.get("start_ts"))
            seg_end = float(seg.get("end_ts"))
        except Exception:
            continue
        if str(seg.get("color")) != color:
            continue
        overlap_start = max(seg_start, start_ts)
        overlap_end = min(seg_end, end_ts)
        if overlap_end > overlap_start:
            overlap_seconds += overlap_end - overlap_start
    return int(round(overlap_seconds / 60.0))


def _fill_for_char(ch: str, *, row: str) -> str:
    if ch == C_GREEN:
        return "#49c96a"
    if ch == C_RED:
        return "#eb5a46"
    if ch == C_YELLOW:
        return "#d6c143"
    if row == "ping":
        return "url(#tg_unknown_ping)"
    return "url(#tg_unknown_soft)" if row == "actual" else "url(#tg_unknown)"


def _local_hhmm(ts: float) -> str:
    return dt_util.as_local(dt_util.utc_from_timestamp(ts)).strftime("%H:%M")


def _context_cache_key(entity: Any, now_ts: int) -> tuple[Any, ...]:
    coordinator = getattr(entity, "coordinator", None)
    ping = getattr(entity, "_ping", None)
    entry = getattr(entity, "entry", None)
    schedule_updated = getattr(coordinator, "last_update_success_time", None)
    ping_updated = getattr(ping, "last_update_success_time", None) if ping is not None else None

    street_id = ""
    street_name = ""
    house_number = ""
    group = ""
    if entry is not None:
        street_id = str(entry.options.get(CONF_STREET_ID, entry.data.get(CONF_STREET_ID, "")) or "")
        street_name = str(entry.data.get(CONF_STREET_NAME, "") or "")
        house_number = str(entry.data.get(CONF_HOUSE_NUMBER, "") or "")
        group = str(entry.data.get(CONF_GROUP, "") or "")

    return (
        int(now_ts),
        schedule_updated.isoformat() if schedule_updated is not None else None,
        ping_updated.isoformat() if ping_updated is not None else None,
        bool(getattr(coordinator, "last_update_success", False)),
        bool(getattr(ping, "last_update_success", False)) if ping is not None else None,
        street_id,
        street_name,
        house_number,
        group,
    )


def _build_chart_svg(
    planned_bins: list[str],
    actual_bins: list[str],
    chart_start_ts: int,
    now_ts: int,
) -> str:
    width = 1000
    height = 304
    left = 34
    right = 34
    content_width = width - left - right
    slot_gap = 4
    slot_width = (content_width - ((CHART_TOTAL_SLOTS - 1) * slot_gap)) / CHART_TOTAL_SLOTS
    planned_y = 82
    actual_y = 236
    pill_height = 48
    actual_height = 34
    chart_end_ts = chart_start_ts + (CHART_TOTAL_SLOTS * SLOT_SECONDS)
    marker_ratio = max(0.0, min(1.0, (now_ts - chart_start_ts) / float(chart_end_ts - chart_start_ts)))
    marker_x = left + (marker_ratio * content_width)

    def x_at(index: int) -> float:
        return left + (index * (slot_width + slot_gap))

    def pill(index: int, y: int, height_px: int, fill: str) -> str:
        return (
            f'<rect x="{x_at(index):.3f}" y="{y}" width="{slot_width:.3f}" height="{height_px}" '
            f'rx="{min(10, height_px / 2):.1f}" ry="{min(10, height_px / 2):.1f}" fill="{fill}" />'
        )

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="auto">',
        "<defs>",
        '<pattern id="tg_unknown" width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">',
        '<rect width="12" height="12" fill="#3c3d46" />',
        '<rect width="4" height="12" fill="#5b5d67" />',
        "</pattern>",
        '<pattern id="tg_unknown_soft" width="12" height="12" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">',
        '<rect width="12" height="12" fill="#31323a" />',
        '<rect width="4" height="12" fill="#4d4f59" />',
        "</pattern>",
        "</defs>",
        '<rect x="0" y="0" width="1000" height="304" rx="28" ry="28" fill="#262730" />',
        '<text x="30" y="52" font-size="28" font-family="Georgia, Times New Roman, serif" fill="#eadfd2">Planned</text>',
    ]

    for index, char in enumerate(planned_bins[:CHART_TOTAL_SLOTS]):
        parts.append(pill(index, planned_y, pill_height, _fill_for_char(char, row="planned")))

    for index in range(CHART_TOTAL_SLOTS + 1):
        tick_x = left + (index * (slot_width + slot_gap)) - (slot_gap / 2 if index > 0 else 0)
        tick_height = 14 if index % 2 == 0 else 8
        parts.append(
            f'<line x1="{tick_x:.3f}" y1="142" x2="{tick_x:.3f}" y2="{142 + tick_height}" stroke="#646774" stroke-width="1" opacity="0.9" />'
        )

    local_start = dt_util.as_local(dt_util.utc_from_timestamp(chart_start_ts))
    local_end = dt_util.as_local(dt_util.utc_from_timestamp(chart_end_ts))
    hour_tick = local_start.replace(minute=0, second=0, microsecond=0)
    if hour_tick < local_start:
        hour_tick += timedelta(hours=1)

    while hour_tick <= local_end:
        tick_ts = dt_util.as_utc(hour_tick).timestamp()
        if chart_start_ts <= tick_ts <= chart_end_ts:
            ratio = (tick_ts - chart_start_ts) / float(chart_end_ts - chart_start_ts)
            tick_x = left + (ratio * content_width)
            hour_text = hour_tick.strftime("%H").lstrip("0") or "0"
            major = hour_tick.hour % 3 == 0
            parts.append(
                f'<line x1="{tick_x:.3f}" y1="136" x2="{tick_x:.3f}" y2="168" stroke="#85889b" stroke-width="{2 if major else 1}" opacity="0.95" />'
            )
            parts.append(
                f'<text x="{tick_x:.3f}" y="190" text-anchor="middle" font-size="{"20" if major else "18"}" '
                f'font-weight="{"700" if major else "500"}" font-family="Georgia, Times New Roman, serif" '
                f'fill="{"#ece1d5" if major else "#9ca0b1"}">{hour_text}</text>'
            )
        hour_tick += timedelta(hours=1)

    parts.extend(
        [
            f'<line x1="{marker_x:.3f}" y1="68" x2="{marker_x:.3f}" y2="274" stroke="#7fa1ff" stroke-width="2" />',
            f'<polygon points="{marker_x - 8:.3f},68 {marker_x + 8:.3f},68 {marker_x:.3f},84" fill="#7fa1ff" />',
            f'<rect x="{marker_x + 6:.3f}" y="46" width="112" height="36" rx="10" ry="10" fill="#1e1f26" stroke="#cbc2b8" stroke-width="1.3" />',
            f'<text x="{marker_x + 62:.3f}" y="70" text-anchor="middle" font-size="18" font-weight="700" font-family="Georgia, Times New Roman, serif" fill="#f4ece3">{_local_hhmm(now_ts)}</text>',
            '<text x="30" y="224" font-size="18" font-family="Georgia, Times New Roman, serif" fill="#d7c6b8">Actual (inferred vs schedule)</text>',
        ]
    )

    for index, char in enumerate(actual_bins[:CHART_TOTAL_SLOTS]):
        parts.append(pill(index, actual_y, actual_height, _fill_for_char(char, row="actual")))

    parts.append("</svg>")
    return "".join(parts)


def _build_context(entity: "TernopilScheduleSensor") -> dict[str, Any]:
    now = dt_util.utcnow()
    now_ts = int(now.timestamp())
    cache_key = _context_cache_key(entity, now_ts)
    cache = getattr(entity.coordinator, "_tg_context_cache", None)
    if isinstance(cache, dict) and cache.get("key") == cache_key and isinstance(cache.get("value"), dict):
        return cache["value"]

    segs: list[dict[str, Any]] = entity.coordinator.data or []

    local_now = dt_util.as_local(now)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_ts = int(dt_util.as_utc(local_midnight).timestamp())
    day_end_ts = day_start_ts + 86400
    planned_bins = _bins_from_segments(segs, day_start_ts, day_end_ts)
    planned_str = "".join(planned_bins)

    ping_ok = None
    ping_disabled = True
    ping_history_hours = DEFAULT_PING_HISTORY_HOURS
    ping_bins_day = [C_UNKNOWN] * SLOTS_24H
    history_slots = []
    ping_target_entity_id = None
    ping_target_name = None
    ping_target_display = None
    ping_ip = None
    ping_port = None
    ping_method = None

    if entity._ping is not None:
        ping_data = getattr(entity._ping, "data", None) or {}
        ping_ok = ping_data.get("ok")
        ping_disabled = bool(ping_data.get("disabled", True))
        history_slots = ping_data.get("history_slots") or []
        ping_history_hours = int(getattr(entity._ping, "_history_hours", DEFAULT_PING_HISTORY_HOURS) or DEFAULT_PING_HISTORY_HOURS)
        ping_target_entity_id = ping_data.get("target_entity_id")
        ping_target_name = ping_data.get("target_name")
        ping_target_display = ping_data.get("target_display")
        ping_ip = ping_data.get("ip")
        ping_port = ping_data.get("port")
        ping_method = ping_data.get("method")
        if isinstance(history_slots, list):
            ping_bins_day = _bins_from_ping_history(history_slots, day_start_ts)

    ping_str = "".join(ping_bins_day)
    actual_bins_day = _infer_actual_bins(planned_bins, ping_bins_day)
    actual_str = "".join(actual_bins_day)
    planned_rgba = [_char_to_rgba(ch) for ch in planned_bins]
    actual_rgba = [_char_to_rgba(ch) for ch in actual_bins_day]
    ping_rgba = [_char_to_rgba(ch, transparent_for_unknown=True) for ch in ping_bins_day]
    next_ts, countdown = _next_change_from_segments(segs, now_ts)

    today0 = dt_util.as_utc(local_now.replace(hour=0, minute=0, second=0, microsecond=0)).timestamp()
    tomorrow0 = today0 + 86400

    def off_minutes(day_start_utc_ts: int) -> int:
        day_bins = _bins_from_segments(segs, int(day_start_utc_ts), int(day_start_utc_ts + 86400))
        return int(sum(1 for ch in day_bins if ch == C_RED) * (SLOT_SECONDS // 60))

    off_today = off_minutes(int(today0))
    off_tomorrow = off_minutes(int(tomorrow0))

    rolling_start_ts = _bucket_ts(now_ts) - (CHART_PAST_SLOTS * SLOT_SECONDS)
    rolling_end_ts = rolling_start_ts + (CHART_TOTAL_SLOTS * SLOT_SECONDS)
    rolling_planned_bins = _bins_from_segments(
        segs,
        rolling_start_ts,
        rolling_end_ts,
        slots=CHART_TOTAL_SLOTS,
    )
    rolling_ping_bins = (
        _bins_from_ping_history(history_slots, rolling_start_ts, slots=CHART_TOTAL_SLOTS)
        if isinstance(history_slots, list)
        else [C_UNKNOWN] * CHART_TOTAL_SLOTS
    )
    rolling_actual_bins = _infer_actual_bins(rolling_planned_bins, rolling_ping_bins)

    off_next_24h = _overlap_minutes(segs, now_ts, now_ts + 86400, color="red")
    next_off_start, next_off_end = _segment_window(segs, now_ts, "red")
    next_on_start = _next_on_start(segs, now_ts)
    current_seg = _current_segment(segs, now_ts)

    source_updated_at = None
    if getattr(entity.coordinator, "last_update_success_time", None) is not None:
        source_updated_at = entity.coordinator.last_update_success_time.isoformat()

    street_name = str(entity.entry.data.get(CONF_STREET_NAME, "") or "")
    house_number = str(entity.entry.data.get(CONF_HOUSE_NUMBER, "") or "")
    group = str(entity.entry.data.get(CONF_GROUP, "") or "")
    ping_last_ok = getattr(entity._ping, "last_update_success", None) if entity._ping is not None else None
    api_ok = bool(getattr(entity.coordinator, "last_update_success", False))

    current_color = str(current_seg.get("color")) if current_seg else "unknown"
    current_state = {
        "green": "power_on",
        "red": "planned_outage",
        "yellow": "limited",
    }.get(current_color, "unknown")

    events = [
        {"kind": "current_state", "at": now.isoformat(), "value": current_state},
        {"kind": "next_change", "at": _iso(next_ts), "countdown_s": int(countdown)},
        {"kind": "ping", "at": now.isoformat(), "ok": ping_ok, "disabled": ping_disabled},
    ]

    context = {
        "state": planned_str,
        "window_start_ts": day_start_ts,
        "window_end_ts": day_end_ts,
        "bin_seconds": SLOT_SECONDS,
        "bins_30m_str": planned_str,
        "bins_30m": [
            {
                "start_ts": day_start_ts + index * SLOT_SECONDS,
                "end_ts": day_start_ts + (index + 1) * SLOT_SECONDS,
                "state": planned_bins[index],
            }
            for index in range(SLOTS_24H)
        ],
        "actual_bins_30m_str": actual_str,
        "actual_ping_bins_30m_str": ping_str,
        "planned_bars": planned_rgba,
        "actual_bars": actual_rgba,
        "actual_ping_bars": ping_rgba,
        "chart_svg": _build_chart_svg(
            rolling_planned_bins,
            rolling_actual_bins,
            rolling_start_ts,
            now_ts,
        ),
        "chart_window_start_ts": rolling_start_ts,
        "chart_window_end_ts": rolling_end_ts,
        "chart_now_ts": now_ts,
        "chart_mode": "rolling_24h_window",
        "next_change": _dt(next_ts),
        "next_change_iso": _iso(next_ts),
        "countdown": int(countdown),
        "off_today": off_today,
        "off_tomorrow": off_tomorrow,
        "off_next_24h": off_next_24h,
        "ping_ok": ping_ok,
        "ping_disabled": ping_disabled,
        "ping_api_ok": ping_last_ok,
        "ping_history_hours": ping_history_hours,
        "ping_target_entity_id": ping_target_entity_id,
        "ping_target_name": ping_target_name,
        "ping_target_display": ping_target_display,
        "ping_ip": ping_ip,
        "ping_port": ping_port,
        "ping_method": ping_method,
        "current_group": group,
        "group": group,
        "street": street_name,
        "house_number": house_number,
        "source_updated_at": source_updated_at,
        "api_ok": api_ok,
        "next_off_start": _iso(next_off_start),
        "next_off_end": _iso(next_off_end),
        "next_on_start": _iso(next_on_start),
        "current_color": current_color,
        "current_state": current_state,
        "events": events,
    }
    entity.coordinator._tg_context_cache = {"key": cache_key, "value": context}
    return context


SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(key="schedule_rolling_24h", name=f"{ENTITY_PREFIX} Schedule Rolling 24h", icon="mdi:timeline-clock"),
    SensorEntityDescription(key="next_change", name=f"{ENTITY_PREFIX} Next Change", device_class="timestamp", icon="mdi:calendar-clock"),
    SensorEntityDescription(
        key="countdown",
        name=f"{ENTITY_PREFIX} Countdown",
        native_unit_of_measurement="s",
        icon="mdi:timer",
    ),
    SensorEntityDescription(key="off_today", name=f"{ENTITY_PREFIX} Off Today", icon="mdi:calendar-today", native_unit_of_measurement="min"),
    SensorEntityDescription(key="off_tomorrow", name=f"{ENTITY_PREFIX} Off Tomorrow", icon="mdi:calendar", native_unit_of_measurement="min"),
    SensorEntityDescription(key="activity_log", name=f"{ENTITY_PREFIX} Activity Log", icon="mdi:clipboard-text-clock-outline"),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    store = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    schedule = store.get(STORE_SCHEDULE_COORDINATOR) or store.get(STORE_LEGACY_SCHEDULE)
    ping = store.get(STORE_PING_COORDINATOR) or store.get(STORE_LEGACY_PING)

    if not schedule:
        _LOGGER.error("Missing schedule coordinator in hass.data")
        return

    async_add_entities([TernopilScheduleSensor(hass, entry, schedule, ping, description) for description in SENSORS])


class TernopilScheduleSensor(CoordinatorEntity, SensorEntity):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        schedule_coordinator,
        ping_coordinator,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(schedule_coordinator)
        self.hass = hass
        self.entry = entry
        self._ping = ping_coordinator
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_icon = description.icon

    @property
    def native_value(self) -> Any:
        attrs = self.extra_state_attributes
        key = self.entity_description.key
        if key == "schedule_rolling_24h":
            return attrs.get("state", C_UNKNOWN * SLOTS_24H)
        if key == "next_change":
            return attrs.get("next_change")
        if key == "countdown":
            return attrs.get("countdown")
        if key == "off_today":
            return attrs.get("off_today")
        if key == "off_tomorrow":
            return attrs.get("off_tomorrow")
        if key == "activity_log":
            return attrs.get("current_state")
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        context = _build_context(self)
        if self.entity_description.key == "activity_log":
            return {
                "events": context.get("events", []),
                "current_state": context.get("current_state"),
                "current_group": context.get("current_group"),
                "next_change": context.get("next_change_iso"),
                "next_off_start": context.get("next_off_start"),
                "next_off_end": context.get("next_off_end"),
                "next_on_start": context.get("next_on_start"),
                "source_updated_at": context.get("source_updated_at"),
                "api_ok": context.get("api_ok"),
            }
        return context

    async def async_update(self) -> None:
        await super().async_update()
